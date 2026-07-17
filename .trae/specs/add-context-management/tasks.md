# Tasks

- [x] Task 1: 添加 tiktoken 可选依赖与 Token 估算器
  - [x] SubTask 1.1: 在 `requirements.txt` 中添加 `tiktoken>=0.7.0`（注释标注为可选依赖）
  - [x] SubTask 1.2: 创建 `src/sagent/context/token_counter.py`，实现 `count_tokens(messages, model, method) -> int` 与 `count_text_tokens(text, model, method) -> int`，支持 `auto`/`tiktoken`/`heuristic` 三种模式切换；`auto` 优先 tiktoken 回退启发式，`heuristic` 始终字符估算，`tiktoken` 强制但不可用时回退并告警
  - [x] SubTask 1.3: 创建 `src/sagent/context/__init__.py`，导出公共接口

- [x] Task 2: 新增 ContextConfig 配置模型
  - [x] SubTask 2.1: 在 `src/sagent/config/models.py` 中新增 `ContextConfig` 类，字段包括 max_context_tokens、compression_threshold、safe_threshold、keep_recent_messages、max_tool_output_tokens、token_counter_method（Literal["auto","tiktoken","heuristic"]）、enable_summary、summary_max_tokens
  - [x] SubTask 2.2: 在 `AppConfig` 中添加 `context: ContextConfig` 字段（带默认工厂）
  - [x] SubTask 2.3: 在 `config.example.yaml` 中添加 context 配置段示例，含 token_counter_method 说明

- [x] Task 3: 实现分层压缩策略
  - [x] SubTask 3.1: 创建 `src/sagent/context/strategies.py`，定义压缩策略抽象基类 `CompressionStrategy`
  - [x] SubTask 3.2: 实现 `ToolOutputTruncation` 策略（第一层：工具输出截断）
  - [x] SubTask 3.3: 实现 `ToolMessageOffload` 策略（第二层：大体积工具消息卸载）
  - [x] SubTask 3.4: 实现 `LLMSummaryCompression` 策略（第三层：LLM 摘要压缩，含层级摘要，先于裁剪执行以保留语义）
  - [x] SubTask 3.5: 实现 `SlidingWindowPruning` 策略（第四层：滑动窗口裁剪，兜底）
  - [x] SubTask 3.6: 创建 `src/sagent/context/prompts.py`，定义摘要生成系统提示词

- [x] Task 4: 实现 ContextManager 核心管理器
  - [x] SubTask 4.1: 创建 `src/sagent/context/context_manager.py`，实现 `ContextManager` 类
  - [x] SubTask 4.2: 实现 `add_message(msg)` 方法（含第一层内联截断）
  - [x] SubTask 4.3: 实现 `get_messages() -> list` 方法（含阈值检查与分层压缩触发）
  - [x] SubTask 4.4: 实现 `_compress()` 内部方法，按序执行第二至第四层策略直到 token 降至安全线
  - [x] SubTask 4.5: 实现 `_is_over_threshold()` 与 `token_count` 属性

- [x] Task 5: ReActEngine 集成 ContextManager
  - [x] SubTask 5.1: 在 `ReActEngine.__init__` 中添加可选 `context_manager` 参数，默认 None
  - [x] SubTask 5.2: 在 `run()` 方法中：有 context_manager 时用它管理消息（add/get），无时保持原有逻辑
  - [x] SubTask 5.3: 在 `run()` 方法中，发送给 LLM 前使用 `context_manager.get_messages()` 获取压缩后消息
  - [x] SubTask 5.4: 工具执行后通过 `context_manager.add_message()` 追加工具调用与观察消息

- [x] Task 6: PlanEngine 集成 ContextManager
  - [x] SubTask 6.1: 在 `PlanEngine.__init__` 中添加可选 `context_manager` 参数并传递给内部 ReActEngine
  - [x] SubTask 6.2: 在 `_decompose()` 和 `_summarize()` 中，有 context_manager 时用它管理消息

