# Tasks

- [x] Task 1: 扩展事件模型与取消能力：为 Web 端提供运行状态、审批与用量事件的底层契约。
  - [x] SubTask 1.1: 在 `src/sagent/core/events.py` 中扩展 `AgentEvent`：新增事件类型 `run_status`、`approval_required`、`approval_resolved`、`usage`，新增可选字段 `status`、`approval_id`、`approval_action`、`usage`、`latency_ms`，保持既有字段与 `with_plan_context` 行为不变。
  - [x] SubTask 1.2: 新增 `src/sagent/core/cancellation.py`，定义 `CancelToken`（`cancel()` / `is_cancelled()` / `raise_if_cancelled()`）与 `RunCancelled` 异常。
  - [x] SubTask 1.3: 在 `ReActEngine.__init__` 增加可选 `cancel_token` 参数（默认 `None`），在每次迭代开始与每个工具调用前调用 `raise_if_cancelled()`；`llm_completed` 事件填充 usage 与 `latency_ms`。
  - [x] SubTask 1.4: 在 `PlanEngine` 的步骤边界与重规划前增加同样的取消检查，并在转发事件时保留新增字段。
  - [x] SubTask 1.5: 在 `EventPresenter` 中按事件类型白名单渲染，未知类型静默忽略；CLI 捕获 `RunCancelled` 后走既有中断分支。
  - [x] SubTask 1.6: 补充单测：未知事件不抛异常、取消令牌在工具调用前生效、`llm_completed` 携带 usage。

- [x] Task 2: 会话存储与用量统计扩展：支撑 Web 并发访问、会话搜索与轮次统计。
  - [x] SubTask 2.1: `SessionStore` 连接改为允许跨线程，所有执行经内部 `threading.Lock` 串行化；保证既有调用方签名不变。
  - [x] SubTask 2.2: 新增 `turns` 表与索引，新增 `append_turn` / `list_turns` 方法；对旧库自动建表。
  - [x] SubTask 2.3: 新增 `search_sessions(keyword, limit)`：标题 `LIKE` 与 FTS5 消息命中合并去重，返回会话元数据 + 命中来源 + 摘要；FTS5 不可用时降级。
  - [x] SubTask 2.4: 在 `src/sagent/session/models.py` 新增 `TurnRecord` 模型，并在 `ContextManager` 增加只读用量快照方法（不改累计逻辑）。
  - [x] SubTask 2.5: 补充单测：旧库升级建表、跨线程写入、标题与内容两类搜索命中、用量快照差值。

- [x] Task 3: Web 审批器：把 terminal 阻塞审批替换为可等待的页面审批。
  - [x] SubTask 3.1: 新增 `src/sagent/server/approval.py`，实现 `WebApprovalHandler`：为每次请求生成 `approval_id`，登记 `PendingApproval`，阻塞等待 `threading.Event` 或超时，返回与 `InteractiveApprovalHandler` 相同的 `ConfirmAction` 语义。
  - [x] SubTask 3.2: 支持 `always` 动作：在实例内按"工具 + 规范化参数"记忆，命中后直接返回 `ALLOWED_ALWAYS`。
  - [x] SubTask 3.3: 超时按 `non_interactive` 收敛并标记过期；对过期 approval 的再次提交返回冲突；审批结果写入既有权限审计日志。
  - [x] SubTask 3.4: 审批登记/决策时通过回调向事件桥上抛 `approval_required` / `approval_resolved` 事件。
  - [x] SubTask 3.5: 补充单测：批准、拒绝、总是允许、超时、过期提交。

- [x] Task 4: 后端服务骨架与配置：提供可启动的 FastAPI 应用与配置项。
  - [x] SubTask 4.1: 在 `src/sagent/config/models.py` 新增 `ServerConfig`（`host`、`port`、`cors_origins`、`static_dir`、`enable_cors`）并挂到 `AppConfig`；同步 `config.example.yaml`。
  - [x] SubTask 4.2: 新增 `src/sagent/server/__init__.py`、`app.py`、`__main__.py`：按 CLI 相同顺序装配 LLM / 权限 / 工具注册表 / 上下文 / 记忆 / Skill / MCP / 会话存储，但不创建 `TerminalInput` 与 stdout presenter。
  - [x] SubTask 4.3: 实现 `GET /api/health`，返回状态与版本；实现统一错误响应结构（`code`、`message`、`detail`）。
  - [x] SubTask 4.4: 实现 CORS 配置与静态资源挂载：`static_dir` 存在时挂载并提供 SPA 回退，缺失时 `/` 返回"前端未构建"提示。
  - [x] SubTask 4.5: 补充单测：健康检查、配置缺省值、静态目录缺失兜底。

