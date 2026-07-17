"""ContextManager 与引擎集成的测试。

覆盖以下场景：
1. 向后兼容：不注入 context_manager 时 ReAct / Plan 引擎行为不变
2. ReActEngine + ContextManager 集成：直接回答、工具调用、多轮对话上下文持久化
3. 压缩触发：token 超阈值时自动压缩，保留 system 消息与近期消息完整
"""

from __future__ import annotations

from conftest import make_tool_call, text_response, tool_response

from sagent.config.models import ContextConfig
from sagent.context.context_manager import ContextManager
from sagent.core.plan_engine import PlanEngine
from sagent.core.react_engine import ReActEngine
from sagent.tools import build_default_registry


# ========== 1. 向后兼容测试 ==========


def test_react_without_context_manager_unchanged(make_fake_llm, agent_config):
    """不注入 context_manager 时，ReActEngine 行为与原来一致。

    用 FakeLLMClient 设置单次文本响应，验证只调用 1 次 chat 且结果正确。
    """
    llm = make_fake_llm([text_response("答案")])
    engine = ReActEngine(llm, build_default_registry(), agent_config)
    result = engine.run("你好")

    assert result == "答案"
    # 仅调用一次 chat
    assert len(llm.calls) == 1


def test_plan_without_context_manager_unchanged(make_fake_llm, agent_config):
    """不注入 context_manager 时，PlanEngine 行为与原来一致。

    预设分解响应 + 步骤执行响应 + 汇总响应，验证完整流程正常。
    """
    responses = [
        # 拆解为 1 步
        text_response('{"steps": ["唯一步骤"]}'),
        # 步骤执行（ReAct 直接答）
        text_response("步骤完成结果"),
        # 汇总
        text_response("整体汇总结果"),
    ]
    llm = make_fake_llm(responses)
    engine = PlanEngine(llm, build_default_registry(), agent_config)
    result = engine.run("一个任务")

    assert result == "整体汇总结果"
    assert llm.remaining == 0


# ========== 2. ReActEngine + ContextManager 集成 ==========


def test_react_with_context_manager_direct_answer(make_fake_llm, agent_config):
    """注入 ContextManager，首轮直接返回答案。

    验证：
    - 结果正确
    - 第一次 LLM 调用的 messages 包含 system + user 消息
    - context_manager 中有 system + user + assistant 消息
    """
    ctx_config = ContextConfig()
    cm = ContextManager(ctx_config)
    llm = make_fake_llm([text_response("最终答案")])
    engine = ReActEngine(
        llm, build_default_registry(), agent_config, context_manager=cm
    )
    result = engine.run("你好")

    assert result == "最终答案"

    # 第一次 LLM 调用的 messages 包含 system + user 消息
    first_messages = llm.calls[0]["messages"]
    assert any(m.get("role") == "system" for m in first_messages)
    assert any(
        m.get("role") == "user" and m.get("content") == "你好"
        for m in first_messages
    )

    # context_manager 中有 system + user + assistant 消息
    cm_messages = cm.get_messages()
    roles = [m.get("role") for m in cm_messages]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles


def test_react_with_context_manager_tool_call(make_fake_llm, agent_config, tmp_path):
    """注入 ContextManager，首轮工具调用，第二轮返回答案。

    用 tmp_path 创建文件，调用 read_file 工具。验证：
    - 工具被调用（tool 消息内容为文件内容）
    - context_manager 中有 system + user + assistant(tool_calls) + tool + assistant 消息
    - 第二轮 LLM 调用的 messages 包含 tool 消息
    """
    target = tmp_path / "data.txt"
    target.write_text("文件内容", encoding="utf-8")

    ctx_config = ContextConfig()
    cm = ContextManager(ctx_config)
    llm = make_fake_llm(
        [
            tool_response([make_tool_call("read_file", {"path": str(target)})]),
            text_response("文件内容已读取完毕"),
        ]
    )
    engine = ReActEngine(
        llm, build_default_registry(), agent_config, context_manager=cm
    )
    result = engine.run("读取文件")

    assert result == "文件内容已读取完毕"

    # context_manager 中有 system + user + assistant(tool_calls) + tool + assistant
    cm_messages = cm.get_messages()
    roles = [m.get("role") for m in cm_messages]
    assert roles[0] == "system"
    assert "user" in roles
    # 存在含 tool_calls 的 assistant 消息
    assistant_msgs = [m for m in cm_messages if m.get("role") == "assistant"]
    assert any(m.get("tool_calls") for m in assistant_msgs)
    # 存在 tool 消息，且内容为文件内容（工具被调用）
    assert any(
        m.get("role") == "tool" and m.get("content") == "文件内容"
        for m in cm_messages
    )
    # 最后一条是最终答案的 assistant 消息
    assert roles[-1] == "assistant"

    # 第二轮 LLM 调用的 messages 包含 tool 消息
    second_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in second_messages)


