"""对话流式路由。

接收用户消息，在会话级运行时的独立工作线程中执行一轮 Agent 对话，
并通过 AI SDK UI Message Stream（SSE，实装 SDK 为 v7）把过程事件与终态
推送给前端。
单轮执行流程、记忆/Skill 前缀构建、异常兜底与增量落盘均对齐 CLI 的
交互循环，仅把命令行输出替换为事件桥 + 流适配器。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...core.cancellation import RunCancelled
from ...observability import get_logger
from ...session.models import TurnRecord
from ...skills import build_skill_catalog_prefix
from ..event_bridge import EventBridge
from ..runtime import (
    ActiveRun,
    RunFinishedError,
    RunNotFoundError,
    RuntimeRegistry,
    SessionBusyError,
    SessionNotFoundError,
    SessionRuntime,
)
from ..schemas import ChatMessageRequest
from ..stream_adapter import UI_MESSAGE_STREAM_HEADERS, UIStreamAdapter

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["chat"])

# SSE 媒体类型；协议头由 UIStreamAdapter 模块统一提供
_STREAM_MEDIA_TYPE = "text/event-stream"


@dataclass
class RunOutcome:
    """工作线程与事件流协程之间共享的运行终态。

    工作线程在关闭事件桥之前写入 status/message，事件流在收到结束哨兵后
    读取，因此无需额外的同步原语。
    """

    status: str = "failed"
    message: str | None = None


def _run_turn(
    *,
    session_id: str,
    run_id: str,
    message: str,
    runtime: SessionRuntime,
    active: ActiveRun,
    bridge: EventBridge,
    outcome: RunOutcome,
    store,
    memory_manager,
    skill_manager,
    auto_save: bool,
) -> None:
    """在后台线程中执行一轮对话并完成终点处理。

    参数:
        session_id: 会话 id。
        run_id: 本次运行 id。
        message: 已去除首尾空白的用户消息。
        runtime: 所属会话运行时。
        active: 本次运行的活动记录。
        bridge: 事件桥，用于向事件循环投递事件并发送结束哨兵。
        outcome: 与路由协程共享的终态容器。
        store: 会话存储。
        memory_manager: 记忆管理器，未启用时为 None。
        skill_manager: Skill 管理器，未启用时为 None。
        auto_save: 是否在每轮结束后增量保存会话消息。
    """
    turn_index = store.count_turns(session_id) + 1
    started_at = datetime.now().isoformat()
    usage_before = runtime.context_manager.usage_snapshot()
    start_perf = time.perf_counter()
    status = "completed"
    error_message: str | None = None

    try:
        # 与 CLI 一致：记忆前缀与 Skill 目录前缀在每轮请求前构建并传给引擎
        memory_prefix = (
            memory_manager.build_memory_prefix() if memory_manager is not None else None
        )
        skill_prefix = (
            build_skill_catalog_prefix(skill_manager)
            if skill_manager is not None
            else None
        )
        # 引擎负责把用户消息写入上下文，路由层不重复写入
        active.engine.run(
            message,
            memory_prefix=memory_prefix,
            skill_prefix=skill_prefix,
        )
    except RunCancelled:
        # 用户主动取消不属于失败，仍保留已完成的消息与轮次记录
        status = "cancelled"
    except Exception as exc:  # 捕获运行期异常，避免工作线程静默退出
        status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "Web 运行失败",
            extra={
                "event": "web_run_error",
                "session_id": session_id,
                "run_id": run_id,
            },
        )
    finally:
        outcome.status = status
        outcome.message = error_message
        # 先关闭事件桥，让事件流协程在消费完剩余事件后正常收尾
        bridge.close()

        usage_after = runtime.context_manager.usage_snapshot()
        duration_ms = round((time.perf_counter() - start_perf) * 1000)
        turn = TurnRecord(
            session_id=session_id,
            turn_index=turn_index,
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
            duration_ms=duration_ms,
            prompt_tokens=usage_after["prompt_tokens"] - usage_before["prompt_tokens"],
            completion_tokens=usage_after["completion_tokens"]
            - usage_before["completion_tokens"],
            total_tokens=usage_after["total_tokens"] - usage_before["total_tokens"],
            outcome=status,
            mode=active.engine.last_mode,
        )

        # 与 CLI 一致：成功、取消、失败都尝试增量保存已完成的消息
        if auto_save:
            try:
                runtime.session_manager.save_current()
            except Exception:
                logger.exception(
                    "Web 会话自动保存失败",
                    extra={
                        "event": "session_save_error",
                        "session_id": session_id,
                        "run_id": run_id,
                    },
                )

        # 先落盘轮次再结束运行：下一次运行的 turn_index 依赖本轮的记录
        try:
            store.append_turn(turn)
        except Exception:
            logger.exception(
                "Web 轮次统计写入失败",
                extra={
                    "event": "turn_record_error",
                    "session_id": session_id,
                    "run_id": run_id,
                },
            )

        try:
            runtime.finish_run(run_id, status)
        except (RunNotFoundError, RunFinishedError):
            logger.debug(
                "结束运行时运行已结束",
                extra={
                    "event": "web_run_finish_skipped",
                    "session_id": session_id,
                    "run_id": run_id,
                },
            )


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    payload: ChatMessageRequest,
    request: Request,
) -> StreamingResponse:
    """发送一条用户消息并以 SSE 流返回本轮执行过程。

    参数:
        session_id: 会话 id。
        payload: 对话请求体。
        request: 当前 HTTP 请求，用于读取共享依赖。

    返回:
        text/event-stream 响应。
    """
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    runtimes: RuntimeRegistry | None = request.app.state.runtimes
    if runtimes is None:
        raise HTTPException(status_code=503, detail="会话功能未启用")

    try:
        runtime = runtimes.get_or_create(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    loop = asyncio.get_running_loop()
    bridge = EventBridge(loop)
    try:
        active = runtime.begin_run(loop=loop, on_event=bridge.emit)
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 运行 id 由运行时生成；适配器与取消接口必须使用同一个 id
    run_id = active.run_id
    adapter = UIStreamAdapter(
        run_id=run_id,
        message_id=payload.message_id,
        session_id=session_id,
        plan_snapshot=runtime.session_manager.get_active_plan,
    )
    outcome = RunOutcome()

    def _worker() -> None:
        """后台线程入口：执行本轮对话并完成落盘。"""
        _run_turn(
            session_id=session_id,
            run_id=run_id,
            message=message,
            runtime=runtime,
            active=active,
            bridge=bridge,
            outcome=outcome,
            store=request.app.state.store,
            memory_manager=request.app.state.memory,
            skill_manager=request.app.state.skill_manager,
            auto_save=request.app.state.config.session.auto_save,
        )

    async def _event_stream() -> AsyncIterator[str]:
        """把事件桥中的事件转换为 SSE 帧并输出终态。"""
        thread = threading.Thread(
            target=_worker,
            name=f"web-run-{run_id}",
            daemon=True,
        )
        thread.start()
        try:
            for frame in adapter.start():
                yield frame
            async for event in bridge.events():
                for frame in adapter.handle(event):
                    yield frame
        except asyncio.CancelledError:
            # 客户端断开：协作式取消运行，忽略运行已结束的竞态
            try:
                runtime.cancel_run(run_id)
            except (RunNotFoundError, RunFinishedError):
                logger.debug(
                    "断开连接时运行已结束",
                    extra={
                        "event": "web_run_cancel_skipped",
                        "session_id": session_id,
                        "run_id": run_id,
                    },
                )
            raise
        for frame in adapter.close(outcome.status, outcome.message):
            yield frame

    return StreamingResponse(
        _event_stream(),
        media_type=_STREAM_MEDIA_TYPE,
        headers=dict(UI_MESSAGE_STREAM_HEADERS),
    )