- [x] Task 7: CLI 集成与多轮对话支持
  - [x] SubTask 7.1: 在 `cli/app.py` 的 `build_engine` 函数中添加 context_manager 参数
  - [x] SubTask 7.2: 在 `run()` 中创建 `ContextManager` 实例（基于 config.context），传入引擎
  - [x] SubTask 7.3: 在交互循环中每次 `engine.run()` 前将用户输入追加到 context_manager，使跨轮次上下文可用
  - [x] SubTask 7.4: engine.run 返回后将助手回复追加到 context_manager

- [x] Task 8: 单元测试
  - [x] SubTask 8.1: 创建 `tests/unit/test_token_counter.py`，测试 auto/tiktoken/heuristic 三种模式的计数逻辑与回退行为
  - [x] SubTask 8.2: 创建 `tests/unit/test_strategies.py`，测试四层压缩策略各自逻辑
  - [x] SubTask 8.3: 创建 `tests/unit/test_context_manager.py`，测试 ContextManager 整体流程：阈值触发、分层执行、系统提示词保护

- [x] Task 8.5: 混合校准（API prompt_tokens 校准）
  - [x] SubTask 8.5.1: 在 `LLMResponse` 中新增 `usage` 字段，`client.py` 返回时从 `completion.usage` 构造并赋值
  - [x] SubTask 8.5.2: 在 `ContextConfig` 中新增 `use_api_calibration` 配置项（默认 True）
  - [x] SubTask 8.5.3: 在 `ContextManager` 中新增 `record_llm_usage(usage)` 方法，记录精确 prompt_tokens 与基准消息数；`token_count` 属性改为「精确基准 + delta 估算」混合逻辑
  - [x] SubTask 8.5.4: `_compress()` 触发时重置校准基准
  - [x] SubTask 8.5.5: `ReActEngine._run_with_context()` 中每次 `llm.chat()` 后调用 `cm.record_llm_usage(response.usage)`
  - [x] SubTask 8.5.6: 更新 `conftest.py` 的 `text_response`/`tool_response` 支持 `usage` 参数
  - [x] SubTask 8.5.7: 新增测试覆盖：精确校准、禁用回退、None usage 忽略、压缩后重置
  - [x] SubTask 8.5.8: 更新 `config.example.yaml` 新增 `use_api_calibration` 配置项

- [x] Task 9: 引擎集成测试
  - [x] SubTask 9.1: 创建 `tests/engines/test_context_integration.py`，测试 ReActEngine 注入 ContextManager 后的工具调用与压缩
  - [x] SubTask 9.2: 测试无 ContextManager 时引擎行为不变（向后兼容）
  - [x] SubTask 9.3: 测试多轮对话场景下的上下文累积与压缩

- [x] Task 10: 全量测试验证
  - [x] SubTask 10.1: 运行 `python -m pytest` 确认全部用例通过

# Task Dependencies
- Task 2 depends on Task 1（ContextConfig 中引用 token 计数概念）
- Task 3 depends on Task 1（策略中需要 token 计数）与 Task 2（需要 ContextConfig）
- Task 4 depends on Task 1、Task 2、Task 3（ContextManager 组合使用 token 计数、配置与策略）
- Task 5 depends on Task 4（引擎使用 ContextManager）
- Task 6 depends on Task 5（PlanEngine 复用 ReActEngine）
- Task 7 depends on Task 5、Task 6（CLI 注入到引擎）
- Task 8 depends on Task 1、Task 2、Task 3、Task 4（测试各组件）
- Task 8.5 depends on Task 4、Task 5（混合校准需 ContextManager 与引擎集成）
- Task 9 depends on Task 5、Task 6、Task 7（测试引擎与 CLI 集成）
- Task 10 depends on Task 8、Task 8.5、Task 9（全量验证）
