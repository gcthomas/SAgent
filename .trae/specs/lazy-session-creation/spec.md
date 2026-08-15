# 延迟创建会话 Spec

## Why

当前 SAgent 启动时（`cli/app.py` 第 219 行）立即调用 `session_manager.ensure_current_session()`，在用户尚未输入任何内容之前就在 SQLite 中创建了一个空会话（0 条消息）。如果用户直接退出或只使用斜杠命令浏览历史，这个空会话就成了垃圾数据。结合业界主流 Agent 的做法（GitHub Copilot SDK 的 [PR #1452](https://github.com/github/copilot-sdk/pull/1452) 明确将 session 从 eager 改为 lazy 初始化；Open Interpreter / Aider 的会话都是在首次对话时隐式创建），延迟到第一次真正需要时再创建会话是更合理的做法。

## 推荐方案（结合业界最佳实践）

### 方案对比

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. 延迟到首次用户输入时创建（推荐）** | 启动时不创建会话；用户首次输入非斜杠、非空、非退出内容时，自动创建会话再执行引擎 | 零垃圾会话；与 Open Interpreter / Aider 的隐式创建一致；改动最小 | 首次交互有微小延迟（可忽略） |
| B. 启动时自动恢复最近会话 | 启动时加载最近一条会话而非新建 | 可延续上次对话 | 改变用户预期，可能不是用户想要的；超出当前需求范围 |
| C. 完全手动创建 | 只有 `/new` 才创建会话，首次输入不自动创建 | 最大灵活 | 破坏现有交互体验，用户必须先 `/new` |

**选择方案 A**：最小改动，符合业界 lazy initialization 趋势，不改变用户交互习惯（用户照常输入，首次输入时自动创建会话）。

### 核心设计

1. **移除启动时的 `ensure_current_session()` 调用**
   - `cli/app.py` 第 219 行删除 `session_manager.ensure_current_session()`
   - `SessionManager` 构造时 `_current_session_id` 保持为 `None`，直到首次真正需要时才创建

2. **首次用户输入时延迟创建**
   - 在交互循环中，收到第一条非斜杠、非空、非退出的用户输入时，调用 `session_manager.ensure_current_session()` 创建会话
   - 使用一个布尔标志位 `session_started` 跟踪是否已创建，避免重复调用
   - 创建发生在 `engine.run()` 之前、`new_trace_id()` 之后

3. **启动横幅调整**
   - 会话管理启用但尚无当前会话时，显示提示信息而非会话 ID
   - 格式：`会话: 尚未创建（首次对话时自动创建）`
   - 斜杠命令列表仍然显示

4. **斜杠命令无会话时的行为（已有容错，无需改动）**
   - `/sessions`：列出全部会话，不标记当前（`cur_id = None`）
   - `/switch <id>`：切换到已有会话（成功后 `session_started` 标志位也应置 True）
   - `/new`：创建新会话（成功后 `session_started` 标志位也应置 True）
   - `/session`：显示"无当前会话"（已有处理）
   - `/rename`：显示"重命名失败:无当前会话"（已有处理）
   - `/delete`、`/search`、`/help`：不依赖当前会话，正常工作

5. **退出时的保存（已有容错，无需改动）**
   - `save_current()` 在 `_current_session_id is None` 时返回 0，不会报错
   - 用户未输入任何内容就退出时，不会产生垃圾会话

6. **SessionManager 不变**
   - `ensure_current_session()` 方法保持不变，仍然由 `app.py` 在合适时机调用
   - `new_session()`、`switch_session()`、`save_current()`、`get_current_session()` 等方法均不变
   - SessionManager 本身已经是"无会话安全"的（所有方法对 `_current_session_id is None` 做了容错）

## What Changes

- 修改 `src/sagent/cli/app.py`：
  - 移除第 219 行 `session_manager.ensure_current_session()` 调用
  - 调整启动横幅（第 238-242 行）：无当前会话时显示提示文本
  - 交互循环中新增延迟创建逻辑：首次非斜杠用户输入时调用 `ensure_current_session()`
  - `/new` 和 `/switch` 命令成功后同步设置 `session_started` 标志位
- 修改 `tests/unit/test_session_manager.py`：现有 `ensure_current_session` 测试不受影响（测试的是方法本身，不是启动行为）
- 不改动：SessionManager、SessionStore、ContextManager、引擎、工具、记忆系统

## Impact

- Affected specs: add-session-management（修改"启动创建默认会话"场景的行为）
- Affected code:
  - 修改：`src/sagent/cli/app.py`（移除启动时创建、新增延迟创建、调整横幅）
  - 不改动：`src/sagent/session/manager.py`、`src/sagent/session/store.py`、`src/sagent/session/models.py`
  - 不改动：`src/sagent/context/context_manager.py`
  - 不改动：引擎、工具、记忆系统

## MODIFIED Requirements

### Requirement: 会话生命周期管理（在 add-session-management 基础上修改）

系统 SHALL 提供 SessionManager 管理"当前会话"状态，支持创建、切换、重命名、删除会话。会话的创建时机从"启动时立即创建"改为"首次用户输入时延迟创建"。

#### Scenario: 启动时不创建会话（修改原"启动创建默认会话"场景）
- **WHEN** CLI 启动且会话管理已启用
- **THEN** 系统不自动创建会话，`SessionManager._current_session_id` 保持为 None，启动横幅显示"尚未创建"提示

#### Scenario: 首次用户输入时延迟创建会话
- **WHEN** 用户首次输入非斜杠、非空、非退出的内容
- **THEN** 系统在调用引擎前自动创建新会话并设为当前会话，后续输入复用该会话

#### Scenario: 斜杠命令不触发延迟创建
- **WHEN** 用户在首次真正对话前输入斜杠命令（如 `/sessions`、`/search`）
- **THEN** 系统不创建会话，仅执行对应命令

#### Scenario: 通过 /new 或 /switch 显式创建/切换会话
- **WHEN** 用户在首次对话前执行 `/new` 或 `/switch <id>`
- **THEN** 系统创建或切换会话，且后续首次对话不再重复创建

#### Scenario: 用户直接退出不产生垃圾会话
- **WHEN** 用户在没有任何对话输入的情况下退出（exit/quit/Ctrl+C/EOF）
- **THEN** 系统不创建任何会话，SQLite 中不新增空会话记录

#### Scenario: 创建并切换新会话（不变）
- **WHEN** 用户执行 `/new [标题]`
- **THEN** 系统创建新会话、将其设为当前会话，并清空/重置 ContextManager 的消息上下文

#### Scenario: 切换到已有会话（不变）
- **WHEN** 用户执行 `/switch <id>` 且该会话存在
- **THEN** 系统还原该会话的工作上下文装载到 ContextManager，并将其设为当前会话

#### Scenario: 切换到不存在的会话（不变）
- **WHEN** 用户执行 `/switch <id>` 但该会话不存在
- **THEN** 系统返回错误提示，不改变当前会话状态

#### Scenario: 删除会话保护（不变）
- **WHEN** 用户执行 `/delete <id>` 且 id 为当前会话
- **THEN** 系统拒绝删除并提示需先切换到其他会话
