# Agent 上下文管理能力 Spec

## Why

当前 SAgent 的 ReAct/Plan 引擎在每次 `run()` 中直接维护 `messages` 列表，消息随工具调用与观察结果不断累积，无任何长度控制。在长任务或多轮交互中，发给 LLM 的 prompt 可能超出模型的 context window，导致 API 报错或上下文丢失。需要引入上下文管理能力，保证发送给 LLM 的 prompt 长度始终在安全范围内。

## 推荐方案（结合业界最佳实践）

综合 LangChain `ConversationSummaryBufferMemory`、LangChain Deep Agents SDK 的分层压缩策略、以及 MemGPT 的分页思想，采用 **阈值触发 + 分层多策略压缩** 方案：

### 核心设计

1. **Token 估算器**：支持两种计数模式，由配置项 `token_counter_method` 控制：
   - `auto`（默认）：优先使用 `tiktoken` 精确计数，tiktoken 不可用时回退到字符启发式估算。
   - `tiktoken`：强制使用 tiktoken 精确计数（适用于对精度要求高、环境已安装 tiktoken 的场景）。
   - `heuristic`：始终使用字符启发式估算（中文约 2 字符/token，英文约 4 字符/token），不依赖 tiktoken，适用于轻量环境或对性能敏感的场景。

   **混合校准（`use_api_calibration`，默认启用）**：LLM API 每次响应会返回 `usage.prompt_tokens`（实际消耗的精确 token 数）。ContextManager 在每次 LLM 调用后记录此精确值作为基准，后续新增消息（tool 结果、assistant 回复等）仅估算 delta。总量 = 精确基准 + 估算 delta。该方案精度最高（直接来自模型）、自动纠偏（每轮 API 调用后重校准）、且不依赖 tiktoken。压缩触发后基准自动重置，下次 API 调用后重新校准。不返回 usage 的兼容服务自动回退到全量估算。

   **性能考量**：tiktoken 基于 Rust 实现，单次编码速度较快，但每次 LLM 调用前对全部消息列表计数仍有开销。启用混合校准后，常见场景下无需 tiktoken 即可获得高精度计数（仅 delta 估算按 `token_counter_method` 选择）。tiktoken 列为可选依赖（requirements.txt 中标注），用户按需安装。

2. **阈值触发机制**：在每次发送给 LLM 前，检查当前消息列表的 token 总量。当总量超过 `max_context_tokens * compression_threshold`（默认 70%）时，触发压缩流程。压缩持续执行各层策略，直到 token 降至 `max_context_tokens * safe_threshold`（安全线，默认 50%）以下停止。安全线低于触发阈值，确保压缩后留有充足余量，避免连续多轮交互频繁反复触发压缩。

3. **分层多策略压缩**（依次执行，前两层为低成本无 LLM 调用操作，第三层保留语义信息，第四层为兜底裁剪，直到 token 降至安全线 `safe_threshold` 以下）：
   - **第一层：工具输出截断**（always-on，内联）—— 当工具结果内容超过 `max_tool_output_tokens` 时，截断为「头部 + 尾部 + 截断标记」。成本极低，无 LLM 调用。
   - **第二层：大体积工具消息卸载**（threshold-triggered）—— 对已过期的 tool 消息（含 tool_call 的 assistant 消息及其对应的 tool 结果），将原始内容替换为简短引用摘要（如 `[已压缩: read_file(path=xxx), 结果 N 字符]`），保留语义标记但释放 token。成本低，无 LLM 调用。
   - **第三层：LLM 摘要压缩**（threshold-triggered，成本较高）—— 将早期消息发送给 LLM 生成结构化摘要，以单条摘要消息替换，保留语义信息。支持层级摘要（已有摘要 + 新旧消息 -> 新摘要）。先于裁剪执行，以最大化信息保留。
   - **第四层：滑动窗口裁剪**（threshold-triggered，兜底）—— 若前三层仍不足以降至安全线，保留最近 `keep_recent_messages` 条消息，丢弃更早的消息（系统提示词除外）。作为最终兜底策略，确保 token 一定降至安全线以下。

4. **Token 预算管理**：总 token 预算 = `max_context_tokens`，按用途分配：系统提示词 + 工具 schema + 对话历史 + 响应预留。确保压缩后留有足够空间。

### 多轮对话上下文持久化

当前 CLI 每次 `engine.run()` 创建全新 messages 列表，无跨轮次上下文。引入 `ContextManager` 作为消息历史的持有者：
- CLI 交互循环中创建一个 `ContextManager` 实例，在多次用户输入间复用
- 每次 `engine.run()` 时，引擎从 `ContextManager` 获取已有历史，追加新消息，压缩后发给 LLM
- `ContextManager` 透明地处理压缩，引擎无需关心

## What Changes

