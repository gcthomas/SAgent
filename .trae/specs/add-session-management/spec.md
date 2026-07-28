# 会话管理能力 Spec

## Why

当前 SAgent 的对话上下文只存在于单次进程运行内存中（`ContextManager._messages`），进程退出即丢失，用户无法保存、切换或回溯历史会话。同时缺乏一个持久化的会话存储层，使得后续的「记忆能力」（跨会话记忆、长期记忆检索）无从建立。需要引入 **会话（Session）管理能力**：支持创建、切换会话，持久化保存会话历史，并为后续记忆能力提供可检索的数据基础。

## 推荐方案（结合业界最佳实践）

参考业界 Agent 产品（Claude Code / Cursor 的会话列表、OpenAI Assistants 的 thread 概念、LangGraph 的 checkpointer/thread_id 持久化模型），采用 **SQLite 单文件存储 + 会话为一等公民（thread）+ 交互式斜杠命令管理** 方案：

### 核心设计

1. **存储：SQLite 单文件 + FTS5 全文索引 + 双层模型**（用户选定）
   - 单文件数据库 `sessions.db`（路径可配置，默认运行目录下），零外部服务依赖，随项目携带。
   - 采用业界主流的 **双层存储模型**（LangGraph append-only history、OpenAI Assistants Thread Messages、Claude Code transcript、MemGPT recall storage 的共同范式）：**完整历史层永远只追加（append-only），压缩只发生在内存工作上下文，绝不回写删改持久层。**
   - 四张表：
     - `sessions`：会话元数据（`id` 主键、`title` 标题、`created_at`、`updated_at`、`mode`、`message_count`、`persisted_seq` 已持久化的最大 seq 游标）。
     - `messages`：**append-only 完整历史**（`id` 自增、`session_id` 外键、`seq` 会话内单调递增序号、`role`、`content`、`tool_calls`(JSON 文本)、`tool_call_id`、`created_at`）。每轮只 INSERT 本轮新增消息，行永不因压缩而删改，保留完整无损历史。
     - `session_events`：**压缩事件日志**（`id` 自增、`session_id` 外键、`type`(如 `compaction`)、`summary` 摘要文本、`covered_from_seq` / `covered_to_seq` 被摘要覆盖的 seq 区间、`created_at`）。压缩发生时只在此追加一条事件，不动 `messages` 表。
     - `messages_fts`：基于 FTS5 的全文索引虚表，索引 `content`，通过触发器与 `messages` 同步，为后续记忆检索/历史搜索提供基础。
   - 采用 WAL 模式提升并发写入体验；FTS5 不可用时降级为普通表 + LIKE 查询并记录 WARNING（不阻断主流程）。

2. **会话为一等公民（thread 模型）**
   - 每个会话有唯一 `id`（短 uuid），承载一条独立的对话线程。
   - 启动时：若指定会话则加载，否则自动创建一个新会话作为「当前会话」。
   - 会话内每条消息带单调递增的 `seq`；`persisted_seq` 游标记录已落盘的最大 seq，作为增量写入的基准。

3. **交互式斜杠命令管理**（用户选定）
   - **斜杠命令解析下沉到 CLI 层的通用纯函数**（如 `cli/commands.py::parse_command(raw) -> ParsedCommand | None`）：只负责把 `/name arg...` 解析为「命令名 + 参数」结构，与具体命令族（session 或未来其他命令）无关；不识别为斜杠命令时返回 `None`。这样未来新增任意 slash command 都复用同一解析器与同一测试文件，避免每类命令各自产生一个割裂的解析/测试入口。
   - 会话命令的**分发**（把解析结果路由到 SessionManager 操作）留在 `cli/app.py`，解析与分发解耦：解析是纯函数可单测，分发是 IO 编排。
   - 在 CLI 交互循环中识别以 `/` 开头的输入作为会话管理命令，不进入 LLM：
     - `/new [标题]`：创建并切换到新会话。
     - `/sessions`：列出全部会话（id、标题、更新时间、消息数）。
     - `/switch <id>`：切换到指定会话，加载其历史到上下文。
     - `/rename <标题>`：重命名当前会话。
     - `/delete <id>`：删除指定会话（不允许删除当前会话，需先切换）。
     - `/search <关键词>`：基于 FTS5 全文检索历史消息，返回命中会话与片段。
     - `/session`：显示当前会话信息。
     - `/help`：显示会话命令帮助。
   - 未识别的 `/` 命令给出错误提示，不影响正常对话。

