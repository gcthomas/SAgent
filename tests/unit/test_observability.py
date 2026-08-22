"""可观测性模块单元测试。

覆盖配置模型、Span 上下文管理、指标聚合、成本估算、本地导出、
OTel SDK 集成（SpanExporter/SpanProcessor）与 record_span_metrics 分发逻辑，不依赖真实 LLM。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult

from sagent.config.models import AppConfig, LLMConfig, ObservabilityConfig
from sagent.observability import (
    ALLOWED_METRIC_LABELS,
    LocalSpanExporter,
    MetricSnapshot,
    Span,
    SpanRecord,
    calculate_cost,
    close_observability,
    current_parent_span_id,
    current_span_id,
    get_metrics,
    MetricsRegistry,
    record_span_metrics,
    setup_cost_estimator,
    setup_metrics,
    setup_observability,
)
from sagent.observability import content as content_module
from sagent.observability import context as obs_context
from sagent.observability import cost as cost_module
from sagent.observability.exporter import _span_data_to_record
from sagent.observability import metrics as metrics_module


# ---------- 测试辅助类 ----------


class _CapturingExporter:
    """测试用 SpanExporter，捕获 SpanRecord 而不写文件。"""

    def __init__(self) -> None:
        self.captured: list[SpanRecord] = []

    def export(self, spans: Sequence[Any]) -> SpanExportResult:
        for span in spans:
            try:
                record = _span_data_to_record(span)
                self.captured.append(record)
            except Exception:
                pass
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _MockSpanData:
    """模拟 OTel SpanData 用于测试 LocalSpanExporter。"""

    def __init__(
        self,
        name: str = "test",
        trace_id: int = 1,
        span_id: int = 1,
        parent: Any = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self._trace_id = trace_id
        self._span_id = span_id
        self.parent = parent
        start_ns = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        end_ns = int(datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        self.start_time = start_ns
        self.end_time = end_ns
        self.attributes = attributes if attributes is not None else {"key": "value"}
        self.events: list = []

    def get_span_context(self) -> Any:
        class _Ctx:
            def __init__(self, trace_id: int, span_id: int) -> None:
                self.trace_id = trace_id
                self.span_id = span_id
        return _Ctx(self._trace_id, self._span_id)

    @property
    def status(self) -> Any:
        class _Status:
            status_code = None
        return _Status()


# ---------- 公共 fixture ----------


@pytest.fixture
def obs_setup(tmp_path):
    """初始化可观测性模块状态，测试后恢复模块级单例。"""
    config = ObservabilityConfig(enabled=True, trace_dir=str(tmp_path))
    setup_observability(config)
    # OTel 全局 TracerProvider 只能设置一次，后续调用会被忽略。
    # 直接用新 provider 的 tracer 确保 Span 路由到当前 provider。
    obs_context._tracer = obs_context._tracer_provider.get_tracer("sagent")
    yield config
    close_observability()
    obs_context._tracer_provider = None
    obs_context._meter_provider = None
    obs_context._tracer = None
    obs_context._local_exporter = None
    obs_context._obs_config = None
    obs_context._last_metric_flush = None
    obs_context._metrics_recorded_spans.clear()
    metrics_module._metrics = None
    cost_module._pricing = {}
    content_module._observability_config = None
    content_module._log_llm_content_flag = False


@pytest.fixture
def metrics_setup():
    """仅初始化指标注册表，测试后恢复。"""
    setup_metrics(ObservabilityConfig())
    yield get_metrics()
    metrics_module._metrics = None


@pytest.fixture
def capture_spans(obs_setup):
    """捕获导出的 SpanRecord，通过自定义 SpanExporter 实现。"""
    capturing = _CapturingExporter()
    processor = SimpleSpanProcessor(capturing)
    obs_context._tracer_provider.add_span_processor(processor)
    yield capturing.captured


def _iso_pair(seconds: float = 1.0) -> tuple[str, str]:
    """构造一对相隔指定秒数的 ISO 时间戳。"""
    start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, 0, 0, int(seconds), tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat()


# ---------- 配置测试 ----------


def test_observability_config_defaults():
    """ObservabilityConfig 所有字段具有正确的默认值。"""
    config = ObservabilityConfig()
    assert config.enabled is True
    assert config.trace_dir == "logs"
    assert config.trace_file == "sagent_trace.jsonl"
    assert config.metric_file == "sagent_metrics.jsonl"
    assert config.capture_content is False
    assert config.content_max_length == 500
    assert config.model_pricing == {}
    assert config.otlp_enabled is False
    assert config.otlp_endpoint == "http://localhost:4318"
    assert config.otlp_protocol == "http"
    assert config.otlp_timeout == 10.0
    assert config.metrics_flush_interval == 60.0
    assert config.enable_openai_auto_instrumentation is False


def test_app_config_provides_default_observability():
    """AppConfig 提供默认 observability 配置。"""
    app = AppConfig(llm=LLMConfig(model="test-model"))
    assert isinstance(app.observability, ObservabilityConfig)
    assert app.observability.enabled is True
    assert app.observability.metrics_flush_interval == 60.0


def test_observability_config_metrics_flush_interval_exists():
    """metrics_flush_interval 字段存在且可自定义。"""
    config = ObservabilityConfig(metrics_flush_interval=30.0)
    assert config.metrics_flush_interval == 30.0


# ---------- Span 上下文与父子关系 ----------


def test_span_root_context(capture_spans):
    """根 Span 的上下文 trace_id/span_id/parent_span_id 正确。"""
    with Span("root") as span:
        assert current_span_id() == span.span_id
        assert current_parent_span_id() == "-"

    # 退出后上下文恢复为 None
    assert current_span_id() == "-"
    assert current_parent_span_id() == "-"


def test_span_parent_child(capture_spans):
    """子 Span 的 parent_span_id 等于根 Span 的 span_id。"""
    with Span("root") as root_span:
        root_id = root_span.span_id
        with Span("child") as child_span:
            assert current_parent_span_id() == root_id
            assert current_span_id() == child_span.span_id

    # 子 Span 先退出，根 Span 后退出
    assert len(capture_spans) == 2
    child_record = capture_spans[0]
    root_record = capture_spans[1]
    assert child_record.parent_span_id == root_id
    assert root_record.parent_span_id == "-"


def test_span_trace_id_inherited(capture_spans):
    """子 Span 继承根 Span 的 trace_id。"""
    with Span("root") as root_span:
        trace_id = root_span.trace_id
        with Span("child") as child_span:
            assert child_span.trace_id == trace_id

    assert capture_spans[0].trace_id == trace_id
    assert capture_spans[1].trace_id == trace_id


# ---------- Span 状态 ----------


def test_span_default_status_ok(capture_spans):
    """Span 默认状态为 ok。"""
    with Span("test"):
        pass
    assert len(capture_spans) == 1
    assert capture_spans[0].status == "ok"


def test_span_status_error_on_exception(capture_spans):
    """Span 中抛异常时状态自动设为 error。"""
    with pytest.raises(ValueError):
        with Span("test"):
            raise ValueError("boom")

    assert len(capture_spans) == 1
    assert capture_spans[0].status == "error"


def test_span_set_status(capture_spans):
    """set_status 可手动设置状态为 error。"""
    with Span("test") as span:
        span.set_status("error")

    assert len(capture_spans) == 1
    assert capture_spans[0].status == "error"


# ---------- Span 属性与事件 ----------


def test_span_set_attribute(capture_spans):
    """set_attribute 存储属性。"""
    with Span("test") as span:
        span.set_attribute("key1", "value1")
        span.set_attribute("key2", 42)
        span.set_attribute("key3", True)

    record = capture_spans[0]
    assert record.attributes["key1"] == "value1"
    assert record.attributes["key2"] == 42
    assert record.attributes["key3"] is True


def test_span_add_event(capture_spans):
    """add_event 存储事件及时间戳。"""
    with Span("test") as span:
        span.add_event("evt1", {"detail": "info"})
        span.add_event("evt2")

    record = capture_spans[0]
    assert len(record.events) == 2
    assert record.events[0].name == "evt1"
    assert record.events[0].attributes == {"detail": "info"}
    assert record.events[0].timestamp  # 非空时间戳
    assert record.events[1].name == "evt2"
    assert record.events[1].attributes == {}


# ---------- 指标聚合 ----------


def test_metrics_increment_counter():
    """计数器增量与标签聚合。"""
    registry = MetricsRegistry()
    registry.increment_counter("calls", {"mode": "react"})
    registry.increment_counter("calls", {"mode": "react"})
    registry.increment_counter("calls", {"mode": "plan"})
    snapshot = registry.snapshot()
    assert snapshot.metrics["calls"]["mode=react"]["value"] == 2.0
    assert snapshot.metrics["calls"]["mode=plan"]["value"] == 1.0


def test_metrics_record_histogram():
    """直方图记录 count/sum/min/max/avg。"""
    registry = MetricsRegistry()
    registry.record_histogram("latency", 10.0)
    registry.record_histogram("latency", 20.0)
    registry.record_histogram("latency", 30.0)
    snapshot = registry.snapshot()
    hist = snapshot.metrics["latency"][""]
    assert hist["count"] == 3
    assert hist["sum"] == 60.0
    assert hist["min"] == 10.0
    assert hist["max"] == 30.0
    assert hist["avg"] == 20.0


def test_metrics_accumulate_tokens():
    """按 trace 累积 token 与成本。"""
    registry = MetricsRegistry()
    registry.accumulate_tokens("t1", input_tokens=100, output_tokens=20, cost=0.01)
    registry.accumulate_tokens("t1", input_tokens=50, output_tokens=10, cost=0.005)
    acc = registry.get_trace_accumulation("t1")
    assert acc["sagent.accumulated_input_tokens"] == 150.0
    assert acc["sagent.accumulated_output_tokens"] == 30.0
    assert acc["sagent.accumulated_cost"] == 0.015


def test_metrics_accumulate_tokens_no_cost():
    """cost 为 None 时不累积成本。"""
    registry = MetricsRegistry()
    registry.accumulate_tokens("t1", input_tokens=10, output_tokens=5)
    acc = registry.get_trace_accumulation("t1")
    assert acc["sagent.accumulated_input_tokens"] == 10.0
    assert acc["sagent.accumulated_output_tokens"] == 5.0
    assert "sagent.accumulated_cost" not in acc


def test_metrics_accumulate_counts():
    """累积迭代/工具/压缩次数。"""
    registry = MetricsRegistry()
    registry.accumulate_iteration("t1")
    registry.accumulate_iteration("t1")
    registry.accumulate_tool_call("t1")
    registry.accumulate_compression("t1")
    acc = registry.get_trace_accumulation("t1")
    assert acc["sagent.iteration_count"] == 2.0
    assert acc["sagent.tool_call_count"] == 1.0
    assert acc["sagent.compression_count"] == 1.0


def test_metrics_get_trace_accumulation_keys():
    """get_trace_accumulation 返回 sagent.accumulated_* 键。"""
    registry = MetricsRegistry()
    registry.accumulate_tokens("t1", input_tokens=1, output_tokens=1, cost=0.001)
    registry.accumulate_iteration("t1")
    registry.accumulate_tool_call("t1")
    registry.accumulate_compression("t1")
    acc = registry.get_trace_accumulation("t1")
    assert "sagent.accumulated_input_tokens" in acc
    assert "sagent.accumulated_output_tokens" in acc
    assert "sagent.accumulated_cost" in acc
    assert "sagent.iteration_count" in acc
    assert "sagent.tool_call_count" in acc
    assert "sagent.compression_count" in acc


def test_metrics_clear_trace():
    """clear_trace 清除指定 trace 的累积。"""
    registry = MetricsRegistry()
    registry.accumulate_tokens("t1", input_tokens=100, output_tokens=20, cost=0.01)
    registry.clear_trace("t1")
    assert registry.get_trace_accumulation("t1") == {}


def test_metrics_snapshot_returns_metric_snapshot():
    """snapshot 返回 MetricSnapshot 实例。"""
    registry = MetricsRegistry()
    registry.increment_counter("c1")
    snapshot = registry.snapshot()
    assert isinstance(snapshot, MetricSnapshot)
    assert snapshot.timestamp  # 非空
    assert "c1" in snapshot.metrics


def test_metrics_reset():
    """reset 清除所有指标。"""
    registry = MetricsRegistry()
    registry.increment_counter("c1")
    registry.record_histogram("h1", 1.0)
    registry.accumulate_tokens("t1", input_tokens=1, output_tokens=1)
    registry.reset()
    snapshot = registry.snapshot()
    assert snapshot.metrics == {}
    assert registry.get_trace_accumulation("t1") == {}


def test_metrics_label_filtering():
    """标签过滤只保留 ALLOWED_METRIC_LABELS 中的键。"""
    registry = MetricsRegistry()
    # 传入合法与非法标签
    registry.increment_counter("calls", {"mode": "react", "unknown_key": "x"})
    snapshot = registry.snapshot()
    # 只保留 mode=react，非法标签被过滤
    assert "mode=react" in snapshot.metrics["calls"]
    for key in snapshot.metrics["calls"]:
        assert "unknown_key" not in key


def test_allowed_metric_labels_contents():
    """ALLOWED_METRIC_LABELS 包含预期的低基数维度。"""
    expected = {
        "mode", "operation", "model", "provider",
        "tool_name", "outcome", "compression_layer",
    }
    assert expected <= set(ALLOWED_METRIC_LABELS)


# ---------- 成本估算 ----------


def test_calculate_cost_valid():
    """有效价格表返回正确成本。"""
    pricing = {"gpt-4": {"input_price_per_million": 30.0, "output_price_per_million": 60.0}}
    # 1000 * 30/1_000_000 + 500 * 60/1_000_000 = 0.03 + 0.03 = 0.06
    cost = calculate_cost("gpt-4", 1000, 500, pricing)
    assert cost == round(0.06, 6)


def test_calculate_cost_model_not_found():
    """模型不在价格表中返回 None。"""
    pricing = {"gpt-4": {"input_price_per_million": 30.0, "output_price_per_million": 60.0}}
    assert calculate_cost("unknown", 100, 50, pricing) is None


def test_calculate_cost_none_tokens():
    """input/output 为 None 时返回 None。"""
    pricing = {"gpt-4": {"input_price_per_million": 30.0, "output_price_per_million": 60.0}}
    assert calculate_cost("gpt-4", None, 50, pricing) is None
    assert calculate_cost("gpt-4", 100, None, pricing) is None


def test_calculate_cost_missing_pricing_fields():
    """价格字段缺失时返回 None。"""
    assert calculate_cost("gpt-4", 100, 50, {"gpt-4": {"input_price_per_million": 30.0}}) is None


def test_setup_cost_estimator_sets_module_pricing():
    """setup_cost_estimator 设置模块级价格表。"""
    pricing = {"gpt-4": {"input_price_per_million": 30.0, "output_price_per_million": 60.0}}
    setup_cost_estimator(pricing)
    try:
        # pricing=None 时使用模块级价格表
        cost = calculate_cost("gpt-4", 1000, 500)
        assert cost == round(0.06, 6)
    finally:
        setup_cost_estimator({})


# ---------- 本地导出 ----------


def _make_span_record() -> SpanRecord:
    """构造测试用 SpanRecord。"""
    start, end = _iso_pair(1.0)
    return SpanRecord(
        trace_id="t1",
        span_id="s1",
        parent_span_id="-",
        name="test",
        start_time=start,
        end_time=end,
        status="ok",
        attributes={"key": "value"},
    )


def test_local_export_span(tmp_path):
    """enabled=True 时写入 SpanRecord 到 JSONL 文件。"""
    config = ObservabilityConfig(enabled=True, trace_dir=str(tmp_path))
    exporter = LocalSpanExporter(config)
    exporter.export([_MockSpanData(name="test", attributes={"key": "value"})])
    exporter.shutdown()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = tmp_path / f"sagent_trace_{today}.jsonl"
    assert trace_file.exists()

    lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
    data = json.loads(lines[0])
    assert data["name"] == "test"
    assert data["attributes"]["key"] == "value"


def test_local_export_disabled(tmp_path):
    """enabled=False 时不写入任何文件。"""
    config = ObservabilityConfig(enabled=False, trace_dir=str(tmp_path))
    exporter = LocalSpanExporter(config)
    exporter.export([_MockSpanData()])
    exporter.shutdown()
    assert not any(tmp_path.iterdir())


def test_local_export_metric(tmp_path):
    """指标快照导出到 JSONL 文件。"""
    config = ObservabilityConfig(enabled=True, trace_dir=str(tmp_path))
    exporter = LocalSpanExporter(config)
    snapshot = MetricSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        metrics={"counter": {"": {"value": 1.0}}},
    )
    exporter.export_metric(snapshot)
    exporter.shutdown()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    metric_file = tmp_path / f"sagent_metrics_{today}.jsonl"
    assert metric_file.exists()


def test_local_export_per_day_rolling(tmp_path):
    """文件名包含日期后缀（按天滚动）。"""
    config = ObservabilityConfig(enabled=True, trace_dir=str(tmp_path))
    exporter = LocalSpanExporter(config)
    exporter.export([_MockSpanData()])
    exporter.shutdown()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    files = list(tmp_path.glob("sagent_trace_*.jsonl"))
    assert any(today in f.name for f in files)


# ---------- 导出失败隔离 ----------


def test_span_exit_catches_export_failure(obs_setup):
    """Span __exit__ 捕获导出失败，不抛异常。"""
    class _RaisingExporter:
        def export(self, spans):
            from opentelemetry.sdk.trace.export import SpanExportResult
            return SpanExportResult.FAILURE
        def shutdown(self):
            pass
        def force_flush(self, timeout_millis=30000):
            return True

    processor = SimpleSpanProcessor(_RaisingExporter())
    obs_context._tracer_provider.add_span_processor(processor)
    # 不应抛异常
    with Span("test"):
        pass


def test_local_exporter_write_failure_silent(tmp_path):
    """写入失败时静默处理，不抛异常。"""
    config = ObservabilityConfig(enabled=True, trace_dir=str(tmp_path))
    exporter = LocalSpanExporter(config)
    # 构造无法序列化的对象会导致 json.dumps 失败，但 SpanRecord 是 pydantic 模型
    # 这里测试 close 后再 export 不抛异常
    exporter.shutdown()
    exporter.export([_MockSpanData()])  # 关闭后写入不应抛异常


# ---------- record_span_metrics 分发 ----------


def test_record_span_metrics_agent_run(metrics_setup):
    """agent.run 触发 agent_runs_total 计数器与 agent_latency_ms 直方图。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "agent.run", "t1",
        {"sagent.mode": "react", "sagent.outcome": "completed"},
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "agent_runs_total" in snapshot.metrics
    assert "agent_latency_ms" in snapshot.metrics
    assert "mode=react|outcome=completed" in snapshot.metrics["agent_runs_total"]


