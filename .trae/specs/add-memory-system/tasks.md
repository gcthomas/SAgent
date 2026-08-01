# Tasks

- [x] Task 1: 新增记忆配置模型
  - [x] SubTask 1.1: 在 `src/sagent/config/models.py` 新增 `MemoryConfig`（enabled/user_file/memory_file/user_max_chars/memory_max_chars）并挂载到 `AppConfig.memory`（缺省可用，默认 enabled=True）
  - [x] SubTask 1.2: 在 `config.example.yaml` 增加 `memory` 段示例（与默认值一致）

- [x] Task 2: 实现本地 Markdown 文件存储
  - [x] SubTask 2.1: 在 `src/sagent/memory/store.py` 实现 `MemoryStore`：加载（文件不存在则空字符串）、读取全文、追加内容、替换文本（查找 old 替换为 new）、删除文本（查找并移除）、原子落盘（临时文件+rename）、字符上限检查（返回是否超限）
  - [x] SubTask 2.2: `src/sagent/memory/__init__.py` 导出公开接口

- [x] Task 3: 实现记忆管理器（注入构建/反思整理）
  - [x] SubTask 3.1: 在 `src/sagent/memory/prompts.py` 定义记忆使用引导提示词（`MEMORY_GUIDE_PROMPT`，参考 Hermes 实践，引导 LLM 何时调用/什么该保存/跳过/写入原则）、反思整理提示词模板（要求去重、合并冗余、精简表述、不超过字符上限）与注入前缀格式
  - [x] SubTask 3.2: 在 `src/sagent/memory/manager.py` 实现 `MemoryManager`：`build_system_prompt(base_prompt)` 构造带记忆前缀的系统提示词（会话开始时调用一次，结果冻结复用）；`add(target, content)` 追加内容、`replace(target, old, new)` 查找替换、`remove(target, content)` 查找删除；三个方法均在超限时触发反思整理（调用 LLM，失败容错记录日志不抛异常）；replace/remove 未找到目标文本时返回提示

- [x] Task 4: 实现三个记忆工具
  - [x] SubTask 4.1: 在 `src/sagent/tools/memory_tool.py` 实现 `AddMemoryTool`（参数 target+content）、`ReplaceMemoryTool`（参数 target+old+new）、`RemoveMemoryTool`（参数 target+content），均继承 `Tool` 基类（与 `file_tools.py`、`shell_tool.py` 同级）；描述引导 LLM 判断目标文件与选择合适操作（新增/更新/删除）；执行分别委托 `MemoryManager.add/replace/remove`
  - [x] SubTask 4.2: 确保三个工具 schema（name/description/parameters）符合 ReAct 工具表要求

- [x] Task 5: CLI 集成（构建/注入冻结/注册工具）
  - [x] SubTask 5.1: 在 `cli/app.py` 构建 `MemoryManager`（`config.memory.enabled` 时）
  - [x] SubTask 5.2: 会话开始前调用 `memory_manager.build_system_prompt(REACT_SYSTEM_PROMPT)` 得到冻结的系统提示词，整个会话复用（不再每轮重建）
  - [x] SubTask 5.3: 构建三个记忆工具（注入 `MemoryManager`）并注册入工具表
  - [x] SubTask 5.4: 启动横幅打印记忆功能状态与文件路径

- [x] Task 6: 单元测试
  - [x] SubTask 6.1: `tests/unit/test_memory_store.py` 覆盖加载/原子写/追加/替换/删除/字符上限检查/文件不存在初始化空
  - [x] SubTask 6.2: `tests/unit/test_memory_manager.py` 用 `make_fake_llm` 回放验证注入前缀构建、反思整理（超限触发/去重合并/失败容错）、add/replace/remove 写入流程（含未找到目标文本返回提示）
  - [x] SubTask 6.3: `tests/unit/test_memory_tool.py` 验证三个工具 schema 与执行（分别委托 manager.add/replace/remove）
  - [x] SubTask 6.4: 运行 `python -m pytest` 确认全部用例通过

# Task Dependencies

- Task 2 依赖 Task 1（配置先行，存储使用配置路径与上限）
- Task 3 依赖 Task 2（管理器使用存储）
- Task 4 依赖 Task 3（工具委托管理器）
- Task 5 依赖 Task 3 与 Task 4（CLI 集成管理器与工具）
- Task 6 依赖 Task 1-5（测试覆盖全部实现）
