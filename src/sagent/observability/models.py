"""可观测性数据模型。

定义内部 Span、Span 事件、指标快照与导出记录的类型，供可观测性子系统
在运行时构造、传递与导出。这些模型为纯数据载体，不依赖 OpenTelemetry 包。
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field

# Span 状态：成功或失败
SpanStatus = Literal["ok", "error"]

# 指标标签白名单：固定的低基数维度，防止高基数标签导致指标膨胀
ALLOWED_METRIC_LABELS: frozenset[str] = frozenset(
    {
        "mode",
        "operation",
        "model",
        "provider",
        "tool_name",
        "outcome",
        "compression_layer",
    }
)

# 指标标签集合类型（用于类型提示，键仅允许取自 ALLOWED_METRIC_LABELS）
MetricLabelSet = dict[str, str]


class SpanEvent(BaseModel):
    """Span 事件。

    记录 Span 生命周期内发生的离散事件，属性需已脱敏。

    参数:
        name: 事件名称
        timestamp: ISO 格式时间戳
        attributes: 已脱敏的事件属性

    """

    # 事件名称
    name: str
    # ISO 格式时间戳
    timestamp: str
    # 已脱敏的事件属性
    attributes: dict[str, Any] = Field(default_factory=dict)


class SpanRecord(BaseModel):
    """Span 记录。

    导出到 JSON Lines 的完整 Span，属性与事件均需已脱敏。

    参数:
        trace_id: Trace ID
        span_id: Span ID
        parent_span_id: 父 Span ID，根 Span 为 "-"
        name: Span 名称（如 agent.run、gen_ai.chat）
        start_time: ISO 格式开始时间
        end_time: ISO 格式结束时间
        status: Span 状态（SpanStatus 值：ok / error）
        attributes: 已脱敏的 Span 属性
        events: Span 事件列表
        session_id: 会话 ID（可选，仅在会话上下文可用时填充）

    """

    # Trace ID
    trace_id: str
    # Span ID
    span_id: str
    # 父 Span ID，根 Span 为 "-"
    parent_span_id: str
    # Span 名称（如 agent.run、gen_ai.chat）
    name: str
    # ISO 格式开始时间
    start_time: str
    # ISO 格式结束时间
    end_time: str
    # Span 状态：SpanStatus 值
    status: str
    # 已脱敏的 Span 属性
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Span 事件列表
    events: list[SpanEvent] = Field(default_factory=list)
    # 会话 ID（可选，仅在会话上下文可用时填充）
    session_id: str | None = None


class MetricSnapshot(BaseModel):
    """指标快照。

    周期性导出到 JSON Lines，metrics 包含计数器值与直方图摘要等。

    参数:
        timestamp: ISO 格式时间戳
        metrics: 指标数据（计数器值、直方图摘要等）
        labels: 低基数标签（只使用 ALLOWED_METRIC_LABELS 中的键）

    """

    # ISO 格式时间戳
    timestamp: str
    # 指标数据（计数器值、直方图摘要等）
    metrics: dict[str, Any] = Field(default_factory=dict)
    # 低基数标签（只使用 ALLOWED_METRIC_LABELS 中的键）
    labels: dict[str, str] = Field(default_factory=dict)


# 可导出的记录：SpanRecord 或 MetricSnapshot
ExportRecord = Union[SpanRecord, MetricSnapshot]
