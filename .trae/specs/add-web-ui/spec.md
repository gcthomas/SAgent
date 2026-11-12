# Web UI 交互能力 Spec

## Why

SAgent 当前只能通过 CLI 交互，会话管理、运行状态、计划进度与轨迹都只能以纯文本形式输出，无法在浏览器中持续观察与操作。新增 Web UI 后，用户可以一边看流式回答、一边查看计划进度与运行状态，并在权限审批点直接在页面上决策；排查问题时可以直接翻看完整轨迹，而不必回翻终端日志。

## What Changes

- 新增 FastAPI 后端服务层 `src/sagent/server/`，以 HTTP + SSE 暴露会话、对话、审批、取消、轨迹能力，并在生产模式下托管前端静态资源。
- 新增会话级 Agent 运行时注册表：每个会话独立持有 ContextManager / SessionManager / Engine / 审批器，解决现有单会话模型的并发问题。
- 扩展 `AgentEvent`：新增运行状态、审批请求/结果、用量三类事件与相关字段；CLI 侧对未知事件静默忽略，保持原有输出不变。
- 新增协作式取消：ReAct 与 Plan 引擎在迭代边界与工具调用前检查取消令牌，取消后抛出专用异常并归为"已取消"结果。
- 新增 Web 审批器：工具授权等待前端决策，带超时与 fail-safe 拒绝，每次请求都有 approval_id 与审计。
- 新增轮次统计：每轮对话记录起止时间、耗时、prompt/completion/total token 与结果状态，供对话视图与轨迹视图共用。
- 新增会话搜索：支持按会话标题与消息内容联合搜索，返回会话元数据与命中摘要。
- 新增前端工程 `web/`：React + Vite + assistant-ui + Vercel AI SDK，左侧会话列表、中间"对话 / 轨迹"双 Tab。
- 新增配置项 `server`（host / port / cors_origins / static_dir），同步示例配置。
- **BREAKING**（对内部接口而言）：`AgentEvent` 新增字段与事件类型，任何穷举事件类型的下游代码需要适配；仅 `EventPresenter` 一处，已要求默认静默忽略未知事件。
- 不改动 CLI 的启动方式、斜杠命令语义与既有输出格式。

## Impact

- Affected specs: `add-session-management`、`add-cli-streaming-output`、`integrate-adaptive-planning`、`add-tool-permission-control`、`add-context-management`（均为局部扩展，不改变既有行为约定）
- Affected code:
  - `src/sagent/core/events.py`（事件模型扩展）
  - `src/sagent/core/react_engine.py`、`src/sagent/core/plan_engine.py`、`src/sagent/core/agent_engine.py`（取消令牌、用量事件）
  - `src/sagent/core/cancellation.py`（新增）
  - `src/sagent/session/store.py`（会话搜索、轮次表、线程安全访问）
  - `src/sagent/session/models.py`（TurnRecord）
  - `src/sagent/context/context_manager.py`（用量只读快照）
  - `src/sagent/permissions/approval.py` 或新增 `src/sagent/server/approval.py`（Web 审批器）
  - `src/sagent/config/models.py`、`config.example.yaml`（server 配置）
  - `src/sagent/cli/event_presenter.py`（未知事件忽略）
  - `src/sagent/server/`（新增：app / schemas / runtime / event_bridge / stream_adapter / routers）
  - `web/`（新增：Vite + React + TypeScript 前端）
  - `tests/unit/`（后端单测）

## 推荐方案（业界 Agent 实践取舍）

以下取舍是本 spec 的架构基线，实现阶段不得随意替换：

1. 事件溯源式运行（event-sourced run）
   - Agent 只产出结构化事件，服务端是唯一权威状态，前端是纯投影，不做状态推断，避免多端不一致。
   - 每个 run 有独立 `run_id` 与状态机：`queued -> thinking -> tool_calling -> waiting_approval -> executing -> completed / failed / cancelled`。
2. 流式协议复用 AI SDK 的 UI Message Stream，而不是自定义协议
   - 后端直接输出 `text/event-stream`，帧格式对齐 AI SDK v5 UIMessageStream（`data: <json>` 帧、`data: [DONE]` 结束帧、`x-vercel-ai-data-stream: v1` 响应头）。
   - 协议映射集中在一个适配模块（`stream_adapter.py`），assistant-ui 与 `useChat` 无需额外转换层；后续 SDK 升级只改这一处。
   - 自定义状态通过 `data-*` 部件下发（`data-run-status` / `data-plan` / `data-usage` / `data-approval`），前端在 `onData` 回调里写入外部 store，不依赖 assistant-ui 内部 parts 结构。
