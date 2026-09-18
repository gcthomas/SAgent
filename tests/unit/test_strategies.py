"""分层压缩策略单元测试。

覆盖四层压缩策略的核心逻辑：
1. ToolOutputTruncation：工具输出截断
2. ToolMessageOffload：工具消息卸载
3. SlidingWindowPruning：滑动窗口裁剪
4. LLMSummaryCompression：LLM 摘要压缩
"""

from __future__ import annotations

from conftest import make_tool_call, text_response

from sagent.context.strategies import (
    LLMSummaryCompression,
    SlidingWindowPruning,
    ToolMessageOffload,
    ToolOutputTruncation,
)
from sagent.context.token_counter import count_text_tokens


# ========== 第一层：ToolOutputTruncation ==========


def test_truncation_short_content_unchanged():
    """content 未超过 max_tokens 时，消息不变。"""
    strategy = ToolOutputTruncation(max_tokens=100, method="heuristic")
    msg = {"role": "tool", "tool_call_id": "call_1", "content": "短内容"}
    result = strategy.compress([msg])
    assert len(result) == 1
    assert result[0]["content"] == "短内容"


def test_truncation_long_content_truncated():
    """content 超过 max_tokens 时，被截断为头部+尾部+截断标记，且 token 不超过 max_tokens。"""
    strategy = ToolOutputTruncation(max_tokens=100, method="heuristic")
    # 1000 个中文字符 -> 500 tokens，远超 max_tokens=100
    long_content = "测" * 1000
    msg = {"role": "tool", "tool_call_id": "call_1", "content": long_content}
    result = strategy.compress([msg])
    assert len(result) == 1
    # 截断后的内容包含截断标记
    assert "已截断" in result[0]["content"]
    # 截断后的内容比原文短
    assert len(result[0]["content"]) < len(long_content)
    # 截断后的 token 数不超过 max_tokens
    assert count_text_tokens(result[0]["content"], method="heuristic") <= 100


def test_truncation_non_tool_message_unchanged():
    """非 tool 角色消息不受截断影响。"""
    strategy = ToolOutputTruncation(max_tokens=10, method="heuristic")
    msg = {"role": "user", "content": "测" * 100}
    result = strategy.compress([msg])
    assert len(result) == 1
    assert result[0]["content"] == "测" * 100


# ========== 第二层：ToolMessageOffload ==========


def test_offload_old_tool_messages_replaced():
    """旧的 assistant(tool_calls) 被替换为摘要，对应 tool 结果消息被删除（避免孤儿 tool 消息）。"""
    messages = [
        {"role": "system", "content": "系统提示"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [make_tool_call("read_file", {"path": "/tmp/test.txt"})],
        },
        {"role": "tool", "tool_call_id": "call_read_file", "content": "这是文件内容" * 10},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "好的"},
    ]
    strategy = ToolMessageOffload(keep_recent=2)
    result = strategy.compress(messages)

    # 第一条 system 不变
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "系统提示"

    # 第二条 assistant 的 tool_calls 被移除，content 被替换为摘要
    assert result[1]["role"] == "assistant"
    assert "tool_calls" not in result[1]
    assert "已压缩" in result[1]["content"]

    # 卸载后不保留任何 role="tool" 的消息：父消息的 tool_calls 已被移除，
    # 保留会形成孤儿 tool 消息，导致严格端点返回 400
    assert len(result) == 4
    assert all(m.get("role") != "tool" for m in result)

    # 最后两条不受影响
    assert result[2]["role"] == "user"
    assert result[2]["content"] == "继续"
    assert result[3]["role"] == "assistant"
    assert result[3]["content"] == "好的"


def test_offload_recent_tool_messages_preserved():
    """在 keep_recent 范围内的工具消息不被卸载。"""
    messages = [
        {"role": "system", "content": "系统提示"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [make_tool_call("read_file", {"path": "/a.txt"})],
        },
        {"role": "tool", "tool_call_id": "call_read_file", "content": "文件A内容"},
        {"role": "user", "content": "继续"},
    ]
    # keep_recent=3：保护最后 3 条，assistant(tool_calls) 在保护范围内
    strategy = ToolMessageOffload(keep_recent=3)
    result = strategy.compress(messages)

    # tool_calls 仍在，未被卸载
    assert result[1].get("tool_calls") is not None
    # tool content 未被替换
    assert result[2]["content"] == "文件A内容"


