"""Agent 事件的 CLI 展示适配器。"""

from __future__ import annotations

import sys
from typing import TextIO

from ..core.events import AgentEvent


class EventPresenter:
    """将结构化 Agent 事件渲染为 CLI 过程输出。"""

    def __init__(self, output: TextIO | None = None) -> None:
        """初始化展示器。"""
        self._output = output or sys.stdout

    def handle(self, event: AgentEvent) -> None:
        """渲染一个 Agent 事件。"""
        if event.event_type == "plan_started":
            self._write(event.content or "")
        elif event.event_type == "plan_step_started":
            self._write(
                f"\n[执行步骤 {event.plan_step_index}/{event.plan_step_total}] "
                f"{event.plan_step or event.content or ''}"
            )
        elif event.event_type == "tool_call":
            self._write(f"[行动] 调用工具 {event.tool_name}，参数: {event.tool_arguments}")
        elif event.event_type == "tool_result":
            self._write(f"[观察] {event.content or ''}")
        elif event.event_type == "notice":
            if event.mode == "plan" and event.plan_step_index is not None and event.scope == "top_level":
                self._write(f"  {event.plan_step_index}. {event.plan_step}")
            else:
                self._write(f"[提示] {event.content or ''}")

    def _write(self, text: str) -> None:
        """输出过程文本。"""
        print(text, file=self._output, flush=True)
