"""Skill 正文加载器。

将 SkillManager 解析出的 Skill 渲染为工具返回文本：
- 校验正文 token 预算（估算）与渲染后全文的输出预算，超限拒绝并给出可操作指引；
- 渲染含技能元信息、资源目录说明与正文原文的完整文本；
- 提供技能目录内资源文件的受限读取（load_resource），带路径逃逸防护；
- 成功路径包一层可观测 Span 并记录 skill_loaded 日志事件。

关于预算口径：渲染后全文与资源内容沿用 len//2 的字符启发式估算；
与上下文侧 count_text_tokens 的中文启发式相比，len//2 对含非中文字符
的文本更高（保守），不会出现本模块放行而上下文侧超限截断的反向偏差。
正文、渲染后全文、单个资源文件分别由 skills.max_body_tokens、
context.max_tool_output_tokens、skills.max_resource_tokens 控制。
装配方可通过 token_counter 注入与上下文侧一致的计数函数（如
count_text_tokens 包装），此时估算口径与工具输出截断完全相同；
默认保持 len//2，纯中文短文场景下比 tiktoken 保守度略低，
由装配方按需注入以精确对齐。

本模块不依赖 sagent.tools，避免反向循环导入。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..observability import Span, get_logger
from .manager import SkillManager, SkillNotFoundError
from .models import Skill

logger = get_logger(__name__)


class LoadSkillArgs(BaseModel):
    """load_skill 工具参数。"""

    name: str = Field(..., description="技能名称或来源限定名（如 code-review 或 project:code-review）")


class ReadSkillResourceArgs(BaseModel):
    """read_skill_resource 工具参数。"""

    name: str = Field(..., description="技能名称或来源限定名（如 code-review 或 project:code-review）")
    path: str = Field(..., description="技能目录内的资源文件相对路径（如 references/api.md）")


class SkillLoader:
    """Skill 正文加载器：正文预算、渲染与资源根目录解析。"""

    def __init__(
        self,
        manager: SkillManager,
        max_body_tokens: int = 2000,
        max_output_tokens: int = 2000,
        max_resource_tokens: int = 2000,
        token_counter: Callable[..., int] | None = None,
        token_counter_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """初始化加载器。

        参数:
            manager: 已完成装配的 Skill 管理器。
            max_body_tokens: 正文估算 token 预算上限，来自配置 skills.max_body_tokens。
            max_output_tokens: 渲染后全文（load_skill）的输出预算上限，来自配置
                context.max_tool_output_tokens，与上下文侧对单条工具结果的截断预算保持一致。
            max_resource_tokens: 单个资源文件内容的预算上限，来自配置
                skills.max_resource_tokens；资源读取是独立入口，可单独收紧，
                不受工具输出截断预算约束。
            token_counter: 可选的自定义 token 计数函数（签名需兼容
                count_text_tokens(text, model="", method="auto") 的位置参数调用），
                注入后估算口径与上下文侧截断完全一致；为 None 时使用 len//2 启发式。
            token_counter_kwargs: 传给 token_counter 的附加关键字参数
                （如 model 与 method），与 ContextManager 截断侧配置保持一致。
        """
        self._manager = manager
        self._max_body_tokens = max_body_tokens
        self._max_output_tokens = max_output_tokens
        self._max_resource_tokens = max_resource_tokens
        self._token_counter = token_counter
        self._token_counter_kwargs = token_counter_kwargs or {}

    def estimate_tokens(self, text: str) -> int:
        """估算文本 token 数（按字符数一半的中文启发式估算，非精确值）。

        注入 token_counter 时委托自定义计数函数，与上下文侧口径一致。
        """
        if self._token_counter is not None:
            return self._token_counter(text, **self._token_counter_kwargs)
        return len(text) // 2

    def load(self, ref: str) -> str:
        """按引用加载技能正文并渲染为工具返回文本。

        参数:
            ref: 技能名称或来源限定名（"source:name"）。

        返回:
            成功时返回渲染文本；失败时返回以"错误:"开头的字符串。
        """
        text = ref.strip()
        if not text:
            return "错误:技能引用为空"
        try:
            skill = self._manager.resolve(text)
        except SkillNotFoundError as exc:
            logger.debug(
                "Skill 加载被拒绝",
                extra={
                    "event": "skill_load_rejected",
                    "skill_ref": text,
                    "reason": "not_found",
                    "detail": str(exc),
                },
            )
            return f"错误:{exc}"
        # Span 覆盖实际加载工作（预算估算、渲染与预算校验），
        # 拒绝时置为 error 状态，避免出现时长无意义的空 Span
        with Span("skill.load") as span:
            span.set_attribute("sagent.skill_name", skill.name)
            span.set_attribute("sagent.skill_source", skill.source.value)
            span.set_attribute("sagent.body_chars", len(skill.body))
            estimated = self.estimate_tokens(skill.body)
            if estimated > self._max_body_tokens:
                span.set_status("error")
                reason = (
                    f"技能正文超过预算上限 {self._max_body_tokens} token（估算 {estimated}），"
                    "请拆分 SKILL.md 或把大块内容移动到 references/ 下的文件"
                )
                logger.debug(
                    "Skill 加载被拒绝",
                    extra={
                        "event": "skill_load_rejected",
                        "skill_ref": text,
                        "reason": "body_over_budget",
                        "estimated_tokens": estimated,
                        "max_body_tokens": self._max_body_tokens,
                    },
                )
                return f"错误:{reason}"
            rendered = self._render(skill)
            # 渲染后全文（头部 + 来源 + 目录 + 资源说明 + 分隔线 + 正文）还需校验输出
            # 预算：上下文侧按 context.max_tool_output_tokens 对单条工具结果截断，
            # 若仅校验 body 会出现"加载成功但立刻被截断"。
            rendered_estimated = self.estimate_tokens(rendered)
            span.set_attribute("sagent.rendered_tokens", rendered_estimated)
            if rendered_estimated > self._max_output_tokens:
                span.set_status("error")
                reason = (
                    f"技能渲染后超过输出预算上限 {self._max_output_tokens} token"
                    f"（估算 {rendered_estimated}），请精简技能正文或拆分内容到资源文件"
                )
                logger.debug(
                    "Skill 加载被拒绝",
                    extra={
                        "event": "skill_load_rejected",
                        "skill_ref": text,
                        "reason": "rendered_over_budget",
                        "estimated_tokens": rendered_estimated,
                        "skill_name": skill.name,
                        "source": skill.source.value,
                    },
                )
                return f"错误:{reason}"
        logger.info(
            "Skill 正文加载成功",
            extra={
                "event": "skill_loaded",
                "skill_name": skill.name,
                "source": skill.source.value,
                "body_chars": len(skill.body),
                "estimated_tokens": estimated,
            },
        )
        return rendered

    def _render(self, skill: Skill) -> str:
        """渲染成功加载的技能文本，正文原样保留。"""
        return (
            f"[技能] {skill.qualified_name}\n"
            f"来源: {skill.source.value}\n"
            f"技能目录: {skill.directory}\n"
            "\n"
            "该目录下的 references/、scripts/、assets/ 为技能资源目录：\n"
            "- references/ 下的文件为参考文档，需要时用 read_skill_resource 工具读取"
            "（仅可读取该技能目录内的资源文件，相对路径基于技能目录）\n"
            "- scripts/ 下的脚本不会自动执行；需要执行时按你的常规工具流程确认权限\n"
            "- 加载本技能不会自动安装依赖或建立网络连接\n"
            "\n"
            "--- 技能正文 ---\n"
            f"{skill.body}"
        )

    def load_resource(self, ref: str, relative_path: str) -> str:
        """读取已发现技能目录内的资源文件，返回受限的资源文本。

        安全校验：
        - 先在未解析的拼接路径上逐级拒绝符号链接与 junction（Path.resolve()
          会解析软链，若先解析再检查将无法发现路径组件中的软链）；检查范围
          仅限技能目录之下的路径组件，避免技能目录自身的上级目录（如 Windows
          junction 开发盘）导致误拒；
        - 解析为绝对路径后必须位于技能目录（解析后）之内，越界拒绝；
        - 目标必须是已存在的普通文件；读取后按资源预算校验。

        参数:
            ref: 技能名称或来源限定名（"source:name"）。
            relative_path: 技能目录内的资源文件相对路径。

        返回:
            成功时返回含相对路径与文件内容的文本；失败时返回以"错误:"开头的字符串。
        """
        text = ref.strip()
        if not text:
            return "错误:技能引用为空"
        try:
            skill = self._manager.resolve(text)
        except SkillNotFoundError as exc:
            logger.debug(
                "Skill 资源读取被拒绝",
                extra={
                    "event": "skill_resource_rejected",
                    "skill_ref": text,
                    "reason": "not_found",
                    "detail": str(exc),
                },
            )
            return f"错误:{exc}"
        raw = relative_path.strip()
        if not raw:
            return "错误:资源路径为空"
        # 先规整 . / .. 再逐级检查链接：只检查技能目录之下的真实路径组件，
        # 一旦规整结果以 .. 开头说明已越出技能目录，交由下方 resolve 判定
        relative = Path(os.path.normpath(raw))
        if relative.is_absolute() or relative.drive:
            logger.debug(
                "Skill 资源读取被拒绝",
                extra={
                    "event": "skill_resource_rejected",
                    "skill_name": skill.name,
                    "source": skill.source.value,
                    "reason": "path_escape",
                },
            )
            return "错误:资源路径越出技能目录，已拒绝读取"
        current = skill.directory
        for part in relative.parts:
            if part == "..":
                break
            current = current / part
            # 逐级拒绝符号链接与 Windows junction（is_junction 需 Python 3.12+）
            if current.is_symlink() or current.is_junction():
                logger.debug(
                    "Skill 资源读取被拒绝",
                    extra={
                        "event": "skill_resource_rejected",
                        "skill_name": skill.name,
                        "source": skill.source.value,
                        "reason": "symlink_or_junction",
                    },
                )
                return "错误:资源路径包含符号链接或 junction，已拒绝读取"
        base = skill.directory.resolve()
        target = (skill.directory / relative).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            logger.debug(
                "Skill 资源读取被拒绝",
                extra={
                    "event": "skill_resource_rejected",
                    "skill_name": skill.name,
                    "source": skill.source.value,
                    "reason": "path_escape",
                },
            )
            return "错误:资源路径越出技能目录，已拒绝读取"
        if not target.is_file():
            return "错误:资源路径不存在或不是普通文件"
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug(
                "Skill 资源读取失败",
                extra={
                    "event": "skill_resource_rejected",
                    "skill_name": skill.name,
                    "source": skill.source.value,
                    "reason": "read_failed",
                    "detail": str(exc),
                },
            )
            return f"错误:资源文件读取失败或不是 UTF-8 文本: {exc}"
        estimated = self.estimate_tokens(content)
        if estimated > self._max_resource_tokens:
            logger.debug(
                "Skill 资源读取被拒绝",
                extra={
                    "event": "skill_resource_rejected",
                    "skill_name": skill.name,
                    "source": skill.source.value,
                    "reason": "resource_over_budget",
                    "estimated_tokens": estimated,
                    "max_resource_tokens": self._max_resource_tokens,
                },
            )
            return (
                f"错误:资源内容超过资源预算上限 {self._max_resource_tokens} token"
                f"（估算 {estimated}），请拆分该资源文件"
            )
        return (
            f"[技能资源] {skill.qualified_name}\n"
            f"文件: {relative_path}\n"
            "\n"
            f"{content}"
        )
