# 迁移可观测性至 OpenTelemetry SDK 引擎 Spec

## Why
当前可观测性实现完全自研 Span 上下文传播、指标存储和 OTLP 导出器。其中 OTLP 导出器（`otlp.py`）通过手动构造 `SDKSpan`、写私有字段 `span._attributes`、直接调 `processor.on_end(span)` 实现，属于脆弱的 hack。将 OTel SDK 作为必选依赖后，可直接复用其上下文传播、批处理、标准 OTLP 导出能力，消除自研传输层代码，同时保留业务语义层（成本估算、trace 累积、span→metric 派发、脱敏器、本地 JSON Lines 导出）不变。

## What Changes
- **BREAKING**: `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http` 从可选依赖变为必选依赖，加入 `requirements.txt`。
- **BREAKING**: trace_id 从 8 字符 hex 变为 OTel 原生 32 字符 hex；span_id 从 8 字符 hex 变为 16 字符 hex。本地 JSON Lines 文件和日志中的 ID 格式变更。
- **BREAKING**: 删除 `otlp.py` 整个文件 — OTLP 导出由 OTel SDK 的 `OTLPSpanExporter` + `BatchSpanProcessor` 原生处理。
- **BREAKING**: 删除 `SpanContext` 类和 `_span_context_var` — 上下文传播由 OTel SDK 的 `context` API 处理。
- 重写 `Span` 类为薄包装层：内部创建 OTel SDK `Span`，在 `__exit__` 中、调用 `span.end()` **之前**注入 trace 累积值和 iteration 计数，保证根 Span 属性完整。
- 保留自研 `_Histogram` 类用于本地 snapshot 存储，同时双写 OTel SDK `Histogram` instrument 以支持 OTLP 导出。
- 重写 `MetricsRegistry` counter/histogram 存储为 OTel `Meter` instruments，保留 `_TraceAccumulation` 不变。
- `record_span_metrics` 在 `Span.__exit__`（注入累积值前）和自定义 `SpanProcessor.on_end` 两处调用，通过 `_metrics_recorded_spans` 集合去重防止双写。
- 重写 `LocalExporter` 为实现 OTel `SpanExporter` 接口的 `LocalSpanExporter`，内部复用现有 `_DailyJsonLinesWriter`。
- 更新 `logging_setup.py`：`_TraceIdFilter` 从 OTel context 读取 `trace_id`/`span_id`/`parent_span_id`；`new_trace_id()` 改为生成 32 字符 hex 以保持兼容。
- 保持不变：`cost.py`、`redactor.py`、`content.py`、`_DailyJsonLinesWriter`、`SpanRecord`/`MetricSnapshot`/`ALLOWED_METRIC_LABELS` 模型、Span 树结构、所有 Span 名称和属性。
- 保持不变：所有调用方代码（`cli/app.py`、`react_engine.py`、`plan_engine.py`、`llm/client.py` 等）— `Span` 上下文管理器接口不变。
- 保持不变：`parent_span_id` 根 Span 哨兵值 `"-"` — 在 `SpanRecord` 和本地导出中保留，用于根检测和测试断言兼容。

## Impact
- Affected specs: `add-agent-observability`（修改"配置、依赖与兼容性"、"层次化执行追踪"、"本地可查询与标准导出"需求）。
- Affected code: `src/sagent/observability/context.py`（重写 Span）、`src/sagent/observability/metrics.py`（重写存储层）、`src/sagent/observability/exporter.py`（改为 SpanExporter）、`src/sagent/observability/logging_setup.py`（改用 OTel context）、`src/sagent/observability/__init__.py`（更新导出）、`requirements.txt`（新增必选依赖）。
- 删除文件：`src/sagent/observability/otlp.py`。
- NOT affected: `cost.py`、`redactor.py`、`content.py`、`models.py`、所有调用方代码。

## MODIFIED Requirements

### Requirement: 层次化执行追踪
系统 SHALL 为每轮非斜杠用户交互创建一个根 Span，并以 OpenTelemetry SDK 的上下文传播机制维护当前 trace/span 上下文。所有在根 Span 生命周期内发生的嵌套操作 SHALL 自动继承 trace ID 和当前父 Span ID；根 Span 完成后 SHALL 记录总耗时、最终状态、执行模式、迭代次数、工具调用次数、压缩次数、累计 token 与估算成本。

系统 SHALL 通过薄 Span 包装层在 OTel Span 的 `end()` 调用之前注入 trace 累积值和 iteration 计数等业务属性，保证 OTLP 导出和本地导出的根 Span 属性完整。此设计的原因是 OTel `SpanProcessor.on_end` 收到的是只读 `ReadableSpan`，无法在 Span 结束后修改属性。

trace_id SHALL 使用 OTel 原生 32 字符 hex 格式，span_id SHALL 使用 16 字符 hex 格式。根 Span 的 `parent_span_id` 在 `SpanRecord` 和本地导出中 SHALL 保留 `"-"` 哨兵值，用于根检测和向后兼容。

#### Scenario: ReAct 工具调用成功
- **WHEN** ReAct 在同一轮交互中调用 LLM、获得工具调用并成功执行工具
- **THEN** 本地 trace 中存在 `agent.run -> agent.react -> gen_ai.chat` 与 `agent.run -> agent.react -> tool.execute` 的父子 Span
- **THEN** LLM Span 记录模型、时延、输入/输出 token、工具调用数量和完成原因
- **THEN** 工具 Span 记录工具名、时延、结果长度和成功状态，但不记录未经脱敏的参数或结果

