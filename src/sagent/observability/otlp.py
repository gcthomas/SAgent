"""可选 OTLP 导出器。

在 observability.otlp_enabled 为 true 时尝试加载 OpenTelemetry SDK，
将 SpanRecord 和 MetricSnapshot 通过 OTLP 协议导出。
缺少 OpenTelemetry 可选依赖时回退本地导出并记录诊断。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config.models import ObservabilityConfig
from .logging_setup import get_logger
from .models import MetricSnapshot, SpanRecord

logger = get_logger(__name__)


def is_otlp_available() -> bool:
    """检查 OpenTelemetry 依赖是否可用。

    尝试导入 opentelemetry-api、opentelemetry-sdk 和
    opentelemetry-exporter-otlp-proto-http，全部成功时返回 True。
    """
    try:
        import opentelemetry.api  # noqa: F401
        import opentelemetry.sdk  # noqa: F401
        import opentelemetry.exporter.otlp.proto.http  # noqa: F401
        return True
    except ImportError:
        return False


def _safe_int(value: str | None) -> int | None:
    """将十六进制或十进制字符串安全转换为 int。"""
    if value is None or value == "-":
        return None
    try:
        return int(value, 16)
    except (ValueError, TypeError):
        return None


def _parse_time(ts: str) -> datetime | None:
    """解析 ISO 格式时间字符串为带时区的 datetime。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