3. 协作式取消，不强杀线程
   - 现有引擎是同步阻塞循环，线程无法被强制中断；用 `CancelToken` 在迭代边界与工具调用前检查，取消后由编排层统一收敛为 `cancelled`。
4. 审批是一等公民
   - pending 审批有 id、有超时、有审计、fail-safe 默认拒绝；超时与页面关闭都不会导致工具被静默放行。
5. 会话隔离 + 串行执行
   - 单会话同一时刻只允许一个 run（重复提交返回 409）；不同会话可并行，各自独立上下文与审批器。
6. 持久化事实与派生统计分离
   - `messages` 表仍是 append-only 事实源；`turns` 表只承载耗时与用量等派生统计，缺失时前端降级展示而不是报错。

## ADDED Requirements

### Requirement: Web 服务入口与配置

系统 SHALL 提供独立的 Web 服务入口 `python -m sagent.server`，通过 `server` 配置段控制监听地址、端口、CORS 来源与前端静态目录。

- 默认只监听 `127.0.0.1:8000`，默认 CORS 仅允许本机开发来源。
- Web 服务启动流程复用 CLI 的依赖装配顺序（LLM / 权限 / 工具注册表 / 上下文 / 记忆 / Skill / MCP / 会话存储），但不创建终端输入与 stdout presenter。
- CLI 的启动路径与参数保持不变。

#### Scenario: 启动 Web 服务

- **WHEN** 用户在项目根目录执行 `python -m sagent.server`
- **THEN** 服务在配置的 host/port 上监听，`GET /api/health` 返回 200 与服务版本信息
- **AND** 未构建前端时访问 `/` 返回明确的"前端未构建"提示，而不是 500

#### Scenario: 配置缺失

- **WHEN** 配置文件中不存在 `server` 段
- **THEN** 使用默认值启动，不报错

### Requirement: 会话级 Agent 运行时隔离

系统 SHALL 为每个会话维护独立的运行时，包含独立的 `ContextManager`、`SessionManager`、执行引擎与审批器，避免共享单会话状态。

- 运行时按 `session_id` 惰性创建，首次对话或首次加载上下文时构建。
- 运行时创建时通过 `SessionManager.switch_session(session_id)` 还原工作上下文与活动计划。
- 同一会话的并发 run 请求 SHALL 被拒绝并返回 409；不同会话可并行。
- 会话的增删改查直接基于 `SessionStore`，不复用 `SessionManager.delete_session`（该方法拒绝删除当前会话，与 Web 语义冲突）。

#### Scenario: 两个会话并行对话

- **WHEN** 用户在会话 A 触发 run 后，立即在会话 B 触发 run
- **THEN** 两个 run 各自独立执行，事件流互不串流
- **AND** 各自的上下文、计划状态与审批状态互不影响

#### Scenario: 同一会话重复提交

- **WHEN** 会话 A 已有 run 正在执行，用户再次向会话 A 发送消息
- **THEN** 服务返回 409，并说明该会话正在执行

#### Scenario: 运行时复用

- **WHEN** 用户在同一会话连续进行多轮对话
- **THEN** 复用同一个运行时实例，上下文延续，不重复加载历史

### Requirement: 会话管理 API

系统 SHALL 提供会话列表、创建、重命名、删除与搜索接口。

- `GET /api/sessions` 返回按 `updated_at` 降序的会话元数据（id、title、created_at、updated_at、message_count、mode），支持 `limit` / `offset`。
- `POST /api/sessions` 创建会话，可选标题，返回新会话元数据。
- `PATCH /api/sessions/{id}` 重命名会话，返回更新后的元数据。
- `DELETE /api/sessions/{id}` 删除会话：先取消该会话进行中的 run，再从运行时注册表移除，最后级联删除数据库记录。
- `GET /api/sessions/search?q=` 按关键词搜索，命中范围同时包含会话标题与消息内容。

#### Scenario: 搜索命中标题

