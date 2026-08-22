"""Span 上下文与 OTel SDK 集成。

基于 OpenTelemetry SDK 的 Tracer 实现上下文传播，提供薄 Span 包装层，
在 OTel Span 的 end() 调用之前注入 trace 累积值和 iteration 计数等业务属性。
导出失败不影响 Agent 主流程。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

from ..config.models import ObservabilityConfig
from .cost import setup_cost_estimator
from .exporter import LocalSpanExporter
from .logging_setup import get_logger
from .models import SpanEvent, SpanStatus
from .metrics import MetricsSpanProcessor, get_metrics, record_span_metrics, setup_metrics

logger = get_logger(__name__)

# 模块级 OTel TracerProvider（在 setup_observability 中设置）
_tracer_provider: TracerProvider | None = None
# 模块级 OTel MeterProvider（在 setup_observability 中设置）
_meter_provider: MeterProvider | None = None
# 模块级 OTel Tracer（在 setup_observability 中设置）
_tracer: trace.Tracer | None = None
# 模块级本地导出器（在 setup_observability 中设置）
_local_exporter: LocalSpanExporter | None = None
# 模块级可观测性配置（在 setup_observability 中设置）
_obs_config: ObservabilityConfig | None = None
# 上次指标 flush 时间（用于周期性 flush 判断）
_last_metric_flush: datetime | None = None
# 已记录指标的 span_id 集合，防止 Span.__exit__ 和 MetricsSpanProcessor.on_end 双写
_metrics_recorded_spans: set[int] = set()


def current_span_id() -> str:
    """当前 span_id，不在 span 中返回 '-'。

    从 OTel context 读取当前 span 的 span_id，格式化为 16 字符 hex。
    """
    span = trace.get_current_span()
    if span is not None and span.is_recording():
        span_context = span.get_span_context()
        if span_context is not None and span_context.span_id is not None:
            return format(span_context.span_id, "016x")
    return "-"


def current_parent_span_id() -> str:
    """当前 parent_span_id，不在 span 中返回 '-'。

    从 OTel context 读取当前 span 的 parent span_id，格式化为 16 字符 hex。
    根 span 无 parent 时返回 '-'。
    """
    span = trace.get_current_span()
    if span is not None and span.is_recording():
        parent = getattr(span, "parent", None)
        if parent is not None:
            try:
                return format(parent.span_id, "016x")
            except (AttributeError, ValueError):
                pass
    return "-"


def setup_observability(config: ObservabilityConfig) -> None:
    """初始化可观测性子系统。

    初始化 OTel TracerProvider、Span 处理器、本地导出器和指标注册表。
    如果 enable_openai_auto_instrumentation 为 true 且 observability.enabled 也为 true，
    记录警告日志说明首期不允许同时启用，实际不启用自动埋点。

    参数:
        config: 可观测性配置。
    """
    global _tracer_provider, _meter_provider, _tracer, _local_exporter, _obs_config, _last_metric_flush
    _obs_config = config
    # 初始化成本估算器（设置模块级价格表）
    setup_cost_estimator(config.model_pricing)
    # 初始化本地导出器
    _local_exporter = LocalSpanExporter(config)
    # 重置上次 flush 时间
    _last_metric_flush = None

    # 初始化 OTel TracerProvider
    resource = Resource.create({"service.name": "sagent"})
    _tracer_provider = TracerProvider(resource=resource)

    # 注册 Span 处理器
    # 1. MetricsSpanProcessor（指标派发）
    _tracer_provider.add_span_processor(MetricsSpanProcessor())
    # 2. LocalSpanExporter（本地 JSON Lines 导出）via SimpleSpanProcessor
    _tracer_provider.add_span_processor(
        SimpleSpanProcessor(_local_exporter)
    )
    # 3. 如果启用 OTLP，注册 OTLPSpanExporter via BatchSpanProcessor（lazy import）
    if config.otlp_enabled:
        try:
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            otlp_exporter = OTLPSpanExporter(
                endpoint=f"{config.otlp_endpoint}/v1/traces",
                timeout=config.otlp_timeout,
            )
            _tracer_provider.add_span_processor(
                BatchSpanProcessor(otlp_exporter)
            )
            logger.info(
                "OTLP Span 导出器初始化成功",
                extra={
                    "event": "otlp_init_success",
                    "endpoint": config.otlp_endpoint,
                },
            )
        except ImportError:
            logger.warning(
                "OTLP 已启用但 opentelemetry-exporter-otlp-proto-http 不可用，"
                "仅使用本地导出",
                extra={"event": "otlp_dependency_missing"},
            )
        except Exception:
            logger.exception(
                "OTLP Span 导出器初始化失败",
                extra={"event": "otlp_init_error"},
            )

    # 初始化 OTel MeterProvider（在 setup_metrics 之前设置，使 _get_meter 获取真实 meter）
    if config.otlp_enabled:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics.export import (
                PeriodicExportingMetricReader,
            )

            metric_exporter = OTLPMetricExporter(
                endpoint=f"{config.otlp_endpoint}/v1/metrics",
                timeout=config.otlp_timeout,
            )
            metric_reader = PeriodicExportingMetricReader(
                metric_exporter,
                export_interval_millis=config.metrics_flush_interval * 1000,
            )
            _meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )
            logger.info(
                "OTLP Metric 导出器初始化成功",
                extra={
                    "event": "otlp_metric_init_success",
                    "endpoint": config.otlp_endpoint,
                },
            )
        except ImportError:
            logger.warning(
                "OTLP 已启用但 metric exporter 依赖不可用，"
                "指标仅使用本地存储",
                extra={"event": "otlp_metric_dependency_missing"},
            )
            _meter_provider = MeterProvider(resource=resource)
        except Exception:
            logger.exception(
                "OTLP Metric 导出器初始化失败",
                extra={"event": "otlp_metric_init_error"},
            )
            _meter_provider = MeterProvider(resource=resource)
    else:
        _meter_provider = MeterProvider(resource=resource)

    # 设置全局 MeterProvider（只能设置一次，后续调用会被忽略）
    try:
        metrics.set_meter_provider(_meter_provider)
    except Exception:
        logger.debug("设置全局 MeterProvider 失败", exc_info=True)

    # 初始化指标注册表（在 MeterProvider 设置后，确保 _get_meter 获取真实 meter）
    setup_metrics(config)

    # 设置全局 TracerProvider
    trace.set_tracer_provider(_tracer_provider)
    _tracer = trace.get_tracer("sagent")

    # 检查 OpenAI SDK 自动埋点（首期禁止与手动 gen_ai.chat Span 同时启用）
    if config.enable_openai_auto_instrumentation and config.enabled:
        logger.warning(
            "OpenAI SDK 自动埋点已配置为启用，但首期禁止与手动 gen_ai.chat Span 同时启用，"
            "实际不启用自动埋点",
            extra={"event": "openai_auto_instrumentation_disabled"},
        )


def _maybe_flush_metrics() -> None:
    """如果距离上次 flush 已超过配置的刷新周期，则生成指标快照并导出。"""
    global _last_metric_flush
    if _obs_config is None:
        return
    metrics = get_metrics()
    if metrics is None or _local_exporter is None:
        return
    now = datetime.now(timezone.utc)
    if _last_metric_flush is None or (
        now - _last_metric_flush
    ).total_seconds() >= _obs_config.metrics_flush_interval:
        snapshot = metrics.snapshot()
        _local_exporter.export_metric(snapshot)
        _last_metric_flush = now


def flush_metrics() -> None:
    """强制 flush 当前指标快照到本地导出器，并 flush OTel provider。"""
    global _last_metric_flush
    metrics = get_metrics()
    if metrics is not None and _local_exporter is not None:
        snapshot = metrics.snapshot()
        _local_exporter.export_metric(snapshot)
    if _meter_provider is not None:
        try:
            _meter_provider.force_flush()
        except Exception:
            logger.exception(
                "OTel MeterProvider flush 失败",
                extra={"event": "meter_flush_error"},
            )
    if _tracer_provider is not None:
        try:
            _tracer_provider.force_flush()
        except Exception:
            logger.exception(
                "OTel provider flush 失败",
                extra={"event": "otel_flush_error"},
            )
    _last_metric_flush = datetime.now(timezone.utc)


def close_observability() -> None:
    """关闭所有导出器（本地与 OTel），flush 拦留数据。"""
    global _tracer_provider, _meter_provider, _tracer, _local_exporter
    if _meter_provider is not None:
        try:
            _meter_provider.force_flush()
            _meter_provider.shutdown()
        except Exception:
            logger.exception(
                "OTel MeterProvider 关闭失败",
                extra={"event": "meter_provider_close_error"},
            )
    if _tracer_provider is not None:
        try:
            _tracer_provider.force_flush()
            _tracer_provider.shutdown()
        except Exception:
            logger.exception(
                "OTel provider 关闭失败",
                extra={"event": "otel_close_error"},
            )
    if _local_exporter is not None:
        try:
            _local_exporter.shutdown()
        except Exception:
            logger.exception(
                "本地导出器关闭失败",
                extra={"event": "local_exporter_close_error"},
            )
    _tracer_provider = None
    _meter_provider = None
    _tracer = None
    _local_exporter = None


class Span:
    """Span 上下文管理器（薄包装层）。

    内部使用 OTel SDK 的 Tracer 创建和管理 Span 生命周期。
    在 OTel Span 的 end() 调用之前注入 trace 累积值和 iteration 计数等业务属性，
    保证 OTLP 导出和本地导出的根 Span 属性完整。
    导出失败不影响 Agent 主流程。
    """

    def __init__(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        # trace_id 和 parent_span_id 参数在 OTel 模型中由 SDK 通过 context 传播管理，
        # 此处接受参数仅为 API 兼容，不用于控制 OTel span 的 ID
        self._name = name
        self._session_id = session_id
        self._attributes: dict[str, Any] = {}
        self._events: list[SpanEvent] = []
        self._status: SpanStatus = "ok"
        self._otel_span: trace.Span | None = None
        self._otel_cm: Any = None

    @property
    def span_id(self) -> str:
        """当前 Span 的 span_id（只读，16 字符 hex）。"""
        if self._otel_span is None:
            return "-"
        span_context = self._otel_span.get_span_context()
        if span_context is not None and span_context.span_id is not None:
            return format(span_context.span_id, "016x")
        return "-"

    @property
    def trace_id(self) -> str:
        """当前 Span 的 trace_id（只读，32 字符 hex）。"""
        if self._otel_span is None:
            return "-"
        span_context = self._otel_span.get_span_context()
        if span_context is not None and span_context.trace_id is not None:
            return format(span_context.trace_id, "032x")
        return "-"

    def __enter__(self) -> Span:
        # 使用 OTel SDK 的 tracer 创建 span（自动处理 context 传播）
        if _tracer is not None:
            self._otel_cm = _tracer.start_as_current_span(self._name)
            self._otel_span = self._otel_cm.__enter__()
        else:
            # 未初始化时使用 NoOp tracer
            self._otel_cm = trace.get_tracer("sagent").start_as_current_span(self._name)
            self._otel_span = self._otel_cm.__enter__()
        # 设置 session_id 属性
        if self._session_id is not None:
            self._otel_span.set_attribute("sagent.session_id", self._session_id)
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        # 有异常时设状态为 error
        if exc_type is not None:
            self._status = "error"

        # 1. 对 agent.react Span：统计 iteration 事件计数并设为属性
        if self._name == "agent.react":
            iteration_count = sum(1 for e in self._events if e.name == "iteration")
            if iteration_count > 0:
                self._attributes["sagent.iteration_count"] = iteration_count

        # 判断是否为根 Span（OTel parent 为 None 或无效）
        otel_parent = getattr(self._otel_span, "parent", None) if self._otel_span else None
        is_root = otel_parent is None or not otel_parent.is_valid

        # 2. 根 Span 退出时：注入 trace 累积值到属性（先记录指标再获取累积）
        # 3. 记录指标（失败不影响主流程）
        #    先记录指标使累积值包含当前 span 的数据，再获取累积值注入属性
        try:
            record_span_metrics(
                name=self._name,
                trace_id=self.trace_id,
                attributes=self._attributes,
                status=self._status,
                start_time="",
                end_time="",
            )
            # 标记此 span 已记录指标，防止 MetricsSpanProcessor.on_end 双写
            if self._otel_span is not None:
                span_id = self._otel_span.get_span_context().span_id
                _metrics_recorded_spans.add(span_id)
        except Exception:
            logger.debug("Span.__exit__ 记录指标失败: span=%s", self._name, exc_info=True)

        # 根 Span 获取累积值注入属性
        if is_root:
            try:
                metrics = get_metrics()
                if metrics is not None:
                    acc = metrics.get_trace_accumulation(self.trace_id)
                    for key, value in acc.items():
                        self._attributes[key] = value
            except Exception:
                logger.debug("获取 trace 累积值失败: span=%s", self._name, exc_info=True)

        # 4. 同步所有 self._attributes 到 OTel span
        if self._otel_span is not None:
            for k, v in self._attributes.items():
                try:
                    self._otel_span.set_attribute(k, v)
                except Exception:
                    logger.debug("设置 OTel span 属性失败: key=%s", k, exc_info=True)
            # 同步事件到 OTel span
            for evt in self._events:
                try:
                    evt_time = datetime.fromisoformat(evt.timestamp)
                    self._otel_span.add_event(evt.name, evt.attributes, evt_time)
                except Exception:
                    try:
                        self._otel_span.add_event(evt.name, evt.attributes)
                    except Exception:
                        logger.debug("添加事件到 OTel span 失败: %s", evt.name, exc_info=True)

        # 5. 设置 OTel span 状态
        if self._otel_span is not None:
            try:
                if self._status == "error":
                    self._otel_span.set_status(Status(StatusCode.ERROR))
                else:
                    self._otel_span.set_status(Status(StatusCode.OK))
            except Exception:
                logger.debug("设置 OTel span 状态失败: span=%s", self._name, exc_info=True)

        # 6. 结束 OTel span（触发 SpanProcessor.on_end -> exporter）
        #    通过 OTel context manager 的 __exit__ 完成 end() 和 context 恢复
        if self._otel_cm is not None:
            try:
                self._otel_cm.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                logger.debug("OTel context manager __exit__ 失败: span=%s", self._name, exc_info=True)

        # 7. 根 Span 退出时：按周期 flush 指标并清除 trace 累积
        if is_root:
            try:
                _maybe_flush_metrics()
            except Exception:
                logger.debug("flush 指标失败: span=%s", self._name, exc_info=True)
            try:
                metrics = get_metrics()
                if metrics is not None:
                    metrics.clear_trace(self.trace_id)
            except Exception:
                logger.debug("清除 trace 累积失败: trace_id=%s", self.trace_id, exc_info=True)

        # 清理已记录 span_id 集合
        if self._otel_span is not None:
            span_id = self._otel_span.get_span_context().span_id
            _metrics_recorded_spans.discard(span_id)

    def set_attribute(self, key: str, value: Any) -> None:
        """设置 Span 属性。"""
        self._attributes[key] = value

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """添加 Span 事件（timestamp 为当前 ISO 格式 UTC）。"""
        event = SpanEvent(
            name=name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            attributes=attributes or {},
        )
        self._events.append(event)

    def set_status(self, status: SpanStatus) -> None:
        """设置 Span 状态。"""
        self._status = status