- [x] Task 5: 会话级运行时注册表：解决单会话模型与 Web 并发的冲突。
  - [x] SubTask 5.1: 新增 `src/sagent/server/runtime.py`：`SessionRuntime`（持有 `ContextManager`、`SessionManager`、引擎、审批器、当前 run）与 `RuntimeRegistry`（按 `session_id` 惰性创建、获取、释放）。
  - [x] SubTask 5.2: 运行时创建时调用 `switch_session` 还原上下文与活动计划；释放时保存并关闭连接。
  - [x] SubTask 5.3: 实现单会话互斥：同一会话已有活跃 run 时拒绝新 run（返回 409 语义）；不同会话可并行。
  - [x] SubTask 5.4: 实现会话删除编排：先取消 run，再释放运行时，最后走 `SessionStore.delete_session`。
  - [x] SubTask 5.5: 补充单测：并行两会话互不干扰、重复提交被拒、删除进行中会话后访问 404。

- [x] Task 6: 事件桥与 AI SDK 协议适配：把同步 `AgentEvent` 映射为 UI Message Stream。
  - [x] SubTask 6.1: 新增 `src/sagent/server/event_bridge.py`：`EventBridge` 以线程安全队列收集同步回调产生的事件，支持关闭与终态标记。
  - [x] SubTask 6.2: 新增 `src/sagent/server/stream_adapter.py`：将事件序列转为 AI SDK UI Message Stream 帧（`text-start/text-delta/text-end`、`tool-input-available/tool-output-available`、`finish`、`data: [DONE]`），并设置 `x-vercel-ai-data-stream: v1` 响应头。
  - [x] SubTask 6.3: 实现 `data-run-status` / `data-plan` / `data-usage` / `data-approval` 四类自定义部件，字段严格对齐 spec 中定义的契约。
  - [x] SubTask 6.4: 把 `tool_call` / `tool_result` 事件推导为 `tool_calling` / `executing` / `thinking` 状态流转，并保证流关闭前必有一个终态。
  - [x] SubTask 6.5: 引擎异常映射为 `failed` 终态并携带错误摘要；`RunCancelled` 映射为 `cancelled`。
  - [x] SubTask 6.6: 补充单测：帧序列正确、终态唯一、异常与取消的收敛行为。

- [x] Task 7: 会话与轨迹 API：实现左侧列表与轨迹 Tab 的数据来源。
  - [x] SubTask 7.1: 新增 `src/sagent/server/schemas.py`，定义会话、消息、计划、审批、轮次、轨迹的请求/响应模型。
  - [x] SubTask 7.2: 新增 `src/sagent/server/routers/sessions.py`：`GET /api/sessions`、`POST /api/sessions`、`PATCH /api/sessions/{id}`、`DELETE /api/sessions/{id}`、`GET /api/sessions/search`。
  - [x] SubTask 7.3: 实现 `GET /api/sessions/{id}/messages` 返回会话历史消息（供切换会话时加载）。
  - [x] SubTask 7.4: 实现 `GET /api/sessions/{id}/trace`：系统初始提示词（首条 `system` 消息，缺失时回退当前引擎默认提示词）、按用户消息切分的轮次、`tool_call_id` 关联、压缩事件标记、轮次与总用量。
  - [x] SubTask 7.5: 补充单测：会话 CRUD、搜索两类命中、轨迹切分与工具关联、压缩事件标记、不存在会话返回 404。

- [x] Task 8: 对话与运行控制 API：串起请求、流、审批与取消。
  - [x] SubTask 8.1: 新增 `src/sagent/server/routers/chat.py`：`POST /api/sessions/{id}/messages`，校验非空、会话存在与互斥，返回 `StreamingResponse`。
  - [x] SubTask 8.2: 在请求生命周期内启动后台线程执行引擎，主协程消费事件桥并转为 SSE 帧；首轮先加载上下文；结束后写入 `TurnRecord` 并保存消息。
  - [x] SubTask 8.3: 新增 `src/sagent/server/routers/runs.py`：`POST /api/runs/{run_id}/cancel`，区分 404 与 409。
  - [x] SubTask 8.4: 新增 `src/sagent/server/routers/approvals.py`：`POST /api/approvals/{approval_id}`，校验动作枚举与过期，返回决策结果。
  - [x] SubTask 8.5: 补充单测：流式帧序列、409/404 分支、取消后落盘、审批决策闭环。

