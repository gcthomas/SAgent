"""Span 上下文与本地导出。

基于 contextvars 实现的 trace/span 上下文管理，提供 Span 上下文管理器，
在 Span 退出时构造 SpanRecord 并通过导出回调写入本地文件。
导出失败不影响 Agent 主流程。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from ..config.models import ObservabilityConfig
from .cost import setup_cost_estimator
from .exporter import LocalExporter
from .logging_setup import get_logger
from .models import SpanEvent, SpanRecord, SpanStatus
from .metrics import MetricsRegistry, get_metrics, record_span_metrics, setup_metrics
from .otlp import OTLPExporter

logger = get_logger(__name__)


class SpanContext:
    """当前 Span 上下文。"""

    def __init__(self, trace_id: str, span_id: str, parent_span_id: str) -> None:
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id


# 当前 Span 上下文（跨函数调用共享）
_span_context_var: ContextVar[SpanContext | None] = ContextVar(
    "span_context", default=None
)

# 模块级本地导出器（在 setup_observability 中设置）
_exporter: LocalExporter | None = None
# 模块级 OTLP 导出器（在 setup_observability 中设置）
_otlp_exporter: OTLPExporter | None = None
# 模块级可观测性配置（在 setup_observability 中设置）
_obs_config: ObservabilityConfig | None = None
# 上次指标 flush 时间（用于周期性 flush 判断）
_last_metric_flush: datetime | None = None


def current_span_context() -> SpanContext | None:
    """获取当前 span 上下文。"""
    return _span_context_var.get()


def current_span_id() -> str:
    """当前 span_id，不在 span 中返回 '-'。"""
    ctx = _span_context_var.get()
    if ctx is not None:
        return ctx.span_id
    return "-"


def current_parent_span_id() -> str:
    """当前 parent_span_id，不在 span 中返回 '-'。"""
    ctx = _span_context_var.get()
    if ctx is not None:
        return ctx.parent_span_id
    return "-"


def _export_span(record: SpanRecord) -> None:
    """导出 Span 记录到本地文件和可选 OTLP。"""
    if _exporter is not None:
        _exporter.export_span(record)
    if _otlp_exporter is not None and _otlp_exporter.is_available():
        try:
            _otlp_exporter.export_span(record)
        except Exception:
            logger.exception("OTLP Span 导出失败", extra={"event": "otlp_export_error"})


def setup_observability(config: ObservabilityConfig) -> None:
    """初始化可观测性子系统。

    初始化本地导出器、可选 OTLP 导出器和指标注册表。
    如果 enable_openai_auto_instrumentation 为 true 且 observability.enabled 也为 true，
    记录警告日志说明首期不允许同时启用，实际不启用自动埋点。

    参数:
        config: 可观测性配置。
    """
    global _exporter, _otlp_exporter, _obs_config, _last_metric_flush
    _obs_config = config
    # 初始化成本估算器（设置模块级价格表）
    setup_cost_estimator(config.model_pricing)
    # 初始化本地导出器
    _exporter = LocalExporter(config)
    # 初始化指标注册表
    setup_metrics(config)
    # 重置上次 flush 时间
    _last_metric_flush = None
    # 初始化 OTLP 导出器（如果启用）
    if config.otlp_enabled:
        _otlp_exporter = OTLPExporter(config)
        if not _otlp_exporter.is_available():
            logger.warning(
                "OTLP 已启用但不可用，回退本地导出",
                extra={"event": "otlp_fallback_local"},
            )
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
    if metrics is None or _exporter is None:
        return
    now = datetime.now(timezone.utc)
    if _last_metric_flush is None or (
        now - _last_metric_flush
    ).total_seconds() >= _obs_config.metrics_flush_interval:
        snapshot = metrics.snapshot()
        _exporter.export_metric(snapshot)
        if _otlp_exporter is not None:
            try:
                _otlp_exporter.export_metric(snapshot)
            except Exception:
                logger.exception(
                    "OTLP 指标导出失败",
                    extra={"event": "otlp_metric_export_error"},
                )
        _last_metric_flush = now


def flush_metrics() -> None:
    """强制 flush 当前指标快照到本地和 OTLP 导出器。"""
    global _last_metric_flush
    metrics = get_metrics()
    if metrics is None or _exporter is None:
        return
    snapshot = metrics.snapshot()
    _exporter.export_metric(snapshot)
    if _otlp_exporter is not None:
        try:
            _otlp_exporter.export_metric(snapshot)
        except Exception:
            logger.exception(
                "OTLP 指标导出失败",
                extra={"event": "otlp_metric_export_error"},
            )
    _last_metric_flush = datetime.now(timezone.utc)


def close_observability() -> None:
    """关闭所有导出器（本地与 OTLP），flush 拦留数据。"""
    global _exporter, _otlp_exporter
    if _otlp_exporter is not None:
        try:
            _otlp_exporter.close()
        except Exception:
            logger.exception(
                "OTLP 导出器关闭失败",
                extra={"event": "otlp_close_error"},
            )
    if _exporter is not None:
        _exporter.close()


class Span:
    """Span 上下文管理器。

    创建时生成新的 span_id，继承或生成 trace_id，记录父 span_id。
    退出时构造 SpanRecord 并通过导出回调写入。
    导出失败不影响 Agent 主流程。
    """

    def __init__(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        # 确定 trace_id：参数优先，其次继承当前上下文，最后生成新值
        if trace_id is not None:
            self._trace_id = trace_id
        else:
            ctx = _span_context_var.get()
            if ctx is not None:
                self._trace_id = ctx.trace_id
            else:
                self._trace_id = uuid.uuid4().hex[:8]

        # 确定 parent_span_id：参数优先，其次继承当前上下文 span_id，最后 '-'（根 Span）
        if parent_span_id is not None:
            self._parent_span_id = parent_span_id
        else:
            ctx = _span_context_var.get()
            if ctx is not None:
                self._parent_span_id = ctx.span_id
            else:
                self._parent_span_id = "-"

        # 生成新 span_id
        self._span_id = uuid.uuid4().hex[:8]

        self._name = name
        self._session_id = session_id
        self._attributes: dict[str, Any] = {}
        self._events: list[SpanEvent] = []
        self._status: SpanStatus = "ok"
        self._start_time: str | None = None
        self._end_time: str | None = None
        self._token: Any = None

    @property
    def span_id(self) -> str:
        """当前 Span 的 span_id（只读）。"""
        return self._span_id

    @property
    def trace_id(self) -> str:
        """当前 Span 的 trace_id（只读）。"""
        return self._trace_id

    def __enter__(self) -> Span:
        # 记录开始时间（ISO 格式 UTC）
        self._start_time = datetime.now(timezone.utc).isoformat()
        # 设置 ContextVar
        ctx = SpanContext(
            trace_id=self._trace_id,
            span_id=self._span_id,
            parent_span_id=self._parent_span_id,
        )
        self._token = _span_context_var.set(ctx)
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        # 记录结束时间
        self._end_time = datetime.now(timezone.utc).isoformat()
        # 有异常时设状态为 error
        if exc_type is not None:
            self._status = "error"
        # 恢复 ContextVar
        if self._token is not None:
            _span_context_var.reset(self._token)
            self._token = None

        # 对 agent.react Span：统计 iteration 事件计数并设为属性
        if self._name == "agent.react":
            iteration_count = sum(1 for e in self._events if e.name == "iteration")
            if iteration_count > 0:
                self._attributes["sagent.iteration_count"] = iteration_count

        # 记录指标（失败不影响主流程）
        try:
            record_span_metrics(
                name=self._name,
                trace_id=self._trace_id,
                attributes=self._attributes,
                status=self._status,
                start_time=self._start_time or "",
                end_time=self._end_time or "",
            )
        except Exception:
            pass

        # 根 Span 退出时：注入 trace 累积值到属性
        is_root = self._parent_span_id == "-"
        if is_root:
            try:
                metrics = get_metrics()
                if metrics is not None:
                    acc = metrics.get_trace_accumulation(self._trace_id)
                    for key, value in acc.items():
                        self._attributes[key] = value
            except Exception:
                pass

        # 构造 SpanRecord 并导出，失败时不影响主流程
        try:
            record = SpanRecord(
                trace_id=self._trace_id,
                span_id=self._span_id,
                parent_span_id=self._parent_span_id,
                name=self._name,
                start_time=self._start_time or "",
                end_time=self._end_time or "",
                status=self._status,
                attributes=self._attributes,
                events=self._events,
                session_id=self._session_id,
            )
            _export_span(record)
        except Exception:
            pass  # 导出失败不影响主流程

        # 根 Span 退出时：按周期 flush 指标并清除 trace 累积
        if is_root:
            try:
                _maybe_flush_metrics()
            except Exception:
                pass
            try:
                metrics = get_metrics()
                if metrics is not None:
                    metrics.clear_trace(self._trace_id)
            except Exception:
                pass

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