- 新增 `src/sagent/context/` 模块，包含 token 估算器、上下文管理器、压缩策略
- 在 `config/models.py` 新增 `ContextConfig` 配置模型（含 `use_api_calibration` 混合校准开关）
- 在 `config.example.yaml` 新增 context 配置段
- 在 `cli/app.py` 中创建 `ContextManager` 并注入引擎，实现跨轮次上下文持久化
- 对 `ReActEngine` 做最小化改造：支持可选注入 `ContextManager`，有则用它管理消息，无则保持原有行为（向后兼容）
- 对 `PlanEngine` 做最小化改造：Plan 拆解与汇总阶段也经过 `ContextManager` 管理
- 对 `LLMClient/LLMResponse` 改造：`LLMResponse` 新增 `usage` 字段暴露 API 返回的 token 用量
- 新增 `context/prompts.py` 定义摘要生成提示词
- 新增 `tests/unit/` 测试覆盖 token 估算、各压缩策略、上下文管理器整体流程、混合校准
- 新增 `tests/engines/` 测试覆盖引擎集成 `ContextManager` 后的多轮对话与压缩触发
- 在 `requirements.txt` 中添加 `tiktoken` 作为可选依赖

## Impact

- Affected specs: agent-core（ReAct/Plan 引擎）、cli（交互循环）、config（新增 ContextConfig）、llm（LLMResponse 新增 usage 字段）
- Affected code:
  - 新增：`src/sagent/context/`（token_counter.py、context_manager.py、strategies.py、prompts.py、__init__.py）
  - 修改：`src/sagent/config/models.py`（新增 ContextConfig，AppConfig 添加 context 字段）
  - 修改：`src/sagent/config/loader.py`（无需改动，AppConfig 自动解析）
  - 修改：`src/sagent/llm/client.py`（LLMResponse 新增 usage 字段，chat 方法返回时赋值）
  - 修改：`src/sagent/core/react_engine.py`（可选注入 ContextManager，LLM 调用后 record_llm_usage）
  - 修改：`src/sagent/core/plan_engine.py`（可选注入 ContextManager）
  - 修改：`src/sagent/cli/app.py`（创建 ContextManager，跨轮次复用）
  - 修改：`config.example.yaml`（新增 context 配置段）
  - 修改：`requirements.txt`（新增 tiktoken）
  - 新增：`tests/unit/test_token_counter.py`、`tests/unit/test_context_manager.py`、`tests/unit/test_strategies.py`
  - 新增：`tests/engines/test_context_integration.py`

## ADDED Requirements

### Requirement: Token 估算

系统 SHALL 提供对消息列表的 token 估算能力，计数方式由配置项 `token_counter_method` 控制，支持 `auto`/`tiktoken`/`heuristic` 三种模式。系统 SHALL 支持混合校准（由 `use_api_calibration` 控制，默认启用），利用 LLM API 返回的 `usage.prompt_tokens` 作为精确基准。

#### Scenario: 使用 tiktoken 计数
- **WHEN** `token_counter_method` 为 `auto` 或 `tiktoken`，且 tiktoken 可用、模型编码已知
- **THEN** 系统使用 tiktoken 对消息内容进行精确 token 计数

#### Scenario: 字符启发式回退（auto 模式）
- **WHEN** `token_counter_method` 为 `auto`，但 tiktoken 不可用或模型编码未知
- **THEN** 系统回退到字符启发式估算（中文约 2 字符/token，英文约 4 字符/token），结果合理近似

#### Scenario: 强制字符启发式（heuristic 模式）
- **WHEN** `token_counter_method` 为 `heuristic`
- **THEN** 系统始终使用字符启发式估算，不加载 tiktoken，不依赖该库

#### Scenario: tiktoken 强制模式但不可用
- **WHEN** `token_counter_method` 为 `tiktoken`，但 tiktoken 未安装
- **THEN** 系统回退到字符启发式估算并记录一条 WARNING 日志，提示 tiktoken 不可用

#### Scenario: 混合校准 - 使用 API 返回的精确 token 数
- **WHEN** `use_api_calibration` 为 `true`，且 LLM API 响应包含 `usage.prompt_tokens`
- **THEN** ContextManager 记录 `prompt_tokens` 作为精确基准，`token_count` 返回「精确基准 + 新增消息估算 delta」

#### Scenario: 混合校准 - 压缩后基准重置
- **WHEN** 触发压缩（`_compress` 执行）
- **THEN** 混合校准基准被重置，`token_count` 回退到全量估算；下次 LLM 调用后自动重新校准

#### Scenario: 混合校准 - API 不返回 usage
- **WHEN** `use_api_calibration` 为 `true`，但 LLM API 响应不包含 `usage`（部分兼容服务）
- **THEN** ContextManager 跳过校准，`token_count` 回退到全量估算（按 `token_counter_method` 计数）

