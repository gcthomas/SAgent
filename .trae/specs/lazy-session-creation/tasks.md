# Tasks

- [x] Task 1: 移除启动时的会话自动创建
  - [x] 在 `src/sagent/cli/app.py` 的 `run()` 函数中，删除第 219 行 `session_manager.ensure_current_session()` 调用
  - [x] 确保 SessionManager 构造后 `_current_session_id` 保持为 None
  - [x] 不改动 SessionManager 构造与压缩回调注册（第 217-218 行保持不变）

- [x] Task 2: 调整启动横幅显示
  - [x] 修改 `app.py` 第 238-242 行的横幅逻辑：当 `session_manager is not None` 但 `get_current_session()` 返回 None 时，显示 `会话: 尚未创建（首次对话时自动创建）` 而非不打印任何会话信息
  - [x] 斜杠命令提示行 `会话命令: /new /sessions /switch ...` 保持不变

- [x] Task 3: 在交互循环中实现延迟创建逻辑
  - [x] 采用备选方案（更简洁）：不使用标志位，改为在每次 `engine.run()` 前检查 `session_manager is not None and session_manager.get_current_session() is None`，若为 True 则创建
  - [x] 此检查在 `new_trace_id()` 之后、`engine.run()` 之前（app.py 第 299-302 行）

- [x] Task 4: 处理 /new 和 /switch 命令的标志位同步
  - [x] 采用备选方案：不使用标志位，通过 `get_current_session() is None` 检查判断是否需要创建。`/new` 和 `/switch` 成功后 `get_current_session()` 自然返回非 None，无需额外同步

- [x] Task 5: 运行测试验证
  - [x] 运行 `python -m pytest` 确认全部已有用例通过（251 passed, 2 skipped）
  - [x] 确认 `tests/unit/test_session_manager.py` 中的 `ensure_current_session` 测试仍通过（测试的是方法本身，不受 app.py 改动影响）
  - [x] 确认 `tests/engines/` 引擎测试仍通过（引擎测试不涉及 SessionManager）

# Task Dependencies
- Task 2 和 Task 3 可以在 Task 1 完成后并行进行
- Task 4 依赖 Task 3 的设计决策（标志位 vs get_current_session 检查）
- Task 5 在所有任务完成后执行