# ========== 第四层：SlidingWindowPruning ==========


def test_pruning_keeps_system_and_recent():
    """保留 system 消息与最后 keep_recent 条消息。"""
    messages = [{"role": "system", "content": "系统"}]
    for i in range(10):
        messages.append({"role": "user", "content": f"消息{i}"})

    strategy = SlidingWindowPruning(keep_recent=3)
    result = strategy.compress(messages)

    # system + 最后 3 条 = 4 条
    assert len(result) == 4
    assert result[0]["role"] == "system"
    assert result[1]["content"] == "消息7"
    assert result[2]["content"] == "消息8"
    assert result[3]["content"] == "消息9"


def test_pruning_short_list_unchanged():
    """消息数不超过 keep_recent+1 时不变。"""
    messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "你好"},
    ]
    strategy = SlidingWindowPruning(keep_recent=3)
    result = strategy.compress(messages)
    assert len(result) == len(messages)
    assert result[0]["content"] == "系统"
    assert result[1]["content"] == "你好"


def test_pruning_keeps_tool_call_pair_intact():
    """裁剪边界落在 tool 结果消息上时，向前扩展以包含父 assistant(tool_calls)。"""
    messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "用户提问"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "文件A内容"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "b"}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_2", "content": "文件B内容"},
        {"role": "assistant", "content": "最终回答"},
        {"role": "user", "content": "继续"},
    ]
    # keep_recent=3 时，初始切点落在 tool(call_2) 上
    # 应向前扩展到 assistant(tool_calls=[call_2])，保证 pair 完整
    strategy = SlidingWindowPruning(keep_recent=3)
    result = strategy.compress(messages)

    # 找到裁剪后第一条 body 消息（跳过 system）
    first_body = result[1]
    # 切点不应是孤儿 tool 消息，而应是其父 assistant
    assert first_body["role"] != "tool", "裁剪边界不应以孤儿 tool 消息开头"
    assert first_body["role"] == "assistant"
    assert first_body.get("tool_calls") is not None
    # 确认对应的 tool 结果也在
    tool_msgs = [m for m in result if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_2"


def test_pruning_keeps_multi_tool_pair_intact():
    """一个 assistant 含多个 tool_calls 时，所有 tool 结果与父消息一起保留。"""
    messages = [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "提问"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{}"}},
            {"id": "call_2", "type": "function", "function": {"name": "read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "结果1"},
        {"role": "tool", "tool_call_id": "call_2", "content": "结果2"},
        {"role": "assistant", "content": "回答"},
    ]
    # keep_recent=2 时切点落在 tool(call_1) 上
    # 应向前扩展到 assistant(tool_calls=[call_1, call_2])
    strategy = SlidingWindowPruning(keep_recent=2)
    result = strategy.compress(messages)

    body = result[1:]  # 去掉 system
    assert body[0]["role"] == "assistant"
    assert body[0].get("tool_calls") is not None
    tool_msgs = [m for m in body if m.get("role") == "tool"]
    assert len(tool_msgs) == 2


# ========== 第三层：LLMSummaryCompression ==========


def test_summary_calls_llm_and_replaces(make_fake_llm):
    """summarize 方法调用 LLM 并返回摘要内容。"""
    llm = make_fake_llm([text_response("这是摘要内容")])
    strategy = LLMSummaryCompression(llm, summary_max_tokens=500)
    old_messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你的？"},
    ]
    summary = strategy.summarize(old_messages)
    assert summary == "这是摘要内容"
    # LLM 被调用一次
    assert len(llm.calls) == 1


def test_summary_compress_replaces_old_messages(make_fake_llm):
    """compress 方法将旧消息替换为摘要消息，近期消息保留。"""
    llm = make_fake_llm([text_response("历史摘要文本")])
    strategy = LLMSummaryCompression(
        llm, summary_max_tokens=500, keep_recent=2
    )
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "第一个问题"},
        {"role": "assistant", "content": "第一个回答"},
        {"role": "user", "content": "第二个问题"},
        {"role": "assistant", "content": "第二个回答"},
    ]
    result = strategy.compress(messages)

    # 第一条是原始 system 消息
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "系统提示"

    # 第二条是摘要 system 消息
    assert result[1]["role"] == "system"
    assert "历史摘要" in result[1]["content"]
    assert "历史摘要文本" in result[1]["content"]

    # 最后 2 条是近期消息，内容保留完整
    assert len(result) == 4  # system + summary + 2 recent
    assert result[2]["role"] == "user"
    assert result[2]["content"] == "第二个问题"
    assert result[3]["role"] == "assistant"
    assert result[3]["content"] == "第二个回答"


