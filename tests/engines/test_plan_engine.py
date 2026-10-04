"""Plan 引擎测试（使用 FakeLLMClient 离线回放）。"""

from __future__ import annotations

from conftest import text_response, tool_response

from sagent.core.events import AgentEvent
from sagent.core.plan_engine import PlanEngine
from sagent.tools import build_default_registry


def test_plan_decompose_execute_summarize(make_fake_llm, agent_config):
    """完整流程：拆解 2 步 -> 每步 ReAct 直接答 -> 汇总。"""
    responses = [
        # 1) 拆解
        text_response('{"steps": ["步骤一", "步骤二"]}'),
        # 2) 步骤一（ReAct 直接答）
        text_response("步骤一完成"),
        # 3) 步骤二（ReAct 直接答）
        text_response("步骤二完成"),
        # 4) 汇总
        text_response("整体汇总结果"),
    ]
    llm = make_fake_llm(responses)
    events: list[str] = []
    engine = PlanEngine(
        llm, build_default_registry(), agent_config, on_event=events.append
    )
    result = engine.run("一个复杂任务")

    assert result == "整体汇总结果"
    assert llm.remaining == 0
    assert all(isinstance(event, AgentEvent) for event in events)
    assert any(event.event_type == "plan_started" for event in events)
    step_events = [event for event in events if event.event_type == "plan_step_started"]
    assert [(event.plan_step_index, event.plan_step_total) for event in step_events] == [(1, 2), (2, 2)]
    completed_events = [event for event in events if event.event_type == "react_completed"]
    assert [(event.plan_step_index, event.plan_step) for event in completed_events] == [(1, "步骤一"), (2, "步骤二")]


def test_plan_streaming_decompose_steps_and_summary(make_fake_llm, agent_config):
    """Plan 拆解不渲染内部 JSON，步骤和汇总复用流式路径。"""
    llm = make_fake_llm(
        [
            text_response('{"steps": ["步骤一"]}'),
            text_response("步骤完成"),
            text_response("最终汇总"),
        ],
        stream=True,
    )
    events: list[AgentEvent] = []
    engine = PlanEngine(llm, build_default_registry(), agent_config, on_event=events.append)

    assert engine.run("复杂任务") == "最终汇总"
    assert len(llm.calls) == 3
    assert [event.content for event in events if event.event_type == "llm_text_delta"] == list("步骤完成最终汇总")
    assert not any(event.content and '{"steps"' in event.content for event in events)


def test_plan_fallback_when_decompose_fails(make_fake_llm, agent_config):
    """拆解结果无法解析时，回退为直接 ReAct 执行整个任务。"""
    responses = [
        # 1) 拆解失败（非 JSON）
        text_response("我无法拆解"),
        # 2) 回退的 ReAct 直接答
        text_response("直接执行的结果"),
    ]
    llm = make_fake_llm(responses)
    events: list[str] = []
    engine = PlanEngine(
        llm, build_default_registry(), agent_config, on_event=events.append
    )
    result = engine.run("任务")

    assert result == "直接执行的结果"
    fallback_events = [event for event in events if event.event_type == "notice"]
    assert any(
        event.scope == "plan_fallback" and "直接执行整个任务" in (event.content or "")
        for event in fallback_events
    )


def test_plan_step_uses_tool(make_fake_llm, agent_config, tmp_path):
    """单步骤内触发工具调用，验证 Plan 复用 ReAct 的工具执行能力。"""
    target = tmp_path / "f.txt"
    target.write_text("XYZ", encoding="utf-8")

    responses = [
        # 拆解为 1 步
        text_response('{"steps": ["读取文件"]}'),
        # 步骤内：请求工具
        tool_response(
            [
                {
                    "id": "c1",
                    "name": "read_file",
                    "arguments": f'{{"path": "{target.as_posix()}"}}',
                }
            ]
        ),
        # 步骤内：基于观察给出答案
        text_response("已读取 XYZ"),
        # 汇总
        text_response("汇总：读取成功"),
    ]
    llm = make_fake_llm(responses)
    engine = PlanEngine(llm, build_default_registry(), agent_config)
    result = engine.run("读取并总结")

    assert result == "汇总：读取成功"
    assert llm.remaining == 0
