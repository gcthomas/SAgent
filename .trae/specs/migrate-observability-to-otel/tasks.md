# Tasks

- [x] Task 1: 添加 OTel SDK 必选依赖并更新配置
  - [ ] 在 `requirements.txt` 新增 `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`
  - [ ] 运行 `pip install -r requirements.txt` 确认依赖安装成功
  - [ ] 更新 `config/models.py` `ObservabilityConfig`：保留 `otlp_enabled` 字段，注释更新为"控制 OTLP 导出器是否加入 provider 链"
  - [ ] 更新 `config.example.yaml` 和 `config.yaml` 中 `otlp_enabled` 的注释说明

- [x] Task 2: 重写 Span 包装层和上下文传播
  - [ ] 重写 `context.py` `Span` 类：内部用 `tracer.start_span()` 创建 OTel SDK Span，trace_id 为 32 字符 hex、span_id 为 16 字符 hex（OTel 原生格式）
  - [ ] 在 `Span.__exit__` 中 `span.end()` 调用**之前**注入 trace 累积值和 iteration 计数，设置到 OTel Span 属性上
  - [ ] 删除 `_span_context_var` 和 `SpanContext` 类 — 改用 OTel SDK 的 `context` / `trace` API
  - [ ] 更新 `current_span_id()` / `current_parent_span_id()`：从 OTel context 读取当前 span，返回 OTel 格式 span_id；不在 span 中返回 `"-"`
  - [ ] 根 Span 的 `parent_span_id` 在 `SpanRecord` 中保留 `"-"` 哨兵值
  - [ ] 更新 `setup_observability()`：初始化 OTel `TracerProvider`（含 `Resource`），注册 `SpanProcessor`（自定义 metrics processor + `LocalSpanExporter`）和可选 `OTLPSpanExporter`
  - [ ] 更新 `flush_metrics()` / `close_observability()`：调用 OTel provider 的 `force_flush()` / `shutdown()`
  - [ ] 删除 `_export_span()` 函数 — Span 导出由 OTel SDK processor 链处理
  - [ ] 保持 `Span` 的公开接口不变：`__enter__`/`__exit__`/`set_attribute`/`add_event`/`set_status`/`span_id`/`trace_id`

- [x] Task 3: 删除 otlp.py 并重写 exporter.py 为 SpanExporter
  - [ ] 删除 `src/sagent/observability/otlp.py` 整个文件
  - [ ] 重写 `exporter.py`：`LocalSpanExporter` 实现 OTel `SpanExporter` 接口（`export`/`shutdown`），将 OTel `Span`/`ReadableSpan` 转换为 `SpanRecord` 后通过 `_DailyJsonLinesWriter` 写入
  - [ ] 在 `LocalSpanExporter.export()` 中对 `SpanRecord` 属性和事件递归调用 `redact()` 脱敏
  - [ ] OTel Span 的 trace_id（int）转换为 `SpanRecord.trace_id`（str，32 hex）；span_id（int）转换为 `SpanRecord.span_id`（str，16 hex）；根 Span 的 `parent_span_id` 设为 `"-"`
  - [ ] 保持 `_DailyJsonLinesWriter` 不变
  - [ ] 保持 `SpanRecord`/`MetricSnapshot` 模型不变

- [x] Task 4: 重写 metrics.py — SpanProcessor + OTel Meter + 保留 _TraceAccumulation
  - [ ] 删除 `_Histogram` 类 — 改用 OTel `Histogram` instrument
  - [ ] 重写 `MetricsRegistry`：counter 改为 OTel `Counter`，histogram 改为 OTel `Histogram`，通过 OTel `Meter` 创建
  - [ ] 将 `record_span_metrics` 逻辑移至自定义 `SpanProcessor.on_end`：根据 span name 从 `ReadableSpan` 读取属性，派发到对应 OTel instrument，调用 `accumulate_tokens`/`accumulate_iteration` 等
  - [ ] 保留 `_TraceAccumulation` 及其 `accumulate_tokens`/`accumulate_iteration`/`accumulate_tool_call`/`accumulate_compression`/`get_trace_accumulation`/`clear_trace` 不变
  - [ ] 保留 `ALLOWED_METRIC_LABELS` 标签过滤逻辑（在调用 OTel instrument 前过滤）
  - [ ] 保留 `snapshot()` 用于本地 metric JSON Lines 导出（从 OTel instrument 读取或保留自研快照存储）
  - [ ] 更新 `setup_metrics()` — 初始化 OTel `Meter` 和 instruments

- [x] Task 5: 更新 logging_setup.py 和 __init__.py
  - [ ] 更新 `logging_setup.py` `new_trace_id()`：改为生成 32 字符 hex（`uuid.uuid4().hex`）以与 OTel trace_id 格式兼容
  - [ ] 更新 `_TraceIdFilter`：从 OTel context 读取当前 span 的 `trace_id`/`span_id`/`parent_span_id`，不在 span 中时回退到 `_trace_id_var`
  - [ ] 更新 `__init__.py`：移除 `OTLPExporter` 和 `SpanContext` 导出，新增 `LocalSpanExporter` 导出（如需要）
  - [ ] 确认所有调用方代码（`cli/app.py`、`react_engine.py`、`plan_engine.py`、`llm/client.py`、`tools/registry.py`、`context/context_manager.py`、`tools/mcp/session_manager.py`）无需改动 — Span 接口不变

- [x] Task 6: 更新测试用例
  - [ ] 更新 `tests/unit/test_observability.py`：适配 OTel SDK 初始化方式，用自定义 `SpanExporter` 捕获 SpanRecord 替代 patch `_export_span`，更新 ID 格式断言（32/16 hex），保持测试覆盖范围不变
  - [ ] 更新 `tests/engines/test_observability_engines.py`：更新 `obs_env` fixture 初始化方式，更新 SpanRecord 捕获策略，更新 ID 格式断言，保持测试覆盖范围不变
  - [ ] 确认 `tests/unit/test_redactor.py` 无需改动（redactor 不变）
  - [ ] 运行 `python -m pytest` 确认全部用例通过

- [x] Task 7: 更新 spec 文档
  - [ ] 更新 `add-agent-observability/spec.md` 中"配置、依赖与兼容性"需求：将"默认本地观测 SHALL 不依赖 OpenTelemetry 包"改为"系统 SHALL 依赖 OpenTelemetry SDK"
  - [ ] 删除 `add-agent-observability/spec.md` 中"未安装可选 OTLP 依赖"场景和"默认本地观测"场景，新增"OTLP 禁用"和"OTLP 启用"场景
  - [ ] 更新 `add-agent-observability/spec.md` 中"层次化执行追踪"需求：将"默认实现可使用 ContextVar"改为"使用 OTel SDK 上下文传播"
  - [ ] 更新 `add-agent-observability/checklist.md`：修改"默认本地观测不新增依赖"条目为"OTel SDK 为必选依赖"

# Task Dependencies
- Task 2 依赖 Task 1。
- Task 3 依赖 Task 1（需要 OTel SDK 的 SpanExporter 接口）。
- Task 4 依赖 Task 2（SpanProcessor 需要 OTel TracerProvider）。
- Task 5 依赖 Task 2 和 Task 4。
- Task 6 依赖 Task 2 至 Task 5。
- Task 7 依赖 Task 6（测试通过后更新文档）。
