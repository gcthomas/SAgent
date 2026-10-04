"""Agent 事件的 CLI 展示适配器。"""

from __future__ import annotations

import time
from typing import Callable, Protocol

from prompt_toolkit.output import Output, create_output

from ..core.events import AgentEvent


class OutputAdapter(Protocol):
    """Presenter 使用的最小终端写入协议。"""

    def write(self, data: str) -> None:
        """追加写入文本。"""

    def flush(self) -> None:
        """刷新已写入的文本。"""


class EventPresenter:
    """将结构化 Agent 事件渲染为 CLI 过程输出。"""

    def __init__(
        self,
        output: OutputAdapter | Output | None = None,
        *,
        flush_interval: float = 0.05,
        flush_chars: int = 80,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化展示器。"""
        self._output = output or create_output()
        self._flush_interval = flush_interval
        self._flush_chars = flush_chars
        self._clock = clock
        self._streaming = False
        self._delta_buffer = ""
        self._last_flush = self._clock()
        self._turn_streamed = False

    def handle(self, event: AgentEvent) -> None:
        """渲染一个 Agent 事件。"""
        if event.event_type == "llm_text_delta":
            self._write_delta(event.content or "")
        elif event.event_type == "llm_completed":
            self.end_streaming()
        elif event.event_type == "plan_started":
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

    def begin_turn(self) -> None:
        """开始新一轮对话：终结上一轮残留的流式输出并重置轮内流式标记。"""
        self.end_streaming()
        self._turn_streamed = False

    @property
    def turn_streamed(self) -> bool:
        """本轮对话是否已有流式文本增量输出。"""
        return self._turn_streamed

    def end_streaming(self) -> None:
        """结束一轮流式文本输出：刷新缓冲、补齐换行并复位流式状态。"""
        if self._streaming:
            self._flush_delta()
            self._output.write("\n")
            self._output.flush()
            self._streaming = False

    def _write(self, text: str) -> None:
        """输出过程文本。"""
        self.end_streaming()
        self._output.write(f"{text}\n")
        self._output.flush()

    def _write_delta(self, text: str) -> None:
        """追加模型文本增量，并按时间或字符阈值刷新。"""
        if not text:
            return
        if not self._streaming:
            self._output.write("助手 > ")
            self._streaming = True
            self._turn_streamed = True
        self._delta_buffer += text
        now = self._clock()
        if "\n" in self._delta_buffer or len(self._delta_buffer) >= self._flush_chars:
            self._flush_delta(now)
        elif now - self._last_flush >= self._flush_interval:
            self._flush_delta(now)

    def _flush_delta(self, now: float | None = None) -> None:
        """追加写出尚未刷新的文本并刷新适配器。"""
        if self._delta_buffer:
            self._output.write(self._delta_buffer)
            self._delta_buffer = ""
        self._output.flush()
        self._last_flush = self._clock() if now is None else now
