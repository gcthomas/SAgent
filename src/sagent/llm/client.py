"""LLM 客户端。

基于 openai SDK 封装统一的对话调用接口，支持 function calling 与流式响应。
兼容任意 OpenAI 兼容服务（通过 base_url 指定）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from openai import OpenAI

from ..config.models import LLMConfig
from ..observability import Span, get_logger
from ..observability.content import is_content_capture_enabled, truncate_content

logger = get_logger(__name__)


class LLMResponse:
    """统一的 LLM 响应结构。"""

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


@dataclass(frozen=True)
class LLMStreamEvent:
    """LLM 流式响应的内部增量事件。"""

    event_type: Literal["text_delta", "tool_call_delta", "completed"]
    content: str | None = None
    tool_call_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    response: LLMResponse | None = None


StreamCallback = Callable[[LLMStreamEvent], None]


class LLMClient:
    """封装 openai SDK 的 LLM 客户端。"""

    def __init__(self, config: LLMConfig):
        """根据 LLM 配置初始化底层 OpenAI 客户端。"""
        self.config = config
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
        on_event: StreamCallback | None = None,
    ) -> LLMResponse:
        """发起一次对话请求，按配置选择流式或非流式路径。"""
        if self.config.stream:
            return self.stream_chat(messages, tools=tools, on_event=on_event)
        return self._chat_non_streaming(messages, tools)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_event: StreamCallback | None = None,
    ) -> LLMResponse:
        """发起流式请求并聚合为完整响应，同时按顺序发送增量事件。"""
        with Span("gen_ai.chat") as span:
            self._set_request_span(span, messages, tools)
            kwargs = self._build_kwargs(messages, tools)
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            self._log_request(messages, tools)
            start = time.perf_counter()
            try:
                chunks = self._client.chat.completions.create(**kwargs)
                content_parts: list[str] = []
                calls: dict[int, dict[str, str]] = {}
                usage_obj: Any = None
                for chunk in chunks:
                    if getattr(chunk, "usage", None) is not None:
                        usage_obj = chunk.usage
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    text = getattr(delta, "content", None)
                    if text:
                        content_parts.append(text)
                        self._emit(on_event, LLMStreamEvent("text_delta", content=text))
                    for index, call_delta in enumerate(getattr(delta, "tool_calls", None) or []):
                        call_index = getattr(call_delta, "index", None)
                        if call_index is None:
                            call_index = index
                        function = getattr(call_delta, "function", None)
                        item = calls.setdefault(call_index, {"id": "", "name": "", "arguments": ""})
                        call_id = getattr(call_delta, "id", None)
                        if call_id:
                            item["id"] = call_id
                        name = getattr(function, "name", None) if function else None
                        arguments = getattr(function, "arguments", None) if function else None
                        if name:
                            item["name"] += name
                        if arguments:
                            item["arguments"] += arguments
                        self._emit(
                            on_event,
                            LLMStreamEvent(
                                "tool_call_delta",
                                tool_call_index=call_index,
                                tool_call_id=call_id,
                                tool_name=name,
                                tool_arguments=arguments,
                            ),
                        )
            except Exception:
                self._log_error(start)
                raise

            content = "".join(content_parts)
            tool_calls = [calls[index] for index in sorted(calls)]
            raw_message = {
                "role": "assistant",
                "content": content,
                **({"tool_calls": [
                    {"id": call["id"], "type": "function", "function": {
                        "name": call["name"], "arguments": call["arguments"]
                    }} for call in tool_calls
                ]} if tool_calls else {}),
            }
            response = LLMResponse(content, tool_calls, raw_message, self._usage_dict(usage_obj))
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            self._log_response(response, response.usage, latency_ms, usage_obj)
            self._set_response_span(span, response, latency_ms, usage_obj)
            self._emit(on_event, LLMStreamEvent("completed", response=response))
            return response

    def _chat_non_streaming(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> LLMResponse:
        """执行原有非流式请求路径。"""
        with Span("gen_ai.chat") as span:
            self._set_request_span(span, messages, tools)
            kwargs = self._build_kwargs(messages, tools)
            self._log_request(messages, tools)
            start = time.perf_counter()
            try:
                completion = self._client.chat.completions.create(**kwargs)
            except Exception:
                self._log_error(start)
                raise
            message = completion.choices[0].message
            content = message.content or ""
            tool_calls = self._parse_tool_calls(getattr(message, "tool_calls", None))
            usage = self._usage_dict(getattr(completion, "usage", None))
            response = LLMResponse(content, tool_calls, message, usage)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            self._log_response(response, usage, latency_ms, getattr(completion, "usage", None))
            self._set_response_span(span, response, latency_ms, getattr(completion, "usage", None))
            return response

    def _build_kwargs(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    @staticmethod
    def _parse_tool_calls(calls: Any) -> list[dict[str, Any]]:
        return [
            {"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
            for call in (calls or [])
        ]

    @staticmethod
    def _usage_dict(usage: Any) -> dict[str, int] | None:
        if usage is None:
            return None
        return {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }

    @staticmethod
    def _emit(callback: StreamCallback | None, event: LLMStreamEvent) -> None:
        if callback is not None:
            callback(event)

    def _set_request_span(self, span: Span, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> None:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.provider.name", "openai")
        span.set_attribute("gen_ai.request.model", self.config.model)
        span.set_attribute("gen_ai.request.message_count", len(messages))
        if tools:
            span.set_attribute("gen_ai.request.tool_count", len(tools))

    def _log_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> None:
        extra: dict[str, Any] = {
            "event": "llm_request", "model": self.config.model,
            "message_count": len(messages), "tool_count": len(tools) if tools else 0,
        }
        if is_content_capture_enabled():
            extra["messages"] = truncate_content(messages)
            extra["sagent.content.capture"] = True
        logger.info("发起 LLM 请求", extra=extra)

    def _log_error(self, start: float) -> None:
        logger.exception("LLM 请求失败", extra={
            "event": "llm_error", "model": self.config.model,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        })

    def _log_response(self, response: LLMResponse, usage: dict[str, int] | None, latency_ms: float, usage_obj: Any = None) -> None:
        extra: dict[str, Any] = {
            "event": "llm_response", "model": self.config.model,
            "latency_ms": latency_ms, "content_length": len(response.content),
            "has_tool_calls": response.has_tool_calls, "tool_call_count": len(response.tool_calls),
        }
        if usage:
            extra.update({"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"], "total_tokens": usage["total_tokens"]})
            prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
            cached = getattr(prompt_details, "cached_tokens", None) if prompt_details is not None else None
            if cached is not None:
                extra["cached_tokens"] = cached
            completion_details = getattr(usage_obj, "completion_tokens_details", None)
            reasoning = getattr(completion_details, "reasoning_tokens", None) if completion_details is not None else None
            if reasoning is not None:
                extra["reasoning_tokens"] = reasoning
        if is_content_capture_enabled():
            extra["content"] = truncate_content(response.content)
            extra["tool_calls"] = truncate_content(response.tool_calls)
            extra["sagent.content.capture"] = True
        logger.info("收到 LLM 响应", extra=extra)

    @staticmethod
    def _set_response_span(span: Span, response: LLMResponse, latency_ms: float, usage_obj: Any = None) -> None:
        if response.usage:
            span.set_attribute("gen_ai.usage.input_tokens", response.usage["prompt_tokens"])
            span.set_attribute("gen_ai.usage.output_tokens", response.usage["completion_tokens"])
            cached = getattr(getattr(usage_obj, "prompt_tokens_details", None), "cached_tokens", None)
            if cached is not None:
                span.set_attribute("gen_ai.usage.cache_read.input_tokens", cached)
            reasoning = getattr(getattr(usage_obj, "completion_tokens_details", None), "reasoning_tokens", None)
            if reasoning is not None:
                span.set_attribute("gen_ai.usage.reasoning_tokens", reasoning)
        span.set_attribute("gen_ai.response.latency_ms", latency_ms)
        span.set_attribute("gen_ai.response.content_length", len(response.content))
        span.set_attribute("gen_ai.response.tool_call_count", len(response.tool_calls))
