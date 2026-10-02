"""Agent 事件 CLI 展示测试。"""

from __future__ import annotations

from io import StringIO

from sagent.cli.event_presenter import EventPresenter
from sagent.core.events import AgentEvent


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
