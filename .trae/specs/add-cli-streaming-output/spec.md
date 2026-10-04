# CLI 流式输出 Spec

## Why
当前 CLI 只有在一次 LLM 请求或完整 Agent 轮次结束后才展示助手内容，用户在等待模型生成、工具调用和多轮 Agent 执行时缺少即时反馈。需要默认启用面向 Agent 交互的流式输出，同时保留配置关闭能力，并保持现有 ReAct、Plan、工具调用、会话持久化和观测行为不变。

## What Changes
- 在 LLM 配置中增加流式输出开关，默认开启，可通过 YAML 关闭。
- LLM 客户端提供按增量事件消费的流式调用接口；文本增量即时交给 CLI 展示层。
- 将文本增量、工具调用增量和工具调用完成统一为结构化 Agent 事件。
- ReAct 与 Plan 引擎继续负责 Agent 循环和消息历史，仅在每次 LLM 调用处消费流式结果并在请求结束后形成完整 `LLMResponse`。
- CLI 展示层按事件类型输出：助手文本增量直接刷新、工具调用显示开始/完成、工具结果和计划状态继续即时输出。
- 流式输出关闭时沿用当前一次性响应行为；不改变最终返回文本、工具参数解析、上下文记录和会话保存语义。
- 更新示例配置和离线测试，覆盖默认开启、显式关闭、文本增量、工具调用增量聚合和 Agent 循环。

## Impact
- Affected specs: CLI 交互输出、LLM 客户端、ReAct/Plan Agent 事件、配置加载、测试。
- Affected code: `src/sagent/config/models.py`、`config.example.yaml`、`src/sagent/llm/client.py`、`src/sagent/core/events.py`、`src/sagent/core/react_engine.py`、`src/sagent/core/plan_engine.py`、`src/sagent/cli/event_presenter.py`、相关测试。
- 不新增生产依赖，使用现有 OpenAI SDK 的 Chat Completions streaming 能力。

## 推荐方案

### 1. 分层流式，而不是让 CLI 直接读取 SDK
LLM 客户端负责把 OpenAI SDK chunk 转换成稳定的内部流式事件；Agent 引擎负责消费事件并组装最终响应；CLI 只渲染内部事件。这样可以避免 OpenAI SDK 的 chunk 结构泄漏到 ReAct、Plan 和 CLI，也能让同步执行循环保持清晰。

建议事件生命周期为：

`llm_text_delta* -> llm_tool_call_delta* -> llm_completed`

其中 `llm_completed` 携带完整文本、完整工具调用、原始 assistant message 和 usage，供上下文、日志和后续工具执行使用。

### 2. 文本增量直接输出，工具调用只展示稳定状态
- 普通文本 delta 到达后立即 `print(..., end="", flush=True)`，避免按 token 输出额外标签。
- 工具调用的 function name 和 JSON arguments 可能跨多个 chunk，不能在参数不完整时执行；必须按 tool-call index/id 聚合，只有流结束后才解析和执行。
- 工具调用开始时展示 `[行动]` 状态，参数完整后展示最终参数；工具执行期间继续使用现有 `[观察]` 输出。
- 不向用户输出模型内部 reasoning/token 细节；只显示可展示的 assistant text 和 Agent 状态。
- 每轮流式助手文本结束后补换行，避免工具状态或下一轮输出粘连。

### 3. ReAct 与 Plan 的交互语义
- ReAct 每个 LLM 轮次使用流式调用；没有工具调用时，文本 delta 即时显示，并在完成后返回完整答案。
- 有工具调用时，已生成的可展示文本仍即时显示；完成事件后再执行工具，并进入下一轮。
- 达到最大迭代次数的收尾请求同样遵循开关配置。
- Plan 的拆解和汇总请求也使用相同流式路径，但拆解 JSON 内容仅作为内部数据，不直接作为最终助手回复渲染；计划事件仍按现有方式展示。
- Plan 步骤内复用 ReAct 的流式事件，并保留现有步骤上下文。

