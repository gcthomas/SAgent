"""运行取消路由。

按运行 id 找到所属会话运行时并触发协作式取消；运行不存在与运行已结束
分别映射为 404 与 409，便于前端区分重试语义。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ...observability import get_logger
from ..runtime import (
    RunFinishedError,
    RunNotFoundError,
    RuntimeRegistry,
)
from ..schemas import RunCancelResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunCancelResponse,
)
def cancel_run(run_id: str, request: Request) -> RunCancelResponse:
    """取消指定运行。

    参数:
        run_id: 运行 id。
        request: 当前 HTTP 请求。

    返回:
        取消受理响应。
    """
    runtimes: RuntimeRegistry | None = request.app.state.runtimes
    if runtimes is None:
        raise HTTPException(status_code=503, detail="会话功能未启用")

    runtime = runtimes.find_by_run_id(run_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")

    try:
        runtime.cancel_run(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")
    except RunFinishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return RunCancelResponse(status="cancelling")
