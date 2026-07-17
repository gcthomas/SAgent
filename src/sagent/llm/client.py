"""LLM 客户端。

基于 openai SDK 封装统一的对话调用接口，支持 function calling。
兼容任意 OpenAI 兼容服务（通过 base_url 指定）。
"""

from __future__ import annotations

import time
from typing import Any

from openai import OpenAI

from ..config.models import LLMConfig
from ..observability import get_logger, log_llm_content_enabled

logger = get_logger(__name__)


class LLMResponse:
    """统一的 LLM 响应结构。

    属性:
        content: 助手返回的文本内容，可能为空字符串。
        tool_calls: 工具调用列表，每个元素为 dict，包含 id、name、arguments（原始 JSON 字符串）。
        raw_message: openai SDK 返回的原始 message 对象，供构造消息历史使用。
        usage: LLM API 返回的 token 用量，形如 {"prompt_tokens": N, "completion_tokens": M, "total_tokens": T}；
               部分 OpenAI 兼容服务不返回 usage，此时为 None。
    """

    def __init__(
        self,
        content: str,
        tool_calls: list[dict[str, Any]],
        raw_message: Any,
        usage: dict[str, int] | None = None,
    ):
        self.content = content
        self.tool_calls = tool_calls
        self.raw_message = raw_message
        self.usage = usage

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

        # 记录请求：默认仅摘要，开关开启时附完整 messages
        request_extra: dict[str, Any] = {
            "event": "llm_request",
            "model": self.config.model,
            "message_count": len(messages),
            "tool_count": len(tools) if tools else 0,
        }
        if log_llm_content_enabled():
            request_extra["messages"] = messages
        logger.info("发起 LLM 请求", extra=request_extra)

        start = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception:
            logger.exception(
                "LLM 请求失败",
                extra={
                    "event": "llm_error",
                    "model": self.config.model,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            raise
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
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

        # 记录响应：默认仅摘要，开关开启时附完整内容
        response_extra: dict[str, Any] = {
            "event": "llm_response",
            "model": self.config.model,
            "latency_ms": latency_ms,
            "content_length": len(content),
            "has_tool_calls": len(tool_calls) > 0,
            "tool_call_count": len(tool_calls),
        }
        # 记录 token 用量（并非所有 OpenAI 兼容服务都返回 usage，需容错）
        usage = getattr(completion, "usage", None)
        usage_dict: dict[str, int] | None = None
        if usage is not None:
            response_extra["input_tokens"] = usage.prompt_tokens
            response_extra["output_tokens"] = usage.completion_tokens
            response_extra["total_tokens"] = usage.total_tokens
            usage_dict = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            if prompt_details is not None:
                cached = getattr(prompt_details, "cached_tokens", None)
                if cached is not None:
                    response_extra["cached_tokens"] = cached
            completion_details = getattr(usage, "completion_tokens_details", None)
            if completion_details is not None:
                reasoning = getattr(completion_details, "reasoning_tokens", None)
                if reasoning is not None:
                    response_extra["reasoning_tokens"] = reasoning
        if log_llm_content_enabled():
            response_extra["content"] = content
            response_extra["tool_calls"] = tool_calls
        logger.info("收到 LLM 响应", extra=response_extra)

        return LLMResponse(content=content, tool_calls=tool_calls, raw_message=message, usage=usage_dict)