def test_record_span_metrics_gen_ai_chat(metrics_setup):
    """gen_ai.chat 触发 llm_calls_total、延迟与 token 直方图，并累积。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "gen_ai.chat", "t1",
        {
            "gen_ai.request.model": "gpt-4",
            "gen_ai.provider.name": "openai",
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 20,
        },
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "llm_calls_total" in snapshot.metrics
    assert "llm_latency_ms" in snapshot.metrics
    assert "llm_input_tokens" in snapshot.metrics
    assert "llm_output_tokens" in snapshot.metrics
    # 累积到 trace
    acc = metrics_setup.get_trace_accumulation("t1")
    assert acc["sagent.accumulated_input_tokens"] == 100.0
    assert acc["sagent.accumulated_output_tokens"] == 20.0


def test_record_span_metrics_gen_ai_chat_error(metrics_setup):
    """gen_ai.chat 出错时递增 llm_errors_total。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "gen_ai.chat", "t1",
        {
            "gen_ai.request.model": "gpt-4",
            "gen_ai.provider.name": "openai",
        },
        "error", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "llm_errors_total" in snapshot.metrics


def test_record_span_metrics_tool_execute(metrics_setup):
    """tool.execute 触发 tool_calls_total、延迟直方图，并累积工具调用。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "tool.execute", "t1",
        {"tool.name": "read_file"},
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "tool_calls_total" in snapshot.metrics
    assert "tool_latency_ms" in snapshot.metrics
    acc = metrics_setup.get_trace_accumulation("t1")
    assert acc["sagent.tool_call_count"] == 1.0


def test_record_span_metrics_tool_execute_error(metrics_setup):
    """tool.execute 出错时递增 tool_errors_total。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "tool.execute", "t1",
        {"tool.name": "read_file"},
        "error", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "tool_errors_total" in snapshot.metrics


