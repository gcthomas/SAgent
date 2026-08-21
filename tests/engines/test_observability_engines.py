"""可观测性引擎集成测试。

使用 FakeLLMClient 离线回放 ReAct / Plan 引擎，通过包装客户端创建 gen_ai.chat Span，
捕获导出的 SpanRecord 验证完整 Span 树结构、父子关系与累积指标。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from conftest import make_tool_call, text_response, tool_response

from sagent.config.models import AgentConfig, ObservabilityConfig
from sagent.core.plan_engine import PlanEngine
from sagent.core.react_engine import ReActEngine
from sagent.observability import Span, close_observability, setup_observability
from sagent.observability import content as content_module
from sagent.observability import context as obs_context
from sagent.observability import cost as cost_module
from sagent.observability import metrics as metrics_module
from sagent.tools import build_default_registry
from sagent.tools.base import Tool


# ---------- 辅助类 ----------


class _SpannedLLM:
    """包装 FakeLLMClient，在 chat 调用时创建 gen_ai.chat Span。

    模拟真实 LLMClient 的 Span 埋点行为，使集成测试能验证完整的 Span 树。
    透明暴露 calls 与 remaining 属性，便于断言。
    """

    _MODEL = "fake-model"

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def calls(self) -> list:
        """暴露内部客户端的调用记录。"""
        return self._inner.calls

    @property
    def remaining(self) -> int:
        """暴露内部客户端的剩余响应数。"""
        return self._inner.remaining

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any:
        """发起对话请求，在 gen_ai.chat Span 中执行。"""
        with Span("gen_ai.chat") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "openai")
            span.set_attribute("gen_ai.request.model", self._MODEL)
            span.set_attribute("gen_ai.request.message_count", len(messages))
            if tools:
                span.set_attribute("gen_ai.request.tool_count", len(tools))
            response = self._inner.chat(messages, tools)
            if response.usage:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage["prompt_tokens"])
                span.set_attribute("gen_ai.usage.output_tokens", response.usage["completion_tokens"])
            return response


class _NoArgs(BaseModel):
    """无参数工具的 args_schema。"""


class _FailingTool(Tool):
    """总是抛异常的测试工具，用于验证 tool.execute Span 的 error 状态。"""

    name = "failing_tool"
    description = "总是失败的测试工具"
    args_schema = _NoArgs

    def run(self, args: BaseModel) -> str:
        raise RuntimeError("工具执行失败")


# ---------- 公共 fixture ----------


@pytest.fixture
def obs_env(tmp_path):
    """初始化可观测性环境并捕获导出的 SpanRecord，测试后恢复模块级状态。"""
    config = ObservabilityConfig(
        enabled=True,
        trace_dir=str(tmp_path),
        model_pricing={
            "fake-model": {
                "input_price_per_million": 10.0,
                "output_price_per_million": 20.0,
            }
        },
    )
    setup_observability(config)

    captured: list = []
    original_export = obs_context._export_span

    def _capture(record: Any) -> None:
        captured.append(record)

    obs_context._export_span = _capture

    yield captured

    obs_context._export_span = original_export
    close_observability()
    obs_context._exporter = None
    obs_context._otlp_exporter = None
    obs_context._obs_config = None
    obs_context._last_metric_flush = None
    metrics_module._metrics = None
    cost_module._pricing = {}
    content_module._observability_config = None
    content_module._log_llm_content_flag = False


# ---------- 辅助函数 ----------


def _assert_no_orphans(records: list) -> None:
    """断言所有 Span 的 parent_span_id 指向已存在的 span_id 或为根（'-'）。"""
    span_ids = {r.span_id for r in records}
    for r in records:
        if r.parent_span_id != "-":
            assert r.parent_span_id in span_ids, (
                f"孤儿 Span: {r.name} 的 parent_span_id={r.parent_span_id} 不存在"
            )


def _find(records: list, name: str) -> list:
    """查找指定名称的所有 Span 记录。"""
    return [r for r in records if r.name == name]


# ---------- ReAct 引擎测试 ----------


def test_react_trace_tree(make_fake_llm, agent_config, obs_env):
    """ReAct 直接回答的 Span 树包含 agent.react（根）与 gen_ai.chat（子）。"""
    llm = _SpannedLLM(make_fake_llm([text_response("最终答案")]))
    engine = ReActEngine(llm, build_default_registry(), agent_config)
    result = engine.run("你好")

    assert result == "最终答案"
    captured = obs_env
    names = [r.name for r in captured]

    # 包含 agent.react 根 Span 和 gen_ai.chat 子 Span
    assert "agent.react" in names
    assert "gen_ai.chat" in names

    # 父子关系：gen_ai.chat 的 parent 是 agent.react
    react_records = _find(captured, "agent.react")
    assert len(react_records) == 1
    react_record = react_records[0]
    assert react_record.parent_span_id == "-"  # 根 Span

    chat_records = _find(captured, "gen_ai.chat")
    assert len(chat_records) == 1
    assert chat_records[0].parent_span_id == react_record.span_id

    # 无孤儿 Span
    _assert_no_orphans(captured)


def test_react_tool_call_trace(make_fake_llm, agent_config, tmp_path, obs_env):
    """ReAct 工具调用链包含 agent.react、gen_ai.chat（x2）、tool.execute。"""
    target = tmp_path / "data.txt"
    target.write_text("文件内容", encoding="utf-8")

    llm = _SpannedLLM(
        make_fake_llm(
            [
                tool_response([make_tool_call("read_file", {"path": str(target)})]),
                text_response("文件内容已读取完毕"),
            ]
        )
    )
    engine = ReActEngine(llm, build_default_registry(), agent_config)
    result = engine.run("读取文件")

    assert result == "文件内容已读取完毕"
    captured = obs_env
    names = [r.name for r in captured]

    # 包含 agent.react、2 个 gen_ai.chat、1 个 tool.execute
    assert "agent.react" in names
    assert names.count("gen_ai.chat") == 2
    assert "tool.execute" in names

    # tool.execute 有 tool.name 属性
    tool_records = _find(captured, "tool.execute")
    assert len(tool_records) == 1
    assert tool_records[0].attributes.get("tool.name") == "read_file"

    # tool.execute 的 parent 是 agent.react
    react_record = _find(captured, "agent.react")[0]
    assert tool_records[0].parent_span_id == react_record.span_id

    _assert_no_orphans(captured)


def test_react_max_iterations_trace(make_fake_llm, obs_env):
    """ReAct 达到最大迭代次数时 agent.react 有 outcome=max_iterations，且存在 agent.finalize。"""
    config = AgentConfig(mode="react", max_iterations=2)
    llm = _SpannedLLM(
        make_fake_llm(
            [
                tool_response([make_tool_call("read_file", {"path": "x"})]),
                tool_response([make_tool_call("read_file", {"path": "y"})]),
                text_response("收尾结论"),
            ]
        )
    )
    engine = ReActEngine(llm, build_default_registry(), config)
    result = engine.run("永不停止的任务")

    assert result == "收尾结论"
    captured = obs_env
    names = [r.name for r in captured]

    # agent.react 存在且有 sagent.outcome = max_iterations
    react_record = _find(captured, "agent.react")[0]
    assert react_record.attributes.get("sagent.outcome") == "max_iterations"

    # agent.finalize 存在且为 agent.react 的子 Span
    finalize_records = _find(captured, "agent.finalize")
    assert len(finalize_records) == 1
    assert finalize_records[0].parent_span_id == react_record.span_id

    _assert_no_orphans(captured)


def test_react_tool_error_trace(make_fake_llm, agent_config, obs_env):
    """工具执行抛异常时 tool.execute Span 状态为 error。"""
    registry = build_default_registry()
    registry.register(_FailingTool())

    llm = _SpannedLLM(
        make_fake_llm(
            [
                tool_response([make_tool_call("failing_tool", {})]),
                text_response("工具失败了"),
            ]
        )
    )
    engine = ReActEngine(llm, registry, agent_config)
    result = engine.run("调用失败工具")

    assert result == "工具失败了"
    captured = obs_env

    # tool.execute 存在且状态为 error
    tool_records = _find(captured, "tool.execute")
    assert len(tool_records) == 1
    assert tool_records[0].status == "error"
    assert tool_records[0].attributes.get("tool.name") == "failing_tool"

    _assert_no_orphans(captured)


# ---------- Plan 引擎测试 ----------


def test_plan_trace_tree(make_fake_llm, agent_config, obs_env):
    """Plan 2 步流程包含 plan.decompose、plan.step（x2）、plan.summarize。"""
    llm = _SpannedLLM(
        make_fake_llm(
            [
                text_response('{"steps": ["步骤一", "步骤二"]}'),
                text_response("步骤一完成"),
                text_response("步骤二完成"),
                text_response("整体汇总结果"),
            ]
        )
    )
    engine = PlanEngine(llm, build_default_registry(), agent_config)
    result = engine.run("一个复杂任务")

    assert result == "整体汇总结果"
    captured = obs_env
    names = [r.name for r in captured]

    # 包含 plan.decompose、2 个 plan.step、plan.summarize
    assert "plan.decompose" in names
    assert names.count("plan.step") == 2
    assert "plan.summarize" in names

    # 每个 plan.step 有 step_index 和 step_total 属性
    step_records = _find(captured, "plan.step")
    indices = sorted(r.attributes.get("sagent.step_index") for r in step_records)
    assert indices == [1, 2]
    for step in step_records:
        assert step.attributes.get("sagent.step_total") == 2

    _assert_no_orphans(captured)


def test_plan_fallback_trace(make_fake_llm, agent_config, obs_env):
    """Plan 拆解失败时回退 ReAct，无 plan.step / plan.summarize。"""
    llm = _SpannedLLM(
        make_fake_llm(
            [
                text_response("我无法拆解"),
                text_response("直接执行的结果"),
            ]
        )
    )
    engine = PlanEngine(llm, build_default_registry(), agent_config)
    result = engine.run("任务")

    assert result == "直接执行的结果"
    captured = obs_env
    names = [r.name for r in captured]

    # plan.decompose 存在
    assert "plan.decompose" in names
    # 不应有 plan.step 和 plan.summarize
    assert "plan.step" not in names
    assert "plan.summarize" not in names
    # 回退的 agent.react 存在
    assert "agent.react" in names

    _assert_no_orphans(captured)


# ---------- 累积指标测试 ----------


def test_react_accumulated_metrics(make_fake_llm, agent_config, obs_env):
    """ReAct 引擎的根 Span 注入正确的 token 与成本累积值。"""
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    llm = _SpannedLLM(make_fake_llm([text_response("最终答案", usage=usage)]))
    engine = ReActEngine(llm, build_default_registry(), agent_config)
    engine.run("你好")

    captured = obs_env

    # 找到 agent.react 根 Span
    react_record = _find(captured, "agent.react")[0]
    attrs = react_record.attributes

    # 累积 token 正确
    assert attrs.get("sagent.accumulated_input_tokens") == 100.0
    assert attrs.get("sagent.accumulated_output_tokens") == 20.0

    # 累积成本正确：100/1_000_000 * 10 + 20/1_000_000 * 20 = 0.001 + 0.0004 = 0.0014
    assert attrs.get("sagent.accumulated_cost") == round(0.0014, 6)

    # 迭代次数为 1（直接回答，1 轮迭代）
    assert attrs.get("sagent.iteration_count") == 1.0
    # 无工具调用
    assert attrs.get("sagent.tool_call_count") == 0.0
    # 无压缩
    assert attrs.get("sagent.compression_count") == 0.0
