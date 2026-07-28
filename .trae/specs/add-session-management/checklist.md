# Checklist

## 数据模型与配置
- [x] `SessionMeta`（含 persisted_seq）、`SessionMessage`（含 seq）、`CompactionEvent` pydantic 模型按 spec 字段定义，含中文 docstring
- [x] `SessionConfig` 定义 enabled/db_path/enable_fts/auto_save 字段，`AppConfig` 含 session 字段且缺省可用
- [x] `config.example.yaml` 新增 session 段，注释为中文且无乱码

## SessionStore（SQLite + FTS5 + 双层表）
- [x] 首次连接自动创建 sessions、messages、session_events 表与 messages_fts FTS5 虚表及同步触发器
- [x] 连接启用 WAL 模式
- [x] 会话 CRUD（create/list/get/rename/delete）实现正确
- [x] append_messages 仅追加 seq > persisted_seq 的新增消息，推进 persisted_seq 并更新 updated_at 与 message_count；已存在历史行不被删改
- [x] append_compaction_event 向 session_events 追加事件且不修改 messages 表
- [x] load_messages 按 seq 顺序无损还原 OpenAI 消息结构（含 tool_calls / tool_call_id）
- [x] build_working_context 正确还原「最近 compaction 摘要 + 其后原始消息」；无事件时返回全部消息
- [x] search 使用 FTS5 返回排序命中结果
- [x] FTS5 不可用时记录 WARNING 并降级为 LIKE 查询，主流程不受影响

## ContextManager 桥接
- [x] 为消息维护会话内单调递增 seq
- [x] 新增 export_new_messages(after_seq) 返回 seq 之后的新增消息
- [x] 新增 load_messages 替换消息、重建 seq 并重置混合校准基准
- [x] 新增 reset 清空消息与校准基准
- [x] 第三层摘要压缩生成摘要时暴露「摘要文本 + 被覆盖 seq 区间」供记录 compaction 事件
- [x] 现有 add_message/get_messages/compress 等原有压缩逻辑未被移除或破坏

## SessionManager
- [x] 启动时创建默认会话或加载指定会话
- [x] /new 创建并切换、重置上下文
- [x] /switch 通过 build_working_context 还原工作上下文并装载；不存在时报错且不改变当前会话
- [x] /rename 重命名当前会话
- [x] /delete 拒绝删除当前会话
- [x] save_current 增量导出并追加持久化、推进 persisted_seq
- [x] record_compaction 将压缩摘要事件写入 session_events

## CLI 集成
- [x] `cli/commands.py` 提供与命令族无关的纯函数 `parse_command`，将 `/name arg...` 解析为命令名+参数结构，非斜杠输入返回 None
- [x] 以 `/` 开头的输入被识别为会话命令且不进入 LLM
- [x] /new、/sessions、/switch、/rename、/delete、/search、/session、/help 均可用
- [x] 未知 `/` 命令给出错误提示，普通输入正常交由引擎执行
- [x] 每轮 engine.run() 后增量保存当前会话
- [x] exit/quit/Ctrl+C/EOF 退出前增量保存当前会话

## 测试与质量
- [x] test_session_store.py 覆盖 CRUD、增量追加（seq 游标）、compaction 事件、build_working_context 还原、消息无损、FTS5 检索与降级
- [x] test_session_manager.py 覆盖 new/switch/增量 save/工作上下文还原桥接、压缩后已落盘历史不被删改、删除保护
- [x] test_cli.py 扩展覆盖 parse_command 解析（`/name arg`、未知命令、非斜杠输入返回 None），未新建 test_session_commands.py
- [x] test_context_manager.py 扩展覆盖 seq 维护、export_new_messages、load_messages、reset 与压缩摘要事件通知，且原有压缩用例回归通过
- [x] `python -m pytest` 全部用例通过
- [x] 所有新增/修改 Python 文件为 UTF-8 无 BOM，中文注释无乱码
- [x] 未改动 ReActEngine/PlanEngine/LLMClient/压缩策略等既有正确功能