4. **每轮问答后增量自动保存**（用户选定）
   - 每次 `engine.run()` 完成后，`SessionManager` 只将 `seq > persisted_seq` 的**新增消息**以 INSERT 追加到 `messages` 表，并推进 `persisted_seq`、更新 `updated_at` 与 `message_count`。开销为 O(新增条数)，且完整历史无损累积。
   - 增量的关键：`ContextManager` 为每条消息维护会话内单调递增的 `seq`。**压缩只改写/删除内存中 seq 较小的旧消息，不影响已落盘的行**——因此增量写入不会漏写、也不会与压缩冲突。
   - 异常退出（Ctrl+C / EOF）前也触发一次增量保存，避免丢失最近一轮对话。

   **触发上下文压缩时的保存（业界最佳实践：双层解耦）**
   - 参考 LangGraph（append-only checkpoint + 运行时裁剪 state）、OpenAI Assistants（Thread Messages 永久 + Run 时截断）、Claude Code（完整 transcript + `/compact` 摘要）、MemGPT（recall storage 全量 + main context 分页）的共同做法：**把「持久化的完整历史」与「喂给 LLM 的压缩工作上下文」彻底解耦。**
   - 当 `ContextManager` 触发压缩（截断/卸载/摘要/裁剪）时：
     - **不回写、不删改 `messages` 表**——原始消息在持久层始终完整保留。
     - 若压缩产生了摘要（第三层 LLM 摘要），`SessionManager` 向 `session_events` 追加一条 `compaction` 事件（记录摘要文本 + 被覆盖的 seq 区间），使压缩语义也被持久化。
   - **会话恢复（还原工作上下文）**：`/switch` 加载会话时，读取该会话最近一次 `compaction` 事件的摘要 + 其后（`seq > covered_to_seq`）的原始消息，重建为「压缩后的工作上下文」装载回 `ContextManager`；若无压缩事件则直接装载全部 `messages`。完整历史仍可通过 `messages` 表 / `/search` 检索。

5. **会话 ↔ 上下文的桥接**
   - `ContextManager` 新增能力：为消息维护会话内 `seq`、导出「自某 seq 之后的新增消息」（`export_new_messages(after_seq)`）、装载工作上下文（`load_messages`）、重置（`reset`）。切换会话时用还原出的工作上下文替换内存。
   - 系统提示词由引擎在运行时保证存在（`ensure_system_prompt`），不写入 `messages` 表（持久化只保存对话主体的 user/assistant/tool 消息）；压缩摘要通过 `session_events` 持久化，而非混入 `messages`。

6. **为记忆能力预留**
   - `messages`（append-only 完整事件流）+ `session_events`（压缩摘要）+ FTS5 索引，共同构成记忆能力的底座：完整事件流可回放、可检索、可抽取，天然契合 Event Sourcing / 双层记忆思想。
   - 后续可在此之上实现「跨会话记忆检索」「重要信息抽取入库」等，无需改动会话存储结构。
   - 预留 `SessionStore.search()` 接口，返回结构化命中结果供未来记忆模块复用。

### 分层职责

- `sagent/session/store.py`：`SessionStore`，封装 SQLite 连接、建表/建索引、会话与消息的 CRUD、FTS5 检索。所有错误以返回值/日志方式处理，不向上抛断流程（与工具约定一致，但 store 内部严重错误仍需暴露）。
- `sagent/session/models.py`：`SessionMeta`、`SessionMessage` 等 pydantic 数据模型。
- `sagent/session/manager.py`：`SessionManager`，维护「当前会话」状态，协调 `SessionStore` 与 `ContextManager`（创建/切换/保存/加载）。
- `config/models.py`：新增 `SessionConfig`（是否启用、db 路径、是否启用 FTS5、自动保存开关）。
- `cli/commands.py`：斜杠命令的**通用纯函数解析器** `parse_command`（与命令族无关，未来所有 slash command 复用）。
- `cli/app.py`：交互循环中调用 `parse_command` 并把结果分发到 `SessionManager` 操作，以及接入自动保存。

## What Changes

