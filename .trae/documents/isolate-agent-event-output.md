# Agent 事件与 CLI 交互输出隔离方案

## Summary

将当前 ReAct/Plan 引擎通过 `Callable[[str], None]` 发送、由 CLI 直接 `print()` 的扁平字符串事件，升级为“结构化执行事件 + CLI Presenter + 顶层最终答案独立输出”的边界设计。

目标是解决：

- Agent 过程事件与用户输入提示、斜杠命令、权限审批和最终答案混屏；
- ReAct 直接回答和 CLI 最终回答重复展示；
- Plan 内部 ReAct 事件缺少步骤上下文；
- 展示层必须通过字符串前缀解析事件；
- 事件生产逻辑与终端渲染逻辑耦合，难以测试和复用。

本次实现保持现有执行流程、LLM 消息历史、日志和 OpenTelemetry 逻辑不变，只调整事件协议、事件转发和 CLI 过程展示职责。

## Current State Analysis

- `src/sagent/core/react_engine.py` 的 `_emit()` 只接收字符串，事件内容通过 `[行动]`、`[观察]`、`[最终答案]`、`[提示]` 等文本前缀表达语义。
- `src/sagent/core/plan_engine.py` 将同一个 `on_event` 直接传给内部 `ReActEngine`，所以 Plan 步骤内的 ReAct 事件没有 `step_index`、`step_total` 或步骤作用域。
- `src/sagent/cli/app.py` 中 `_print_event()` 直接调用 `print()`，交互输入、命令输出、异常输出、权限审批和最终答案也由多个位置直接输出。
- ReAct 在得到最终答案时发送 `[最终答案]`，CLI 在 `engine.run()` 返回后又输出 `助手 > ...`；Plan 中每个步骤也会发送该事件，造成内部步骤结果与顶层回答语义混杂。
- `tests/engines/test_react_engine.py` 和 `tests/engines/test_plan_engine.py` 当前主要断言事件文本前缀，没有验证事件结构、Plan 层级和 CLI 展示去重。
- 日志与 OpenTelemetry 已经有独立职责，不应让日志替代用户展示，也不应将 CLI 输出写入消息上下文。

## Proposed Changes

### 1. 新增结构化 AgentEvent 模型

新增一个核心事件模型文件，例如 `src/sagent/core/events.py`，遵循现有模块的 UTF-8、中文 docstring 和类型标注约定。

模型使用明确的事件类型枚举或受限字符串，并携带必要上下文，至少包括：

- `event_type`：如 `plan_started`、`plan_step_started`、`tool_call`、`tool_result`、`notice`、`react_completed`；
- `content`：面向过程展示的文本内容；
- `mode`：`react` 或 `plan`；
- `iteration`：ReAct 当前迭代，可选；
- `tool_name`、`tool_arguments`、`tool_call_id`：工具调用相关字段，可选；
- `plan_step_index`、`plan_step_total`、`plan_step`：Plan 步骤上下文，可选；
- `scope` 或等价字段：区分顶层执行、Plan 步骤内部执行和回退执行。

提供一个事件回调类型别名，例如 `EventHandler = Callable[[AgentEvent], None]`。事件模型只描述执行事实，不执行终端输出，不写入 LLM 消息历史。

### 2. 修改 ReActEngine 产生结构化过程事件

修改 `src/sagent/core/react_engine.py`：

- 将构造函数 `on_event` 类型改为结构化事件回调；
- 保留 `_emit` 作为唯一事件出口，但改为接收 `AgentEvent`；
- 在无上下文和上下文两条执行路径中统一发送结构化 `tool_call`、`tool_result`、`notice` 事件；
- 工具事件分别填充工具名、参数、调用 ID、观察结果和迭代编号，不再把这些字段拼接进唯一字符串；
- 将当前 `[最终答案]` 事件改为内部执行完成事件，例如 `react_completed`，并通过 `scope` 标识其属于顶层 ReAct 还是 Plan 步骤；该事件不承担 CLI 顶层 `助手 >` 的最终回答展示职责；
- 保留现有返回值、工具执行顺序、上下文持久化、最大迭代收尾和日志/Span 行为，不修改执行控制流。

### 3. 为 PlanEngine 增加步骤事件上下文

修改 `src/sagent/core/plan_engine.py`：

- 将 `on_event` 改为结构化事件回调；
- Plan 自身发送 `plan_started`、`plan_step_started`、`notice` 等结构化事件，携带步骤总数、步骤索引和步骤文本；
- 不再把原始回调直接传给内部 ReAct，而是注入一个内部转发器；该转发器为每个 ReAct 事件补充 `mode=plan`、当前步骤索引/总数、步骤描述和内部作用域，再转发给外部回调；
- 拆解失败回退时发送结构化 `notice`，并明确 `scope=plan_fallback`；
- 保留计划拆解、逐步执行、结果汇总、上下文消息和现有日志/Span 逻辑；
- 不新增对最终汇总结果的重复过程输出，顶层最终答案继续由 CLI 在 `engine.run()` 返回后统一显示。

