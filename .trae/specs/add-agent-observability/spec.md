# 增强 Agent 可观测性 Spec

## Why
当前系统以 `trace_id` 关联 JSON 日志，能够检索一轮交互中的事件，但不能还原事件的因果层级、量化执行质量，也未在统一出口防止敏感内容进入日志。需要建立兼容 OpenTelemetry 的 Agent 可观测基础设施，以定位慢调用、失败工具、循环行为、成本异常和低质量运行。

## What Changes
- 新增独立的 `observability` 观测内核，提供 trace/span 上下文、span 生命周期、结构化事件和进程内指标聚合。
- 每轮用户交互创建根 Span；ReAct、Plan、LLM 调用、工具执行、上下文压缩及 MCP 调用创建嵌套 Span，形成可还原的树。
- Span 采用 OpenTelemetry GenAI 语义约定的字段命名，并保留项目自定义 `sagent.*` 字段用于 Agent 行为诊断。
- 记录性能、token、估算成本、工具、迭代、压缩、失败和结果质量代理指标；不将高基数任务内容、用户输入或完整工具参数作为指标标签。
- 新增严格默认脱敏器，在任何 JSON 日志、Span 属性、Span 事件或本地观测导出前递归处理敏感字段和值；完整内容采集改为显式配置且仍经过脱敏和长度限制。
- 将现有 JSON 日志与 Span 上下文关联，保留 `trace_id` 以兼容既有日志检索；日志新增 `span_id`、`parent_span_id`。
- 提供默认本地 JSON Lines Trace/Metric 导出器，支持按 trace 查询；配置启用时可通过 OTLP 导出 traces 和 metrics。观测导出失败不得影响 Agent 主流程。
- 不在本变更中实现人工标注、LLM-as-judge、在线评测平台或告警/仪表盘；为后续接入预留稳定的 trace 与质量记录字段。

## Impact
- Affected specs: 可观测性、LLM 调用、ReAct/Plan 编排、工具执行、上下文管理、MCP 调用、CLI 配置。
- Affected code: `src/sagent/observability/`、`src/sagent/config/models.py`、`config.example.yaml`、`src/sagent/cli/app.py`、`src/sagent/core/react_engine.py`、`src/sagent/core/plan_engine.py`、`src/sagent/llm/client.py`、`src/sagent/tools/registry.py`、`src/sagent/context/context_manager.py`、`src/sagent/tools/mcp/session_manager.py`、相关单元和引擎测试。

## 现状分析与推荐设计

现有 `trace_id` 在 CLI 每轮输入时生成，借助 `contextvars` 注入 JSON 日志。LLM 调用已产生时延与 token 字段，工具注册表已产生工具时延和结果长度，ReAct/Plan/上下文压缩也已有事件日志。因此本变更应复用这些明确的生命周期边界，而非在日志文本上二次解析。

现有不足如下：
- `trace_id` 是扁平关联键，没有 Span ID、父子关系、开始/结束时间和最终状态，无法区分一轮交互中的 Plan 拆解、步骤执行、LLM 和工具耗时归属。
- 日志事件对 token、时延和异常仅零散记录，缺少统一指标定义、进程内聚合、成本估算、结果状态和行为异常信号。
- `logging.log_llm_content` 为真时会直接写入完整 messages、content 与 tool_calls；CLI 用户输入和工具参数也会进入日志，现有 `_safe` 只负责 JSON 序列化，不负责脱敏。
- Plan 的拆解和汇总调用没有统一的 usage 校准及生命周期量化；MCP 的连接与调用也尚未纳入单轮 Agent 执行树。

推荐以 OpenTelemetry GenAI 语义约定作为外部合同：该约定提供 LLM/Agent/工具调用的通用字段、token 使用量和操作时延，允许后续接入任意 OTLP 兼容后端；自定义行为字段统一使用 `sagent.*` 命名空间，避免随意扩散字段。其 GenAI 约定仍在演进，内部观测模型应是稳定的，OTLP 属性映射集中在导出器中，降低未来升级语义约定的影响。

