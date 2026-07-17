"""pytest 公共 fixture 与测试辅助。

核心是 FakeLLMClient：它实现与 sagent.llm.client.LLMClient 相同的 chat() 接口，
按预设的响应队列逐次返回 LLMResponse，从而在不调用真实模型的情况下驱动
ReAct / Plan 引擎的多轮交互，实现可复现的离线测试。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sagent.config.models import AgentConfig
from sagent.llm.client import LLMResponse


def make_tool_call(name: str, arguments: dict[str, Any] | str, call_id: str | None = None) -> dict[str, Any]:
    """构造一个 tool_call dict，格式与 LLMClient 解析后的结构一致。

    参数:
        name: 工具名称。
        arguments: 参数，dict 会被序列化为 JSON 字符串（模拟 LLM 输出）。
        call_id: 可选调用 id，默认根据名称生成。
    """
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "id": call_id or f"call_{name}",
        "name": name,
        "arguments": arguments,
    }


def text_response(content: str, usage: dict[str, int] | None = None) -> LLMResponse:
    """构造一个纯文本响应（无工具调用）。

    参数:
        content: 助手返回的文本内容。
        usage: 可选的 token 用量，形如 {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}。
    """
    return LLMResponse(content=content, tool_calls=[], raw_message={"role": "assistant", "content": content}, usage=usage)


def tool_response(tool_calls: list[dict[str, Any]], content: str = "", usage: dict[str, int] | None = None) -> LLMResponse:
    """构造一个带工具调用的响应。

    参数:
        tool_calls: 工具调用列表。
        content: 助手返回的文本内容（通常为空）。
        usage: 可选的 token 用量。
    """
    return LLMResponse(content=content, tool_calls=tool_calls, raw_message={"role": "assistant", "content": content}, usage=usage)


class FakeLLMClient:
    """假的 LLM 客户端，按队列返回预设响应。

    与真实 LLMClient 一样暴露 chat(messages, tools=None) -> LLMResponse。
    每次调用 chat 会弹出队列头部的一个响应；同时记录所有入参，便于断言。
    """

    def __init__(self, responses: list[LLMResponse]):
        # 使用副本，避免测试间相互影响
        self._responses = list(responses)
        # 记录每次 chat 的调用参数：{"messages": ..., "tools": ...}
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if not self._responses:
            raise AssertionError(
                "FakeLLMClient 响应队列已空，但引擎再次调用了 chat()。"
                "请检查预设响应数量是否与预期轮次匹配。"
            )
        return self._responses.pop(0)

    @property
    def remaining(self) -> int:
        """队列中剩余未消费的响应数量。"""
        return len(self._responses)


@pytest.fixture
def agent_config() -> AgentConfig:
    """提供一个默认 Agent 配置（较小的迭代上限，便于测试上限逻辑）。"""
    return AgentConfig(mode="react", max_iterations=5)


@pytest.fixture
def make_fake_llm():
    """工厂 fixture：传入响应列表，返回 FakeLLMClient。"""

    def _factory(responses: list[LLMResponse]) -> FakeLLMClient:
        return FakeLLMClient(responses)

    return _factory