### 4. 新增 CLI EventPresenter，隔离过程展示

新增 CLI 展示适配器文件，例如 `src/sagent/cli/event_presenter.py`，或在现有 CLI 模块内增加独立的 presenter 类，优先选择独立文件以避免继续扩大 `app.py` 的直接输出职责。

Presenter 负责：

- 接收 `AgentEvent`，按照事件类型渲染过程信息；
- 统一控制过程输出的换行、前缀和 flush；
- 对 Plan 步骤和工具调用使用事件字段，不解析旧文本前缀；
- 忽略不应展示给用户的内部完成事件，避免与 `助手 >` 顶层回答重复；
- 保持过程事件输出在 CLI 交互通道中可控，但不接管最终答案、斜杠命令、权限审批和错误处理。

修改 `src/sagent/cli/app.py`：

- 删除或替换 `_print_event()` 的直接 `print()` 实现；
- 创建 `EventPresenter` 并将其事件处理方法传给 `build_engine()`；
- 保持现有启动信息、输入提示、命令输出、异常输出、权限审批和 `助手 >` 最终答案输出逻辑，避免无关重构；
- 如需统一输出流，Presenter 使用与现有过程输出一致的 stdout，日志仍由 logging handler 管理，不将日志转为用户事件。

### 5. 更新导出与类型引用

如 `src/sagent/core/__init__.py` 或 `src/sagent/cli/__init__.py` 存在公开导出约定，则仅增加新的事件模型和 Presenter 必要导出；不新增配置项，不改变外部 CLI 参数。

### 6. 补充测试

修改 `tests/engines/test_react_engine.py`：

- 将事件收集器改为收集 `AgentEvent`；
- 断言工具调用事件的 `event_type`、工具名、参数、调用 ID 和迭代字段；
- 断言观察事件的结果字段；
- 断言 ReAct 完成事件不再承担顶层最终答案文本展示语义；
- 覆盖上下文管理器路径的事件协议一致性（若现有测试已覆盖该路径，则在对应测试中补充断言）。

修改 `tests/engines/test_plan_engine.py`：

- 断言计划开始、步骤开始和内部 ReAct 事件的结构化类型；
- 断言内部工具/完成事件包含正确的步骤索引、总数和步骤文本；
- 断言拆解失败回退事件的作用域和事件类型；
- 保留现有返回结果和 FakeLLM 调用轮次断言。

修改或补充 `tests/unit/test_cli.py`，必要时新增同目录下的 Presenter 单元测试文件：

- 使用可注入的输出流测试不同事件类型的渲染结果；
- 验证内部 `react_completed` 不会输出重复的最终答案；
- 验证 Plan 步骤事件与工具事件可以按结构化字段展示；
- 不把完整交互循环扩展成真实终端测试，保持现有 CLI 测试范围，除非为了验证回调注入必须增加最小测试。

## Assumptions & Decisions

- 推荐采用“结构化事件对象 + CLI Presenter + 顶层最终答案独立输出”，而不是继续扩展字符串前缀解析；这是对当前问题最直接且可持续的解决方案。
- 本次不引入 Rich、Textual 等新的终端 UI 依赖；先通过 Presenter 做职责隔离，保持依赖和 CLI 行为稳定。
- 不把日志和 OpenTelemetry Span 作为 CLI 事件来源；它们继续服务诊断和追踪。
- 不新增兼容旧字符串回调的 fallback 或双协议适配层；项目内部调用方和测试统一迁移到结构化事件，避免长期保留两套接口。
- ReAct 的“最终答案”仍由 `run()` 返回，CLI 顶层统一输出；ReAct 事件只用于过程状态展示，Plan 步骤完成事件只作为过程信息，不直接冒充用户最终回答。
- 现有权限审批模块属于交互输入边界，本次不重构其直接输出；Presenter 只处理引擎事件，避免扩大需求范围。

## Verification

按项目约定执行：

1. 运行受影响测试：
   - `python -m pytest tests/engines/test_react_engine.py tests/engines/test_plan_engine.py tests/unit/test_cli.py`
2. 运行完整离线测试：
   - `python -m pytest`
3. 检查新增/修改 Python 文件的中文编码、模块 docstring、类型标注和无 Emoji；确认事件字段没有进入 LLM 消息历史。
4. 手工验证 ReAct CLI：工具调用过程只显示一次过程事件，最终回答只显示一次 `助手 > ...`。
5. 手工验证 Plan CLI：计划与步骤过程带有明确步骤上下文，步骤内部完成事件不会重复显示为顶层答案，最终汇总只显示一次。
6. 检查现有日志和 OpenTelemetry 相关测试，确认事件协议迁移没有改变日志字段、Span 名称和 Span 层级。
