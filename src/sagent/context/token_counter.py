"""Token 估算器。提供对消息列表与文本的 token 计数能力，支持 tiktoken 精确计数与字符启发式估算。"""

from __future__ import annotations

from typing import Any

from ..observability import get_logger

logger = get_logger(__name__)


def _count_with_tiktoken(text: str, model: str) -> int | None:
    """尝试用 tiktoken 对文本计数。

    参数:
        text: 待计数的文本
        model: 模型名称，用于选择合适的编码器
    返回:
        token 数；tiktoken 未安装或编码获取失败时返回 None，由调用方回退
    """
    try:
        import tiktoken
    except ImportError:
        return None
    # 先按模型名选择编码器，失败则回退到通用编码
    try:
        encoding = tiktoken.encoding_for_model(model)
    except Exception:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None
    return len(encoding.encode(text))


def _count_with_heuristic(text: str) -> int:
    """字符启发式估算 token 数。

    统计中文字符（CJK 统一表意文字范围）与非中文字符数，
    中文字符约 2 字符/token，非中文字符约 4 字符/token。

    参数:
        text: 待估算的文本
    返回:
        估算的 token 数；非空文本至少为 1，空文本为 0
    """
    cjk_count = 0
    non_cjk_count = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            cjk_count += 1
        else:
            non_cjk_count += 1
    estimate = cjk_count // 2 + non_cjk_count // 4
    if estimate < 1 and text:
        estimate = 1
    return estimate


def count_text_tokens(text: str, model: str = "", method: str = "auto") -> int:
    """对单段文本估算 token 数。

    参数:
        text: 待计数的文本
        model: 模型名称，用于 tiktoken 选择编码器
        method: 计数方法，"auto"/"tiktoken"/"heuristic"
            - "auto": 优先 tiktoken 精确计数，不可用或模型编码未知时回退到字符启发式
            - "tiktoken": 强制使用 tiktoken，失败时回退到字符启发式并记录 WARNING
            - "heuristic": 始终使用字符启发式估算，不加载 tiktoken
    返回:
        估算的 token 数
    """
    if method == "heuristic":
        return _count_with_heuristic(text)

    if method == "tiktoken":
        result = _count_with_tiktoken(text, model)
        if result is None:
            logger.warning(
                "tiktoken 不可用或模型编码未知，回退到字符启发式估算",
                extra={"event": "token_counter_fallback", "method": method, "model": model},
            )
            return _count_with_heuristic(text)
        return result

    # method == "auto"：优先 tiktoken，失败则静默回退
    result = _count_with_tiktoken(text, model)
    if result is None:
        return _count_with_heuristic(text)
    return result


def count_tokens(
    messages: list[dict[str, Any]], model: str = "", method: str = "auto"
) -> int:
    """对 OpenAI 格式的消息列表估算总 token 数。

    遍历每条消息累加 content 字段的 token 数；对于含 tool_calls 的 assistant
    消息，额外累加每个 tool_call 的 name 与 arguments 的 token 数；每条消息
    额外加 4 个 token 的格式开销（role 标签等），最后额外加 3 个 token
    作为对话尾部的辅助标记。

    参数:
        messages: OpenAI 格式的消息列表
        model: 模型名称，用于 tiktoken 选择编码器
        method: 计数方法，"auto"/"tiktoken"/"heuristic"
    返回:
        消息列表估算的总 token 数
    """
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if content:
            total += count_text_tokens(content, model, method)

        # assistant 消息的 tool_calls：累加每个调用的 name 与 arguments
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                func = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = func.get("name", "") or ""
                arguments = func.get("arguments", "") or ""
                if name:
                    total += count_text_tokens(name, model, method)
                if arguments:
                    total += count_text_tokens(arguments, model, method)

        # 每条消息 4 个 token 格式开销（role 标签等）
        total += 4

    # 对话尾部辅助标记
    total += 3
    return total
