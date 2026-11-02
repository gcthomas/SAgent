"""Skill 发现器。

扫描多个 Skill 根目录（项目、用户主目录与配置额外目录）的直接子目录，
解析并校验各子目录下的 SKILL.md，产出通过校验的 Skill 列表与诊断信息。

规则：
- 按传入的 roots 顺序逐个扫描，单个根目录或单个 Skill 失败不影响其他；
- 只遍历根目录的直接子目录，嵌套子目录不作为 Skill；
- 同名 Skill 跨来源全部保留，不做覆盖（由后续解析层处理限定名）；
- allow / deny 过滤：先 allow 后 deny，被过滤的 Skill 记入 issues 便于诊断。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..observability import get_logger
from .models import DiscoveryResult, Skill, SkillIssue, SkillSource
from .parser import SkillParseError, parse_skill_md

logger = get_logger(__name__)

# Skill 定义文件名，固定不可配置
SKILL_MD_FILENAME = "SKILL.md"


@dataclass
class SkillRoot:
    """一个待扫描的 Skill 根目录。

    属性:
        path: 根目录路径（如 <项目>/.sagent/skills）。
        source: 该根目录对应的 Skill 来源类型。
        qualifier: 限定名前缀，多个额外目录时由调用方指定 extra1、extra2 等编号；
            为 None 时使用 source.value 作为前缀。
        optional: 目录不存在时是否允许跳过而不记诊断。项目级、用户级这类
            隐式默认根目录未创建属于正常情况，应设为 True；显式配置的
            extra 目录缺失通常意味着配置路径有误，保持 False 以便诊断。
    """

    path: Path
    source: SkillSource
    qualifier: str | None = None
    optional: bool = False


class SkillDiscovery:
    """Skill 发现器：扫描多个根目录的直接子目录，解析并校验 SKILL.md。"""

    def __init__(
        self,
        roots: list[SkillRoot],
        allow: list[str] | None = None,
        deny: list[str] | None = None,
    ) -> None:
        """初始化发现器。

        参数:
            roots: 待扫描的 Skill 根目录列表，按列表顺序扫描。
            allow: 允许的 Skill 名称列表；非空时仅保留命中的 Skill。
            deny: 禁止的 Skill 名称列表；非空时移除命中的 Skill。
        """
        self._roots = roots
        self._allow = allow
        self._deny = deny

    def discover(self) -> DiscoveryResult:
        """扫描全部根目录，返回 DiscoveryResult。

        返回:
            包含全部通过校验的 Skill（含跨来源重名）与诊断信息的结果。
        """
        skills: list[Skill] = []
        issues: list[SkillIssue] = []
        for root in self._roots:
            self._scan_root(root, skills, issues)
        skills, filter_issues = self._apply_filter(skills)
        issues.extend(filter_issues)
        logger.info(
            "Skill 扫描完成",
            extra={
                "event": "skills_discovered",
                "total": len(skills),
                "filtered_count": len(filter_issues),
                "issue_count": len(issues),
            },
        )
        return DiscoveryResult(skills=skills, issues=issues)

    def _scan_root(
        self,
        root: SkillRoot,
        skills: list[Skill],
        issues: list[SkillIssue],
    ) -> None:
        """扫描单个根目录的直接子目录。

        根目录不存在、不是目录或不可读时记一条诊断后返回；
        普通文件与非目录条目直接跳过。

        参数:
            root: 待扫描的根目录。
            skills: 收集通过校验的 Skill 的列表。
            issues: 收集诊断信息的列表。
        """
        path = root.path
        if not path.exists():
            # 隐式默认根目录（项目级、用户级）不存在属于正常情况，跳过且不记诊断
            if root.optional:
                logger.debug(
                    "Skill 根目录不存在，已跳过",
                    extra={
                        "event": "skill_root_skipped",
                        "root": str(path),
                        "source": root.source.value,
                    },
                )
                return
            issues.append(
                SkillIssue(directory=path, reason=f"Skill 根目录不存在: {path}")
            )
            return
        if not path.is_dir():
            issues.append(
                SkillIssue(directory=path, reason=f"Skill 根目录不是目录: {path}")
            )
            return
        try:
            entries = sorted(path.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            issues.append(
                SkillIssue(directory=path, reason=f"Skill 根目录不可读: {path}: {exc}")
            )
            return
        for entry in entries:
            # 非目录（普通文件、隐藏文件如 .DS_Store 等）直接跳过
            if not entry.is_dir():
                continue
            skill, issue = self._load_skill(entry, root)
            if skill is not None:
                skills.append(skill)
            if issue is not None:
                issues.append(issue)

    def _load_skill(
        self,
        directory: Path,
        root: SkillRoot,
    ) -> tuple[Skill | None, SkillIssue | None]:
        """加载单个 Skill 子目录。

        参数:
            directory: Skill 自身目录（根目录的直接子目录）。
            root: 所属根目录，用于填充来源与根目录信息。

        返回:
            元组 (Skill 或 None, 诊断或 None)。成功时第二项为 None；
            失败时第一项为 None，第二项为中文诊断。
        """
        skill_md = directory / SKILL_MD_FILENAME
        if not skill_md.is_file():
            return None, SkillIssue(directory=directory, reason="缺少 SKILL.md")
        try:
            skill = parse_skill_md(skill_md)
        except SkillParseError as exc:
            return None, SkillIssue(directory=directory, reason=str(exc))
        except OSError as exc:
            return None, SkillIssue(directory=directory, reason=f"读取 SKILL.md 失败: {exc}")
        # 覆盖 parser 中的占位值，填充真实来源、根目录与限定名前缀
        skill.source = root.source
        skill.root_dir = root.path
        skill.qualifier = root.qualifier or root.source.value
        return skill, None

    def _apply_filter(self, skills: list[Skill]) -> tuple[list[Skill], list[SkillIssue]]:
        """按 allow / deny 过滤 Skill 列表。

        先 allow 后 deny：allow 非空时仅保留名称命中的 Skill；
        deny 非空时移除名称命中的 Skill。被过滤的 Skill 记入诊断。

        参数:
            skills: 待过滤的 Skill 列表。

        返回:
            元组 (过滤后的 Skill 列表, 被过滤 Skill 的诊断列表)。
        """
        allow = self._allow
        deny = self._deny
        if not allow and not deny:
            return skills, []
        kept: list[Skill] = []
        issues: list[SkillIssue] = []
        for skill in skills:
            if allow and skill.name not in allow:
                issues.append(
                    SkillIssue(
                        directory=skill.directory,
                        reason=f"未命中 skills.allow 过滤: {skill.qualified_name}",
                    )
                )
                continue
            if deny and skill.name in deny:
                issues.append(
                    SkillIssue(
                        directory=skill.directory,
                        reason=f"被 skills.deny 过滤: {skill.qualified_name}",
                    )
                )
                continue
            kept.append(skill)
        return kept, issues
