"""交互审批器。

当权限策略决策为 ask 时，向用户请求确认（y / n / a）：
- y：批准本次执行
- n：拒绝执行
- a：总是允许（记入会话级 always-allow 白名单，本会话内同"工具+参数"不再询问）

遵循 fail-safe 原则：无交互终端（TTY 不可用）、输入超时、EOF / 中断、无效输入
时默认拒绝；仅当 non_interactive 显式配置为 allow 时，无终端与超时场景才放行。
"""

from __future__ import annotations

import sys
import threading
from abc import ABC, abstractmethod
from enum import Enum

from .policy import Decision, PermissionPolicy, PermissionRequest, args_to_text

# 参数摘要显示的最大字符数，超出部分截断
_MAX_SUMMARY_CHARS = 200


class ConfirmAction(str, Enum):
    """审批结果四态。"""

    APPROVED = "approved"  # 用户批准本次
    DENIED = "denied"  # 用户拒绝（或 fail-safe 拒绝）
    ALLOWED_ALWAYS = "allowed_always"  # 用户选择总是允许（会话级记忆）
    TIMED_OUT = "timed_out"  # 等待输入超时


class ApprovalHandler(ABC):
    """审批器抽象接口。"""

    @abstractmethod
    def approve(self, request: PermissionRequest, policy: PermissionPolicy) -> ConfirmAction:
        """对 ask 决策的请求执行审批。

        参数:
            request: 权限请求（工具名 + 参数）。
            policy: 当前权限策略（用于展示风险提示）。

        返回:
            ConfirmAction 审批结果。
        """
        raise NotImplementedError


class InteractiveApprovalHandler(ApprovalHandler):
    """交互式审批器：读取 stdin 的 y / n / a 输入，维护会话级 always-allow 记忆。

    超时通过后台守护线程读取 stdin 实现（跨平台，Windows 无 select-on-stdin）：
    主线程 join(timeout) 等待；超时后后台线程仍阻塞在 input() 上，
    进程退出时随 daemon 线程自动回收，不影响主流程。
    """

    def __init__(self, timeout: float = 600.0, non_interactive: str = "deny") -> None:
        """初始化审批器。

        参数:
            timeout: 等待用户输入的超时秒数，缺省 600 秒（10 分钟，
                对齐业界交互式 Agent 的询问等待尺度，避免用户短暂离开被误拒）。
            non_interactive: 无交互终端或超时时的动作，"deny"（默认拒绝）
                或 "allow"（显式放行）。
        """
        self._timeout = timeout
        self._non_interactive = non_interactive
        # 会话级 always-allow 白名单，键为 (工具名, 参数文本)
        self._always_allow: set[tuple[str, str]] = set()

    def configure(self, timeout: float | None = None, non_interactive: str | None = None) -> None:
        """更新审批配置（供权限执行器组装时覆盖超时与非交互动作）。

        参数:
            timeout: 审批等待超时秒数；None 表示保持当前值不变。
            non_interactive: 非交互/超时场景的动作（deny / allow）；None 表示保持当前值不变。
        """
        if timeout is not None:
            self._timeout = timeout
        if non_interactive is not None:
            self._non_interactive = non_interactive

    def approve(self, request: PermissionRequest, policy: PermissionPolicy) -> ConfirmAction:
        """对 ask 决策的请求执行交互审批。

        参数:
            request: 权限请求（工具名 + 参数）。
            policy: 当前权限策略（用于展示风险提示）。

        返回:
            ConfirmAction 审批结果。
        """
        key = (request.tool, args_to_text(request.args))

        # 会话级 always-allow 白名单命中：直接放行，不再询问
        if key in self._always_allow:
            return ConfirmAction.ALLOWED_ALWAYS

        # 非交互终端检测（无 TTY）：按配置放行或默认拒绝（fail-safe）
        if sys.stdin is None or not sys.stdin.isatty():
            if self._non_interactive == "allow":
                return ConfirmAction.APPROVED
            print("（检测到非交互终端，权限审批默认拒绝）")
            return ConfirmAction.DENIED

        # 打印审批提示：工具名、参数摘要与风险提示
        summary = args_to_text(request.args)
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[:_MAX_SUMMARY_CHARS] + "..."
        print()
        print("---------- 权限确认 ----------")
        print(f"工具: {request.tool}")
        print(f"参数: {summary}")
        if policy.decide(request) == Decision.ASK:
            print("默认策略: 需用户确认（ask）")
        if request.tool == "run_shell":
            print("该工具将执行任意系统命令，请确认命令内容")
        if request.tool == "write_file":
            print("该工具将写入指定文件（默认覆盖已有内容），请确认目标路径")
        print("批准执行吗? [y] 批准 / [n] 拒绝 / [a] 总是允许(本会话): ", end="", flush=True)

        # 后台守护线程读取 stdin：EOF / 中断以空串标记，供主线程裁决
        result: list[str] = []

        def _read() -> None:
            try:
                result.append(input())
            except (EOFError, KeyboardInterrupt):
                result.append("")

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(self._timeout)

        # 超时：后台线程仍阻塞在 input() 上，随 daemon 线程在进程退出时回收
        if reader.is_alive():
            if self._non_interactive == "allow":
                print(f"\n（审批等待超过 {self._timeout} 秒，已按配置放行）")
                return ConfirmAction.APPROVED
            print(f"\n（审批等待超过 {self._timeout} 秒，默认拒绝）")
            return ConfirmAction.TIMED_OUT

        # EOF / 中断：以空串标记，默认拒绝（fail-safe）
        if not result or not result[0]:
            print("（输入中断，已拒绝）")
            return ConfirmAction.DENIED

        answer = result[0].strip().lower()
        if answer == "y":
            return ConfirmAction.APPROVED
        if answer == "n":
            return ConfirmAction.DENIED
        if answer == "a":
            self._always_allow.add(key)
            print("（本会话内将自动允许该工具调用的相同参数）")
            return ConfirmAction.ALLOWED_ALWAYS
        print("（无效输入，默认拒绝；有效输入: y / n / a）")
        return ConfirmAction.DENIED