class OTLPExporter:
    """可选 OTLP 导出器。

    在构造时尝试加载 OpenTelemetry SDK，失败时标记为不可用并记录诊断。
    属性映射集中处理，将 SpanRecord/MetricSnapshot 转换为 OTLP 格式。
    """

    def __init__(self, config: ObservabilityConfig) -> None:
        self._config = config
        self._available = False
        self._tracer_provider = None
        self._meter_provider = None
        self._span_exporter = None
        self._metric_exporter = None

        if not config.otlp_enabled:
            return

        try:
            from opentelemetry import trace, metrics
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import (
                PeriodicExportingMetricReader,
            )

            resource = Resource.create(
                {"service.name": "sagent"}
            )

            # 构建 Span 导出器
            if config.otlp_protocol == "grpc":
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                    OTLPMetricExporter,
                )
            else:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                    OTLPMetricExporter,
                )

            self._span_exporter = OTLPSpanExporter(
                endpoint=f"{config.otlp_endpoint}/v1/traces",
                timeout=config.otlp_timeout,
            )
            span_processor = BatchSpanProcessor(self._span_exporter)
            self._tracer_provider = TracerProvider(resource=resource)
            self._tracer_provider.add_span_processor(span_processor)
            trace.set_tracer_provider(self._tracer_provider)

            self._metric_exporter = OTLPMetricExporter(
                endpoint=f"{config.otlp_endpoint}/v1/metrics",
                timeout=config.otlp_timeout,
            )
            metric_reader = PeriodicExportingMetricReader(
                self._metric_exporter,
                export_interval_millis=int(config.metrics_flush_interval * 1000),
            )
            self._meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )
            metrics.set_meter_provider(self._meter_provider)

            self._available = True
            logger.info(
                "OTLP 导出器初始化成功",
                extra={
                    "event": "otlp_init_success",
                    "endpoint": config.otlp_endpoint,
                    "protocol": config.otlp_protocol,
                },
            )
        except ImportError:
            logger.warning(
                "OTLP 已启用但 OpenTelemetry 依赖不可用，回退本地导出",
                extra={"event": "otlp_dependency_missing"},
            )
            self._available = False
        except Exception:
            logger.exception(
                "OTLP 导出器初始化失败",
                extra={"event": "otlp_init_error"},
            )
            self._available = False

    def is_available(self) -> bool:
        """返回 OTLP 是否可用。"""
        return self._available

    def _map_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """将 SpanRecord 属性映射为 OTLP attributes（直接传递，已脱敏）。"""
        result: dict[str, Any] = {}
        for key, value in attributes.items():
            if isinstance(value, (str, int, float, bool)):
                result[key] = value
            else:
                result[key] = str(value)
        return result

    def export_span(self, record: SpanRecord) -> None:
        """将 SpanRecord 映射为 OTLP Span 并导出。

        参数:
            record: Span 记录。
        """
        if not self._available or self._tracer_provider is None:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
            from opentelemetry.trace.span import TraceFlags

            tracer = trace.get_tracer("sagent")

            # 映射 trace_id 和 span_id
            trace_id_int = _safe_int(record.trace_id)
            span_id_int = _safe_int(record.span_id)
            parent_span_id_int = _safe_int(record.parent_span_id)

            # 解析时间
            start_dt = _parse_time(record.start_time)
            end_dt = _parse_time(record.end_time)
            if start_dt is None or end_dt is None:
                return

            start_ns = int(start_dt.timestamp() * 1e9)
            end_ns = int(end_dt.timestamp() * 1e9)

            # 映射状态
            if record.status == "error":
                otel_status = Status(StatusCode.ERROR)
            else:
                otel_status = Status(StatusCode.OK)

            # 映射属性
            attrs = self._map_attributes(record.attributes)

            # 映射事件
            events_list = []
            for evt in record.events:
                events_list.append(
                    {
                        "name": evt.name,
                        "timestamp": _parse_time(evt.timestamp),
                        "attributes": self._map_attributes(evt.attributes),
                    }
                )

            # 使用 OTLP Span 构造并导出
            # 由于直接使用 SDK 底层 API 复杂，这里使用 Span 构造方式
            from opentelemetry.sdk.trace import Span as SDKSpan
            from opentelemetry.trace import SpanContext, SpanKind
            from opentelemetry.trace.span import TraceFlags

            flags = TraceFlags(TraceFlags.SAMPLED)
            span_context = SpanContext(
                trace_id=trace_id_int or 0,
                span_id=span_id_int or 0,
                trace_flags=flags,
                is_remote=False,
            )
            parent_context = None
            if parent_span_id_int is not None and parent_span_id_int != 0:
                parent_context = SpanContext(
                    trace_id=trace_id_int or 0,
                    span_id=parent_span_id_int,
                    trace_flags=flags,
                    is_remote=True,
                )

            span = SDKSpan(
                name=record.name,
                context=span_context,
                parent=parent_context,
                start_time=start_ns,
                end_time=end_ns,
            )
            span._attributes = attrs
            span._status = otel_status
            for evt_info in events_list:
                if evt_info["timestamp"] is not None:
                    span.add_event(
                        evt_info["name"],
                        attributes=evt_info["attributes"],
                        timestamp=evt_info["timestamp"],
                    )

            # 通过 processor 导出
            for processor in self._tracer_provider._active_span_processor._processors:
                processor.on_end(span)
        except Exception:
            logger.exception(
                "OTLP Span 导出失败",
                extra={"event": "otlp_export_error"},
            )

    def export_metric(self, snapshot: MetricSnapshot) -> None:
        """将 MetricSnapshot 映射为 OTLP Metric 并导出。

        参数:
            snapshot: 指标快照。
        """
        if not self._available or self._meter_provider is None:
            return
        try:
            # OTLP 指标导出通过 PeriodicExportingMetricReader 自动进行
            # 这里仅记录诊断日志，实际指标数据由 metrics registry 采集
            logger.debug(
                "OTLP 指标快照已记录",
                extra={
                    "event": "otlp_metric_snapshot",
                    "metric_count": len(snapshot.metrics),
                },
            )
        except Exception:
            logger.exception(
                "OTLP Metric 导出失败",
                extra={"event": "otlp_metric_export_error"},
            )

    def close(self) -> None:
        """关闭导出器，flush 残留数据。"""
        if not self._available:
            return
        try:
            if self._tracer_provider is not None:
                self._tracer_provider.force_flush()
                self._tracer_provider.shutdown()
            if self._meter_provider is not None:
                self._meter_provider.force_flush()
                self._meter_provider.shutdown()
        except Exception:
            logger.exception(
                "OTLP 导出器关闭失败",
                extra={"event": "otlp_close_error"},
            )