推荐的树结构：

```text
agent.run (根：一轮用户交互)
├── plan.decompose                         # 仅 Plan 模式
│   └── gen_ai.chat
├── plan.step (每个步骤)                   # 仅 Plan 模式
│   └── agent.react
│       ├── gen_ai.chat (每次模型调用)
│       ├── tool.execute (每个工具调用)
│       └── context.compress               # 触发时
├── plan.summarize                          # 仅 Plan 模式
│   └── gen_ai.chat
└── agent.finalize                          # 最大迭代收尾时
    └── gen_ai.chat
```

## ADDED Requirements

### Requirement: 层次化执行追踪
系统 SHALL 为每轮非斜杠用户交互创建一个根 Span，并以统一的观测上下文接口维护当前 trace/span 上下文。默认实现可使用 `ContextVar`；启用 OpenTelemetry SDK 时 SHALL 与其上下文传播保持一致，且不得生成重复根 Span。所有在根 Span 生命周期内发生的嵌套操作 SHALL 自动继承 trace ID 和当前父 Span ID；根 Span 完成后 SHALL 记录总耗时、最终状态、执行模式、迭代次数、工具调用次数、压缩次数、累计 token 与估算成本。

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

### Requirement: 统一指标与运行质量代理
系统 SHALL 以固定、低基数维度聚合并导出指标，覆盖调用量、成功/失败、延迟、token、成本、迭代和工具行为。指标标签仅允许 mode、operation、model、provider、tool_name、outcome、compression_layer 等有限枚举值。

#### Scenario: LLM 用量和成本
- **WHEN** LLM API 返回 usage
- **THEN** 系统记录输入、输出、缓存和 reasoning token（提供时）计数，并以配置的模型单价（元/百万 token）计算估算成本
- **THEN** 每个根 Span 累积其子 LLM Span 的 token 和估算成本
- **THEN** API 未返回 usage 时不伪造 token 或成本数值，而是记录 usage 不可用状态

#### Scenario: 性能和行为指标
- **WHEN** 一轮 Agent 交互结束
- **THEN** 系统聚合以下指标：交互总耗时、LLM 调用时延、工具调用时延、每轮迭代数、每轮工具调用数、工具错误率、最大迭代耗尽数、上下文压缩次数及压缩前后 token
- **THEN** 系统记录质量代理结果：`completed`、`failed`、`max_iterations`、`plan_fallback`、`tool_error`、`empty_answer` 等枚举结果
- **THEN** `plan_fallback` 在 Plan 拆解失败或所有步骤执行失败后降级为 ReAct 模式继续执行时触发
- **THEN** 系统不将模型内部思维链、原始用户输入或自由文本答案作为质量评分或指标标签

### Requirement: 默认脱敏和最小化采集
系统 SHALL 在结构化日志格式化、Span 属性/事件写入和本地/OTLP 导出之前使用同一递归脱敏器。脱敏默认启用且不可被内容采集开关绕过；安全规则覆盖敏感字段名、常见凭证、Bearer/JWT、PEM 私钥、邮箱、手机号、银行卡和连接 URL 中的凭证或查询参数。

#### Scenario: 用户输入或工具参数包含秘密
- **WHEN** 用户输入、工具参数、LLM 内容或异常文本含 API Key、Authorization、密码、token 或个人信息
- **THEN** 日志、Span 和本地导出中的对应片段均被替换为统一掩码
- **THEN** 系统可记录已脱敏字段计数和规则类别，但不得记录原值、可逆 hash 或完整敏感片段

#### Scenario: 内容采集策略
- **WHEN** 默认配置运行
- **THEN** 系统仅采集长度、消息数量、角色分布、摘要和受限的诊断属性，不采集 prompt、completion、工具参数或工具结果正文
- **WHEN** 显式启用诊断内容采集
- **THEN** 系统只写入脱敏后且不超过配置长度上限的内容，并在 trace 中标记 `sagent.content.capture=true`