def test_react_context_manager_multi_turn(make_fake_llm, agent_config):
    """连续两次 run()，验证第二轮的 messages 包含第一轮的历史。

    设置两轮响应，第二次 run 时 FakeLLMClient 收到的 messages 应包含
    第一次的 user 和 assistant 消息。
    """
    ctx_config = ContextConfig()
    cm = ContextManager(ctx_config)
    llm = make_fake_llm(
        [
            text_response("第一轮答案"),
            text_response("第二轮答案"),
        ]
    )
    engine = ReActEngine(
        llm, build_default_registry(), agent_config, context_manager=cm
    )

    result1 = engine.run("第一个问题")
    assert result1 == "第一轮答案"

    result2 = engine.run("第二个问题")
    assert result2 == "第二轮答案"

    # 第二轮 LLM 调用的 messages 应包含第一轮的历史
    second_messages = llm.calls[1]["messages"]
    # 包含第一轮的 user 消息
    assert any(
        m.get("role") == "user" and m.get("content") == "第一个问题"
        for m in second_messages
    )
    # 包含第一轮的 assistant 消息
    assert any(
        m.get("role") == "assistant" and m.get("content") == "第一轮答案"
        for m in second_messages
    )
    # 也包含第二轮的 user 消息
    assert any(
        m.get("role") == "user" and m.get("content") == "第二个问题"
        for m in second_messages
    )


# ========== 3. 压缩触发测试 ==========


def test_compression_triggered_during_conversation():
    """添加大量消息超过 token 阈值时，get_messages 触发压缩返回更少消息。

    用很小的 max_context_tokens（200），enable_summary=False，
    添加大量消息触发压缩，验证 get_messages 返回的消息数比添加的总数少。
    """
    ctx_config = ContextConfig(
        max_context_tokens=200,
        compression_threshold=0.7,
        safe_threshold=0.5,
        keep_recent_messages=10,
        enable_summary=False,
    )
    cm = ContextManager(ctx_config)
    cm.ensure_system_prompt("系统提示词")
    # 添加大量消息，使 token 超过压缩阈值
    for i in range(25):
        cm.add_message(
            {"role": "user", "content": f"这是第{i}条测试消息，用于验证压缩功能是否正常工作"}
        )

    total_added = 26  # 1 system + 25 user
    messages = cm.get_messages()
    # 压缩后消息数应少于添加的总数
    assert len(messages) < total_added
    # system 消息仍在首位
    assert messages[0].get("role") == "system"


def test_context_manager_multi_turn_with_compression():
    """多轮对话中 token 超限时自动压缩，但最近消息保留完整。

    构造多轮对话使 token 超限，验证压缩后 system 消息仍在首位，
    且最近的消息内容保持完整。
    """
    ctx_config = ContextConfig(
        max_context_tokens=200,
        compression_threshold=0.7,
        safe_threshold=0.5,
        keep_recent_messages=10,
        enable_summary=False,
    )
    cm = ContextManager(ctx_config)
    cm.ensure_system_prompt("你是助手")
    # 模拟多轮对话，添加足够多的消息使 token 超限
    for i in range(15):
        cm.add_message(
            {"role": "user", "content": f"用户问题{i}：这是一段较长的问题内容用于消耗token预算"}
        )
        cm.add_message(
            {"role": "assistant", "content": f"助手回答{i}：这是一段较长的回答内容用于消耗token预算"}
        )

    total_added = 31  # 1 system + 30 user/assistant
    messages = cm.get_messages()
    # 压缩后消息数少于添加的总数
    assert len(messages) < total_added
    # system 消息仍在首位
    assert messages[0].get("role") == "system"
    # 最近的消息保留完整
    last_msg = messages[-1]
    assert last_msg.get("role") == "assistant"
    assert "助手回答14" in last_msg.get("content", "")