- 新增 `src/sagent/session/` 模块：`store.py`（SQLite + FTS5 + 双层表）、`manager.py`、`models.py`、`__init__.py`。
- 在 `config/models.py` 新增 `SessionConfig` 配置模型；`AppConfig` 增加 `session` 字段。
- 在 `config.example.yaml` 新增 `session` 配置段。
- 对 `ContextManager` 做最小化扩展：为消息维护会话内 `seq`，新增 `export_new_messages(after_seq)` / `load_messages()` / `reset()` 方法，并在压缩产生摘要时通过回调/返回值告知 `SessionManager`（保留现有全部压缩逻辑不变）。
- 在 `cli/app.py` 接入 `SessionManager`：启动时创建/加载当前会话、交互循环中调用 `parse_command` 解析并分发斜杠命令、每轮 `run()` 后增量保存、退出前保存；新增 `cli/commands.py` 提供与命令族无关的纯函数 `parse_command`。
- 新增 `tests/unit/test_session_store.py`（SQLite CRUD、增量写入、compaction 事件、FTS5 检索与降级）、`tests/unit/test_session_manager.py`（创建/切换/增量保存/工作上下文还原）。
- 扩展 `tests/unit/test_context_manager.py`：新增用例覆盖 seq 维护、`export_new_messages(after_seq)`、`load_messages`、`reset` 及第三层摘要压缩暴露的「摘要文本 + 覆盖 seq 区间」，并回归原有压缩用例。
- 扩展现有 `tests/unit/test_cli.py`：新增用例覆盖 `parse_command` 纯函数化解析逻辑（`/name arg` 解析、未知命令、非斜杠普通输入返回 None）。斜杠命令解析属 CLI 层职责，与既有 `_build_parser` / `resolve_mode` 测试同源，故并入 `test_cli.py`；未来新增的其他 slash command 解析测试同样落到此文件，保持单一 CLI 测试入口，不再新建 `test_session_commands.py`。

## Impact

- Affected specs: cli（交互循环新增命令分发与自动保存）、config（新增 SessionConfig）、context（ContextManager 新增装载/导出方法）。
- Affected code:
  - 新增：`src/sagent/session/__init__.py`、`store.py`、`manager.py`、`models.py`
  - 新增：`src/sagent/cli/commands.py`（与命令族无关的斜杠命令纯函数解析器 `parse_command`）
  - 修改：`src/sagent/config/models.py`（新增 SessionConfig，AppConfig 添加 session 字段）
  - 修改：`src/sagent/context/context_manager.py`（新增 load_messages / export_messages / reset）
  - 修改：`src/sagent/cli/app.py`（接入 SessionManager、调用 parse_command 分发斜杠命令、自动保存）
  - 修改：`config.example.yaml`（新增 session 段）
  - 新增：`tests/unit/test_session_store.py`、`tests/unit/test_session_manager.py`
  - 修改：`tests/unit/test_cli.py`（新增 parse_command 解析用例；未来 slash command 解析测试统一落此文件）
  - 修改：`tests/unit/test_context_manager.py`（新增 seq 维护、export_new_messages、load_messages、reset 及压缩摘要事件通知的用例，回归原有压缩用例）
- 不改动：ReActEngine / PlanEngine 核心执行逻辑、LLMClient、压缩策略（保持已完成功能不动）。

## ADDED Requirements

### Requirement: 会话持久化存储

系统 SHALL 提供基于 SQLite 的会话存储，以 append-only 方式持久化保存会话元数据、完整消息历史与压缩事件，并支持基于 FTS5 的全文检索。

#### Scenario: 初始化建表
- **WHEN** SessionStore 首次连接数据库文件
- **THEN** 系统创建 `sessions`、`messages`、`session_events` 表及 `messages_fts` FTS5 虚表与同步触发器（若不存在）

#### Scenario: 增量追加消息
- **WHEN** 调用保存接口传入会话 id 与新增消息列表（`seq > persisted_seq`）
- **THEN** 系统仅以 INSERT 追加这些新增消息到 `messages` 表，推进 `sessions.persisted_seq`，并更新 `updated_at` 与 `message_count`；已存在的历史行不被删改

#### Scenario: 记录压缩事件
- **WHEN** 上下文压缩产生摘要并调用记录接口，传入摘要文本与被覆盖的 seq 区间
- **THEN** 系统向 `session_events` 追加一条 `compaction` 事件，不修改 `messages` 表

#### Scenario: 加载完整历史
- **WHEN** 调用加载接口传入会话 id
- **THEN** 系统按 `seq` 顺序返回该会话的全部消息，且结构可无损还原为 OpenAI 消息列表（含 tool_calls / tool_call_id）

#### Scenario: 还原工作上下文
- **WHEN** 调用还原接口传入会话 id 且存在 `compaction` 事件
- **THEN** 系统返回「最近一次 compaction 的摘要消息 + 其后（seq > covered_to_seq）的原始消息」组成的工作上下文；无 compaction 事件时返回全部消息

#### Scenario: FTS5 全文检索
- **WHEN** FTS5 可用且调用 search 接口传入关键词
- **THEN** 系统返回命中的会话 id、消息片段与匹配得分排序结果

#### Scenario: FTS5 不可用降级
- **WHEN** 当前 SQLite 编译不支持 FTS5
- **THEN** 系统记录一条 WARNING 日志，search 接口降级为普通 LIKE 查询，其余功能不受影响

### Requirement: 会话生命周期管理

系统 SHALL 提供 SessionManager 管理「当前会话」状态，支持创建、切换、重命名、删除会话。

