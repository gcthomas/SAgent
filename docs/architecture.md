# 架构说明

本文件描述 SAgent 的整体架构，聚焦两件事：模块职责与依赖分层，以及一次请求从 CLI 输入到最终答复的全链路数据流。文件级实现细节、配置字段与测试事实以源码、测试和 `config.example.yaml` 为准，本文件不重复。

## 一、模块职责与依赖分层

### 依赖分层

从下到上共 6 层。箭头表示「依赖」方向：上层可依赖下层，下层不反向依赖上层。

```text
L5 入口    cli/                                       组合并驱动全部子系统
L4 会话    session/                                   依赖 context、core.plan_state
L3 编排    core/                                      依赖 tools、context、llm、config
L2 工具    tools/                                     依赖 permissions、memory、config
L1 能力    llm/  context/  memory/  permissions/       依赖 config、observability
L0 基础    config/  observability/                      observability 依赖 config
```

### 模块职责

| 层 | 模块 | 职责 | 主要内部依赖 |
| --- | --- | --- | --- |
| L0 | `config/` | Pydantic 配置模型与 YAML 加载、环境变量覆盖 | 无（仅 pydantic/yaml） |
| L0 | `observability/` | 日志、OTel Span/指标、脱敏、本地 JSON Lines 导出 | `config` |
| L1 | `llm/` | 封装 openai SDK，统一对话调用与流式事件 | `config`、`observability` |
| L1 | `context/` | 消息历史、token 统计与分层压缩 | `config`、`observability` |
| L1 | `memory/` | 记忆文件存储、安全扫描与反思整理 | `observability` |
| L1 | `permissions/` | 权限策略、审批交互与审计日志 | `observability`、`terminal_input` |
| L2 | `tools/` | 工具基类、注册表、内置工具与 MCP 接入 | `permissions`、`memory`、`config` |
| L3 | `core/` | ReAct / Plan 执行引擎、事件模型与提示词 | `tools`、`context`、`llm`、`config` |
| L4 | `session/` | 会话元数据、消息与计划快照的持久化 | `context`、`core.plan_state` |
| L5 | `cli/` | 参数解析、依赖装配、交互循环与事件展示 | 上述全部 |

`terminal_input.py` 为 CLI 与审批交互共享的终端输入工具，不属于上述分层。

### 关键解耦点

- **core 只依赖抽象。** 引擎通过 `ToolRegistry` 调用工具，不感知具体工具与权限实现；`core/` 不 import `session/`、`memory/`、`permissions/`、`cli/`。
- **会话管理器注入而非依赖。** CLI 构造 `AgentEngine` 后以 `engine._session_manager` 注入 `SessionManager`，core 仅在计划模式下调用其 `set_active_plan` / `get_active_plan`，不作为构造依赖。
- **上下文管理器可选注入。** `ContextManager` 以构造参数注入引擎；缺省为 `None` 时引擎退化为单轮内存消息列表，不跨轮次持久化。
- **LLM 以调用约定注入。** `context` 与 `memory` 需要 LLM 时，由调用方注入具备 `chat(messages, tools=None)` 且返回含 `content` 对象的客户端，因此这两个模块不 import `llm/`。
- **权限是执行期单点闸门。** `permissions/` 由 `tools/registry.py` 注入调用；依赖方向为 `tools → permissions`，反向不依赖。
- **observability 是纯横切依赖。** 所有业务模块引用它记录日志与 Span，但不被任何业务模块反向依赖。

## 二、单次请求全链路数据流

### 2.1 启动与依赖装配（`cli/app.py`，每进程一次）