- [x] Task 9: 前端工程骨架与会话侧栏：搭建与后端联通的最小可运行前端。
  - [x] SubTask 9.1: 创建 `web/`（Vite + React + TypeScript），配置 `/api` 代理到后端与基础路径别名。
  - [x] SubTask 9.2: 实现布局：左侧会话侧栏 + 右侧"对话 / 轨迹"双 Tab 容器。
  - [x] SubTask 9.3: 实现会话列表：展示 id 与名称、当前会话高亮、新建、切换、重命名（二次确认）、删除（二次确认）。
  - [x] SubTask 9.4: 实现侧栏搜索框，接入 `GET /api/sessions/search`，做输入防抖；清空恢复完整列表。
  - [x] SubTask 9.5: 实现统一 API 客户端与错误提示（含 404 / 409 的中文文案）。

- [x] Task 10: 前端对话视图：接入 assistant-ui 与 AI SDK，实现流式对话与状态展示。
  - [x] SubTask 10.1: 集成 `@assistant-ui/react` 与 `@ai-sdk/react` 的 `useChat`，指向 `POST /api/sessions/{id}/messages`，渲染助手消息与 Markdown 代码块。
  - [x] SubTask 10.2: 实现输入区：Enter 发送、Shift+Enter 换行；执行中发送按钮切换为停止，调用 cancel 接口。
  - [x] SubTask 10.3: 通过 `onData` 接收自定义部件并写入外部 store：运行状态、计划、用量、审批。
  - [x] SubTask 10.4: 实现状态条：思考中 / 调用工具 / 等待审批 / 执行中 / 已完成 / 失败 / 已取消。
  - [x] SubTask 10.5: 实现工具调用卡片（名称、参数、结果）与审批卡片（工具名、参数摘要、批准/拒绝/总是允许）。
  - [x] SubTask 10.6: 实现计划卡片：第几步 / 总步数、步骤列表与状态；ReAct 模式不渲染。
  - [x] SubTask 10.7: 实现每轮耗时、token 用量与会话累计用量展示；缺失显示"未记录"。

- [x] Task 11: 前端轨迹视图与生产托管：完成轨迹 Tab 与构建部署链路。
  - [x] SubTask 11.1: 实现轨迹 Tab：可折叠的系统提示词（含复制）、轮次概览（轮数、总耗时、总 token）。
  - [x] SubTask 11.2: 实现逐轮展开区块：用户输入、助手回答、工具调用明细、耗时与 token；压缩事件以分隔条展示。
  - [x] SubTask 11.3: 配置 `npm run build` 输出到 `web/dist`，并在后端按 `static_dir` 挂载与 SPA 回退。
  - [x] SubTask 11.4: 在 `README.md` 补充启动方式（后端 `python -m sagent.server`、前端 `npm install && npm run dev`、生产构建）。
  - [x] SubTask 11.5: 端到端手动验证：新建会话 → 流式提问 → 触发审批 → 停止 → 查看轨迹 → 搜索与删除。

- [x] Task 12: 验收补齐：Markdown 代码高亮、事件流中断提示与轨迹总耗时（checklist 核验失败项）。
  - [x] SubTask 12.1: 助手消息接入 Markdown 渲染与代码块高亮，保留现有流式文本展示逻辑。
  - [x] SubTask 12.2: 对话事件流断开时展示"连接中断"提示与重新加载入口。
  - [x] SubTask 12.3: 轨迹轮次概览补充总耗时（按轮次 `duration_ms` 汇总，缺失显示"未记录"）。

# Task Dependencies

- Task 2 依赖 Task 1（用量快照与事件字段先用后接）
- Task 3 依赖 Task 1（审批事件契约）
- Task 4 依赖 Task 2（配置与存储就绪）
- Task 5 依赖 Task 3、Task 4（审批器与装配骨架）
- Task 6 依赖 Task 1（事件字段）
- Task 7 依赖 Task 2、Task 5（存储扩展与运行时）
- Task 8 依赖 Task 6、Task 7（协议适配与数据接口）
- Task 9 依赖 Task 4（后端可启动）
- Task 10 依赖 Task 8、Task 9
- Task 11 依赖 Task 7、Task 10
