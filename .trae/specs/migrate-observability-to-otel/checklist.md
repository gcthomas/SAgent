# Checklist

## 依赖与配置
- [x] `requirements.txt` 包含 `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http` 为必选依赖。
- [x] `pip install -r requirements.txt` 成功安装全部依赖，无缺失。
- [x] `ObservabilityConfig.otlp_enabled` 注释更新为"控制 OTLP 导出器是否加入 provider 链"。
- [x] `config.example.yaml` 和 `config.yaml` 中 OTLP 相关注释更新。
- [x] `otlp.py` 文件已删除，代码中无对 `OTLPExporter` 或 `is_otlp_available` 的引用。

## Span 包装层
- [x] `Span` 类内部使用 OTel SDK `tracer.start_as_current_span()` 创建 Span。
- [x] trace_id 为 OTel 原生 32 字符 hex 格式，span_id 为 16 字符 hex 格式。
- [x] `Span.__exit__` 在调用 `span.end()` **之前**注入 trace 累积值（`sagent.accumulated_*`）和 iteration 计数到 OTel Span 属性。
- [x] `Span` 的 `set_attribute`/`add_event`/`set_status`/`span_id`/`trace_id` 公开接口不变。
- [x] `current_span_id()` / `current_parent_span_id()` 从 OTel context 读取，不在 span 中返回 `-`。
- [x] 根 Span 的 `parent_span_id` 在 `SpanRecord` 中保留 `"-"` 哨兵值。
- [x] `_span_context_var` 和 `SpanContext` 类已删除，无残留引用。
- [x] `setup_observability()` 初始化 OTel `TracerProvider`（含 `Resource`），注册 SpanProcessor 和 SpanExporter。
- [x] `flush_metrics()` 调用 OTel provider 的 `force_flush()`。
- [x] `close_observability()` 调用 OTel provider 的 `shutdown()`。
- [x] `_export_span()` 函数已删除，Span 导出由 OTel processor 链处理。

## 导出器
- [x] `LocalSpanExporter` 实现 OTel `SpanExporter` 接口（`export`/`shutdown`）。
- [x] `LocalSpanExporter` 将 OTel `ReadableSpan` 转换为 `SpanRecord` 后写入本地 JSON Lines 文件。
- [x] 导出前对 `SpanRecord` 属性和事件递归调用 `redact()` 脱敏。
- [x] OTel Span 的 trace_id/span_id（int）正确转换为 `SpanRecord` 的 str 格式（32/16 hex）。
- [x] `_DailyJsonLinesWriter` 保持不变，按天滚动文件格式不变。
- [x] `SpanRecord`/`MetricSnapshot` 模型不变。

## 指标与 SpanProcessor
- [x] `_Histogram` 类保留用于本地 snapshot 存储，同时双写 OTel `Histogram` instrument。
- [x] `MetricsRegistry` 的 counter/histogram 存储采用本地 dict + OTel `Meter` instruments 双写。
- [x] `record_span_metrics` 逻辑在 `Span.__exit__` 和 `MetricsSpanProcessor.on_end` 中调用，覆盖所有 span name 的指标派发。
- [x] `_TraceAccumulation` 及其 `accumulate_*`/`get_trace_accumulation`/`clear_trace` 方法不变。
- [x] `ALLOWED_METRIC_LABELS` 标签过滤逻辑不变。
- [x] `snapshot()` 仍可生成 `MetricSnapshot` 用于本地 metric JSON Lines 导出。

## 日志与公开接口
- [x] `new_trace_id()` 改为生成 32 字符 hex（`uuid.uuid4().hex`）。
- [x] `_TraceIdFilter` 从 OTel context 读取 `trace_id`/`span_id`/`parent_span_id`，不在 span 中时回退到 `_trace_id_var`。
- [x] `__init__.py` 移除 `OTLPExporter` 和 `SpanContext` 导出，新增 `LocalSpanExporter` 和 `MetricsSpanProcessor` 导出。
- [x] 所有调用方代码（`cli/app.py`、`react_engine.py`、`plan_engine.py`、`llm/client.py`、`tools/registry.py`、`context/context_manager.py`、`tools/mcp/session_manager.py`）无需改动。

## 行为一致性
- [x] Span 树结构不变（`agent.run` -> `agent.react` / `gen_ai.chat` / `tool.execute` 等父子关系一致）。
- [x] Span 属性名和值不变（GenAI 语义约定 + `sagent.*` 自定义字段）。
- [x] 本地 JSON Lines 文件格式不变（字段结构一致，ID 长度从 8 变为 32/16 hex）。
- [x] 根 Span 仍注入 `sagent.accumulated_input_tokens`/`accumulated_output_tokens`/`accumulated_cost`/`iteration_count`/`tool_call_count`/`compression_count`。
- [x] 脱敏器覆盖范围不变（13 个 key 子串 + 8 个值正则）。
- [x] 成本估算逻辑不变（`input_price_per_million` / `output_price_per_million`，元/百万 token）。
- [x] 内容采集 AND 逻辑不变（`capture_content AND log_llm_content`）。
- [x] 导出失败不影响 Agent 主流程（非阻塞）。

## 测试与验证
- [x] `tests/unit/test_observability.py` 全部用例通过，覆盖范围不变（配置、Span 上下文、指标、导出、成本计算）。
- [x] `tests/unit/test_redactor.py` 全部用例通过（无需改动）。
- [x] `tests/engines/test_observability_engines.py` 全部用例通过，覆盖 ReAct/Plan trace 树、累积指标。
- [x] `python -m pytest` 全部用例通过，无回归。
