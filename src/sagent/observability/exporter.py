"""本地 JSON Lines 导出器。

提供按天滚动的 JSON Lines 文件写入器与实现 OTel SpanExporter 接口的
LocalSpanExporter，将 SpanData 转换为 SpanRecord 后写入本地文件。
同时保留 MetricSnapshot 的本地导出能力。
导出失败时不影响 Agent 主流程（非阻塞）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from opentelemetry.sdk.trace.export import SpanExportResult

from ..config.models import ObservabilityConfig
from .logging_setup import get_logger
from .models import MetricSnapshot, SpanEvent, SpanRecord
from .redactor import redact

logger = get_logger(__name__)


class _DailyJsonLinesWriter:
    """按天滚动的 JSON Lines 文件写入器。

    每天自动创建新文件，文件名包含日期后缀。
    写入失败时不抛异常（非阻塞）。
    """

    def __init__(self, directory: Path, filename: str) -> None:
        self._directory = directory
        self._filename = filename
        self._current_date: str | None = None
        self._file: Any = None

    def _ensure_open(self) -> None:
        """确保文件打开且日期正确，跨天时自动切换。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._current_date != today:
            if self._file is not None:
                self._file.close()
            self._current_date = today
            # 文件名形如 sagent_trace_2026-08-17.jsonl
            stem = Path(self._filename).stem
            suffix = Path(self._filename).suffix
            path = self._directory / f"{stem}_{today}{suffix}"
            self._file = open(path, "a", encoding="utf-8")

    def write(self, data: dict[str, Any]) -> None:
        """写入一行 JSON。失败时静默。"""
        try:
            self._ensure_open()
            if self._file is not None:
                self._file.write(json.dumps(data, ensure_ascii=False) + "\n")
                self._file.flush()
        except Exception:
            logger.debug("JSON Lines 写入失败", exc_info=True)

    def close(self) -> None:
        """关闭文件。"""
        if self._file is not None:
            self._file.close()
            self._file = None


def _ns_to_iso(ns: int | datetime | None) -> str:
    """将纳秒时间戳或 datetime 转换为 ISO 格式字符串。"""
    if ns is None:
        return ""
    try:
        if isinstance(ns, datetime):
            return ns.isoformat()
        dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def _otel_status_to_str(status: Any) -> str:
    """将 OTel Status 对象映射为本地状态字符串。"""
    if status is None:
        return "ok"
    try:
        from opentelemetry.trace import StatusCode
        if hasattr(status, "status_code") and status.status_code == StatusCode.ERROR:
            return "error"
    except Exception:
        logger.debug("OTel Status 映射失败", exc_info=True)
    return "ok"


def _span_data_to_record(span: Any) -> SpanRecord:
    """将 OTel SpanData（ReadableSpan）转换为 SpanRecord。

    参数:
        span: OTel ReadableSpan 对象。

    返回:
        转换后的 SpanRecord，属性和事件经脱敏处理。
    """
    span_context = span.get_span_context()
    trace_id = format(span_context.trace_id, "032x")
    span_id = format(span_context.span_id, "016x")

    # parent_span_id：parent 为 None 或无效时返回 "-"
    parent_span_id = "-"
    parent = getattr(span, "parent", None)
    if parent is not None:
        try:
            parent_span_id = format(parent.span_id, "016x")
        except (AttributeError, ValueError):
            parent_span_id = "-"

    # 时间转换
    start_time = _ns_to_iso(span.start_time)
    end_time = _ns_to_iso(span.end_time)

    # 状态
    status = _otel_status_to_str(span.status)

    # 属性（脱敏）
    raw_attrs = dict(span.attributes) if span.attributes else {}
    attributes = redact(raw_attrs)

    # 事件（脱敏）
    events: list[SpanEvent] = []
    if span.events:
        for evt in span.events:
            evt_attrs = redact(dict(evt.attributes)) if evt.attributes else {}
            events.append(
                SpanEvent(
                    name=evt.name,
                    timestamp=_ns_to_iso(evt.timestamp),
                    attributes=evt_attrs,
                )
            )

    # session_id 从属性中提取（如果有）
    session_id = attributes.get("sagent.session_id") if isinstance(attributes, dict) else None

    return SpanRecord(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=span.name,
        start_time=start_time,
        end_time=end_time,
        status=status,
        attributes=attributes,
        events=events,
        session_id=session_id,
    )


class LocalSpanExporter:
    """本地 JSON Lines Span 导出器。

    实现 OTel SpanExporter 接口，将 SpanData 转换为 SpanRecord
    并写入按天滚动的 JSON Lines 文件。
    导出失败时不影响 Agent 主流程。
    """

    def __init__(self, config: ObservabilityConfig) -> None:
        self._config = config
        self._enabled = config.enabled
        self._trace_writer: _DailyJsonLinesWriter | None = None
        self._metric_writer: _DailyJsonLinesWriter | None = None
        self._last_metric_data: dict[str, Any] | None = None
        if self._enabled:
            trace_dir = Path(config.trace_dir)
            trace_dir.mkdir(parents=True, exist_ok=True)
            self._trace_writer = _DailyJsonLinesWriter(trace_dir, config.trace_file)
            self._metric_writer = _DailyJsonLinesWriter(trace_dir, config.metric_file)

    def export(self, spans: Sequence[Any]) -> SpanExportResult:
        """导出一批 Span 到本地 JSON Lines 文件。

        参数:
            spans: OTel SpanData 序列。

        返回:
            SpanExportResult.SUCCESS。
        """
        if not self._enabled or self._trace_writer is None:
            return SpanExportResult.SUCCESS
        for span in spans:
            try:
                record = _span_data_to_record(span)
                self._trace_writer.write(record.model_dump())
            except Exception:
                # 单条 Span 导出失败不影响其他 Span
                pass
        return SpanExportResult.SUCCESS

    def export_metric(self, snapshot: MetricSnapshot) -> None:
        """导出指标快照到本地 JSON Lines 文件。

        指标内容与上次完全相同时跳过写入，避免重复行。
        """
        if not self._enabled or self._metric_writer is None:
            return
        if snapshot.metrics == self._last_metric_data:
            return
        self._last_metric_data = snapshot.metrics
        self._metric_writer.write(snapshot.model_dump())

    def shutdown(self) -> None:
        """关闭写入器。"""
        if self._trace_writer is not None:
            self._trace_writer.close()
        if self._metric_writer is not None:
            self._metric_writer.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """强制 flush（文件写入已即时 flush，无需额外操作）。"""
        return True
