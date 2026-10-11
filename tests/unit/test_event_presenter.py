"""Agent 事件 CLI 展示测试。"""

from __future__ import annotations

from io import StringIO

from sagent.cli.event_presenter import EventPresenter
from sagent.core.events import AgentEvent


class RecordingOutput:
    def __init__(self) -> None:
        self.text = ""
        self.flushes = 0

    def write(self, data: str) -> None:
        self.text += data

    def flush(self) -> None:
        self.flushes += 1


def test_presenter_renders_tool_events_without_event_text_parsing():
    output = StringIO()
    presenter = EventPresenter(output)

    presenter.handle(
        AgentEvent(
            event_type="tool_call",
            tool_name="read_file",
            tool_arguments={"path": "a.txt"},
        )
    )
    presenter.handle(AgentEvent(event_type="tool_result", content="文件内容"))

    assert output.getvalue() == "[行动] 调用工具 read_file，参数: {'path': 'a.txt'}\n[观察] 文件内容\n"


def test_presenter_does_not_render_react_completed():
    output = StringIO()
    EventPresenter(output).handle(
        AgentEvent(event_type="react_completed", content="最终答案")
    )

    assert output.getvalue() == ""


def test_presenter_renders_plan_step_context():
    output = StringIO()
    EventPresenter(output).handle(
        AgentEvent(
            event_type="plan_step_started",
            mode="plan",
            plan_step_index=1,
            plan_step_total=2,
            plan_step="读取文件",
        )
    )

    assert output.getvalue() == "\n[执行步骤 1/2] 读取文件\n"


def test_presenter_renders_top_level_content_deltas_once():
    output = RecordingOutput()
    presenter = EventPresenter(output, flush_interval=100.0)
    presenter.handle(AgentEvent(event_type="llm_text_delta", content="正常正文"))
    presenter.handle(AgentEvent(event_type="llm_completed", content="正常正文"))
    assert output.text == "助手 > 正常正文\n"
    assert presenter.turn_streamed is True


def test_presenter_renders_plan_status():
    output = StringIO()
    presenter = EventPresenter(output)
    presenter.handle(AgentEvent(event_type="plan_completed", mode="plan", plan_status="blocked"))
    assert output.getvalue() == "[计划] 计划受阻\n"


def test_presenter_flushes_by_character_threshold_and_separates_tool_output():
    output = RecordingOutput()
    presenter = EventPresenter(output, flush_interval=100.0, flush_chars=3)

    presenter.handle(AgentEvent(event_type="llm_text_delta", content="正文"))
    assert output.text == "助手 > "
    presenter.handle(AgentEvent(event_type="llm_text_delta", content="内容"))
    presenter.handle(
        AgentEvent(event_type="tool_call", tool_name="read_file", tool_arguments={"path": "a"})
    )
    presenter.handle(AgentEvent(event_type="tool_result", content="完成"))

    assert output.text == (
        "助手 > 正文内容\n"
        "[行动] 调用工具 read_file，参数: {'path': 'a'}\n"
        "[观察] 完成\n"
    )
    assert output.flushes == 5


def test_presenter_end_streaming_finishes_partial_line_and_next_turn_reprefixes():
    """流式中途异常时 end_streaming 终结半行，新一轮重新输出前缀不粘连。"""
    output = RecordingOutput()
    presenter = EventPresenter(output, flush_interval=100.0, flush_chars=100)

    presenter.handle(AgentEvent(event_type="llm_text_delta", content="正文"))
    presenter.end_streaming()
    assert output.text == "助手 > 正文\n"

    # 新一轮：begin_turn 重置标记，增量重新输出前缀
    presenter.begin_turn()
    assert presenter.turn_streamed is False
    presenter.handle(AgentEvent(event_type="llm_text_delta", content="正文"))
    assert output.text.endswith("助手 > ")
    presenter.handle(AgentEvent(event_type="llm_completed"))
    assert output.text.endswith("助手 > 正文\n")
    assert presenter.turn_streamed is True


def test_presenter_end_streaming_without_streaming_is_noop():
    """无流式输出时 end_streaming 不产生任何输出与刷新。"""
    output = RecordingOutput()
    presenter = EventPresenter(output)

    presenter.end_streaming()

    assert output.text == ""
    assert output.flushes == 0


def test_presenter_turn_streamed_tracks_delta_and_resets_on_begin_turn():
    """turn_streamed 在首个增量后置位，llm_completed 后保持，begin_turn 后复位。"""
    output = RecordingOutput()
    presenter = EventPresenter(output)

    presenter.begin_turn()
    assert presenter.turn_streamed is False

    presenter.handle(AgentEvent(event_type="llm_text_delta", content="正文"))
    assert presenter.turn_streamed is True

    presenter.handle(AgentEvent(event_type="llm_completed"))
    assert presenter.turn_streamed is True

    presenter.begin_turn()
    assert presenter.turn_streamed is False
