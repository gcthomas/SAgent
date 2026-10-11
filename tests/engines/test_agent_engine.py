"""统一 Agent 编排入口测试。"""

from __future__ import annotations

from conftest import text_response

from sagent.config.models import ContextConfig, PlanningConfig
from sagent.core.plan_state import PlanState, PlanStepState
from sagent.context.context_manager import ContextManager
from sagent.core.agent_engine import AgentEngine
from sagent.core.plan_engine import PlanEngine
from sagent.tools import build_default_registry


def test_never_uses_react_without_decision(make_fake_llm, agent_config):
    """never 策略直接执行 ReAct，不额外调用计划判断。"""
    llm = make_fake_llm([text_response("直接答案")])
    engine = AgentEngine(
        llm,
        build_default_registry(),
        agent_config,
        planning_config=PlanningConfig(strategy="never"),
    )

    assert engine.run("简单问题") == "直接答案"
    assert len(llm.calls) == 1
    assert "needs_plan" not in llm.calls[0]["messages"][0]["content"]


def test_always_uses_plan_without_decision(make_fake_llm, agent_config):
    """always 策略直接进入计划拆解流程。"""
    llm = make_fake_llm(
        [
            text_response('{"steps": ["执行步骤"]}'),
            text_response("步骤结果"),
            text_response("计划结果"),
        ]
    )
    engine = AgentEngine(
        llm,
        build_default_registry(),
        agent_config,
        planning_config=PlanningConfig(strategy="always"),
    )

    assert engine.run("复杂问题") == "计划结果"
    assert len(llm.calls) == 3
    assert "needs_plan" not in llm.calls[0]["messages"][0]["content"]


def test_adaptive_false_goes_to_react(make_fake_llm, agent_config):
    """adaptive 判断为 false 时直通 ReAct。"""
    llm = make_fake_llm(
        [
            text_response('{"needs_plan": false, "reason": "任务简单"}'),
            text_response("直接结果"),
        ]
    )
    engine = AgentEngine(
        llm,
        build_default_registry(),
        agent_config,
        planning_config=PlanningConfig(strategy="adaptive"),
    )

    assert engine.run("简单问题") == "直接结果"
    assert len(llm.calls) == 2


def test_adaptive_true_enters_plan(make_fake_llm, agent_config):
    """adaptive 判断为 true 时进入计划流程。"""
    llm = make_fake_llm(
        [
            text_response('{"needs_plan": true, "reason": "需要多步协作"}'),
            text_response('{"steps": ["执行步骤"]}'),
            text_response("步骤结果"),
            text_response("计划结果"),
        ]
    )
    engine = AgentEngine(
        llm,
        build_default_registry(),
        agent_config,
        planning_config=PlanningConfig(strategy="adaptive"),
    )

    assert engine.run("复杂问题") == "计划结果"
    assert len(llm.calls) == 4


def test_adaptive_invalid_json_goes_to_react(make_fake_llm, agent_config):
    """adaptive 判断无法解析时不阻断任务，直接执行 ReAct。"""
    llm = make_fake_llm([text_response("不是 JSON"), text_response("直接结果")])
    engine = AgentEngine(
        llm,
        build_default_registry(),
        agent_config,
        planning_config=PlanningConfig(strategy="adaptive"),
    )

    assert engine.run("问题") == "直接结果"
    assert len(llm.calls) == 2


def test_agent_engine_resumes_active_plan_without_redecomposing(make_fake_llm, agent_config):
    llm = make_fake_llm([text_response("恢复步骤结果"), text_response("恢复计划汇总")])
    engine = AgentEngine(
        llm,
        build_default_registry(),
        agent_config,
        planning_config=PlanningConfig(strategy="always"),
    )
    restored = PlanState(
        version=3,
        task="恢复任务",
        steps=[
            PlanStepState(description="已完成步骤", status="completed", summary="已有结果"),
            PlanStepState(description="待执行步骤"),
        ],
        status="executing",
    )
    engine._plan.plan_state = restored
    events = []
    engine._on_event = events.append
    engine._plan._on_event = events.append

    assert engine.run("恢复任务") == "恢复计划汇总"
    assert len(llm.calls) == 2
    assert restored.version == 3
    assert restored.steps[0].status == "completed"
    assert restored.steps[1].status == "completed"
    assert all("已完成步骤" not in str(call["messages"]) for call in llm.calls)


def test_plan_projection_is_request_local(make_fake_llm, agent_config):
    """计划投影进入请求消息副本，但不会写回 ContextManager。"""
    context_manager = ContextManager(
        ContextConfig(enable_summary=False, max_context_tokens=100000)
    )
    llm = make_fake_llm(
        [
            text_response('{"steps": ["执行步骤"]}'),
            text_response("步骤结果"),
            text_response("计划结果"),
        ]
    )
    engine = PlanEngine(
        llm,
        build_default_registry(),
        agent_config,
        context_manager=context_manager,
        planning_config=PlanningConfig(max_steps=1),
    )

    assert engine.run("复杂问题") == "计划结果"
    step_call = llm.calls[1]
    assert any(
        message.get("role") == "system" and "[计划投影]" in message.get("content", "")
        for message in step_call["messages"]
    )
    history = context_manager.get_messages()
    assert not any("[计划投影]" in message.get("content", "") for message in history)
    assert any(message.get("role") == "assistant" and message.get("content") == "步骤结果" for message in history)
    assert any(message.get("role") == "assistant" and message.get("content") == "计划结果" for message in history)
