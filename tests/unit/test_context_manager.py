"""ContextManager 单元测试。

覆盖消息添加、获取、token 计数、压缩触发、系统提示保护与工具消息截断等核心逻辑。
"""

from __future__ import annotations

from sagent.config.models import ContextConfig
from sagent.context.context_manager import ContextManager


def test_add_and_get_messages():
    """添加 system + user 消息，get_messages 返回全部，不压缩。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    cm.add_message({"role": "user", "content": "你好"})
    msgs = cm.get_messages()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "你是助手"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "你好"


def test_token_count_property():
    """添加消息后 token_count 返回正整数。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    cm.add_message({"role": "user", "content": "你好世界"})
    assert cm.token_count > 0


def test_compression_triggered():
    """小 max_context_tokens 下添加大量消息，get_messages 触发压缩使消息数减少。"""
    config = ContextConfig(
        max_context_tokens=100,
        compression_threshold=0.7,
        safe_threshold=0.5,
        keep_recent_messages=3,
        enable_summary=False,
        token_counter_method="heuristic",
    )
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    for _ in range(10):
        cm.add_message(
            {"role": "user", "content": "这是一段用于测试压缩功能的较长文本内容"}
        )
    # 添加了 11 条消息（1 system + 10 user），token 超过阈值 70
    assert cm.token_count > 70
    # get_messages 触发压缩
    msgs = cm.get_messages()
    # 压缩后消息数减少（system + 3 recent = 4）
    assert len(msgs) < 11
    assert len(msgs) == 4


def test_system_prompt_protected():
    """压缩后第一条消息仍是 system 角色。"""
    config = ContextConfig(
        max_context_tokens=100,
        compression_threshold=0.7,
        safe_threshold=0.5,
        keep_recent_messages=3,
        enable_summary=False,
        token_counter_method="heuristic",
    )
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    for _ in range(10):
        cm.add_message(
            {"role": "user", "content": "这是一段用于测试压缩功能的较长文本内容"}
        )
    msgs = cm.get_messages()
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "你是助手"


def test_ensure_system_prompt():
    """空 context_manager 调用 ensure_system_prompt 后第一条是 system；已有 system 时不重复添加。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")

    # 空 context_manager
    cm1 = ContextManager(config)
    cm1.ensure_system_prompt("系统提示")
    msgs = cm1.get_messages()
    assert len(msgs) == 1
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "系统提示"

    # 已有 system 消息时不重复添加
    cm2 = ContextManager(config)
    cm2.add_message({"role": "system", "content": "已有提示"})
    cm2.ensure_system_prompt("新提示")
    msgs = cm2.get_messages()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "已有提示"


def test_tool_message_truncated_on_add():
    """添加超长 content 的 tool 消息后，content 被截断。"""
    config = ContextConfig(
        max_context_tokens=128000,
        max_tool_output_tokens=50,
        token_counter_method="heuristic",
    )
    cm = ContextManager(config)
    # 200 个中文字符 -> 100 tokens，超过 max_tool_output_tokens=50
    long_content = "测" * 200
    cm.add_message(
        {"role": "tool", "tool_call_id": "call_1", "content": long_content}
    )
    msgs = cm.get_messages()
    assert len(msgs) == 1
    # content 被截断，包含截断标记
    assert "已截断" in msgs[0]["content"]
    # 截断后比原文短
    assert len(msgs[0]["content"]) < len(long_content)


# ========== 混合校准 ==========


def test_calibration_uses_exact_prompt_tokens():
    """record_llm_usage 后，token_count 返回精确基准 + delta 估算。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    cm.add_message({"role": "user", "content": "你好世界"})

    # 模拟 LLM API 返回的精确 prompt_tokens
    cm.record_llm_usage({"prompt_tokens": 42, "completion_tokens": 10, "total_tokens": 52})
    # 未添加新消息时，token_count 应为精确值
    assert cm.token_count == 42

    # 添加新消息后，token_count = 精确基准 + delta
    cm.add_message({"role": "assistant", "content": "你好"})
    count = cm.token_count
    assert count > 42  # delta > 0


def test_calibration_disabled_falls_back_to_estimation():
    """use_api_calibration=False 时，record_llm_usage 不生效，回退全量估算。"""
    config = ContextConfig(
        max_context_tokens=128000,
        token_counter_method="heuristic",
        use_api_calibration=False,
    )
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    cm.add_message({"role": "user", "content": "你好世界"})

    # 即使调用 record_llm_usage，也不应生效
    cm.record_llm_usage({"prompt_tokens": 999, "completion_tokens": 0, "total_tokens": 999})
    # token_count 应为全量估算值，而非 999
    assert cm.token_count != 999
    assert cm.token_count > 0


def test_calibration_none_usage_ignored():
    """LLM 不返回 usage 时（None），不影响 token_count。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})

    count_before = cm.token_count
    cm.record_llm_usage(None)
    assert cm.token_count == count_before


def test_calibration_reset_after_compression():
    """压缩后混合校准基准被重置，token_count 回退全量估算。"""
    config = ContextConfig(
        max_context_tokens=100,
        compression_threshold=0.7,
        safe_threshold=0.5,
        keep_recent_messages=3,
        enable_summary=False,
        token_counter_method="heuristic",
    )
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    for _ in range(10):
        cm.add_message({"role": "user", "content": "这是一段用于测试压缩功能的较长文本内容"})

    # 记录精确基准
    cm.record_llm_usage({"prompt_tokens": 500, "completion_tokens": 0, "total_tokens": 500})
    assert cm._calibrated_tokens == 500

    # 触发压缩，校准应被重置
    cm.get_messages()
    assert cm._calibrated_tokens is None
