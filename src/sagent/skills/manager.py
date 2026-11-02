"""Skill 管理器。

负责把 SkillConfig 装配为可用的发现能力：
- 按固定顺序构建 Skill 根目录列表（项目级、用户级、配置额外目录）；
- 执行一次发现扫描并缓存结果（不自动刷新）；
- 解析技能名称引用：支持短名与来源限定名（"source:name"，多个额外目录
  时 source 为 extra1、extra2 等编号）。

正文加载、预算校验与资源路径解析由后续任务实现，本模块不涉及。
"""

from __future__ import annotations

from pathlib import Path

from ..config.models import SkillConfig
from ..observability import get_logger
from .discovery import SkillDiscovery, SkillRoot
from .models import DiscoveryResult, Skill, SkillIssue, SkillSource

logger = get_logger(__name__)


def _resolve_project_root(user_home: Path) -> Path:
    """从当前运行目录向上查找包含 .sagent 目录的最近祖先作为项目根。

    未找到时使用当前运行目录，保证从任意子目录启动都能命中项目级技能目录。
    遍历在用户主目录处停止：用户主目录下的 .sagent 是用户级技能根，不能被
    当作项目级根，否则会在无项目目录的场景下把用户级技能重复发现为项目级技能。

    参数:
        user_home: 用户主目录（测试可注入隔离路径），向上遍历的边界。

    返回:
        项目根目录。
    """
    cwd = Path.cwd()
    # 真实用户主目录同样作为边界：注入的测试主目录之外，运行目录仍可能位于
    # 真实主目录之下，此时不应把真实主目录的 .sagent 当作项目级根
    home_dirs = {user_home, Path.home()}
    for candidate in (cwd, *cwd.parents):
        if candidate in home_dirs:
            break
        if (candidate / ".sagent").is_dir():
            return candidate
    return cwd


class SkillNotFoundError(Exception):
    """Skill 名称解析失败异常，异常消息为中文原因。"""


class SkillManager:
    """Skill 管理器：装配发现根目录、执行发现并解析技能名称引用。"""

    def __init__(
        self,
        config: SkillConfig,
        *,
        project_root: Path | None = None,
        user_home: Path | None = None,
    ) -> None:
        """初始化管理器，仅构建根目录列表与发现器，不在构造时扫描。

        参数:
            config: Skill 功能配置。
            project_root: 项目根目录；缺省时从当前运行目录向上查找包含
                .sagent 目录的最近祖先，未找到则使用当前运行目录。
            user_home: 用户主目录，缺省为 Path.home()（测试可注入隔离）。
        """
        self._config = config
        # 先确定用户主目录，Project 根向上查找时以它为边界，避免把用户级技能误判为项目级
        user_home = user_home if user_home is not None else Path.home()
        project_root = (
            project_root if project_root is not None else _resolve_project_root(user_home)
        )
        # 根列表顺序固定：项目级 -> 用户级 -> 配置额外目录；
        # 项目级与用户级为隐式默认目录，未创建时跳过而不记诊断
        roots: list[SkillRoot] = [
            SkillRoot(
                path=project_root / ".sagent" / "skills",
                source=SkillSource.PROJECT,
                optional=True,
            ),
            SkillRoot(
                path=user_home / ".sagent" / "skills",
                source=SkillSource.USER,
                optional=True,
            ),
        ]
        extra_paths: list[Path] = []
        for raw_dir in config.extra_dirs:
            # expanduser 处理 ~ 展开用户主目录；相对路径按运行目录解析
            expanded = Path(raw_dir).expanduser()
            if not expanded.is_absolute():
                expanded = Path.cwd() / expanded
            extra_paths.append(expanded)
        for index, path in enumerate(extra_paths, start=1):
            # 多个额外目录时按配置顺序编号（extra1、extra2...），使同名技能
            # 可用限定名分别选中；只有一个额外目录时前缀保持 extra
            qualifier = "extra" if len(extra_paths) == 1 else f"extra{index}"
            roots.append(SkillRoot(path=path, source=SkillSource.EXTRA, qualifier=qualifier))
        self._roots = roots
        self._discovery = SkillDiscovery(roots, allow=config.allow, deny=config.deny)
        self._result: DiscoveryResult | None = None

    def discover(self) -> DiscoveryResult:
        """执行发现扫描并缓存结果。

        首次调用执行扫描，之后调用直接返回缓存结果（同一实例，不自动刷新）。

        返回:
            包含全部通过校验的 Skill 与诊断信息的结果。
        """
        if self._result is None:
            self._result = self._discovery.discover()
            skills = self._result.skills
            source_counts = {source: 0 for source in SkillSource}
            for skill in skills:
                source_counts[skill.source] += 1
            logger.info(
                "Skill 管理器初始化完成",
                extra={
                    "event": "skills_initialized",
                    "total": len(skills),
                    "issue_count": len(self._result.issues),
                    "project_count": source_counts[SkillSource.PROJECT],
                    "user_count": source_counts[SkillSource.USER],
                    "extra_count": source_counts[SkillSource.EXTRA],
                },
            )
        return self._result

    @property
    def skills(self) -> list[Skill]:
        """全部通过校验的 Skill；未扫描时自动触发一次发现。"""
        return self.discover().skills

    @property
    def issues(self) -> list[SkillIssue]:
        """全部诊断信息；未扫描时自动触发一次发现。"""
        return self.discover().issues

    def resolve(self, ref: str) -> Skill:
        """解析技能引用为 Skill 实例。

        参数:
            ref: 技能引用，支持短名（name）与来源限定名（"source:name"）。

        返回:
            命中的 Skill 实例。

        异常:
            SkillNotFoundError: 引用为空、来源非法、技能未知或短名命中多个技能。
        """
        text = ref.strip()
        if not text:
            raise SkillNotFoundError("Skill 引用为空，请提供技能名或来源限定名（格式 source:name）")
        if ":" in text:
            return self._resolve_qualified(text)
        return self._resolve_short_name(text)

    def _resolve_qualified(self, ref: str) -> Skill:
        """按限定名 "source:name" 精确解析。

        参数:
            ref: 来源限定名引用。

        返回:
            命中的 Skill 实例。

        异常:
            SkillNotFoundError: 来源前缀非法或限定名未命中任何技能。
        """
        source_text, _, name_text = ref.partition(":")
        source_text = source_text.strip()
        name_text = name_text.strip()
        # 合法前缀来自装配好的根目录：project、user 以及 extra 或 extra1/extra2 等编号
        qualifiers = [root.qualifier or root.source.value for root in self._roots]
        if source_text not in qualifiers:
            raise SkillNotFoundError(
                f"未知的 Skill 来源: {source_text!r}，合法来源为: {', '.join(qualifiers)}"
            )
        qualified = f"{source_text}:{name_text}"
        for skill in self.skills:
            if skill.qualified_name == qualified:
                return skill
        raise SkillNotFoundError(f"未知的 Skill: {qualified}")

    def _resolve_short_name(self, name: str) -> Skill:
        """按短名 name 解析，命中多个时要求使用来源限定名。

        参数:
            name: 技能短名。

        返回:
            唯一命中的 Skill 实例。

        异常:
            SkillNotFoundError: 未命中任何技能，或命中多个技能（名称歧义）。
        """
        matches = [skill for skill in self.skills if skill.name == name]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SkillNotFoundError(f"未知的 Skill: {name}")
        qualified_names = ", ".join(sorted(skill.qualified_name for skill in matches))
        raise SkillNotFoundError(
            f"Skill 名称歧义: {name} 命中多个技能: {qualified_names}，"
            "请使用来源限定名（格式 source:name）"
        )
