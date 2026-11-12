"""审批决策路由。

接收前端对等待中审批的决策动作，交给所属会话的 Web 审批器处理；
非法动作返回 400，审批不存在返回 404，已过期或已决策返回 409。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...observability import get_logger
from ...permissions import ConfirmAction
from ..approval import (
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalResolvedError,
)
from ..runtime import RuntimeRegistry
from ..schemas import ApprovalDecisionRequest, ApprovalDecisionResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

# 允许的审批动作
_VALID_ACTIONS = ("approve", "deny", "always")


@router.post("/{approval_id}", response_model=ApprovalDecisionResponse)
def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
) -> ApprovalDecisionResponse:
    """提交审批决策并唤醒等待中的工具执行线程。

    参数:
        approval_id: 审批 id。
        payload: 决策请求体。
        request: 当前 HTTP 请求。

    返回:
        审批决策结果。
    """
    if payload.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"无效审批动作: {payload.action!r}，有效取值: approve / deny / always",
        )

    runtimes: RuntimeRegistry | None = request.app.state.runtimes
    if runtimes is None:
        raise HTTPException(status_code=503, detail="会话功能未启用")

    runtime = runtimes.find_by_approval_id(approval_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")

    try:
        result = runtime.approval.resolve(approval_id, payload.action)
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")
    except (ApprovalExpiredError, ApprovalResolvedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    approved = result in (ConfirmAction.APPROVED, ConfirmAction.ALLOWED_ALWAYS)
    logger.info(
        "已提交审批决策",
        extra={
            "event": "web_approval_decided",
            "session_id": runtime.session_id,
            "approval_id": approval_id,
            "action": payload.action,
        },
    )
    return ApprovalDecisionResponse(
        approval_id=approval_id,
        status="approved" if approved else "denied",
    )
