"""指标聚合系统。

提供进程内计数器、直方图和按 trace 累积的 token/成本聚合。
指标标签仅使用 ALLOWED_METRIC_LABELS 中的低基数维度。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config.models import ObservabilityConfig
from .cost import calculate_cost
from .logging_setup import get_logger
from .models import ALLOWED_METRIC_LABELS, MetricSnapshot

logger = get_logger(__name__)


def _filter_labels(labels: dict[str, str] | None) -> dict[str, str]:
    """过滤标签，只保留 ALLOWED_METRIC_LABELS 中的键。"""
    if not labels:
        return {}
    return {k: str(v) for k, v in labels.items() if k in ALLOWED_METRIC_LABELS}


def _parse_iso_time(ts: str) -> datetime | None:
    """解析 ISO 格式时间字符串，失败时返回 None。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _latency_ms(start_time: str, end_time: str) -> float | None:
    """计算两个 ISO 时间戳之间的延迟（毫秒）。"""
    start = _parse_iso_time(start_time)
    end = _parse_iso_time(end_time)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds() * 1000
    return round(delta, 1)


class _Histogram:
    """直方图聚合器。

    记录 count、sum、min、max、avg 统计值。
    """

    def __init__(self) -> None:
        self._count: int = 0
        self._sum: float = 0.0
        self._min: float | None = None
        self._max: float | None = None

    def record(self, value: float) -> None:
        """记录一个值。"""
        self._count += 1
        self._sum += value
        if self._min is None or value < self._min:
            self._min = value
        if self._max is None or value > self._max:
            self._max = value

    def snapshot(self) -> dict[str, Any]:
        """返回当前直方图统计摘要。"""
        avg = self._sum / self._count if self._count > 0 else 0.0
        return {
            "count": self._count,
            "sum": round(self._sum, 6),
            "min": round(self._min, 6) if self._min is not None else 0.0,
            "max": round(self._max, 6) if self._max is not None else 0.0,
            "avg": round(avg, 6),
        }


class _TraceAccumulation:
    """按 trace 累积的 token/成本/迭代/工具/压缩数据。"""

    def __init__(self) -> None:
        self.input_tokens: float = 0.0
        self.output_tokens: float = 0.0
        self.cost: float | None = None
        self.iterations: float = 0.0
        self.tool_calls: float = 0.0
        self.compressions: float = 0.0