- **WHEN** 存在标题包含"部署脚本"的会话，用户搜索"部署"
- **THEN** 结果中包含该会话，并标注命中来源为标题

#### Scenario: 搜索命中消息内容

- **WHEN** 某会话标题不含关键词，但其历史消息包含关键词
- **THEN** 结果中包含该会话，并返回命中片段

#### Scenario: 删除正在执行的会话

- **WHEN** 用户删除一个正在执行 run 的会话
- **THEN** 该 run 被取消，事件流以 `cancelled` 结束
- **AND** 会话及其消息被删除，后续访问返回 404

#### Scenario: 删除不存在的会话

- **WHEN** 用户删除不存在的会话 id
- **THEN** 返回 404

### Requirement: 对话流式接口

系统 SHALL 提供 `POST /api/sessions/{id}/messages` 接收用户输入并返回 AI SDK 兼容的 UI Message Stream。

- 请求体包含用户消息文本；响应为 `text/event-stream`。
- 文本增量以 `text-start` / `text-delta` / `text-end` 部件下发，保证前端可逐字渲染。
- 工具调用以 `tool-input-available` / `tool-output-available` 部件下发，携带 `toolCallId`、`toolName`、输入与输出。
- 响应结束时下发 `finish` 部件与 `data: [DONE]`。
- 首轮对话时若会话 id 未被使用过，系统 SHALL 先加载上下文再执行，不重复写入用户消息。

#### Scenario: 普通流式回答

- **WHEN** 用户发送"列出当前目录"
- **THEN** 前端按增量字符渲染助手回答，结束后消息状态为完成

#### Scenario: 会话不存在

- **WHEN** 用户向不存在的会话 id 发送消息
- **THEN** 返回 404

#### Scenario: 空消息

- **WHEN** 用户提交空文本或仅空白字符
- **THEN** 返回 400，不产生 run

### Requirement: 运行状态事件

系统 SHALL 在事件流中下发运行状态，覆盖：`thinking`、`tool_calling`、`waiting_approval`、`executing`、`completed`、`failed`、`cancelled`。

- 状态以 `data-run-status` 部件下发，包含 `run_id`、`status`、`iteration`（可选）、`message`（可选）。
- `tool_calling` 与 `executing` 由工具调用生命周期推导：`tool_call` 事件到达时进入 `tool_calling`，`tool_result` 返回后回到 `thinking` 或 `executing`。
- 终态只有一个，且在流关闭前必须下发。

#### Scenario: 工具调用状态流转

- **WHEN** Agent 决定调用 `read_file`
- **THEN** 前端依次收到 `tool_calling`、`tool-input-available`、`executing`、`tool-output-available`、`thinking`

#### Scenario: 执行失败

- **WHEN** 引擎抛出未预期异常
- **THEN** 事件流下发 `failed` 状态与错误摘要，随后 `finish` 与 `[DONE]`
- **AND** 前端展示失败状态而不是无限加载

### Requirement: 计划进度事件

系统 SHALL 在 Plan 模式下向前端下发计划结构与当前步骤进度。

- 计划状态以 `data-plan` 部件下发，字段包含 `task`、`status`、`current_step`、`total_steps`、`replans`、`steps[]`（`title`、`description`、`status`、`summary`）。
- 在计划开始、步骤开始、步骤完成、重规划、计划完成时各下发一次。
- `steps[].status` 取值对齐 `PlanState`：`pending / running / completed / failed / blocked / skipped`。

#### Scenario: 多步骤计划执行

- **WHEN** 任务被判定需要计划并拆解为 3 步
- **THEN** 前端显示"第 1/3 步"与步骤标题，随执行推进更新为 2/3、3/3
- **AND** 已完成的步骤展示其摘要

#### Scenario: 计划重规划

- **WHEN** 某步骤失败触发重规划
- **THEN** 前端收到更新后的步骤列表与 `replans` 计数，进度条按新列表重置

#### Scenario: ReAct 模式

- **WHEN** 当前轮次未使用计划
- **THEN** 不下发 `data-plan` 部件，前端不显示计划卡片

### Requirement: Web 权限审批

系统 SHALL 将 `ask` 决策的工具授权转为页面内审批。