def _assert_no_orphan_tool_messages(messages: list[dict]) -> None:
    """孤儿校验器：每条 tool 消息的 tool_call_id 必须能在前序 assistant(tool_calls) 中找到。"""
    pending_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict) and tc.get("id"):
                    pending_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            assert msg.get("tool_call_id") in pending_ids, (
                f"孤儿 tool 消息: tool_call_id={msg.get('tool_call_id')} 无对应父 assistant(tool_calls)"
            )


def test_summary_compress_aligns_tool_pair_boundary(make_fake_llm):
    """切点落在 tool 结果上时，compress 向前对齐到父 assistant(tool_calls)，不产生孤儿 tool 消息。"""
    llm = make_fake_llm([text_response("旧消息摘要")])
    strategy = LLMSummaryCompression(llm, summary_max_tokens=500, keep_recent=2)
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "问题一"},
        {"role": "user", "content": "问题二"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [make_tool_call("read_file", {"path": "/tmp/a.txt"})],
        },
        {"role": "tool", "tool_call_id": "call_read_file", "content": "文件内容"},
        {"role": "user", "content": "继续"},
    ]
    # body 为 [user, user, assistant(tool_calls), tool, user]，keep_recent=2
    # 初始切点落在 tool(call_read_file) 上，应对齐到其父 assistant(tool_calls)
    result = strategy.compress(messages)

    # system + 摘要 + 对齐后的 recent（assistant + tool + user）
    assert len(result) == 5
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "系统提示"
    assert result[1]["role"] == "system"
    assert "旧消息摘要" in result[1]["content"]

    # 摘要后首条 body 消息是含 tool_calls 的 assistant，其 tool 结果紧随
    assert result[2]["role"] == "assistant"
    assert result[2].get("tool_calls") is not None
    assert result[3]["role"] == "tool"
    assert result[3]["tool_call_id"] == "call_read_file"

    _assert_no_orphan_tool_messages(result)


def test_summary_renders_tool_calls_and_tool_result_budget(make_fake_llm):
    """summarize 渲染 assistant 的 tool_calls（工具名/参数），tool 结果使用更大预览预算。"""
    llm = make_fake_llm([text_response("摘要")])
    strategy = LLMSummaryCompression(llm, summary_max_tokens=500)
    tool_result = "数" * 300  # 超过文本预算 200，但未超过 tool 结果预算 500
    old_messages = [
        {"role": "user", "content": "读取文件"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [make_tool_call("read_file", {"path": "/tmp/a.txt"})],
        },
        {"role": "tool", "tool_call_id": "call_read_file", "content": tool_result},
    ]
    strategy.summarize(old_messages)

    user_content = llm.calls[0]["messages"][1]["content"]
    # 工具名与参数（含文件路径）被渲染进摘要输入
    assert "read_file" in user_content
    assert "/tmp/a.txt" in user_content
    # tool 结果 300 字符未超过 500 字符预算，应完整保留
    assert tool_result in user_content


def test_summary_system_prompt_structured(make_fake_llm):
    """摘要 system 提示词包含结构化分段标记与逐字保留要求。"""
    llm = make_fake_llm([text_response("摘要")])
    strategy = LLMSummaryCompression(llm, summary_max_tokens=500)
    strategy.summarize([{"role": "user", "content": "你好"}])

    system_content = llm.calls[0]["messages"][0]["content"]
    assert "[用户目标]" in system_content
    assert "[已完成动作与结果]" in system_content
    assert "逐字保留" in system_content


def test_summary_truncated_to_token_limit(make_fake_llm):
    """LLM 返回超长摘要时，被硬校验截断至 summary_max_tokens 以内。"""
    long_summary = "摘" * 2000
    llm = make_fake_llm([text_response(long_summary)])
    strategy = LLMSummaryCompression(llm, summary_max_tokens=50)
    result = strategy.summarize([{"role": "user", "content": "你好"}])

    # 包含截断标记，且比原文短
    assert "已截断" in result
    assert len(result) < len(long_summary)
    # 与实现使用相同的 token 计数方式，截断后不超过上限
    assert count_text_tokens(result, "", "auto") <= 50
