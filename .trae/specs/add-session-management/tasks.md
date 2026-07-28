# Tasks

- [x] Task 1: 新增会话数据模型与配置: 定义会话领域模型与配置项，作为后续存储与管理的基础。
  - [x] SubTask 1.1: 在 `src/sagent/session/models.py` 定义 `SessionMeta`（id、title、created_at、updated_at、mode、message_count、persisted_seq）、`SessionMessage`（role、content、tool_calls、tool_call_id、seq）与 `CompactionEvent`（type、summary、covered_from_seq、covered_to_seq、created_at）pydantic 模型
  - [x] SubTask 1.2: 在 `src/sagent/config/models.py` 新增 `SessionConfig`（enabled、db_path、enable_fts、auto_save），并在 `AppConfig` 添加 `session` 字段（default_factory）
  - [x] SubTask 1.3: 在 `config.example.yaml` 新增 `session` 配置段，附中文注释

- [x] Task 2: 实现 SessionStore（SQLite + FTS5 + 双层表）: 封装数据库连接、建表建索引、append-only 增量写入、压缩事件与全文检索。
  - [x] SubTask 2.1: 在 `src/sagent/session/store.py` 实现连接初始化（WAL 模式）、建 `sessions`/`messages`/`session_events` 表、建 `messages_fts` FTS5 虚表与同步触发器
  - [x] SubTask 2.2: 实现会话 CRUD：create_session、list_sessions、get_session、rename_session、delete_session
  - [x] SubTask 2.3: 实现 append_messages（仅 INSERT seq > persisted_seq 的新增消息，推进 persisted_seq，更新 updated_at 与 message_count）与 load_messages（按 seq 顺序还原为 OpenAI 消息结构）
  - [x] SubTask 2.4: 实现 append_compaction_event（向 session_events 追加，不动 messages）与 build_working_context（读取最近 compaction 摘要 + 其后原始消息重建工作上下文；无事件则返回全部消息）
  - [x] SubTask 2.5: 实现 search（FTS5 全文检索；检测到 FTS5 不可用时记录 WARNING 并降级为 LIKE 查询）

- [x] Task 3: 扩展 ContextManager 支持会话桥接: 在不改动现有压缩逻辑前提下新增 seq 维护、增量导出与装载能力。
  - [x] SubTask 3.1: 在 `src/sagent/context/context_manager.py` 为消息维护会话内单调递增 `seq`，新增 `export_new_messages(after_seq)` 返回 seq 之后的新增消息
  - [x] SubTask 3.2: 新增 `load_messages(messages)` 与 `reset()`，替换消息、重建 seq 并重置混合校准基准（复用现有重置逻辑，不移除原有方法）
  - [x] SubTask 3.3: 在第三层摘要压缩生成摘要处，通过返回值或回调暴露「摘要文本 + 被覆盖 seq 区间」，供 SessionManager 记录 compaction 事件（不改动压缩策略本身逻辑）

- [x] Task 4: 实现 SessionManager: 维护当前会话状态并协调 Store 与 ContextManager 的增量保存与工作上下文还原。
  - [x] SubTask 4.1: 在 `src/sagent/session/manager.py` 实现 SessionManager，持有 SessionStore、ContextManager、当前会话 id 与 persisted_seq
  - [x] SubTask 4.2: 实现 new_session（创建+切换+reset 上下文）、switch_session（build_working_context 还原并装载）、rename、delete（保护当前会话）
  - [x] SubTask 4.3: 实现 save_current（export_new_messages 增量追加 + 推进 persisted_seq）与 record_compaction（接收摘要事件写入 session_events）、search
  - [x] SubTask 4.4: 实现 ensure_current_session（启动时创建默认会话或加载指定会话）

- [x] Task 5: CLI 接入会话命令与增量自动保存: 在交互循环中分发斜杠命令并每轮增量保存。
  - [x] SubTask 5.1: 在 `src/sagent/cli/app.py` 构建 SessionStore/SessionManager 并注入；启动时确保当前会话
  - [x] SubTask 5.2: 在 `src/sagent/cli/commands.py` 实现与命令族无关的纯函数 `parse_command(raw)`（`/name arg...` 解析为命令名+参数结构，非斜杠输入返回 None）；在 `app.py` 调用并分发（/new、/sessions、/switch、/rename、/delete、/search、/session、/help），未知命令给出错误提示
  - [x] SubTask 5.3: 每轮 `engine.run()` 完成后调用 save_current（增量）；退出（exit/quit/Ctrl+C/EOF）前增量保存
  - [x] SubTask 5.4: 更新启动横幅提示会话命令入口（如提示输入 /help 查看会话命令）

- [x] Task 6: 测试覆盖: 验证增量存储、压缩事件、工作上下文还原、命令解析与降级路径。
  - [x] SubTask 6.1: `tests/unit/test_session_store.py`：建表、会话 CRUD、增量追加（seq 游标）、compaction 事件、build_working_context 还原、消息无损、FTS5 检索与降级
  - [x] SubTask 6.2: `tests/unit/test_session_manager.py`：new/switch/增量 save/工作上下文还原与 ContextManager 桥接、压缩后已落盘历史不被删改、删除当前会话保护
  - [x] SubTask 6.3: 扩展 `tests/unit/test_cli.py`：新增 `parse_command` 纯函数解析用例（`/name arg` 解析、未知命令、非斜杠普通输入返回 None）。斜杠命令解析属 CLI 层，与既有 CLI 用例同源，故并入 `test_cli.py`（不新建 test_session_commands.py）
  - [x] SubTask 6.4: 扩展 `tests/unit/test_context_manager.py`：新增用例覆盖 seq 维护、export_new_messages(after_seq) 增量导出、load_messages 替换消息并重建 seq/重置校准、reset 清空、第三层摘要压缩暴露「摘要文本 + 覆盖 seq 区间」；并回归验证原有压缩用例不受影响
  - [x] SubTask 6.5: 运行 `python -m pytest` 确认全部用例通过

# Task Dependencies
- Task 2 depends on Task 1
- Task 4 depends on Task 2 和 Task 3
- Task 5 depends on Task 4
- Task 6 depends on Task 2、Task 4、Task 5（各子测试对应各自模块，可在依赖模块完成后并行编写）