#### Scenario: 混合校准 - 禁用
- **WHEN** `use_api_calibration` 为 `false`
- **THEN** `record_llm_usage` 不生效，`token_count` 始终使用全量估算

### Requirement: 上下文管理器

系统 SHALL 提供 ContextManager，负责维护消息历史、估算 token 总量、在阈值触发时执行压缩。

#### Scenario: 正常添加与获取消息
- **WHEN** 引擎通过 ContextManager 添加消息并获取消息列表
- **THEN** ContextManager 返回当前全部消息（未超阈值时不压缩）

#### Scenario: 阈值触发压缩
- **WHEN** 消息列表 token 总量超过 `max_context_tokens * compression_threshold`
- **THEN** ContextManager 自动执行分层压缩策略，直到 token 降至 `max_context_tokens * safe_threshold`（安全线）以下，返回压缩后的消息列表

#### Scenario: 系统提示词保护
- **WHEN** 执行压缩时
- **THEN** 系统提示词（role=system 的第一条消息）始终保留不被压缩或丢弃

### Requirement: 分层压缩策略

系统 SHALL 实现四层压缩策略，依次执行，前两层为低成本无 LLM 调用操作，第三层保留语义信息，第四层为兜底裁剪。

#### Scenario: 第一层 - 工具输出截断
- **WHEN** 工具结果消息内容超过 `max_tool_output_tokens`
- **THEN** 系统将内容截断为头部 + 尾部 + 截断标记，保持消息结构不变

#### Scenario: 第二层 - 大体积工具消息卸载
- **WHEN** token 超阈值且存在已过期的工具调用/结果消息对
- **THEN** 系统将旧的工具调用参数与结果替换为简短引用摘要，释放 token 空间

#### Scenario: 第三层 - LLM 摘要压缩
- **WHEN** 前两层不足以降至安全线且 `enable_summary` 为 true
- **THEN** 系统调用 LLM 对早期消息生成结构化摘要，摘要长度受 `summary_max_tokens` 约束（注入提示词告知 LLM 上限），以单条摘要消息替换，插入到系统提示词之后；先于裁剪执行以最大化信息保留

#### Scenario: 第四层 - 滑动窗口裁剪（兜底）
- **WHEN** 前三层仍不足以降至安全线，或摘要失败/未启用
- **THEN** 系统保留最近 `keep_recent_messages` 条消息，丢弃更早的消息（系统提示词除外），作为兜底确保 token 降至安全线以下

#### Scenario: 裁剪边界 tool_call/tool_result pair 完整性保护
- **WHEN** 裁剪边界落在 `tool` 结果消息上（其父 `assistant(tool_calls)` 消息将被丢弃）
- **THEN** 系统自动向前扩展裁剪边界，将父 `assistant(tool_calls)` 消息纳入保留范围，避免产生孤儿 `tool` 消息导致 LLM API 报错

#### Scenario: 层级摘要
- **WHEN** 已存在摘要消息且需要再次压缩
- **THEN** 系统将旧摘要与新待压缩消息合并，生成新的更新摘要，避免摘要无限增长

### Requirement: 多轮对话上下文持久化

系统 SHALL 在 CLI 交互循环中维护跨轮次的对话上下文，使 Agent 能引用之前轮次的信息。

#### Scenario: 跨轮次上下文保持
- **WHEN** 用户在第二轮输入时
- **THEN** 发给 LLM 的消息列表包含第一轮的对话历史（经过压缩管理后的版本）

#### Scenario: 长对话自动压缩
- **WHEN** 多轮对话累积导致 token 超阈值
- **THEN** 系统自动触发压缩，旧对话被摘要或裁剪，最近对话保留完整

### Requirement: 引擎向后兼容

系统 SHALL 保证 ReActEngine 和 PlanEngine 在未注入 ContextManager 时保持原有行为不变。

#### Scenario: 无 ContextManager 时保持原有行为
- **WHEN** 引擎构造时未传入 ContextManager
- **THEN** 引擎使用原有逻辑直接管理 messages 列表，不执行任何压缩

### Requirement: 上下文管理配置

系统 SHALL 通过 YAML 配置文件管理上下文管理相关参数。

#### Scenario: 配置加载
- **WHEN** 配置文件包含 context 段
- **THEN** 系统解析为 ContextConfig，包括 max_context_tokens、compression_threshold、safe_threshold、keep_recent_messages、max_tool_output_tokens、token_counter_method、use_api_calibration、enable_summary、summary_max_tokens 等参数

#### Scenario: 配置缺省
- **WHEN** 配置文件未包含 context 段
- **THEN** 系统使用默认配置（max_context_tokens=128000、compression_threshold=0.7、safe_threshold=0.5 等）