class MetricsRegistry:
    """指标注册表。

    管理计数器、直方图和按 trace 累积的数据，提供快照生成与重置。
    """

    def __init__(self, config: ObservabilityConfig | None = None) -> None:
        self._config = config
        # 计数器：name -> labels_key -> value
        self._counters: dict[str, dict[str, float]] = {}
        # 直方图：name -> labels_key -> _Histogram
        self._histograms: dict[str, dict[str, _Histogram]] = {}
        # 按 trace 累积
        self._trace_acc: dict[str, _TraceAccumulation] = {}

    def _labels_key(self, labels: dict[str, str]) -> str:
        """将标签字典转换为稳定的字符串键。"""
        if not labels:
            return ""
        return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def increment_counter(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        """增加计数器。

        参数:
            name: 计数器名称。
            labels: 标签字典（仅保留 ALLOWED_METRIC_LABELS 中的键）。
            value: 增量值。
        """
        filtered = _filter_labels(labels)
        key = self._labels_key(filtered)
        if name not in self._counters:
            self._counters[name] = {}
        self._counters[name][key] = self._counters[name].get(key, 0.0) + value

    def record_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """记录直方图值。

        参数:
            name: 直方图名称。
            value: 记录值。
            labels: 标签字典（仅保留 ALLOWED_METRIC_LABELS 中的键）。
        """
        filtered = _filter_labels(labels)
        key = self._labels_key(filtered)
        if name not in self._histograms:
            self._histograms[name] = {}
        if key not in self._histograms[name]:
            self._histograms[name][key] = _Histogram()
        self._histograms[name][key].record(value)

    def accumulate_tokens(
        self,
        trace_id: str,
        input_tokens: float = 0.0,
        output_tokens: float = 0.0,
        cost: float | None = None,
    ) -> None:
        """按 trace 累积 token 和成本。

        参数:
            trace_id: Trace ID。
            input_tokens: 输入 token 增量。
            output_tokens: 输出 token 增量。
            cost: 成本增量，为 None 时不累积成本。
        """
        acc = self._trace_acc.setdefault(trace_id, _TraceAccumulation())
        acc.input_tokens += input_tokens
        acc.output_tokens += output_tokens
        if cost is not None:
            acc.cost = (acc.cost or 0.0) + cost

    def accumulate_iteration(self, trace_id: str) -> None:
        """按 trace 累积迭代次数。"""
        acc = self._trace_acc.setdefault(trace_id, _TraceAccumulation())
        acc.iterations += 1

    def accumulate_tool_call(self, trace_id: str) -> None:
        """按 trace 累积工具调用次数。"""
        acc = self._trace_acc.setdefault(trace_id, _TraceAccumulation())
        acc.tool_calls += 1

    def accumulate_compression(self, trace_id: str) -> None:
        """按 trace 累积压缩次数。"""
        acc = self._trace_acc.setdefault(trace_id, _TraceAccumulation())
        acc.compressions += 1

    def get_trace_accumulation(self, trace_id: str) -> dict[str, float]:
        """获取 trace 累积值。

        参数:
            trace_id: Trace ID。

        返回:
            包含累积值的字典，键为 sagent.accumulated_* 形式。
        """
        acc = self._trace_acc.get(trace_id)
        if acc is None:
            return {}
        result: dict[str, float] = {
            "sagent.accumulated_input_tokens": acc.input_tokens,
            "sagent.accumulated_output_tokens": acc.output_tokens,
            "sagent.iteration_count": acc.iterations,
            "sagent.tool_call_count": acc.tool_calls,
            "sagent.compression_count": acc.compressions,
        }
        if acc.cost is not None:
            result["sagent.accumulated_cost"] = acc.cost
        return result

    def clear_trace(self, trace_id: str) -> None:
        """清除 trace 累积。"""
        self._trace_acc.pop(trace_id, None)

    def snapshot(self) -> MetricSnapshot:
        """生成指标快照。"""
        metrics: dict[str, Any] = {}
        # 计数器
        for name, label_map in self._counters.items():
            metrics[name] = {
                key: {"value": val} for key, val in label_map.items()
            }
        # 直方图
        for name, label_map in self._histograms.items():
            if name not in metrics:
                metrics[name] = {}
            for key, hist in label_map.items():
                metrics[name][key] = hist.snapshot()
        return MetricSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            labels={},
        )

    def reset(self) -> None:
        """重置所有指标。"""
        self._counters.clear()
        self._histograms.clear()
        self._trace_acc.clear()


# 模块级单例
_metrics: MetricsRegistry | None = None


def setup_metrics(config: ObservabilityConfig) -> None:
    """初始化指标注册表单例。

    参数:
        config: 可观测性配置。
    """
    global _metrics
    _metrics = MetricsRegistry(config)


def get_metrics() -> MetricsRegistry | None:
    """获取指标注册表单例。"""
    return _metrics


