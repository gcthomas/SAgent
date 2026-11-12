"""Web 审批器。

将权限策略的 ask 决策转为页面内审批：

- 每次请求生成 approval_id，发出 approval_required 事件，并在锁外阻塞等待前端决策；
- 前端通过 resolve 提交 approve / deny / always 决策，唤醒等待线程；
- always 在实例内按"工具 + 规范化参数"记忆，命中后直接返回 ALLOWED_ALWAYS；
- 等待超过配置超时后按 non_interactive 收敛（默认拒绝），并标记 expired；
- 运行取消或会话删除时通过 cancel_pending 以拒绝唤醒全部等待；
- 每次审批结果写入与权限执行器一致的审计日志（event="permission_decision"），
  额外记录 approval_id 与是否超时。

fail-safe 原则：超时默认拒绝；仅当 non_interactive 显式配置为 allow 时超时才放行。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..core.events import AgentEvent, EventHandler
from ..observability import get_logger
from ..permissions.approval import ApprovalHandler, ConfirmAction
from ..permissions.policy import (
    PermissionPolicy,
    PermissionRequest,
    canonical_permission_args,
)

logger = get_logger(__name__)

# resolve 允许的动作取值
_VALID_ACTIONS = frozenset({"approve", "deny", "always"})

# 工具特有的风险提示，口径与交互审批器保持一致
_TOOL_HINTS: dict[str, str] = {
    "run_shell": "该工具将执行任意系统命令，请确认命令内容",
    "write_file": "该工具将写入指定文件（默认覆盖已有内容），请确认目标路径",
}


class ApprovalError(Exception):
    """审批相关错误基类。"""


class ApprovalNotFoundError(ApprovalError):
    """审批 id 不存在。"""


class ApprovalExpiredError(ApprovalError):
    """审批已超时或已取消。"""


class ApprovalResolvedError(ApprovalError):
    """审批已完成决策。"""


@dataclass
class PendingApproval:
    """等待前端决策的审批记录。

    除 event 之外的字段读写均由 WebApprovalHandler 的锁保护；event 用于
    唤醒阻塞在 approve 中的线程。超时时间为 None（无限等待）时
    expires_at 记为 float("inf")。
    """

    approval_id: str
    request: PermissionRequest
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    created_at: float
    expires_at: float
    event: threading.Event = field(default_factory=threading.Event)
    result: ConfirmAction | None = None
    resolved: bool = False
    expired: bool = False

    @property
    def is_terminal(self) -> bool:
        """是否已结束（已决策或已过期 / 已取消）。"""
        return self.resolved or self.expired


class WebApprovalHandler(ApprovalHandler):
    """Web 审批器：把 ask 决策转为页面内等待，支持超时、always 记忆与取消。"""

    def __init__(
        self,
        timeout: float | None = None,
        non_interactive: str = "deny",
        on_event: EventHandler | None = None,
    ) -> None:
        """初始化审批器。

        参数:
            timeout: 等待前端决策的超时秒数；None 表示无限等待。
            non_interactive: 超时后的动作，"deny"（默认拒绝）或 "allow"（显式放行）。
            on_event: 事件回调，用于下发 approval_required / approval_resolved；
                None 时不发事件。
        """
        self._timeout = timeout
        self._non_interactive = non_interactive
        self._on_event = on_event
        self._lock = threading.Lock()
        # approval_id -> 审批记录（已决策 / 已过期的记录保留，便于区分 404 与 409）
        self._pending: dict[str, PendingApproval] = {}
        # 实例级 always 白名单，键为 (工具名, 规范化参数的稳定 JSON)
        self._always: set[tuple[str, str]] = set()

    def set_event_handler(self, on_event: EventHandler | None) -> None:
        """更新审批事件回调（运行开始时由会话运行时注入）。

        参数:
            on_event: 事件回调；None 表示不再发出审批事件。
        """
        with self._lock:
            self._on_event = on_event

    def configure(self, timeout: float | None = None, non_interactive: str | None = None) -> None:
        """更新审批配置（语义与交互审批器一致）。

        参数:
            timeout: 等待超时秒数；None 表示保持当前值不变。
            non_interactive: 超时后的动作（deny / allow）；None 表示保持当前值不变。
        """
        with self._lock:
            if timeout is not None:
                self._timeout = timeout
            if non_interactive is not None:
                self._non_interactive = non_interactive

    def approve(self, request: PermissionRequest, policy: PermissionPolicy) -> ConfirmAction:
        """对 ask 决策的请求等待前端审批。

        参数:
            request: 权限请求（工具名 + 规范化参数）。
            policy: 当前权限策略（用于审计记录原始决策）。

        返回:
            ConfirmAction 审批结果；超时按 non_interactive 收敛。
        """
        key = _always_key(request.tool, request.args)

        with self._lock:
            # always 白名单命中：直接放行，不生成 approval_id、不发事件
            if key in self._always:
                return ConfirmAction.ALLOWED_ALWAYS
            timeout = self._timeout
            now = time.monotonic()
            pending = PendingApproval(
                approval_id=uuid.uuid4().hex,
                request=request,
                tool_name=request.tool,
                arguments=canonical_permission_args(request.tool, request.args),
                reason=_build_reason(request),
                created_at=now,
                expires_at=float("inf") if timeout is None else now + max(timeout, 0.0),
            )
            self._pending[pending.approval_id] = pending

        # 事件回调可能被前端桥接层实现，必须在锁外调用，避免重入死锁
        self._emit(
            AgentEvent(
                event_type="approval_required",
                content=pending.reason,
                tool_name=request.tool,
                tool_arguments=request.args,
                approval_id=pending.approval_id,
                status="waiting_approval",
                # 无限等待时 expires_at 为 inf，序列化前统一转为 None
                expires_at=None if pending.expires_at == float("inf") else pending.expires_at,
            )
        )

        # 等待决策必须在锁外进行，避免阻塞 resolve / cancel_pending
        signaled = pending.event.wait(timeout)
        if signaled:
            with self._lock:
                result = pending.result
                cancelled = pending.expired
            if cancelled:
                # 取消事件已由 cancel_pending 发出，这里只补审计，避免重复事件
                self._audit(pending, policy, result, expired=True, timed_out=False, cancelled=True)
            else:
                self._emit_resolved(pending, result)
                self._audit(pending, policy, result, expired=False, timed_out=False, cancelled=False)
            return result

        # 超时与并发决策 / 取消可能在等待结束瞬间交错，重新加锁判定
        timed_out = False
        cancelled = False
        with self._lock:
            if pending.resolved:
                result = pending.result
            elif pending.expired:
                # 等待期间已被 cancel_pending 拒绝
                result = pending.result
                cancelled = True
            else:
                pending.expired = True
                result = (
                    ConfirmAction.APPROVED
                    if self._non_interactive == "allow"
                    else ConfirmAction.DENIED
                )
                timed_out = True

        if timed_out:
            self._emit_resolved(
                pending, result, timeout_message=True
            )
            self._audit(pending, policy, result, expired=True, timed_out=True, cancelled=False)
        elif cancelled:
            self._audit(pending, policy, result, expired=True, timed_out=False, cancelled=True)
        else:
            self._emit_resolved(pending, result)
            self._audit(pending, policy, result, expired=False, timed_out=False, cancelled=False)
        return result

    def resolve(self, approval_id: str, action: str) -> ConfirmAction:
        """提交前端决策并唤醒等待线程。

        参数:
            approval_id: 审批 id。
            action: 决策动作，仅接受 "approve" / "deny" / "always"。

        返回:
            该决策对应的 ConfirmAction。

        异常:
            ValueError: action 非法。
            ApprovalNotFoundError: 审批 id 不存在。
            ApprovalExpiredError: 审批已超时或已取消。
            ApprovalResolvedError: 审批已完成决策。
        """
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"无效审批动作: {action!r}，有效取值: approve / deny / always"
            )

        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                raise ApprovalNotFoundError(f"审批 {approval_id} 不存在")
            if pending.resolved:
                raise ApprovalResolvedError(f"审批 {approval_id} 已完成决策")
            if pending.expired:
                raise ApprovalExpiredError(f"审批 {approval_id} 已过期")

            if action == "always":
                result = ConfirmAction.ALLOWED_ALWAYS
                self._always.add(_always_key(pending.tool_name, pending.arguments))
            elif action == "approve":
                result = ConfirmAction.APPROVED
            else:
                result = ConfirmAction.DENIED

            pending.result = result
            pending.resolved = True
            pending.event.set()
            return result

    def cancel_pending(self, reason: str = "运行已取消") -> None:
        """以拒绝唤醒全部未决策的审批（用于运行取消与会话删除）。

        参数:
            reason: 取消原因，写入 approval_resolved 事件内容。
        """
        with self._lock:
            targets = [pending for pending in self._pending.values() if not pending.is_terminal]
            for pending in targets:
                pending.result = ConfirmAction.DENIED
                pending.expired = True
                pending.event.set()

        # 事件在锁外发出，等待线程苏醒后只补审计、不重复发事件
        for pending in targets:
            self._emit(
                AgentEvent(
                    event_type="approval_resolved",
                    content=f"审批已取消（{reason}），已拒绝执行",
                    tool_name=pending.tool_name,
                    tool_arguments=pending.request.args,
                    approval_id=pending.approval_id,
                    approval_action="deny",
                )
            )

    @property
    def pending_ids(self) -> list[str]:
        """当前尚未决策的审批 id 列表。"""
        with self._lock:
            return [pid for pid, pending in self._pending.items() if not pending.is_terminal]

    @property
    def known_approval_ids(self) -> set[str]:
        """全部已登记审批 id（含已决策 / 已过期），供上层区分 404 与 409。"""
        with self._lock:
            return set(self._pending.keys())

    def list_pending(self) -> list[PendingApproval]:
        """返回尚未决策的审批记录（按登记顺序）。"""
        with self._lock:
            return [pending for pending in self._pending.values() if not pending.is_terminal]

    def _emit(self, event: AgentEvent) -> None:
        """发出事件；未提供回调时静默跳过。"""
        if self._on_event is None:
            return
        self._on_event(event)

    def _emit_resolved(
        self,
        pending: PendingApproval,
        result: ConfirmAction,
        timeout_message: bool = False,
    ) -> None:
        """发出 approval_resolved 事件。"""
        if timeout_message:
            if result is ConfirmAction.APPROVED:
                content = "审批已超时，已按非交互策略允许"
            else:
                content = "审批已超时，已按非交互策略拒绝"
        else:
            content = _describe_result(result)
        self._emit(
            AgentEvent(
                event_type="approval_resolved",
                content=content,
                tool_name=pending.tool_name,
                tool_arguments=pending.request.args,
                approval_id=pending.approval_id,
                approval_action=_action_of(result),
            )
        )

    def _audit(
        self,
        pending: PendingApproval,
        policy: PermissionPolicy,
        result: ConfirmAction,
        *,
        expired: bool,
        timed_out: bool,
        cancelled: bool,
    ) -> None:
        """写入权限审计日志：放行记 INFO，拒绝与超时记 WARNING。"""
        if cancelled:
            reason_text = "ask：Web 审批已取消，拒绝执行"
        elif timed_out:
            reason_text = (
                "ask：审批等待超时，按非交互策略允许"
                if result is ConfirmAction.APPROVED
                else "ask：审批等待超时，按非交互策略拒绝"
            )
        elif result is ConfirmAction.ALLOWED_ALWAYS:
            reason_text = "ask：Web 审批总是允许（本会话）"
        elif result is ConfirmAction.APPROVED:
            reason_text = "ask：Web 审批通过"
        else:
            reason_text = "ask：Web 审批拒绝"

        extra = {
            "event": "permission_decision",
            "decision": policy.decide(pending.request).value,
            "tool": pending.tool_name,
            "tool_args": pending.arguments,
            "reason": reason_text,
            "approval_id": pending.approval_id,
            "expired": expired,
        }
        if result in (ConfirmAction.APPROVED, ConfirmAction.ALLOWED_ALWAYS):
            logger.info("权限决策: 放行", extra=extra)
        else:
            logger.warning("权限决策: 拒绝", extra=extra)


def _always_key(tool: str, args: dict[str, Any]) -> tuple[str, str]:
    """构造 always 白名单键：工具名 + 规范化参数的稳定 JSON 文本。"""
    canonical = canonical_permission_args(tool, args)
    return tool, json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _build_reason(request: PermissionRequest) -> str:
    """构造审批原因文案（口径与交互审批器一致，供前端展示）。"""
    parts = [f"工具 {request.tool} 请求执行，已被策略要求确认（ask）"]
    hint = _TOOL_HINTS.get(request.tool)
    if hint:
        parts.append(hint)
    return "；".join(parts)


def _describe_result(result: ConfirmAction) -> str:
    """把审批结果转换为面向用户的说明。"""
    if result is ConfirmAction.ALLOWED_ALWAYS:
        return "审批已通过，本会话内相同工具与参数将不再询问"
    if result is ConfirmAction.APPROVED:
        return "审批已通过，工具继续执行"
    if result is ConfirmAction.TIMED_OUT:
        return "审批已超时，工具不执行"
    return "审批已拒绝，工具不执行"


def _action_of(result: ConfirmAction) -> str:
    """把审批结果映射为前端动作取值（approve / deny）。"""
    if result in (ConfirmAction.APPROVED, ConfirmAction.ALLOWED_ALWAYS):
        return "approve"
    return "deny"