#### Scenario: Plan 任务执行
- **WHEN** Plan 模式完成拆解、一个或多个步骤和汇总
- **THEN** trace 树按 `plan.decompose`、每个 `plan.step`、`plan.summarize` 表达执行边界
- **THEN** 每个步骤 Span 记录步骤序号、步骤总数、步骤状态和耗时

#### Scenario: 失败与异常
- **WHEN** LLM、工具、压缩或 MCP 调用失败
- **THEN** 对应 Span 的状态为 error，记录低基数的 `error.type` 和已脱敏错误摘要
- **THEN** 异常继续由现有逻辑处理，观测记录或导出失败不得改变 Agent 返回与容错语义

#### Scenario: trace_id 格式
- **WHEN** 观测功能启用且创建根 Span
- **THEN** trace_id 为 32 字符 hex 字符串，span_id 为 16 字符 hex 字符串
- **THEN** 日志中的 `trace_id` 字段与本地 JSON Lines 中的 `trace_id` 一致
- **WHEN** 观测功能禁用
- **THEN** `new_trace_id()` 仍生成 32 字符 hex 用于日志关联，`span_id`/`parent_span_id` 为 `"-"`

### Requirement: 本地可查询与标准导出
系统 SHALL 默认将已结束的 Span 和周期性指标写入按天滚动的 JSON Lines 文件；每条 Span 包含 trace ID、span ID、parent span ID、名称、开始/结束时间、状态、脱敏属性和事件；若本轮交互属于已建立的会话，则额外包含脱敏后的 `session_id`。日志和 Span 可按 `trace_id` 重建单轮树，也可按 `session_id` 聚合多轮交互。

本地 JSON Lines 导出 SHALL 通过实现 OTel SDK 的 `SpanExporter` 接口完成，复用按天滚动文件写入器。导出前 SHALL 对 Span 属性和事件递归脱敏。`SpanRecord` 模型作为 OTel Span 到本地 JSON Lines 的中间映射层保留。

当 `observability.otlp_enabled` 为 true 时，系统 SHALL 使用 OTel SDK 原生的 `OTLPSpanExporter` 和 `BatchSpanProcessor` 导出 traces，使用 `PeriodicExportingMetricReader` 导出 metrics，通过配置控制 endpoint、协议和超时。

#### Scenario: 本地调试单轮 trace
- **WHEN** 开发者获得一个现有 trace ID
- **THEN** 可从本地 trace 文件按该 trace ID 检索所有 Span，并依据 parent span ID 重建有序 Span 树
- **THEN** 现有业务 JSON 日志保留 `trace_id` 并新增相关 span 标识，原有日志检索方式保持可用（ID 长度从 8 变为 32 字符）

#### Scenario: 导出不可用
- **WHEN** OTLP endpoint 不可达、超时或返回失败
- **THEN** 系统以非阻塞方式记录一次观测导出错误和失败指标
- **THEN** Agent 调用、工具执行和最终回复不因导出失败而失败或明显等待

### Requirement: 配置、依赖与兼容性
系统 SHALL 通过 `observability` 配置段控制启用状态、本地输出位置、内容采集、内容最大长度、脱敏、模型价格表（字段 `input_price_per_million` / `output_price_per_million`，单位元/百万 token）、OTLP 导出与指标刷新周期。

系统 SHALL 依赖 OpenTelemetry SDK（`opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`）作为可观测性引擎，用于上下文传播、Span 生命周期、指标 instrument 和 OTLP 导出。安装 `requirements.txt` 中的依赖即可运行全部观测能力。

`observability.otlp_enabled` 控制 OTLP 导出器是否加入 provider 链；为 false 时仅本地 JSON Lines 导出，为 true 时同时本地和 OTLP 导出。

既有 `logging` 配置和 `log_llm_content` SHALL 继续被解析；内容采集的最终有效值必须同时满足观测内容采集开关和日志内容开关，并始终通过脱敏器。

#### Scenario: OTLP 禁用
- **WHEN** `observability.otlp_enabled` 为 false
- **THEN** 系统仅使用本地 JSON Lines 导出，不初始化 OTLP exporter
- **THEN** Span 树、指标聚合、脱敏和本地 JSON Lines 导出正常可用

#### Scenario: OTLP 启用
- **WHEN** `observability.otlp_enabled` 为 true
- **THEN** 系统通过 OTel SDK 原生 `OTLPSpanExporter` 导出 Span，通过 `PeriodicExportingMetricReader` 导出指标
- **THEN** OTLP endpoint 不可达时本地导出不受影响

#### Scenario: 不启用增强观测
- **WHEN** `observability.enabled` 为 false
- **THEN** 不创建本地 Trace/Metric 文件、不初始化 OTLP，但现有 JSON 日志、`trace_id` 和 Agent 功能维持现有行为

## REMOVED Requirements

### Requirement: 零依赖本地观测
**Reason**: 用户决定放弃"默认本地观测不依赖 OpenTelemetry 包"的约束，OTel SDK 变为必选依赖。
**Migration**: 安装 `requirements.txt` 即自动包含 OTel SDK 依赖。删除 `otlp.py` 中的 `is_otlp_available()` fallback 逻辑。

### Requirement: 未安装可选 OTLP 依赖场景
**Reason**: OTel SDK 为必选依赖后，不存在"未安装"的情况。
**Migration**: 删除 spec 中"未安装可选 OTLP 依赖"场景及其对应的 fallback 代码路径。