- 需要审批时下发 `data-approval` 部件，包含 `approval_id`、`tool_name`、`arguments`、`reason`、`expires_at`。
- 前端通过 `POST /api/approvals/{approval_id}` 提交决策，动作取值为 `approve` / `deny` / `always`。
- `always` 在当前会话内对相同"工具 + 参数"不再询问。
- 等待审批期间运行状态为 `waiting_approval`；决策后恢复执行并下发审批结果。
- 超过 `permissions.ask_timeout` 未决策时按 `permissions.non_interactive` 收敛（默认拒绝），并下发超时结果。
- 审批结果 SHALL 进入现有权限审计日志。

#### Scenario: 页面批准

- **WHEN** `write_file` 触发审批，用户在页面点击"批准"
- **THEN** 工具继续执行，状态从 `waiting_approval` 回到 `executing`

#### Scenario: 页面拒绝

- **WHEN** 用户在页面点击"拒绝"
- **THEN** 工具不执行，拒绝说明作为工具观察结果回写上下文，Agent 继续后续推理

#### Scenario: 总是允许

- **WHEN** 用户对同一工具同一参数点击"总是允许"
- **THEN** 本会话后续相同请求不再触发审批

#### Scenario: 审批超时

- **WHEN** 用户在 `ask_timeout` 内未作决策
- **THEN** 按配置收敛为拒绝（默认），前端展示"审批已超时"

#### Scenario: 审批过期后提交

- **WHEN** 用户对已超时的 approval_id 提交决策
- **THEN** 返回 409，不改变运行状态

### Requirement: 运行取消

系统 SHALL 提供 `POST /api/runs/{run_id}/cancel` 停止进行中的运行。

- 取消采用协作式：引擎在每次迭代开始与每个工具调用前检查取消令牌。
- 取消后不再发起新的 LLM 请求与工具调用，事件流下发 `cancelled` 终态并正常关闭。
- 取消已结束的 run 返回 409；取消不存在的 run 返回 404。
- 已产生的部分消息与工具结果 SHALL 按既有增量保存规则落盘，不丢已完成的上下文。

#### Scenario: 用户点击停止

- **WHEN** Agent 正在等待 LLM 响应时用户点击停止
- **THEN** 当前请求返回后不再进入下一轮，状态变为 `cancelled`，前端停止接收增量

#### Scenario: 停止后继续提问

- **WHEN** 某轮被取消后用户继续发送新消息
- **THEN** 新 run 正常执行，上下文包含取消前已完成的消息

### Requirement: 轮次用量统计

系统 SHALL 记录并展示每轮交互的耗时与 token 用量。