#### Scenario: 启动创建默认会话
- **WHEN** CLI 启动且未指定已存在的会话
- **THEN** 系统自动创建一个新会话并设为当前会话

#### Scenario: 创建并切换新会话
- **WHEN** 用户执行 `/new [标题]`
- **THEN** 系统创建新会话、将其设为当前会话，并清空/重置 ContextManager 的消息上下文

#### Scenario: 切换到已有会话
- **WHEN** 用户执行 `/switch <id>` 且该会话存在
- **THEN** 系统还原该会话的工作上下文（最近 compaction 摘要 + 其后原始消息，或无压缩时的全部消息）装载到 ContextManager，并将其设为当前会话

#### Scenario: 切换到不存在的会话
- **WHEN** 用户执行 `/switch <id>` 但该会话不存在
- **THEN** 系统返回错误提示，不改变当前会话

#### Scenario: 删除会话保护
- **WHEN** 用户执行 `/delete <id>` 且 id 为当前会话
- **THEN** 系统拒绝删除并提示需先切换到其他会话

### Requirement: 会话历史自动保存

系统 SHALL 在每轮问答完成后以增量方式自动将当前会话的新增消息持久化。

#### Scenario: 每轮增量保存
- **WHEN** 一次 `engine.run()` 正常完成
- **THEN** 系统仅将 ContextManager 中 `seq > persisted_seq` 的新增消息追加保存到当前会话，并推进 persisted_seq

#### Scenario: 压缩不影响已落盘历史
- **WHEN** 一轮内触发了上下文压缩（内存中旧消息被摘要或裁剪）
- **THEN** 已落盘的 `messages` 行不被删改，仅追加本轮新增消息；若产生摘要则额外记录一条 compaction 事件

#### Scenario: 退出前保存
- **WHEN** 用户通过 exit/quit 或 Ctrl+C / EOF 退出
- **THEN** 系统在退出前对当前会话执行一次增量保存

### Requirement: 会话管理斜杠命令

系统 SHALL 在 CLI 交互循环中识别以 `/` 开头的输入作为会话管理命令，不发送给 LLM。

#### Scenario: 列出会话
- **WHEN** 用户输入 `/sessions`
- **THEN** 系统打印全部会话的 id、标题、更新时间与消息数

#### Scenario: 全文搜索历史
- **WHEN** 用户输入 `/search <关键词>`
- **THEN** 系统调用 SessionStore 全文检索并打印命中会话与消息片段

#### Scenario: 未知命令
- **WHEN** 用户输入未识别的 `/` 命令
- **THEN** 系统打印错误提示与 `/help`，不进入 LLM、不影响后续对话

#### Scenario: 普通输入不受影响
- **WHEN** 用户输入不以 `/` 开头的文本
- **THEN** 系统按原有逻辑交由引擎执行

### Requirement: 上下文与会话桥接

系统 SHALL 扩展 ContextManager 提供会话内 seq 维护、增量导出与工作上下文装载能力。

#### Scenario: 增量导出新增消息
- **WHEN** 调用 `export_new_messages(after_seq)`
- **THEN** 返回 seq 大于 after_seq 的消息列表（用于增量持久化）

#### Scenario: 装载工作上下文
- **WHEN** 调用 `load_messages(messages)` 传入还原出的会话工作上下文
- **THEN** ContextManager 用该列表替换当前消息、重建 seq，并重置混合校准基准

#### Scenario: 压缩摘要事件通知
- **WHEN** 压缩执行到第三层并生成摘要
- **THEN** ContextManager 通过返回值或回调告知摘要文本与被覆盖的 seq 区间，供 SessionManager 记录 compaction 事件

### Requirement: 会话管理配置

系统 SHALL 通过 YAML 配置管理会话相关参数。

#### Scenario: 配置加载
- **WHEN** 配置文件包含 session 段
- **THEN** 系统解析为 SessionConfig，包括 enabled、db_path、enable_fts、auto_save 等参数

#### Scenario: 配置缺省
- **WHEN** 配置文件未包含 session 段
- **THEN** 系统使用默认配置（enabled=true、db_path="sessions.db"、enable_fts=true、auto_save=true）

## MODIFIED Requirements

### Requirement: 多轮对话上下文持久化（在 add-context-management 基础上增强）

系统 SHALL 在原有「进程内跨轮次上下文保持」基础上，增加「跨进程会话级持久化」：当前会话的上下文在每轮问答后落盘，重启后可通过 `/switch` 还原。

#### Scenario: 跨进程还原会话
- **WHEN** 用户重启 SAgent 并 `/switch` 到之前的会话
- **THEN** 该会话的历史消息被装载回 ContextManager，后续对话可引用历史信息
