"""token_counter 模块单元测试。

覆盖 count_text_tokens 与 count_tokens 的核心逻辑：
- auto / heuristic / tiktoken 三种计数方法
- 空文本与空列表的边界情况
- 消息列表中 tool_calls 的 token 计入
"""

from __future__ import annotations

from sagent.context.token_counter import count_text_tokens, count_tokens


# ========== count_text_tokens ==========


def test_count_text_tokens_auto():
    """auto 模式对中英文混合文本计数，返回正整数。"""
    tokens = count_text_tokens("hello 你好世界", method="auto")
    assert isinstance(tokens, int)
    assert tokens > 0


def test_count_text_tokens_heuristic():
    """heuristic 模式：中文约 2 字符/token，英文约 4 字符/token，比例约 2:1。"""
    # 40 个中文字符 -> 20 tokens
    zh_text = "你" * 40
    # 40 个 ASCII 字符 -> 10 tokens
    en_text = "a" * 40
    zh_tokens = count_text_tokens(zh_text, method="heuristic")
    en_tokens = count_text_tokens(en_text, method="heuristic")
    assert zh_tokens == 20
    assert en_tokens == 10
    # 中文每字符 token 更多，比例约 2:1
    assert zh_tokens == 2 * en_tokens


def test_count_text_tokens_tiktoken_fallback():
    """tiktoken 模式下若 tiktoken 不可用，回退到 heuristic 并返回正整数。"""
    # 无论 tiktoken 是否安装，都不应抛异常，且返回正整数
    tokens = count_text_tokens("这是一段测试文本", method="tiktoken")
    assert isinstance(tokens, int)
    assert tokens > 0


def test_count_text_tokens_empty():
    """空文本返回 0。"""
    assert count_text_tokens("", method="auto") == 0
    assert count_text_tokens("", method="heuristic") == 0


# ========== count_tokens ==========


def test_count_tokens_basic():
    """对 system + user 两条消息列表计数，返回正整数且大于单条消息的 token 数。"""
    messages = [
        {"role": "system", "content": "你是一个助手"},
        {"role": "user", "content": "请帮我完成任务"},
    ]
    total = count_tokens(messages, method="heuristic")
    single = count_tokens([messages[0]], method="heuristic")
    assert total > 0
    assert total > single


def test_count_tokens_with_tool_calls():
    """含 tool_calls 的 assistant 消息，tool_calls 的 name 与 arguments 的 token 被计入。"""
    # 构造不含 tool_calls 的基础消息
    msg_base = {"role": "assistant", "content": "我来帮你"}
    # 构造含 tool_calls 的消息（使用 OpenAI 嵌套 function 格式）
    msg_with_calls = {
        "role": "assistant",
        "content": "我来帮你",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "/tmp/test.txt"}',
                },
            }
        ],
    }
    tokens_base = count_tokens([msg_base], method="heuristic")
    tokens_with = count_tokens([msg_with_calls], method="heuristic")
    # 含 tool_calls 的 token 数应严格大于不含的
    assert tokens_with > tokens_base


def test_count_tokens_empty_list():
    """空消息列表返回 3（对话尾部辅助标记）。"""
    assert count_tokens([], method="heuristic") == 3
