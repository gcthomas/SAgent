"""AI SDK UI Message Stream 协议适配。

把引擎产生的 ``AgentEvent`` 序列转换为 Vercel AI SDK UI Message Stream
（实装 SDK 为 v7）的 SSE 帧：每帧为 ``data: <json>\\n\\n``，结束帧为
``data: [DONE]\\n\\n``。
适配器是有状态的，一个实例对应一个 run（一次对话请求），负责：

- 输出一次 ``start`` 部件与惰性的 ``text-start``；
- 把文本增量、工具调用、审批、计划、用量映射为标准部件与自定义 data 部件；
- 推导 ``data-run-status`` 的运行状态，并保证终态只由 ``close()`` 输出一次。

响应头由路由层使用 ``UI_MESSAGE_STREAM_HEADERS``。

自定义 data 部件字段契约（供前端 ``onData`` 使用）：

1. ``data-run-status``::

       {"type": "data-run-status", "data": {
           "run_id": str,
           "session_id": str,
           "status": "thinking" | "tool_calling" | "executing"
                     | "waiting_approval" | "completed" | "failed" | "cancelled",
           "message": str | null,
           "iteration": int | null,
           "plan_step_index": int | null,
           "plan_step_total": int | null,
           "plan_step_title": str | null
       }}

2. ``data-plan``（仅在 ``plan_snapshot`` 可用且返回非 None 时下发）::

       {"type": "data-plan", "data": {
           "task": str,
           "status": str,                 # PlanState.status：planning / executing / completed / failed / blocked
           "current_step": int | null,    # 从 1 开始；计划收尾后为 null
           "total_steps": int,
           "replans": int,
           "steps": [{
               "index": int,              # 从 1 开始，与 current_step 口径一致
               "title": str,
               "description": str,
               "status": "pending" | "running" | "completed" | "failed" | "blocked" | "skipped",
               "summary": str
           }]
       }}

3. ``data-usage``（仅在 ``llm_completed`` 携带 usage 时下发，单次值 + run 累计值）::

       {"type": "data-usage", "data": {
           "run_id": str,
           "prompt_tokens": int,
           "completion_tokens": int,
           "total_tokens": int,
           "latency_ms": float | null,    # 本次 LLM 请求耗时
           "run_total": {
               "prompt_tokens": int,      # 本次 run 累计
               "completion_tokens": int,
               "total_tokens": int,
               "latency_ms": float        # 本次 run 累计请求耗时
           }
       }}

4. ``data-approval``（审批请求与结果共用同一部件，``status`` 区分阶段）::

       {"type": "data-approval", "data": {
           "approval_id": str,
           "tool_name": str,
           "arguments": dict | list | str,  # 规范化工具参数，解析失败时为原始文本
           "reason": str | null,
           "status": "pending" | "approved" | "denied",
           "expires_at": float | null       # time.monotonic 截止秒；无限等待为 null
       }}

5. ``data-turn-usage``（仅由 ``close()`` 在终态前下发一次）::

       {"type": "data-turn-usage", "data": {
           "run_id": str,
           "message_id": str,             # 助手消息 id，用于定位消息下方展示位置
           "duration_ms": float | null,   # 本轮总耗时
           "prompt_tokens": int,          # 本轮 LLM 用量合计
           "completion_tokens": int,
           "total_tokens": int
       }}

运行状态推导规则：

- ``start()`` / 首个 ``handle()``：``thinking``；
- ``llm_text_delta``：``thinking``；
- ``tool_call``：``tool_calling`` -> ``tool-input-available`` -> ``executing``；
- ``tool_result``：``tool-output-available`` -> ``thinking``；
- ``approval_required``：``waiting_approval``；
- ``approval_resolved``：先下发审批结果，再回到 ``executing``；
- ``plan_step_started``：``executing``，并附带当前步骤上下文；
- ``run_status``：直接采用 ``event.status`` 与 ``event.content``；
- ``notice`` / ``plan_decision``：只更新 ``message``，不改变状态；
- ``react_completed``：只更新 ``message``，终态仍由 ``close()`` 输出；
- 终态仅由 ``close(status)`` 产生，且整个流最多出现一次；
- ``close()`` 之后调用 ``handle()`` 返回空列表，不重复输出。
"""

