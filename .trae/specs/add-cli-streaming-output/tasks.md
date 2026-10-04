# Tasks

- [x] Task 1: 扩展流式输出配置并同步示例配置
  - [x] 在 `LLMConfig` 增加默认开启的布尔开关。
  - [x] 在 `config.example.yaml` 增加带中文说明的配置项；不修改真实 `config.yaml`。
  - [x] 增加配置默认值和显式关闭的单元测试。

- [x] Task 2: 为 LLM 客户端增加流式响应聚合
  - [x] 使用现有 OpenAI Chat Completions 接口的 `stream=True` 获取 chunk。
  - [x] 定义内部增量事件/回调协议，聚合文本、工具调用名称、参数、raw message 和 usage。
  - [x] 按 tool-call index/id 聚合跨 chunk 的 function name 与 JSON arguments，并确保最终 `LLMResponse` 与非流式响应等价。
  - [x] 保留现有日志、Span、usage 与异常语义，在流结束后记录完整响应。
  - [x] 增加文本增量、工具调用分片和 usage 的离线单元测试。

- [x] Task 3: 将流式事件接入 ReAct 与 Plan 引擎
  - [x] 扩展 AgentEvent 表达 LLM 文本增量和流式轮次完成所需的数据。
  - [x] ReAct 在最终回答、工具调用轮次和最大迭代收尾请求中按配置选择流式/非流式路径。
  - [x] Plan 的拆解、步骤内 ReAct 和最终汇总请求复用同一流式路径；内部拆解 JSON 不直接作为最终答案重复输出。
  - [x] 保持工具执行时机、上下文记录、最终返回值和现有事件语义不变。
  - [x] 增加引擎测试，验证流式文本顺序、工具调用执行一次、Plan 流程和关闭开关路径。

- [x] Task 4: 更新 CLI 展示层并验证平滑交互输出
  - [x] 为 Presenter 定义最小写入/刷新协议，生产环境注入 `prompt_toolkit.output.Output`，测试环境注入可记录的内存适配器。
  - [x] 增加增量缓冲区，按时间/字符双阈值刷新，只写出新增文本，不清屏、不回绘；不采用纯粹的遇换行才输出的行缓冲。
  - [x] 在文本流开始、文本流结束、工具调用、工具结果、错误和用户中断等状态边界强制 flush 并补换行，避免不同类型输出粘连。
  - [x] 首期流式文本按原始文本输出，不在半成品 Markdown 状态下做整段 Markdown 重绘。
  - [x] 保留工具调用、工具结果、权限审批、错误和斜杠命令的既有输出行为。
  - [x] 启动信息显示流式输出状态。
  - [x] 增加 Presenter 测试，验证节流、强制刷新、输出顺序和无重复；运行受影响测试及完整离线测试。

# Task Dependencies
- Task 2 depends on Task 1.
- Task 3 depends on Task 2.
- Task 4 depends on Task 3.
- Task 1 的配置测试与 Task 2 的客户端测试可在实现层面并行，但需先确定配置字段名称。