1. `main.py` 调用 `sagent.cli.main()` → `run()`。
2. `run()`：解析 `--config` → `load_config()` 加载配置 → `setup_logging()` / `setup_observability()` / `setup_content_capture()` 初始化日志与可观测性 → 打开 `TerminalInput` → 进入 `_run_interactive()`。
3. `_run_interactive()` 按依赖顺序装配：

   | 顺序 | 构建物 | 说明 |
   | --- | --- | --- |
   | 1 | `LLMClient(config.llm)` | 主对话客户端；上下文摘要启用时另建 `summary_llm` |
   | 2 | `PermissionEnforcer` | 仅 `permissions.enabled` 时构建；YAML 的 ask/allow/deny 规则按 last-match-wins 组装为固定优先级 deny > allow > ask |
   | 3 | `build_default_registry(permission)` | 注册 `read_file`、`write_file`、`run_shell` |
   | 4 | `ContextManager` | 持有 `summary_llm` 与压缩策略实例 |
   | 5 | `MemoryStore` + `MemoryManager` | 仅 `memory.enabled`；向注册表追加记忆工具 |
   | 6 | `MCPSessionManager` + `build_mcp_providers()` | 仅 `mcp.enabled`；以 `register_provider` 批量注册 MCP 工具 |
   | 7 | `SessionStore` + `SessionManager(store, context_manager)` | 仅 `session.enabled`；构造时向 `ContextManager` 注册压缩回调 |
   | 8 | `EventPresenter` + `AgentEngine` | 将 `presenter.handle` 作为 `on_event` 注入引擎；随后注入 `engine._session_manager` |
   | 9 | `memory_manager.build_memory_prefix()` | 记忆启用时在会话开始时冻结一次，整个会话复用 |

### 2.2 交互循环（每轮用户输入）

1. `reader.read()` 读取输入；空输入跳过；`exit` / `quit` / `:q` 退出。
2. `parse_command()` 解析斜杠命令；命中则交给 `_dispatch_session_command()` 处理并 `continue`，不进入引擎。
3. 会话启用且当前无会话时 `ensure_current_session()` 延迟创建（避免启动即产生空会话）。
4. `with Span("agent.run", session_id=...)`：设置计划策略属性 → `presenter.begin_turn()` → `engine.run(user_input, memory_prefix=...)`。
5. 引擎返回后：设置 Span 的 `outcome` / `mode` / `answer_length`；非流式或本轮无流式输出时打印兜底答复。
6. `session_manager.save_current()` 增量保存本轮新增消息（`session.auto_save` 时）。

### 2.3 ReAct 执行（`core/react_engine.py`）

引擎注入 `ContextManager` 时走跨轮次上下文路径 `_run_with_context()`：

1. `cm.ensure_system_prompt(memory_prefix + REACT_SYSTEM_PROMPT)`，将系统提示词置于历史首条。
2. `cm.add_message({role: user, ...})` 写入本次任务。
3. `with Span("agent.react")`，按 `agent.max_iterations` 循环：
   - `cm.get_messages()` 取消息（超阈值时自动压缩，见 2.7）；
   - `llm.chat(messages, tools=registry.to_openai_schemas(), on_event=流式回调)`；
   - `cm.record_llm_usage(response.usage)` 记录精确 token 基准；
   - **无工具调用** → 记录 assistant 消息，发出 `react_completed` 事件，返回最终答案；
   - **有工具调用** → 记录 assistant（含 `tool_calls`）；逐个执行工具（见 2.4），将观察结果作为 `role="tool"` 消息写回历史。
4. 达到最大迭代仍未结束：设置 `outcome=max_iterations`，`with Span("agent.finalize")` 发起一次不带工具的收尾请求。

未注入 `ContextManager` 时使用等价的本地内存消息列表路径。

### 2.4 工具调用与权限闸门（`tools/registry.py`）

`registry.execute(name, arguments)` 的固定顺序：

1. 按名查找工具，未找到返回 `错误: ...`；
2. JSON 字符串参数解析为 dict；
3. `tool.validate_args()` 经 pydantic 校验为参数模型；
4. **权限闸门**：`permission.authorize(name, parsed)`，被拒时直接把拒绝说明作为结果返回，不执行工具；
5. 授权通过后记录 `tool_call` 日志，`with Span("tool.execute")` 调用 `tool.run(args_model)`；
6. 工具返回字符串；执行期异常或参数错误统一转为以 `错误:` 开头的字符串，不向引擎抛出。

审批决策与审计在 `permissions/enforcer.py` 完成：`ALLOW` 直接放行、`DENY` 直接拒绝、`ASK` 委托交互审批（超时/非交互按 `non_interactive` 收敛），并记录 `permission_decision` 审计日志。

### 2.5 Plan 执行（`core/agent_engine.py` + `core/plan_engine.py`）

