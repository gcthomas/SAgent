# Checklist

## 基础设施
- [x] `AppConfig` 提供默认可用的 `observability` 配置，示例配置完整说明各开关。
- [x] `ObservabilityConfig` 提供指标刷新周期配置项，本地 Metric 快照按该周期写入。
- [x] 每轮交互生成一个可关联现有 `trace_id` 的根 Span，并分配唯一 span ID。
- [x] 所有嵌套 Span 正确维护 parent span ID，结束后可重建无孤儿的树。
- [x] 观测功能禁用时不创建增强观测文件，现有日志与 Agent 行为保持不变。
- [x] 本地 JSON Lines 记录每条 Span 包含 trace ID、span ID、parent span ID、名称、开始/结束时间、状态、脱敏属性和事件。
- [x] OTel SDK 为必选依赖，`requirements.txt` 包含 `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`。
- [x] OTLP 导出由 `observability.otlp_enabled` 控制，为 true 时通过 OTel SDK 原生 `OTLPSpanExporter` 导出；依赖或导出失败不阻塞或改变 Agent 执行结果。
- [x] OTLP 导出失败时记录一次观测导出失败指标，且不影响 Agent 主流程。
- [x] 保留现有结构化业务日志；OpenTelemetry 只承担标准上下文、Trace/Metric 模型和导出，不替代 CLI 诊断日志。
- [x] 自动 LLM 埋点未与 `LLMClient.chat()` 的手动 Span 同时产生重复 Span 或重复 Metrics。

## 执行路径
- [x] ReAct trace 包含 Agent 执行、每次 LLM 调用、每个工具调用、上下文压缩和最大迭代收尾边界。
- [x] Plan trace 包含任务拆解、每个步骤、Plan 回退和汇总边界，步骤 Span 有正确顺序和父子关系。
- [x] LLM Span 记录模型、provider、时延、输入/输出 token、缓存/reasoning token（可用时）、工具调用数量和 error 状态。
- [x] 工具与 MCP Span 记录名称、时延、结果大小和失败类型，不采集未经处理的输入或输出正文。
- [x] 上下文压缩 Span 记录层级、前后 token、成功/失败状态。

## 指标与质量
- [x] 记录并可导出交互、LLM、工具调用的计数、成功/失败、延迟、token、迭代、压缩和最大迭代指标。
- [x] 指标标签只使用受控低基数维度，不包含输入、用户标识、会话 ID、trace ID、工具参数或自由文本。
- [x] 配置了价格的模型可从有效 usage 计算每次 LLM 调用和根 Span 的估算成本。
- [x] usage 或价格缺失时不生成虚假成本，状态可被诊断。
- [x] 根 Span 记录 `completed`、`failed`、`max_iterations`、`plan_fallback`、`tool_error`、`empty_answer` 等质量代理 outcome。
- [x] 根 Span 完成后记录总耗时、最终状态、执行模式、迭代次数、工具调用次数、压缩次数、累计 token 与估算成本（含子 LLM Span token 和成本的累积）。
- [x] 不采集或输出模型内部思维链。

## 脱敏
- [x] 默认不采集 prompt、completion、工具参数和工具结果正文。
- [x] 显式内容采集只输出脱敏且长度受限的内容，并留下内容采集标识。
- [x] 内容采集有效值 = 观测内容采集开关 AND 日志内容开关，两者同时为真才采集，且始终经过脱敏器。
- [x] JSON 日志、Span 属性、Span 事件、本地导出和异常文本共用同一脱敏策略。
- [x] API Key、Bearer/JWT、私钥、密码、邮箱、手机号、银行卡和 URL 凭证/查询参数均不会以原文落盘。
- [x] 嵌套 dict/list 中的敏感内容也能被处理，且日志无法通过字段绕过脱敏。
- [x] 现有 `trace_id` 日志检索仍可用，日志中新增 `span_id` 与 `parent_span_id`，并在会话上下文可用时新增 `session_id`。

## 验证
- [x] 单元测试覆盖配置、Span 上下文、树结构、指标、本地导出、OTel SDK 集成（SpanExporter/SpanProcessor）和成本计算。
- [x] 单元测试覆盖敏感数据脱敏和长度限制，断言所有观测输出均不含原值。
- [x] 引擎测试覆盖 ReAct 和 Plan 的 trace 树、工具错误、最大迭代、Plan 回退和累计指标。
- [x] `python -m pytest` 全部离线用例通过。