def record_span_metrics(
    name: str,
    trace_id: str,
    attributes: dict[str, Any],
    status: str,
    start_time: str,
    end_time: str,
) -> None:
    """根据 Span 名称和属性记录对应指标。

    参数:
        name: Span 名称。
        trace_id: Trace ID。
        attributes: Span 属性。
        status: Span 状态（ok/error）。
        start_time: ISO 格式开始时间。
        end_time: ISO 格式结束时间。
    """
    if _metrics is None:
        return
    latency = _latency_ms(start_time, end_time)

    if name == "agent.run":
        mode = str(attributes.get("sagent.mode", ""))
        outcome = str(attributes.get("sagent.outcome", ""))
        _metrics.increment_counter(
            "agent_runs_total",
            {"mode": mode, "outcome": outcome},
        )
        if latency is not None:
            _metrics.record_histogram("agent_latency_ms", latency, {"mode": mode})

    elif name == "gen_ai.chat":
        model = str(attributes.get("gen_ai.request.model", ""))
        provider = str(attributes.get("gen_ai.provider.name", ""))
        input_tokens = attributes.get("gen_ai.usage.input_tokens")
        output_tokens = attributes.get("gen_ai.usage.output_tokens")
        _metrics.increment_counter(
            "llm_calls_total", {"model": model, "provider": provider}
        )
        if latency is not None:
            _metrics.record_histogram("llm_latency_ms", latency, {"model": model})
        if input_tokens is not None:
            _metrics.record_histogram(
                "llm_input_tokens", float(input_tokens), {"model": model}
            )
        if output_tokens is not None:
            _metrics.record_histogram(
                "llm_output_tokens", float(output_tokens), {"model": model}
            )
        # 成本估算（使用模块级价格表，由 setup_cost_estimator 初始化）
        cost = calculate_cost(model, input_tokens, output_tokens)
        if cost is not None:
            _metrics.record_histogram("llm_cost_estimated", cost, {"model": model})
        # 累积 token 和成本
        _metrics.accumulate_tokens(
            trace_id,
            input_tokens=float(input_tokens) if input_tokens is not None else 0.0,
            output_tokens=float(output_tokens) if output_tokens is not None else 0.0,
            cost=cost,
        )
        # LLM 调用出错时记录错误计数器
        if status == "error":
            _metrics.increment_counter("llm_errors_total", {"model": model})

    elif name == "tool.execute":
        tool_name = str(attributes.get("tool.name", ""))
        _metrics.increment_counter("tool_calls_total", {"tool_name": tool_name})
        if latency is not None:
            _metrics.record_histogram("tool_latency_ms", latency, {"tool_name": tool_name})
        if status == "error":
            _metrics.increment_counter("tool_errors_total", {"tool_name": tool_name})
        _metrics.accumulate_tool_call(trace_id)

    elif name == "context.compress":
        layer = str(attributes.get("sagent.compression.layer", ""))
        before_tokens = attributes.get("sagent.compression.before_tokens")
        after_tokens = attributes.get("sagent.compression.after_tokens")
        _metrics.increment_counter(
            "compressions_total", {"compression_layer": layer}
        )
        if before_tokens is not None:
            _metrics.record_histogram(
                "compression_before_tokens", float(before_tokens)
            )
        if after_tokens is not None:
            _metrics.record_histogram(
                "compression_after_tokens", float(after_tokens)
            )
        _metrics.accumulate_compression(trace_id)

    elif name == "agent.react":
        # iteration 事件计数由 context.py 在 __exit__ 中提前设为属性
        # sagent.iteration_count，此处读取并累积到 trace accumulator
        iteration_count = attributes.get("sagent.iteration_count")
        if iteration_count is not None and isinstance(iteration_count, (int, float)):
            for _ in range(int(iteration_count)):
                _metrics.accumulate_iteration(trace_id)
        # 达到最大迭代次数时记录计数器
        outcome = attributes.get("sagent.outcome")
        if outcome == "max_iterations":
            _metrics.increment_counter("max_iterations_total")

    elif name == "agent.finalize":
        # 达到最大迭代次数时的最终化 Span
        _metrics.increment_counter("max_iterations_total")

    elif name == "mcp.tool_call":
        tool_name = str(attributes.get("mcp.tool.name", ""))
        _metrics.increment_counter("mcp_calls_total", {"tool_name": tool_name})
        if latency is not None:
            _metrics.record_histogram("mcp_latency_ms", latency, {"tool_name": tool_name})
        if status == "error":
            _metrics.increment_counter("mcp_errors_total", {"tool_name": tool_name})
        _metrics.accumulate_tool_call(trace_id)


def flush_metrics() -> None:
    """生成指标快照并通过 LocalExporter 导出，然后重置指标。"""
    if _metrics is None:
        return
    try:
        snapshot = _metrics.snapshot()
        from .context import _exporter

        if _exporter is not None:
            _exporter.export_metric(snapshot)
    except Exception:
        logger.exception("指标 flush 失败", extra={"event": "metrics_flush_error"})
