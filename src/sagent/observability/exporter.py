"""本地 JSON Lines 导出器。

提供按天滚动的 JSON Lines 文件写入器与本地导出器，将 SpanRecord 和
MetricSnapshot 写入本地文件。导出失败时不影响 Agent 主流程（非阻塞）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config.models import ObservabilityConfig
from .models import MetricSnapshot, SpanRecord


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
            pass  # 非阻塞

    def close(self) -> None:
        """关闭文件。"""
        if self._file is not None:
            self._file.close()
            self._file = None


class LocalExporter:
    """本地 JSON Lines 导出器。

    将 SpanRecord 和 MetricSnapshot 写入按天滚动的 JSON Lines 文件。
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

    def export_span(self, record: SpanRecord) -> None:
        """导出 Span 记录到本地 JSON Lines 文件。"""
        if not self._enabled or self._trace_writer is None:
            return
        self._trace_writer.write(record.model_dump())

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

    def close(self) -> None:
        """关闭写入器。"""
        if self._trace_writer is not None:
            self._trace_writer.close()
        if self._metric_writer is not None:
            self._metric_writer.close()