def test_record_span_metrics_context_compress(metrics_setup):
    """context.compress 触发 compressions_total、token 直方图，并累积压缩。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "context.compress", "t1",
        {
            "sagent.compression.layer": 1,
            "sagent.compression.before_tokens": 5000,
            "sagent.compression.after_tokens": 2000,
        },
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "compressions_total" in snapshot.metrics
    assert "compression_before_tokens" in snapshot.metrics
    assert "compression_after_tokens" in snapshot.metrics
    acc = metrics_setup.get_trace_accumulation("t1")
    assert acc["sagent.compression_count"] == 1.0


def test_record_span_metrics_agent_react_accumulates_iterations(metrics_setup):
    """agent.react 读取 iteration_count 并累积迭代次数。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "agent.react", "t1",
        {"sagent.iteration_count": 3, "sagent.max_iterations": 5},
        "ok", start, end,
    )
    acc = metrics_setup.get_trace_accumulation("t1")
    assert acc["sagent.iteration_count"] == 3.0


def test_record_span_metrics_agent_react_max_iterations(metrics_setup):
    """agent.react outcome=max_iterations 时递增 max_iterations_total。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "agent.react", "t1",
        {"sagent.outcome": "max_iterations", "sagent.iteration_count": 2},
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "max_iterations_total" in snapshot.metrics


def test_record_span_metrics_agent_finalize(metrics_setup):
    """agent.finalize 递增 max_iterations_total。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "agent.finalize", "t1",
        {"sagent.max_iterations": 5},
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "max_iterations_total" in snapshot.metrics


