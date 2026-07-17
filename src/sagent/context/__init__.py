"""上下文管理模块。

提供 token 估算、分层压缩策略与上下文管理器，保证发送给 LLM 的消息长度始终在安全范围内。
"""

from .context_manager import ContextManager
from .strategies import (
    CompressionStrategy,
    LLMSummaryCompression,
    SlidingWindowPruning,
    ToolMessageOffload,
    ToolOutputTruncation,
)
from .token_counter import count_text_tokens, count_tokens

__all__ = [
    "count_text_tokens",
    "count_tokens",
    "CompressionStrategy",
    "ToolOutputTruncation",
    "ToolMessageOffload",
    "SlidingWindowPruning",
    "LLMSummaryCompression",
    "ContextManager",
]
