"""ContextManager 单元测试。

覆盖消息添加、获取、token 计数、压缩触发、系统提示保护与工具消息截断等核心逻辑。
"""

from __future__ import annotations

from conftest import make_tool_call, text_response
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


# ========== seq 维护 ==========


def test_seq_incremental():
    """连续添加三条消息，seq 应单调递增为 1、2、3。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "system", "content": "你是助手"})
    cm.add_message({"role": "user", "content": "第一条"})
    cm.add_message({"role": "user", "content": "第二条"})
    assert cm._messages[0]["seq"] == 1
    assert cm._messages[1]["seq"] == 2
    assert cm._messages[2]["seq"] == 3


def test_get_messages_strips_internal_seq():
    """get_messages 返回的消息不含内部字段 seq（可直接发送给 LLM），内部列表仍保留 seq。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "你好"})
    cm.add_message({"role": "user", "content": "世界"})
    msgs = cm.get_messages()
    assert len(msgs) == 2
    # 对外返回的消息不含 seq（seq 不属于 OpenAI 消息格式，泄漏会被严格端点拒绝）
    assert all("seq" not in m for m in msgs)
    assert msgs[0]["content"] == "你好"
    assert msgs[1]["content"] == "世界"
    # 内部列表仍保留 seq，供会话持久化（export_new_messages / load_messages）使用
    assert cm._messages[0]["seq"] == 1
    assert cm._messages[1]["seq"] == 2


# ========== export_new_messages 增量导出 ==========


def test_export_new_messages_returns_all_after_zero():
    """add 三条后，export_new_messages(0) 应返回全部 3 条。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.add_message({"role": "user", "content": "二"})
    cm.add_message({"role": "user", "content": "三"})
    exported = cm.export_new_messages(0)
    assert len(exported) == 3
    assert [m["seq"] for m in exported] == [1, 2, 3]


def test_export_new_messages_returns_only_after_seq():
    """add 三条后，export_new_messages(2) 仅返回 seq=3 的一条。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.add_message({"role": "user", "content": "二"})
    cm.add_message({"role": "user", "content": "三"})
    exported = cm.export_new_messages(2)
    assert len(exported) == 1
    assert exported[0]["seq"] == 3


def test_export_new_messages_returns_empty_when_none():
    """无新增消息时，export_new_messages(5) 应返回空列表。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.add_message({"role": "user", "content": "二"})
    exported = cm.export_new_messages(5)
    assert exported == []


def test_export_new_messages_returns_copies():
    """导出的消息应为副本，修改不影响原列表。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "原始内容"})
    exported = cm.export_new_messages(0)
    assert len(exported) == 1
    exported[0]["content"] = "被修改的内容"
    exported[0]["seq"] = 999
    # 原列表不受影响（直接读内部列表，其消息含 seq）
    original = cm._messages
    assert original[0]["content"] == "原始内容"
    assert original[0]["seq"] == 1


# ========== load_messages 替换消息并重建 seq ==========


def test_load_messages_replaces_messages():
    """load 后 get_messages 返回新列表，原消息被替换。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "旧消息"})
    cm.load_messages(
        [
            {"role": "system", "content": "新系统提示"},
            {"role": "user", "content": "新用户消息"},
        ]
    )
    msgs = cm.get_messages()
    assert len(msgs) == 2
    assert msgs[0]["content"] == "新系统提示"
    assert msgs[1]["content"] == "新用户消息"


def test_load_messages_rebuilds_seq_counter():
    """load 含 seq=5 的消息后，再 add_message 时新消息 seq 应为 6。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.load_messages([{"role": "user", "content": "已存在", "seq": 5}])
    assert cm._seq_counter == 5
    cm.add_message({"role": "user", "content": "新增"})
    assert cm._messages[-1]["seq"] == 6


def test_load_messages_resets_calibration():
    """load 后混合校准基准被重置：_calibrated_tokens 为 None、_calibrated_count 为 0。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "你好"})
    cm.record_llm_usage({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    assert cm._calibrated_tokens == 100
    cm.load_messages([{"role": "user", "content": "装载"}])
    assert cm._calibrated_tokens is None
    assert cm._calibrated_count == 0


def test_load_messages_empty_list_resets_seq():
    """load 空列表后 _seq_counter 应为 0。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.add_message({"role": "user", "content": "二"})
    assert cm._seq_counter == 2
    cm.load_messages([])
    assert cm._seq_counter == 0
    assert cm.get_messages() == []


# ========== reset 清空 ==========


def test_reset_clears_messages():
    """reset 后 get_messages 返回空列表。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.add_message({"role": "user", "content": "二"})
    cm.reset()
    assert cm.get_messages() == []


def test_reset_clears_seq_counter():
    """reset 后 _seq_counter 应为 0。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.add_message({"role": "user", "content": "二"})
    cm.reset()
    assert cm._seq_counter == 0