- 每轮结束写入一条 `TurnRecord`：`session_id`、`turn_index`、`started_at`、`finished_at`、`duration_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`outcome`、`mode`。
- 用量取该轮 `ContextManager` 累计 usage 的差值，避免跨轮重复计数。
- 会话级累计用量为全部轮次之和；缺失轮次记录时前端显示"未记录"，不虚构数值。
- 历史消息接口（`GET /api/sessions/{id}/messages`）同时返回会话累计用量；前端以历史累计加本轮实时累计展示，本轮结束后把实时用量折叠进历史累计，避免显示回退为"未记录"或重复计数。

#### Scenario: 单轮用量

- **WHEN** 一轮对话触发 2 次 LLM 请求
- **THEN** 该轮记录 `total_tokens` 为两次之和，`duration_ms` 覆盖从收到用户消息到本轮结束

#### Scenario: 历史 CLI 会话

- **WHEN** 用户查看由 CLI 产生、没有轮次记录的历史会话
- **THEN** 轨迹中消息完整展示，耗时与用量标注为"未记录"

### Requirement: 轨迹查询 API

系统 SHALL 提供 `GET /api/sessions/{id}/trace` 返回会话完整轨迹。

- 返回会话元数据、系统初始提示词、轮次列表与总用量。
- 系统初始提示词优先取持久化历史中的首条 `system` 消息；不存在时回退为当前引擎构建的默认提示词（含记忆前缀与 Skill 目录前缀）。
- 轮次按真实用户输入切分：每轮包含用户输入、助手文本、工具调用及结果、该轮耗时与用量；计划模式下引擎注入的步骤指令（以 `user` 消息持久化）属于当前轮次的内部上下文，不单独计为一轮。
- 工具调用与结果按 `tool_call_id` 关联；无法关联时保留为独立条目。
- 压缩事件 SHALL 以"上下文已压缩"标记插入对应位置，说明被覆盖的消息区间。

#### Scenario: 查看完整轨迹

- **WHEN** 用户打开某会话的轨迹 Tab
- **THEN** 页面显示系统提示词全文、交互轮数、逐轮的用户/上下文/助手内容与工具细节

#### Scenario: 含压缩事件的会话

- **WHEN** 会话历史中发生过一次上下文压缩
- **THEN** 轨迹在对应位置显示压缩标记与被覆盖的 seq 区间

#### Scenario: 会话不存在

- **WHEN** 请求不存在的会话轨迹
- **THEN** 返回 404

### Requirement: 前端应用骨架

系统 SHALL 提供 `web/` 前端工程，使用 React + Vite + TypeScript，集成 assistant-ui 与 Vercel AI SDK。

- 布局为左侧会话侧栏 + 右侧主区，主区以 Tab 承载"对话"与"轨迹"。
- 两个 Tab 的内容常驻挂载、仅切换显隐：切换 Tab 不重复拉取对话消息，对话流在轨迹 Tab 下继续接收；切入轨迹 Tab 时刷新轨迹数据。
- 开发模式通过 Vite proxy 将 `/api` 转发到后端；生产模式由 FastAPI 托管构建产物。
- 左侧侧栏展示会话 id 与名称，支持新建、搜索、重命名、删除、切换。
- 删除与重命名需要二次确认，避免误操作。

#### Scenario: 切换会话

- **WHEN** 用户点击侧栏中另一个会话
- **THEN** 中间区域加载该会话历史消息，Tab 保持当前选择
- **AND** 若该会话有运行中的 run，界面恢复到对应状态

#### Scenario: 新建会话

- **WHEN** 用户点击"新建会话"
- **THEN** 侧栏插入新会话并自动选中，中间区域显示空对话欢迎态

#### Scenario: 搜索会话

- **WHEN** 用户在侧栏搜索框输入关键词
- **THEN** 列表实时过滤为匹配结果，清空后恢复完整列表

### Requirement: 前端对话视图

系统 SHALL 在对话 Tab 中提供发送、停止、审批、状态展示、计划进度与用量展示。

- 输入框支持 Enter 发送、Shift+Enter 换行；发送后按钮切换为停止。
- 助手消息流式渲染，支持 Markdown 代码块；工具调用以卡片展示名称、参数与结果。
- 消息区下方展示当前运行状态中文标签：思考中 / 调用工具 / 等待审批 / 执行中；本轮结束（已完成 / 失败 / 已取消）后状态条隐藏。
- 计划模式下展示计划卡片：当前第几步、总步数、步骤列表与各步状态。
- 每轮消息下方展示该轮耗时与 token 用量；消息区下方单独一行常驻展示会话累计 Token 用量，与运行状态分行。
- 等待审批时展示审批卡片，包含工具名、参数摘要与三个动作按钮。

#### Scenario: 流式渲染中断线

- **WHEN** 事件流异常断开
- **THEN** 界面提示连接中断，状态置为失败，并提供重新加载入口

#### Scenario: 审批卡片出现

- **WHEN** 收到 `data-approval` 部件
- **THEN** 对话区出现审批卡片，停止按钮保持可用
- **AND** 点击任一动作后卡片从对话区移除，不随后续审批叠加
- **AND** 决策失败时在卡片内提示错误

### Requirement: 前端轨迹视图

系统 SHALL 在轨迹 Tab 中展示系统提示词、轮次概览与逐轮详情。

- 顶部展示系统提示词全文，可折叠。
- 轮次概览展示总轮数、总耗时、总 token。
- 每轮为可展开区块，包含用户输入、助手回答、工具调用明细、耗时与 token。
- 压缩事件以分隔条形式插入。

#### Scenario: 展开某一轮

- **WHEN** 用户点击第 2 轮
- **THEN** 展开该轮全部消息与工具调用详情，其余轮次保持收起

#### Scenario: 复制系统提示词

- **WHEN** 用户点击系统提示词区域的复制按钮
- **THEN** 提示词全文进入剪贴板，并给出成功提示

### Requirement: 前端构建与生产托管

系统 SHALL 支持前端构建后由后端直接托管。

- `web/` 下 `npm run build` 产出到 `web/dist`。
- 后端按 `server.static_dir` 挂载静态资源，并对未知非 `/api` 路径回退到 `index.html`，保证前端路由刷新可用。
- 静态目录不存在时不影响 API 正常工作。

#### Scenario: 生产模式刷新页面

- **WHEN** 用户在 `/sessions/abc123/trace` 直接刷新页面
- **THEN** 返回 `index.html`，前端路由正常渲染

#### Scenario: 静态目录缺失

- **WHEN** 未执行前端构建就启动后端
- **THEN** `/api/*` 全部可用，`/` 返回明确的未构建提示

## MODIFIED Requirements

### Requirement: Agent 事件模型

`AgentEvent` 在保留现有字段与语义的前提下扩展：

- 新增事件类型：`run_status`、`approval_required`、`approval_resolved`、`usage`。
- 新增可选字段：`status`、`approval_id`、`approval_action`、`usage`（`dict[str, int]`）、`latency_ms`。
- 现有字段与取值保持不变；`with_plan_context` 行为不变。
- `llm_completed` 事件 SHALL 携带该次请求的 usage 与 `latency_ms`（可得时填充）。
- `EventPresenter` 对未知事件类型 SHALL 静默忽略，不得抛异常中断 CLI。

#### Scenario: CLI 行为不变

- **WHEN** 使用 CLI 完成一轮 ReAct 对话
- **THEN** 输出内容与扩展前一致，新增事件不产生任何可见输出

#### Scenario: 用量事件

- **WHEN** 一轮对话包含 3 次 LLM 请求
- **THEN** 每次 `llm_completed` 事件携带该次请求的 usage 与耗时

### Requirement: 引擎取消能力

`ReActEngine` 与 `PlanEngine` SHALL 支持可选的协作式取消。

- 构造函数新增可选取消令牌参数，缺省为 `None`，此时行为与当前完全一致。
- 每次迭代开始前、每次工具调用前检查令牌；已取消时抛出 `RunCancelled`。
- `PlanEngine` 在步骤边界与重规划前同样检查。
- `RunCancelled` 不得被引擎吞掉为普通答案；由调用方（CLI 或 Web 编排层）负责收敛。
- CLI 捕获该异常后按现有中断语义处理（等同于 `KeyboardInterrupt` 分支的输出）。

#### Scenario: CLI 不使用取消令牌

- **WHEN** CLI 未传入取消令牌
- **THEN** 引擎行为与既有实现逐字一致

#### Scenario: 取消发生在工具调用前

- **WHEN** 令牌在工具调用前被置位
- **THEN** 该工具不被执行，抛出 `RunCancelled`

### Requirement: 会话存储

`SessionStore` 在保留现有表结构与语义的前提下扩展：

- 新增 `turns` 表（`id`、`session_id`、`turn_index`、`started_at`、`finished_at`、`duration_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`outcome`、`mode`），带 `session_id` 外键级联删除与 `(session_id, turn_index)` 唯一索引。
- 新增 `append_turn` / `list_turns` 方法。
- 新增 `search_sessions(keyword, limit)`：标题 `LIKE` 与 FTS5 消息命中合并，按会话聚合去重，返回会话元数据与命中摘要；FTS5 不可用时降级为 `LIKE`。
- 连接 SHALL 支持跨线程访问（`check_same_thread=False`），所有语句执行经内部锁串行化，保证 Web 服务多线程调用安全。
- 现有 `search`（仅搜消息）行为保持不变，CLI `/search` 不受影响。

#### Scenario: 存量数据库升级

- **WHEN** 使用旧版本创建的 `sessions.db` 启动服务
- **THEN** 自动创建 `turns` 表，既有会话与消息不受影响

#### Scenario: 并发行写入

- **WHEN** 两个会话的运行时同时保存消息
- **THEN** 无 `SQLite objects created in a thread can only be used in that same thread` 异常，数据完整

### Requirement: 上下文用量快照

`ContextManager` SHALL 提供只读用量快照方法，返回当前累计 `prompt_tokens`、`completion_tokens`、`total_tokens`。

- 仅新增读取入口，不改变 `record_llm_usage` 的累计逻辑与压缩行为。

#### Scenario: 计算单轮差值

- **WHEN** 编排层在 run 开始前与结束后各取一次快照
- **THEN** 两次差值即为该轮用量

## REMOVED Requirements

无。
