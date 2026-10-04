"""LLM 客户端流式调用单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from sagent.config.models import LLMConfig
from sagent.llm.client import LLMClient


def _chunk(content=None, tool_calls=None, usage=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tool_delta(index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _usage(prompt=11, completion=7, total=18):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def _client(monkeypatch, stream=True):
    client = LLMClient(LLMConfig(model="test-model", api_key="test-key", stream=stream))
    return client


def test_stream_chat_emits_text_and_usage(monkeypatch):
    client = _client(monkeypatch)
    chunks = [
        _chunk("你好"),
        _chunk("，世界"),
        SimpleNamespace(choices=[], usage=_usage()),
    ]
    calls = []
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: (calls.append(kwargs) or chunks))

    events = []
    response = client.chat([{"role": "user", "content": "问候"}], on_event=events.append)

    assert calls[0]["stream"] is True
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert response.content == "你好，世界"
    assert response.tool_calls == []
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert [event.content for event in events if event.event_type == "text_delta"] == ["你好", "，世界"]
    assert events[-1].event_type == "completed"
    assert events[-1].response is response


def test_stream_chat_aggregates_tool_call_fragments(monkeypatch):
    client = _client(monkeypatch)
    chunks = [
        _chunk(tool_calls=[_tool_delta(0, "call_1", "read", '{"pa')]),
        _chunk(tool_calls=[_tool_delta(0, None, "_file", 'th":"a"}')]),
        SimpleNamespace(choices=[], usage=_usage(4, 3, 7)),
    ]
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: chunks)

    events = []
    response = client.chat([], on_event=events.append)

    assert response.content == ""
    assert response.tool_calls == [{"id": "call_1", "name": "read_file", "arguments": '{"path":"a"}'}]
    assert response.raw_message["tool_calls"][0]["function"]["name"] == "read_file"
    assert [event.event_type for event in events] == ["tool_call_delta", "tool_call_delta", "completed"]


def test_chat_uses_non_streaming_when_disabled(monkeypatch):
    client = _client(monkeypatch, stream=False)
    message = SimpleNamespace(content="完成", tool_calls=[])
    completion = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=_usage())
    calls = []
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: (calls.append(kwargs) or completion))

    response = client.chat([])

    assert "stream" not in calls[0]
    assert response.content == "完成"
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
