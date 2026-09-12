"""权限执行器。

统一权限入口 PermissionEnforcer：组合策略决策（PermissionPolicy）与交互审批
（ApprovalHandler），对每次工具调用输出 PermissionOutcome，并记录结构化审计
日志（event="permission_decision"，含 decision / tool / tool_args / reason），
放行记 INFO，拒绝与超时记 WARNING。
本组件由 ToolRegistry 注入使用，是执行期权限闸门：工具在 run 之前须先经授权。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..observability import get_logger
from .approval import ApprovalHandler, ConfirmAction, InteractiveApprovalHandler
from .policy import (
    Decision,
    PermissionPolicy,
    PermissionRequest,
    canonical_permission_args,
)

logger = get_logger(__name__)


@dataclass
class PermissionOutcome:
    """权限授权结果。

    allowed 为 True 时放行执行；为 False 时 message 为以"错误:"开头的拒绝
    说明（供工具注册表直接作为执行结果返回给引擎）。decision 记录策略对
    该请求的原始决策（allow / ask / deny），allowed 则是最终结论。
    """

    allowed: bool
    message: str
    decision: Decision


class PermissionEnforcer:
    """权限执行器：策略决策 + 审批确认 + 审计日志的统一入口。"""

    def __init__(
        self,
        policy: PermissionPolicy,
        approval: ApprovalHandler,
        timeout: float | None = None,
        non_interactive: str = "deny",
    ) -> None:
        """初始化权限执行器。

        参数:
            policy: 权限策略决策器。
            approval: 审批器，决策为 ask 时委托其向用户确认。
            timeout: 审批等待超时秒数；非 None 时应用到交互审批器。
            non_interactive: 非交互/超时场景的动作（deny / allow），应用到交互审批器。
        """
        self._policy = policy
        self._approval = approval
        # 审批器为交互式时，把显式提供的审批配置应用上去（供 CLI 组装层统一注入）
        if isinstance(approval, InteractiveApprovalHandler):
            approval.configure(timeout=timeout, non_interactive=non_interactive)

    def authorize(self, name: str, args: dict[str, Any]) -> PermissionOutcome:
        """对工具调用执行授权，返回放行/拒绝结果。

        参数:
            name: 工具名称。
            args: 已解析的工具参数字典。

        返回:
            PermissionOutcome：allowed 为 True 时放行；为 False 时 message
            为以"错误: 工具 '<name>' 未获用户批准，已拒绝执行"开头的说明。
        """
        # 先规范化为参与权限匹配的主参数（run_shell 只取 command、
        # write_file 只取 path），排除 timeout / content 等无关字段，
        # 保证模式匹配与 always-allow 白名单键不受次要参数干扰
        request = PermissionRequest(
            tool=name, args=canonical_permission_args(name, args)
        )
        decision = self._policy.decide(request)

        # 策略直接放行：无需用户干预
        if decision == Decision.ALLOW:
            self._audit(True, decision, request, reason="策略允许（allow）")
            return PermissionOutcome(allowed=True, message="", decision=decision)

        # 策略直接拒绝：不进入审批流程
        if decision == Decision.DENY:
            message = f"错误: 工具 '{name}' 未获用户批准，已拒绝执行"
            self._audit(False, decision, request, reason="命中拒绝规则（deny）")
            return PermissionOutcome(allowed=False, message=message, decision=decision)

        # 策略要求确认（ask）：委托审批器向用户确认
        action = self._approval.approve(request, self._policy)
        if action == ConfirmAction.APPROVED:
            self._audit(True, decision, request, reason="ask：用户批准本次执行")
            return PermissionOutcome(allowed=True, message="", decision=decision)
        if action == ConfirmAction.ALLOWED_ALWAYS:
            self._audit(True, decision, request, reason="ask：用户总是允许（本会话）")
            return PermissionOutcome(allowed=True, message="", decision=decision)

        # 拒绝（denied）与超时（timed_out）：fail-safe 收敛为拒绝
        message = f"错误: 工具 '{name}' 未获用户批准，已拒绝执行"
        if action == ConfirmAction.TIMED_OUT:
            reason = "ask：审批等待超时，默认拒绝"
        else:
            reason = "ask：用户拒绝执行"
        self._audit(False, decision, request, reason=reason)
        return PermissionOutcome(allowed=False, message=message, decision=decision)

    def _audit(
        self,
        allowed: bool,
        decision: Decision,
        request: PermissionRequest,
        reason: str,
    ) -> None:
        """记录权限决策审计日志：放行记 INFO，拒绝与超时记 WARNING。"""
        extra = {
            "event": "permission_decision",
            "decision": decision.value,
            "tool": request.tool,
            "tool_args": request.args,
            "reason": reason,
        }
        if allowed:
            logger.info("权限决策: 放行", extra=extra)
        else:
            logger.warning("权限决策: 拒绝", extra=extra)
