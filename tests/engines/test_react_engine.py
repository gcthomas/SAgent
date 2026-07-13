"""ReAct 引擎测试（使用 FakeLLMClient 离线回放）。"""

from __future__ import annotations

import logging

from conftest import make_tool_call, text_response, tool_response

from sagent.config.models import AgentConfig
from sagent.core.react_engine import ReActEngine
from sagent.tools import build_default_registry


def test_react_direct_answer(make_fake_llm, agent_config):
    """首轮无工具调用时直接返回最终答案。"""
    llm = make_fake_llm([text_response("最终答案")])
    engine = ReActEngine(llm, build_default_registry(), agent_config)
    result = engine.run("你好")
    assert result == "最终答案"
    assert len(llm.calls) == 1


def test_react_tool_then_answer(make_fake_llm, agent_config, tmp_path, caplog):
    """第一轮请求工具，第二轮基于观察给出答案。"""
    target = tmp_path / "data.txt"
    target.write_text("文件内容", encoding="utf-8")

    llm = make_fake_llm(
        [
            tool_response([make_tool_call("read_file", {"path": str(target)})]),
            text_response("文件内容已读取完毕"),
        ]
    )
    events: list[str] = []
    engine = ReActEngine(
        llm, build_default_registry(), agent_config, on_event=events.append
    )
    # 激活 sagent logger 的 INFO 级别，确保日志记录路径被实际执行，
    # 可捕获 extra 键与 LogRecord 保留属性冲突等错误
    caplog.set_level(logging.INFO, logger="sagent")
    result = engine.run("读取文件")

    assert result == "文件内容已读取完毕"
    assert llm.calls  # 至少调用过
    # 第二轮消息历史应包含 tool 角色的观察结果
    second_call_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in second_call_messages)
    assert any(m.get("content") == "文件内容" for m in second_call_messages)
    # 过程事件包含行动与观察
    assert any("[行动]" in e for e in events)
    assert any("[观察]" in e for e in events)
    # 验证工具执行日志被正确记录，extra 字段无保留属性冲突
    tool_call_records = [
        r for r in caplog.records if getattr(r, "event", None) == "tool_call"
    ]
    assert len(tool_call_records) >= 1
    assert getattr(tool_call_records[0], "tool", None) == "read_file"
    assert hasattr(tool_call_records[0], "tool_args")


def test_react_hits_max_iterations(make_fake_llm):
    """持续请求工具直到达到迭代上限，触发收尾请求。"""
    config = AgentConfig(mode="react", max_iterations=2)
    # 2 轮工具调用 + 1 次收尾无工具请求 = 3 个响应
    responses = [
        tool_response([make_tool_call("read_file", {"path": "x"})]),
        tool_response([make_tool_call("read_file", {"path": "y"})]),
        text_response("收尾结论"),
    ]
    llm = make_fake_llm(responses)
    engine = ReActEngine(llm, build_default_registry(), config)
    result = engine.run("永不停止的任务")

    assert result == "收尾结论"
    assert len(llm.calls) == 3
    # 最后一次收尾请求不带工具
    assert llm.calls[-1]["tools"] is None


def test_react_multiple_tool_calls_in_one_round(make_fake_llm, agent_config, tmp_path):
    """单轮返回多个工具调用，均被执行并追加观察。"""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("AAA", encoding="utf-8")
    f2.write_text("BBB", encoding="utf-8")

    llm = make_fake_llm(
        [
            tool_response(
                [
                    make_tool_call("read_file", {"path": str(f1)}, call_id="c1"),
                    make_tool_call("read_file", {"path": str(f2)}, call_id="c2"),
                ]
            ),
            text_response("两文件均已读取"),
        ]
    )
    engine = ReActEngine(llm, build_default_registry(), agent_config)
    result = engine.run("读取两个文件")

    assert result == "两文件均已读取"
    tool_msgs = [m for m in llm.calls[1]["messages"] if m.get("role") == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2"}
