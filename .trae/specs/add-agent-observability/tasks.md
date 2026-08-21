# Tasks

- [x] Task 1: 定义观测配置和数据模型
  - [x] 在 `config.models` 中新增 `ObservabilityConfig` 及 OTLP、内容采集、模型价格、指标刷新周期配置，并聚合到 `AppConfig`。
  - [x] 在 `config.example.yaml` 增加保守默认值及中文说明。
  - [x] 定义内部 Span、事件、指标快照与导出记录的类型，明确稳定字段和低基数标签白名单。

- [x] Task 2: 实现 Span 上下文与本地导出
  - [x] 在 `observability` 子包实现基于 `ContextVar` 的 trace/span 上下文、Span 上下文管理器与上下文管理器接口。
  - [x] 实现结束 Span 的本地 JSON Lines 导出及周期性 Metric 快照导出，支持按天滚动。
  - [x] 为观测导出失败实现非阻塞隔离，不影响 Agent 主逻辑。

- [x] Task 3: 实现统一脱敏器并接入日志出口
  - [x] 实现递归脱敏，覆盖敏感键、凭证、token、PEM、邮箱、手机号、银行卡及 URL 认证信息和查询参数。
  - [x] 为字符串和嵌套 dict/list 设置内容截断与脱敏计数，保证不保留原始敏感片段。
  - [x] 修改 JSON 日志格式化器，使 `msg`、extra、异常摘要在序列化前脱敏，并附加当前 span 上下文。
  - [x] 修改完整 LLM 日志内容路径，统一使用受限且脱敏后的内容采集策略。

- [x] Task 4: 埋点根交互、ReAct 与 Plan 路径
  - [x] 在 CLI 每轮交互创建 `agent.run` 根 Span，并记录模式、当前 `session_id`（可用时）、结果状态和汇总指标；保证 `session_id` 只用于日志/Span 关联，不进入 Metrics 标签。
  - [x] 在 ReAct 中记录 `agent.react`、迭代事件、最大迭代收尾与对应的质量代理 outcome。
  - [x] 在 Plan 中记录拆解、步骤、回退和汇总 Span，并确保父子关系与步骤序号正确。

- [x] Task 5: 埋点 LLM、工具、上下文压缩和 MCP 调用
  - [x] 在 LLM 客户端建立 `gen_ai.chat` Span，记录 OpenTelemetry GenAI 兼容属性、usage、时延、异常和受控内容事件。
  - [x] 在工具注册表建立 `tool.execute` Span，记录工具名、参数/结果大小、时延和错误类型。
  - [x] 在上下文管理器记录 `context.compress` Span，记录层级、压缩前后 token 与摘要/失败状态。
  - [x] 在 MCP 会话连接与工具调用处建立相应 Span，记录服务器名、传输方式、时延、超时与错误，但不采集远程请求正文。

- [x] Task 6: 实现指标聚合、成本估算与可选 OTLP 导出
  - [x] 以低基数标签实现计数器、直方图和运行结果计数，覆盖 Spec 定义的延迟、token、成本、迭代、工具、压缩和结果指标。
  - [x] 使用配置模型单价表计算已知 usage 的估算成本，缺少价格或 usage 时明确标记为不可用。
  - [x] 实现可选 OTLP trace/metric 导出，复用 OpenTelemetry SDK 的标准上下文、批处理和导出能力，属性映射集中处理，兼容 GenAI 语义约定的更新；缺少 OpenTelemetry 可选依赖时回退本地导出并保持 Agent 主流程可用。
  - [x] 评估 OpenAI SDK 自动埋点仅作为 LLM 请求属性采集的可选补充；首期禁止与 `LLMClient.chat()` 的手动 `gen_ai.chat` Span 同时启用，防止重复 Span 和指标重复计数。

- [x] Task 7: 覆盖测试与全量验证
  - [x] 为配置、Span 树父子关系、状态、指标聚合、本地导出和导出失败隔离添加单元测试。
  - [x] 为脱敏器增加凭证、PII、嵌套对象、异常文本和内容长度限制测试；验证日志和导出记录均无原始敏感值。
  - [x] 为 ReAct 和 Plan 增加 trace 树、token/成本、工具错误、最大迭代和 Plan 回退的集成测试。
  - [x] 运行 `python -m pytest` 并确认全部离线用例通过。

# Task Dependencies
- Task 2 依赖 Task 1。
- Task 3 依赖 Task 1，且可与 Task 2 并行。
- Task 4 依赖 Task 2。
- Task 5 依赖 Task 2、Task 3，可在 Task 4 的接口稳定后并行推进不同模块。
- Task 6 依赖 Task 1、Task 2、Task 5。
- Task 7 依赖 Task 3 至 Task 6。