def test_record_span_metrics_mcp_tool_call(metrics_setup):
    """mcp.tool_call 触发 mcp_calls_total、延迟直方图，并累积工具调用。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "mcp.tool_call", "t1",
        {"mcp.tool.name": "search"},
        "ok", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "mcp_calls_total" in snapshot.metrics
    assert "mcp_latency_ms" in snapshot.metrics
    acc = metrics_setup.get_trace_accumulation("t1")
    assert acc["sagent.tool_call_count"] == 1.0


def test_record_span_metrics_mcp_tool_call_error(metrics_setup):
    """mcp.tool_call 出错时递增 mcp_errors_total。"""
    start, end = _iso_pair(1.0)
    record_span_metrics(
        "mcp.tool_call", "t1",
        {"mcp.tool.name": "search"},
        "error", start, end,
    )
    snapshot = metrics_setup.snapshot()
    assert "mcp_errors_total" in snapshot.metrics


def test_record_span_metrics_no_metrics_singleton():
    """_metrics 为 None 时 record_span_metrics 不抛异常。"""
    metrics_module._metrics = None
    try:
        record_span_metrics("agent.run", "t1", {}, "ok", "", "")
    finally:
        pass


# ---------- 根 Span 注入累积值 ----------


def test_root_span_injects_accumulation(capture_spans):
    """根 Span 退出时将 trace 累积值注入属性。"""
    start, end = _iso_pair(1.0)
    # 创建根 Span，获取 OTel 生成的 trace_id
    with Span("agent.react") as span:
        trace_id = span.trace_id
        # 通过 gen_ai.chat 累积 token 到当前 trace
        record_span_metrics(
            "gen_ai.chat", trace_id,
            {
                "gen_ai.request.model": "gpt-4",
                "gen_ai.provider.name": "openai",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
            },
            "ok", start, end,
        )
        span.set_attribute("sagent.iteration_count", 1)

    record = capture_spans[0]
    assert record.attributes.get("sagent.accumulated_input_tokens") == 100.0
    assert record.attributes.get("sagent.accumulated_output_tokens") == 20.0
    # trace 被清除
    from sagent.observability import get_metrics
    metrics = get_metrics()
    assert metrics is not None
    assert metrics.get_trace_accumulation(trace_id) == {}