def test_reset_clears_calibration():
    """reset 后 _calibrated_tokens 应为 None。"""
    config = ContextConfig(max_context_tokens=128000, token_counter_method="heuristic")
    cm = ContextManager(config)
    cm.add_message({"role": "user", "content": "一"})
    cm.record_llm_usage({"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55})
    assert cm._calibrated_tokens == 50
    cm.reset()
    assert cm._calibrated_tokens is None
    assert cm._calibrated_count == 0


def test_reset_clears_existing_summary(make_fake_llm):
    """reset 后 _existing_summary 应为 None。"""
    config = ContextConfig(
        max_context_tokens=100,
        compression_threshold=0.5,
        safe_threshold=0.3,
        keep_recent_messages=2,
        enable_summary=True,
        summary_max_tokens=100,
        token_counter_method="heuristic",
    )
    llm = make_fake_llm([text_response("这是摘要内容")])
    cm = ContextManager(config, llm=llm, model="")
    cm.add_message({"role": "system", "content": "你是助手"})
    for i in range(10):
        cm.add_message(
            {"role": "user", "content": f"这是第{i}条较长的测试消息内容用于触发压缩"}
        )
    cm.get_messages()  # 触发摘要压缩，_existing_summary 被设置
    assert cm._existing_summary is not None
    cm.reset()
    assert cm._existing_summary is None


# ========== 第三层摘要压缩暴露「摘要文本 + 覆盖 seq 区间」 ==========


def test_compaction_callback_invoked_with_summary_and_seq_range(make_fake_llm):
    """第三层 LLM 摘要压缩触发时，回调被调用且参数含摘要文本与覆盖 seq 区间。

    构造小 max_context_tokens、enable_summary=True，添加足够多消息触发压缩，
    验证回调签名 callback(summary, covered_from_seq, covered_to_seq) 被正确调用，
    且 covered_from_seq/covered_to_seq 为正整数、from_seq <= to_seq。
    """
    config = ContextConfig(
        max_context_tokens=200,
        compression_threshold=0.5,
        safe_threshold=0.3,
        keep_recent_messages=2,
        enable_summary=True,
        summary_max_tokens=100,
        token_counter_method="heuristic",
    )
    llm = make_fake_llm([text_response("这是摘要内容")])
    cm = ContextManager(config, llm=llm, model="")

    captured = []
    cm.set_compaction_callback(lambda summary, frm, to: captured.append((summary, frm, to)))

    cm.add_message({"role": "system", "content": "你是助手"})
    for i in range(10):
        cm.add_message(
            {"role": "user", "content": f"这是第{i}条较长的测试消息内容用于触发压缩"}
        )

    cm.get_messages()  # 触发压缩

    assert len(captured) == 1
    summary, frm, to = captured[0]
    assert isinstance(summary, str) and len(summary) > 0
    assert "摘要" in summary
    assert isinstance(frm, int) and frm >= 1
    assert isinstance(to, int) and to >= 1
    assert frm <= to


# ========== 分层压缩后无孤儿 tool 消息 ==========


def test_compression_produces_no_orphan_tool_messages(make_fake_llm):
    """分层压缩后最终消息不含孤儿 tool 消息。

    构造两组 assistant(tool_calls) + tool 消息对并触发压缩：
    第二层卸载会移除过期对的 tool_calls 并删除对应 tool 结果，
    第三层摘要替换旧消息，任一环节处理不当都会残留孤儿 tool 消息
    （父 assistant 的 tool_calls 已被移除，导致严格端点返回 400）。
    """
    config = ContextConfig(
        max_context_tokens=300,
        compression_threshold=0.7,
        safe_threshold=0.5,
        keep_recent_messages=2,
        enable_summary=True,
        summary_max_tokens=100,
        token_counter_method="heuristic",
    )
    llm = make_fake_llm([text_response("这是摘要内容")])
    cm = ContextManager(config, llm=llm, model="")
    cm.add_message({"role": "system", "content": "你是助手"})
    cm.add_message({"role": "user", "content": "甲" * 80})
    cm.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [make_tool_call("read_file", {"path": "/tmp/a.txt"})],
        }
    )
    cm.add_message({"role": "tool", "tool_call_id": "call_read_file", "content": "乙" * 80})
    cm.add_message({"role": "user", "content": "丙" * 80})
    cm.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [make_tool_call("search", {"query": "关键词"})],
        }
    )
    cm.add_message({"role": "tool", "tool_call_id": "call_search", "content": "丁" * 80})
    cm.add_message({"role": "user", "content": "戊" * 80})

    # 超过触发阈值 0.7 * 300 = 210
    assert cm.token_count > 210
    msgs = cm.get_messages()  # 触发分层压缩

    # 消息数减少
    assert len(msgs) < 8
    # 第三层摘要压缩已执行，历史摘要已生成
    assert cm._existing_summary is not None
    # 孤儿校验：每条 tool 消息的 tool_call_id 必须能在前序 assistant(tool_calls) 中找到
    pending_ids: set[str] = set()
    for msg in msgs:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict) and tc.get("id"):
                    pending_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            assert msg.get("tool_call_id") in pending_ids, (
                f"孤儿 tool 消息: tool_call_id={msg.get('tool_call_id')} 无对应父 assistant(tool_calls)"
            )