from __future__ import annotations

import dataclasses
import json
import time
from typing import Any, Callable

from ..core.events import AgentEvent
from ..observability import get_logger

logger = get_logger(__name__)

# 路由层设置 SSE 响应头时使用，声明 UI Message Stream 协议版本
UI_MESSAGE_STREAM_HEADERS: dict[str, str] = {"x-vercel-ai-data-stream": "v1"}

# 流结束帧
_DONE_FRAME = "data: [DONE]\n\n"

# 触发计划快照下发的事件类型
_PLAN_EVENT_TYPES = frozenset(
    {
        "plan_started",
        "plan_step_started",
        "plan_step_completed",
        "plan_replanned",
        "plan_completed",
    }
)


def _frame(payload: dict[str, Any]) -> str:
    """把一个 JSON 部件序列化为 SSE 帧。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _parse_arguments(raw: Any) -> Any:
    """解析工具参数：dict / list 原样返回，JSON 文本解析为对象，失败保留原文。"""
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    return raw


def _as_dict(value: Any) -> dict[str, Any]:
    """把计划状态对象或字典统一为字典。"""
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return {
        name: getattr(value, name, None)
        for name in ("task", "status", "current_step", "replans", "steps")
    }


def _serialize_plan(state: Any) -> dict[str, Any]:
    """把 PlanState 序列化为 data-plan 契约结构。"""
    data = _as_dict(state)
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(data.get("steps") or [], start=1):
        step_data = _as_dict(step)
        steps.append(
            {
                "index": index,
                "title": step_data.get("title") or "",
                "description": step_data.get("description") or "",
                "status": step_data.get("status") or "pending",
                "summary": step_data.get("summary") or "",
            }
        )
    return {
        "task": data.get("task") or "",
        "status": data.get("status") or "",
        "current_step": data.get("current_step"),
        "total_steps": len(steps),
        "replans": int(data.get("replans") or 0),
        "steps": steps,
    }


class UIStreamAdapter:
    """把一个 run 的事件序列转换为 UI Message Stream 帧。"""

    def __init__(
        self,
        *,
        run_id: str,
        message_id: str | None = None,
        plan_snapshot: Callable[[], Any] | None = None,
        session_id: str = "",
    ) -> None:
        """初始化适配器。

        参数:
            run_id: 本次运行的唯一 id，写入所有自定义 data 部件。
            message_id: 助手消息 id，缺省与 run_id 相同。
            plan_snapshot: 可选回调，返回当前 PlanState（或可序列化对象）；
                为 None 或返回 None 时不下发 data-plan。
            session_id: 会话 id，写入 data-run-status。
        """
        self.run_id = run_id
        self.message_id = message_id or run_id
        self.session_id = session_id
        self._plan_snapshot = plan_snapshot
        self._start_perf = time.perf_counter()
        self._started = False
        self._closed = False
        self._text_started = False
        self._status: str | None = None
        self._message: str | None = None
        self._iteration: int | None = None
        self._plan_step_index: int | None = None
        self._plan_step_total: int | None = None
        self._plan_step_title: str | None = None
        # 本轮起始时间（perf_counter 秒），用于计算轮次耗时
        self._start_perf: float | None = None
        self._usage_totals: dict[str, float] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            # 缓存命中的 prompt token 数
            "cached_tokens": 0,
            "latency_ms": 0.0,
        }

    @property
    def closed(self) -> bool:
        """适配器是否已输出终态。"""
        return self._closed

    def start(self) -> list[str]:
        """输出流起始帧与初始运行状态，重复调用只输出一次。

        返回:
            SSE 文本帧列表。
        """
        if self._closed:
            return []
        return self._ensure_started()

    def handle(self, event: AgentEvent) -> list[str]:
        """处理一个 Agent 事件并返回对应的 SSE 帧。

        参数:
            event: 引擎产生的结构化事件。

        返回:
            SSE 文本帧列表；适配器已关闭时返回空列表。
        """
        if self._closed:
            return []
        frames = self._ensure_started()
        event_type = event.event_type
        if event.iteration is not None:
            self._iteration = event.iteration

        if event_type == "llm_text_delta":
            frames.extend(self._transition("thinking"))
            if event.content:
                if not self._text_started:
                    self._text_started = True
                    frames.append(
                        _frame({"type": "text-start", "id": self.message_id})
                    )
                frames.append(
                    _frame(
                        {
                            "type": "text-delta",
                            "id": self.message_id,
                            "delta": event.content,
                        }
                    )
                )
        elif event_type == "tool_call":
            # 先进入 tool_calling，携带工具输入后再进入 executing
            frames.extend(self._transition("tool_calling", force=True))
            frames.append(
                _frame(
                    {
                        "type": "tool-input-available",
                        "toolCallId": event.tool_call_id,
                        "toolName": event.tool_name,
                        "input": _parse_arguments(event.tool_arguments),
                    }
                )
            )
            frames.extend(self._transition("executing", force=True))
        elif event_type == "tool_result":
            frames.append(
                _frame(
                    {
                        "type": "tool-output-available",
                        "toolCallId": event.tool_call_id,
                        "output": event.content if event.content is not None else "",
                    }
                )
            )
            frames.extend(self._transition("thinking", force=True))
        elif event_type == "llm_completed":
            if event.usage:
                frames.append(self._usage_frame(event))
        elif event_type == "approval_required":
            frames.append(self._approval_frame(event, "pending"))
            frames.extend(self._transition("waiting_approval", force=True))
        elif event_type == "approval_resolved":
            action = (event.approval_action or "").lower()
            status = "approved" if action in ("approve", "always") else "denied"
            frames.append(self._approval_frame(event, status))
            frames.extend(self._transition("executing", force=True))
        elif event_type in _PLAN_EVENT_TYPES:
            if event_type in ("plan_step_started", "plan_step_completed"):
                self._plan_step_index = event.plan_step_index
                self._plan_step_total = event.plan_step_total
                self._plan_step_title = event.plan_step
            payload = self._plan_payload()
            if payload is not None:
                frames.append(_frame({"type": "data-plan", "data": payload}))
            if event_type == "plan_step_started":
                frames.extend(self._transition("executing", force=True))
        elif event_type == "run_status":
            status = event.status or self._status or "thinking"
            frames.extend(self._transition(status, event.content, force=True))
        elif event_type in ("notice", "plan_decision"):
            if event.content is not None:
                frames.extend(
                    self._transition(self._status or "thinking", event.content)
                )
        elif event_type == "react_completed":
            frames.extend(self._transition(self._status or "thinking", "回答已生成"))
        return frames

    def close(self, status: str, message: str | None = None) -> list[str]:
        """输出终态、finish 与 [DONE]，重复调用返回空列表。

        参数:
            status: 终态，取值为 ``completed`` / ``failed`` / ``cancelled``。
            message: 可选的终态说明，例如异常摘要或取消原因。

        返回:
            SSE 文本帧列表；首次调用之后再次调用返回空列表。
        """
        if self._closed:
            return []
        self._closed = True
        frames = self._ensure_started(with_status=False)
        if self._text_started:
            frames.append(_frame({"type": "text-end", "id": self.message_id}))
            self._text_started = False
        self._status = status
        if message is not None:
            self._message = message
        frames.append(self._run_status_frame())
        frames.append(self._turn_usage_frame())
        frames.append(_frame({"type": "finish"}))
        frames.append(_DONE_FRAME)
        return frames

    def _turn_usage_frame(self) -> str:
        """构造 data-turn-usage 帧：本轮耗时与 token 用量。"""
        duration_ms: float | None = None
        if self._start_perf is not None:
            duration_ms = round((time.perf_counter() - self._start_perf) * 1000)
        return _frame(
            {
                "type": "data-turn-usage",
                "data": {
                    "run_id": self.run_id,
                    "message_id": self.message_id,
                    "duration_ms": duration_ms,
                    "prompt_tokens": int(self._usage_totals["prompt_tokens"]),
                    "completion_tokens": int(self._usage_totals["completion_tokens"]),
                    "total_tokens": int(self._usage_totals["total_tokens"]),
                    "cached_tokens": int(self._usage_totals["cached_tokens"]),
                },
            }
        )

    def _ensure_started(self, *, with_status: bool = True) -> list[str]:
        """惰性输出 start 帧与初始状态帧，只输出一次。"""
        if self._started:
            return []
        self._started = True
        frames = [_frame({"type": "start", "messageId": self.message_id})]
        if with_status:
            if self._status is None:
                self._status = "thinking"
            frames.append(self._run_status_frame())
        return frames

    def _transition(
        self, status: str, message: str | None = None, *, force: bool = False
    ) -> list[str]:
        """推进运行状态；状态或消息变化（或强制）时输出 data-run-status。"""
        changed = status != self._status
        self._status = status
        if message is not None:
            changed = changed or message != self._message
            self._message = message
        if changed or force:
            return [self._run_status_frame()]
        return []

    def _run_status_frame(self) -> str:
        """构造 data-run-status 帧。"""
        return _frame(
            {
                "type": "data-run-status",
                "data": {
                    "run_id": self.run_id,
                    "session_id": self.session_id,
                    "status": self._status,
                    "message": self._message,
                    "iteration": self._iteration,
                    "plan_step_index": self._plan_step_index,
                    "plan_step_total": self._plan_step_total,
                    "plan_step_title": self._plan_step_title,
                },
            }
        )

    def _usage_frame(self, event: AgentEvent) -> str:
        """累加本次 run 用量并构造 data-usage 帧。"""
        usage = event.usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or 0)
        cached_tokens = int(usage.get("cached_tokens") or 0)

        self._usage_totals["prompt_tokens"] += prompt_tokens
        self._usage_totals["completion_tokens"] += completion_tokens
        self._usage_totals["total_tokens"] += total_tokens
        self._usage_totals["cached_tokens"] += cached_tokens
        if event.latency_ms is not None:
            self._usage_totals["latency_ms"] = round(
                self._usage_totals["latency_ms"] + event.latency_ms, 1
            )

        return _frame(
            {
                "type": "data-usage",
                "data": {
                    "run_id": self.run_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "cached_tokens": cached_tokens,
                    "latency_ms": event.latency_ms,
                    "run_total": {
                        "prompt_tokens": int(self._usage_totals["prompt_tokens"]),
                        "completion_tokens": int(
                            self._usage_totals["completion_tokens"]
                        ),
                        "total_tokens": int(self._usage_totals["total_tokens"]),
                        "cached_tokens": int(self._usage_totals["cached_tokens"]),
                        "latency_ms": self._usage_totals["latency_ms"],
                    },
                },
            }
        )

    def _approval_frame(self, event: AgentEvent, status: str) -> str:
        """构造 data-approval 帧。"""
        return _frame(
            {
                "type": "data-approval",
                "data": {
                    "approval_id": event.approval_id,
                    "tool_name": event.tool_name,
                    "arguments": _parse_arguments(event.tool_arguments),
                    "reason": event.content,
                    "status": status,
                    "expires_at": event.expires_at,
                },
            }
        )

    def _plan_payload(self) -> dict[str, Any] | None:
        """取当前计划快照并序列化为 data-plan 契约结构。"""
        if self._plan_snapshot is None:
            return None
        state = self._plan_snapshot()
        if state is None:
            return None
        return _serialize_plan(state)