### 4. 配置位置与兼容性
推荐将开关放在 `llm` 下，表示模型请求行为而不是某一种 Agent 模式：

```yaml
llm:
  stream: true
```

默认值为 `true`。设置为 `false` 时调用当前非流式接口和当前 CLI 最终输出路径。配置字段应使用布尔类型校验，并在启动信息中明确显示当前流式输出状态。

### 5. 终端平滑渲染方案
流式输出不能简单地对每个 token 调用 `print()`：模型通常按很小的片段返回，过于频繁的系统调用会造成光标抖动、粘连和 Markdown 半成品闪烁。需要区分“行缓冲”与“增量节流”两种方案：

- **传统行缓冲**：积累内容直到遇到换行符再输出。实现简单，但模型可能长时间不生成换行，用户仍会等待；同时工具状态、权限提示和错误需要强制打断当前行。因此不适合作为唯一策略。
- **增量节流**：追加到内存缓冲，按最小时间间隔或累计字符数输出新增内容，在文本结束、工具调用、工具结果、错误和中断等边界强制刷新。它既能保持首字节低延迟，也能减少高频终端写入，是本项目的主要方案。

### 6. 是否采用 prompt_toolkit 行缓冲/渲染能力
当前项目已经用 `prompt_toolkit.PromptSession` 处理输入，但模型执行期间没有同时接受用户输入，因此不需要把助手输出纳入 prompt_toolkit 的全屏 Application 重绘循环。prompt_toolkit 的 `Output` 抽象适合统一 Windows/ANSI 输出和测试注入；`patch_stdout` 适合后台任务与输入提示并发写入，但本任务的响应阶段没有并发输入场景。

因此推荐采用“**prompt_toolkit Output 作为输出适配层 + 自有增量节流 Presenter**”，而不是采用纯粹的“遇换行才刷新的行缓冲”或全屏重绘：

- Presenter 通过注入的 `prompt_toolkit.output.Output` 写入并 flush，不直接绑定 `sys.stdout`；保留 `StringIO` 等测试输出适配能力。
- 使用单一追加光标，只输出尚未刷新的文本；禁止清屏、光标上移和整段重绘，避免闪烁。
- 文本增量进入 Presenter 缓冲区后，满足时间/字符阈值之一即写出；遇到换行可以立即刷新，但不能把换行作为唯一刷新条件。
- 文本流开始输出一次 `助手 > `；文本流结束或切换到工具/状态事件时强制 flush 并补换行，确保不同事件不粘连。
- 若未来支持响应过程中继续编辑输入、spinner 或并发后台事件，再引入 `patch_stdout` 或完整 prompt_toolkit Application；本期不为不存在的并发场景增加复杂度。

结论：本项目采用“prompt_toolkit Output + 增量节流”。Output 只负责终端写入、flush 和平台适配，节流 Presenter 负责流式增量的缓存与追加式刷新；纯行缓冲会牺牲体验，全屏 prompt_toolkit 重绘则超出需求且增加光标状态管理成本。Presenter 的输出依赖应抽象为最小写入/刷新协议，以便生产环境使用 prompt_toolkit Output、测试环境使用可记录的内存适配器，不让测试依赖真实终端。

### 7. 业界方案对比与本项目选择
- **OpenAI Agents SDK**：以 run stream events 暴露 raw response text delta、tool called、tool output 和完成状态；优点是事件边界清晰，适合 Agent 生命周期，缺点是其事件模型不能直接复用本项目现有同步引擎。
- **Claude Agent SDK / Claude Code**：通过 partial message stream 提供 `text_delta`、工具调用 block 和 block stop；优点是文本块与工具块分离，工具参数可增量聚合，缺点是需要处理更细粒度的底层事件。
- **直接消费 OpenAI Chat Completions SSE**：实现成本最低，适合本项目已有 OpenAI 兼容客户端；但 SDK chunk 结构不应泄漏到 CLI，否则未来更换供应商会扩大改动范围。
- **prompt_toolkit 输出/渲染**：适合交互式输入、终端输出抽象、并发打印和复杂重绘；本项目已有其输入能力，但当前同步响应不需要全屏重绘。采用其 `Output` 通道即可获得兼容性，不将助手文本改造成全屏布局。