`AgentEngine.run()` 按 `planning.strategy` 选择路径：

- `never` → 直接 ReAct；
- `always` → Plan；
- `adaptive` → 由 `_needs_plan()` 调用 LLM 结构化判断，解析失败按 ReAct 继续。

Plan 路径 `PlanEngine.run()`：

1. 确保系统提示词并写入用户原始任务；
2. `with Span("plan.decompose")` 调用 LLM 拆解步骤，失败则回退 ReAct；
3. 逐步执行：`with Span("plan.step")`，每步复用 `ReActEngine.run()`，以 `plan_projection` 传入计划状态投影（仅进入当次请求、不写入历史），`persist_task=False` 避免子步骤提示污染历史；
4. 步骤失败且错误以 `错误:` 开头或抛异常时，在 `planning.max_replans` 内 `_replan()`，耗尽则标记 `blocked` 并停止后续步骤；
5. `with Span("plan.summarize")` 汇总各步骤结果，`blocked` 时明确报告未完成部分；
6. 计划状态经 `_on_plan_state_change` 回调（即 `SessionManager.set_active_plan`）持久化为快照；最终汇总作为 assistant 消息写回上下文。

### 2.6 LLM 客户端与流式事件（`llm/client.py`）

- `chat()` 按 `llm.stream` 选择 `stream_chat()` 或非流式路径，两者均包在 `Span("gen_ai.chat")` 内并设置 `gen_ai.*` 语义属性。
- 流式路径聚合增量并依次回调 `LLMStreamEvent`（`text_delta` / `tool_call_delta` / `completed`）。
- 引擎 `_on_llm_event()` 将其转为 `llm_text_delta` / `llm_completed` 的 `AgentEvent`，交由 `EventPresenter` 渲染。
- `EventPresenter` 仅对顶层（`scope="top_level"`，`mode` 为 react/plan）增量做流式写出，按换行或字符/时间阈值刷新缓冲。

### 2.7 上下文压缩（横切，`context/context_manager.py`）

`cm.get_messages()` 在 `token_count` 超过 `max_context_tokens * compression_threshold` 时触发 `_compress()`，按成本从低到高依次尝试，任一层降到安全线以下即停止：

1. 工具输出截断（`add_message` 时对内联 tool 消息先执行一次，`_compress` 时全量扫描补充）；
2. 工具消息卸载（过期工具消息对替换为简短摘要）；
3. LLM 摘要压缩（保留语义，摘要成功后回调 `SessionManager` 记录压缩事件）；
4. 滑动窗口裁剪（兜底丢弃旧消息）。

token 计数采用混合校准：`record_llm_usage()` 记录 API 返回的精确 `prompt_tokens` 作为基准，后续新增消息只估算 delta；压缩会重置基准。

### 2.8 会话持久化（横切，`session/manager.py`）

- 每条消息在 `ContextManager` 内分配单调递增 `seq`；`save_current()` 通过 `export_new_messages(persisted_seq)` 导出新增消息并增量写入 SQLite，随后推进游标。
- 第三层摘要压缩触发时，`ContextManager` 回调 `SessionManager._on_compaction()` 写入压缩事件。
- `switch_session()` 通过 `build_working_context()` 还原「最近摘要 + 其后消息」并 `cm.load_messages()` 重建工作上下文。
- 计划快照经 `set_active_plan()` 单独持久化，与消息历史分离。

### 2.9 可观测性 Span 结构（横切，`observability/context.py`）

一次请求的 Span 形成树：`agent.run`（根，CLI 层）→ `agent.react` / `plan.decompose` / `plan.step` / `plan.summarize` → `gen_ai.chat`（每次 LLM 调用）与 `tool.execute`（每次工具执行）。`Span` 是 OTel SDK 的薄包装层，退出时注入业务属性、记录指标并在根 Span 处按周期 flush；导出失败或指标异常不影响 Agent 主流程。

### 2.10 退出

`exit` / `quit` / `:q` 或 `EOF` / `Ctrl+C` 时：`save_current()` 落盘 → `mcp_session_manager.shutdown()` 关闭 MCP 会话 → `flush_metrics()` → `close_observability()`。
