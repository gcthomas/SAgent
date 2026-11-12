"""Agent 执行事件模型。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

EventType = Literal[
    "plan_started",
    "plan_decision",
    "plan_replanned",
    "plan_completed",
    "plan_step_started",
    "plan_step_completed",
    "tool_call",
    "tool_result",
    "notice",
    "react_completed",
    "llm_text_delta",
    "llm_completed",
    "run_status",
    "approval_required",
    "approval_resolved",
    "usage",
]


@dataclass(frozen=True)
class AgentEvent:
    """描述 Agent 执行过程中的一个结构化事件。"""

    event_type: EventType
    content: str | None = None
    mode: Literal["react", "plan"] = "react"
    scope: str = "top_level"
    iteration: int | None = None
    tool_name: str | None = None
    tool_arguments: Any = None
    tool_call_id: str | None = None
    plan_step_index: int | None = None
    plan_step_total: int | None = None
    plan_step: str | None = None
    plan_status: str | None = None
    status: str | None = None
    approval_id: str | None = None
    approval_action: str | None = None
    # 审批等待的截止时间戳（time.monotonic 秒）；无限等待为 None
    expires_at: float | None = None
    usage: dict[str, int] | None = None
    latency_ms: float | None = None

    def with_plan_context(self, *, index: int, total: int, step: str) -> AgentEvent:
        """补充当前 Plan 步骤上下文。"""
        return replace(
            self,
            mode="plan",
            scope="plan_step",
            plan_step_index=index,
            plan_step_total=total,
            plan_step=step,
        )


EventHandler = Callable[[AgentEvent], None]