本项目选择“OpenAI Chat Completions 流式传输 + 内部结构化事件 + prompt_toolkit Output + 增量节流 Presenter”：保留现有同步 ReAct/Plan 循环，借鉴 Claude 的 block 聚合和 OpenAI Agents 的生命周期事件，并通过追加式输出与边界 flush 减少终端闪烁。

参考：
- [OpenAI Agents SDK Streaming](https://openai.github.io/openai-agents-python/streaming/)
- [Claude Agent SDK Streaming Output](https://code.claude.com/docs/en/agent-sdk/streaming-output)
- [Claude Agent Loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)

## ADDED Requirements
### Requirement: 默认启用 CLI 流式输出
系统 SHALL 在未配置时启用 LLM 流式输出，并在 CLI 启动信息中显示启用状态。

#### Scenario: 未配置开关
- **WHEN** 配置文件只提供现有 `llm` 必填项
- **THEN** 配置对象的流式开关为 `true`
- **AND** 每次支持流式的 Agent LLM 请求按增量事件消费

#### Scenario: 显式关闭
- **WHEN** 用户设置 `llm.stream: false`
- **THEN** LLM 客户端使用非流式请求
- **AND** CLI 输出结果与当前非流式行为一致

### Requirement: 文本增量即时呈现
系统 SHALL 在收到可展示的文本增量时立即发送 CLI 输出，并刷新输出缓冲区。

#### Scenario: 长文本生成
- **WHEN** LLM 返回多个文本增量
- **THEN** 每个增量按到达顺序立即输出
- **AND** 最终用户可见文本与所有增量拼接结果一致
- **AND** 流结束时只补充必要的换行，不重复输出完整答案

### Requirement: 工具调用安全聚合
系统 SHALL 在工具调用的名称和参数增量完整后再执行工具。

#### Scenario: 参数跨 chunk 返回
- **WHEN** 一个工具调用的 function name 或 JSON arguments 分散在多个流式 chunk
- **THEN** 客户端按 tool-call index 或 id 聚合对应字段
- **AND** 流结束后生成与非流式响应等价的工具调用结构
- **AND** 工具最多按完整调用执行一次

### Requirement: Agent 事件统一
系统 SHALL 使用结构化事件在 LLM、引擎和 CLI 展示层之间传递增量文本、工具状态和完成状态。

#### Scenario: ReAct 流式轮次
- **WHEN** ReAct 请求返回文本增量和工具调用
- **THEN** CLI 即时展示文本增量和工具状态
- **AND** 引擎在完成事件后执行工具并继续原有循环
- **AND** 最终答案、上下文消息和会话保存内容保持完整

#### Scenario: Plan 流式轮次
- **WHEN** Plan 执行拆解、步骤或汇总请求
- **THEN** 继续展示计划/步骤状态
- **AND** 步骤内文本流式输出不破坏步骤上下文
- **AND** 汇总结果仍作为完整 assistant 内容返回

### Requirement: 观测与持久化保持完整
系统 SHALL 在流式请求结束后记录完整响应、usage（若服务返回）和最终 latency，并使用完整 assistant 消息更新上下文和会话。

#### Scenario: 流式请求完成
- **WHEN** 一个流式 LLM 请求正常结束
- **THEN** 日志和 Span 使用聚合后的完整响应信息
- **AND** 上下文管理器及会话持久化收到完整 assistant/tool-call 消息

## MODIFIED Requirements
### Requirement: LLM 对话调用
LLM 客户端 SHALL 同时提供流式和非流式请求路径，二者生成相同的 `LLMResponse` 语义；流式路径额外向调用方发出有序增量事件。Agent 引擎根据 `llm.stream` 选择路径，不改变工具调用协议和最终结果类型。

## REMOVED Requirements
无。
