"""LLM 客户端。

基于 openai SDK 封装统一的对话调用接口，支持 function calling。
兼容任意 OpenAI 兼容服务（通过 base_url 指定）。
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from ..config.models import LLMConfig


class LLMResponse:
    """统一的 LLM 响应结构。

    属性:
        content: 助手返回的文本内容，可能为空字符串。
        tool_calls: 工具调用列表，每个元素为 dict，包含 id、name、arguments（原始 JSON 字符串）。
        raw_message: openai SDK 返回的原始 message 对象，供构造消息历史使用。
    """

    def __init__(self, content: str, tool_calls: list[dict[str, Any]], raw_message: Any):
        self.content = content
        self.tool_calls = tool_calls
        self.raw_message = raw_message

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用。"""
        return len(self.tool_calls) > 0


class LLMClient:
    """封装 openai SDK 的 LLM 客户端。"""

    def __init__(self, config: LLMConfig):
        """根据 LLM 配置初始化底层 OpenAI 客户端。"""
        self.config = config
        # base_url 为空时传入 None，使用官方默认地址
        base_url = config.base_url or None
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=base_url,
            timeout=config.timeout,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """发起一次对话请求。

        参数:
            messages: OpenAI 格式的消息历史。
            tools: 可选的工具 schema 列表（function calling 格式）。

        返回:
            LLMResponse: 统一响应结构。
        """
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        completion = self._client.chat.completions.create(**kwargs)
        message = completion.choices[0].message

        content = message.content or ""

        tool_calls: list[dict[str, Any]] = []
        if getattr(message, "tool_calls", None):
            for call in message.tool_calls:
                tool_calls.append(
                    {
                        "id": call.id,
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    }
                )

        return LLMResponse(content=content, tool_calls=tool_calls, raw_message=message)