### Requirement: 本地可查询与标准导出
系统 SHALL 默认将已结束的 Span 和周期性指标写入按天滚动的 JSON Lines 文件；每条 Span 包含 trace ID、span ID、parent span ID、名称、开始/结束时间、状态、脱敏属性和事件；若本轮交互属于已建立的会话，则额外包含脱敏后的 `session_id`。日志和 Span 可按 `trace_id` 重建单轮树，也可按 `session_id` 聚合多轮交互。系统 SHALL 可选地使用 OTLP 导出 traces 与 metrics，并通过配置控制 endpoint、协议和超时。

#### Scenario: 本地调试单轮 trace
- **WHEN** 开发者获得一个现有 trace ID
- **THEN** 可从本地 trace 文件按该 trace ID 检索所有 Span，并依据 parent span ID 重建有序 Span 树
- **THEN** 现有业务 JSON 日志保留 `trace_id` 并新增相关 span 标识，原有日志检索方式保持可用

#### Scenario: 导出不可用
- **WHEN** OTLP endpoint 不可达、超时或返回失败
- **THEN** 系统以非阻塞方式记录一次观测导出错误和失败指标
- **THEN** Agent 调用、工具执行和最终回复不因导出失败而失败或明显等待

### Requirement: 配置、依赖与兼容性
系统 SHALL 通过新增 `observability` 配置段控制启用状态、本地输出位置、内容采集、内容最大长度、脱敏、模型价格表（字段 `input_price_per_million` / `output_price_per_million`，单位元/百万 token）、OTLP 导出与指标刷新周期。默认本地观测 SHALL 不依赖 OpenTelemetry 包；仅在 `observability.otlp_enabled` 为 true 时尝试加载 `opentelemetry-api`、`opentelemetry-sdk` 和 `opentelemetry-exporter-otlp-proto-http`。依赖缺失时 SHALL 记录脱敏诊断并继续执行本地观测和 Agent 主流程。既有 `logging` 配置和 `log_llm_content` SHALL 继续被解析；内容采集的最终有效值必须同时满足观测内容采集开关和日志内容开关，并始终通过脱敏器。

#### Scenario: 未安装可选 OTLP 依赖
- **WHEN** `observability.otlp_enabled` 为 true，但未安装 OpenTelemetry 可选依赖
- **THEN** 系统保留本地 JSON Lines Trace/Metric 导出并记录脱敏后的依赖缺失诊断
- **THEN** Agent 调用、工具执行和最终回复不失败

#### Scenario: 默认本地观测
- **WHEN** 使用默认配置且未安装 OpenTelemetry 包
- **THEN** Span 树、指标聚合、脱敏和本地 JSON Lines 导出正常可用
- **THEN** 安装现有 `requirements.txt` 中的依赖即可运行该能力

#### Scenario: 不启用增强观测
- **WHEN** `observability.enabled` 为 false
- **THEN** 不创建本地 Trace/Metric 文件、不初始化 OTLP，但现有 JSON 日志、`trace_id` 和 Agent 功能维持现有行为

## MODIFIED Requirements

### Requirement: 日志安全输出
所有项目结构化日志 SHALL 在 JSON 格式化前经共享脱敏器处理 `msg`、`extra`、异常摘要和嵌套对象。原有日志字段和滚动策略保持不变；每条处于 Span 上下文的日志额外包含 `span_id` 与 `parent_span_id`。

### Requirement: LLM 调用观测
LLM 客户端 SHALL 将一次 `chat()` 调用表示为 `gen_ai.chat` Span，并保留现有请求/响应摘要日志。完整 messages、content 和 tool_calls 不得直接写入日志或 Span；仅在内容采集有效时写入经过脱敏和长度截断后的副本。OpenAI SDK 自动埋点 SHALL 仅作为 LLM 请求属性采集的可选补充，且不得与手动 `gen_ai.chat` Span 同时启用，以避免重复 Span、重复 token 计数和重复成本统计。

## REMOVED Requirements

无。
