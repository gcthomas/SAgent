# Checklist

- [x] 启动时不再自动创建会话（`app.py` 中 `ensure_current_session()` 调用已移除）
- [x] 启动横幅在无当前会话时显示"尚未创建（首次对话时自动创建）"提示
- [x] 用户首次输入非斜杠、非空、非退出内容时自动创建会话
- [x] 斜杠命令（如 `/sessions`、`/search`）不会触发会话创建
- [x] `/new` 命令成功后，后续首次对话不重复创建会话
- [x] `/switch` 命令成功后，后续首次对话不重复创建会话
- [x] 用户直接退出（exit/quit/Ctrl+C/EOF）不产生垃圾会话
- [x] 退出时的 `save_current()` 在无会话时不报错（返回 0）
- [x] `python -m pytest` 全部已有用例通过（251 passed, 2 skipped）
- [x] `tests/unit/test_session_manager.py` 中 `ensure_current_session` 测试不受影响
