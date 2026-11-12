"""事件模型扩展、取消令牌与 LLM 用量事件的单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_tool_call, text_response, tool_response

from sagent.cli.event_presenter import EventPresenter
from sagent.config.models import LLMConfig
from sagent.core.cancellation import CancelToken, RunCancelled
from sagent.core.events import AgentEvent
from sagent.core.plan_engine import PlanEngine
from sagent.core.react_engine import ReActEngine
from sagent.llm.client import LLMClient, LLMResponse, LLMStreamEvent
from sagent.tools import build_default_registry


class RecordingOutput:
    """记录写入内容的 Presenter 输出适配器。"""

    def __init__(self) -> None:
        self.text = ""

    def write(self, data: str) -> None:
        self.text += data

    def flush(self) -> None:
        pass


@pytest.mark.parametrize(
    "event",
    [
        AgentEvent(event_type="unknown_future_event"),  # type: ignore[arg-type]
        AgentEvent(event_type="run_status", status="thinking"),
        AgentEvent(event_type="approval_required", approval_id="a1"),
        AgentEvent(event_type="approval_resolved", approval_id="a1", approval_action="approve"),
        AgentEvent(event_type="usage", usage={"total_tokens": 1}),
    ],
)
def test_presenter_ignores_unknown_and_new_event_types(event):
    """未知与新增运行状态类事件不产生任何输出且不抛异常。"""
    output = RecordingOutput()
    EventPresenter(output).handle(event)
    assert output.text == ""


def test_cancel_token_raises_only_after_cancel():
    """未取消时不抛异常，取消后 raise_if_cancelled 抛出 RunCancelled。"""
    token = CancelToken()
    assert token.is_cancelled() is False
    token.raise_if_cancelled()
    assert token.is_cancelled() is False

    token.cancel()
    assert token.is_cancelled() is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()


def test_react_engine_stops_before_tool_execute_when_cancelled(
    make_fake_llm, agent_config, monkeypatch
):
    """取消后工具不被执行，RunCancelled 向上传播且不再发起新的 LLM 请求。"""
    llm = make_fake_llm(
        [
            tool_response([make_tool_call("read_file", {"path": "a.txt"})]),
            text_response("不应到达这里"),
        ]
    )
    token = CancelToken()
    registry = build_default_registry()
    executed: list[str] = []

    def _record_execute(name, arguments):
        executed.append(name)
        return "不应执行"

    monkeypatch.setattr(registry, "execute", _record_execute)

    engine = ReActEngine(llm, registry, agent_config, cancel_token=token)

    original_chat = llm.chat

    def _chat_then_cancel(messages, tools=None, on_event=None):
        response = original_chat(messages, tools=tools, on_event=on_event)
        # 模拟用户恰好在收到工具调用响应后点击停止
        token.cancel()
        return response

    monkeypatch.setattr(llm, "chat", _chat_then_cancel)

    with pytest.raises(RunCancelled):
        engine.run("读取文件")

    assert executed == []
    assert len(llm.calls) == 1


def test_plan_engine_propagates_cancelled_from_step(make_fake_llm, agent_config):
    """步骤内取消不会被重规划逻辑吞掉，而是继续向上抛出。"""
    steps_json = '{"steps": [{"title": "读取", "description": "读取文件"}]}'
    llm = make_fake_llm(
        [
            text_response(steps_json),
            tool_response([make_tool_call("read_file", {"path": "a.txt"})]),
        ]
    )
    token = CancelToken()
    engine = PlanEngine(llm, build_default_registry(), agent_config, cancel_token=token)

    original_chat = llm.chat
    call_count = 0

    def _chat_then_cancel(messages, tools=None, on_event=None):
        nonlocal call_count
        call_count += 1
        response = original_chat(messages, tools=tools, on_event=on_event)
        if call_count == 2:
            # 步骤内 ReAct 拿到工具调用响应后立即取消
            token.cancel()
        return response

    setattr(llm, "chat", _chat_then_cancel)

    with pytest.raises(RunCancelled):
        engine.run("读取文件")

    # 仅拆解与步骤请求，取消后未触发重规划请求
    assert call_count == 2


def test_react_llm_completed_event_carries_usage_and_latency(make_fake_llm, agent_config):
    """llm_completed 事件填充 usage 与 latency_ms。"""
    events: list[AgentEvent] = []
    engine = ReActEngine(
        make_fake_llm([]),
        build_default_registry(),
        agent_config,
        on_event=events.append,
    )
    response = text_response(
        "答案",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )

    engine._on_llm_event(
        LLMStreamEvent("completed", response=response, latency_ms=12.5)
    )

    completed = [event for event in events if event.event_type == "llm_completed"]
    assert len(completed) == 1
    assert completed[0].usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert completed[0].latency_ms == 12.5


def test_stream_chat_emits_latency_in_completed_event():
    """LLMClient.stream_chat 的 completed 事件携带耗时与响应。"""
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(delta=SimpleNamespace(content="答", tool_calls=None))],
        ),
        SimpleNamespace(usage=usage, choices=[]),
    ]

    client = object.__new__(LLMClient)
    client.config = LLMConfig(model="test-model", stream=True)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: chunks)
        )
    )

    events: list[LLMStreamEvent] = []
    response: LLMResponse = client.stream_chat([{"role": "user", "content": "问"}], on_event=events.append)

    assert response.content == "答"
    completed = [event for event in events if event.event_type == "completed"]
    assert len(completed) == 1
    assert completed[0].latency_ms is not None
    assert completed[0].latency_ms >= 0
    assert completed[0].response is response


def test_react_engine_without_cancel_token_behaves_as_before(make_fake_llm, agent_config):
    """不传取消令牌时普通单轮 ReAct 行为不变。"""
    llm = make_fake_llm([text_response("最终答案")])
    engine = ReActEngine(llm, build_default_registry(), agent_config)

    assert engine.run("你好") == "最终答案"
    assert len(llm.calls) == 1
