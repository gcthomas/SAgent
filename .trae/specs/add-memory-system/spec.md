# 长期记忆系统 Spec

## 推荐方案概述

借鉴 Hermes Agent 的双文件 Markdown 记忆架构，并贴合 SAgent「简单、通用、可扩展、本地文件持久化」的定位，采用**轻量级 Markdown 记忆方案**：

- **持久化**：两个本地 Markdown 文件——`USER.md`（用户偏好与环境信息）与 `MEMORY.md`（项目上下文与学习到的经验）。Markdown 人可读、程序友好，用户可直接用编辑器查看与修改；元数据（id、时间、分类等）非必要，聚焦「记住了什么」。
- **字符上限与反思整理**：两个文件分别设字符上限（`USER.md` 默认 2000 字符、`MEMORY.md` 默认 4000 字符）。当 LLM 写入新内容导致某文件超限时，触发一次 LLM 反思整理（去重、合并冗余、精简表述），将该文件压缩回其上限以内。反思整理仅在超限时触发，非每轮调用，成本可控。
- **LLM 自主提取（三工具）**：提供 `add_memory` / `replace_memory` / `remove_memory` 三个工具注册入 ReAct 工具表，LLM 在推理过程中自主决定是否调用及调用哪个——有价值的新信息用 `add_memory` 追加，偏好/事实变化时用 `replace_memory` 更新，过时内容用 `remove_memory` 删除，无价值则不调用，零额外 LLM 开销。替代原方案「每轮问答后强制调用 LLM 提取」的高成本模式。为引导 LLM 正确使用记忆工具，在注入前缀中包含记忆使用引导提示词（`MEMORY_GUIDE_PROMPT`），参考 Hermes 实践说明两个文件用途、工具使用时机、什么该保存/跳过与写入原则。
- **会话开始前注入并冻结**：会话启动时加载两个文件内容，与记忆使用引导提示词一同拼装为系统提示词前缀注入（结构：`MEMORY_GUIDE_PROMPT` + `[长期记忆]` + 各文件小节），整个会话期间冻结不变。即使记忆文件为空也注入引导提示词，使 LLM 知道记忆能力可用。此设计使 LLM 服务商可命中 prefix cache（前缀缓存），显著降低成本。记忆文件在会话期间的更新（经记忆工具）写入磁盘，但已注入的前缀快照不变，更新于下次会话生效。
- **不提供斜杠命令**：因 Markdown 文件人可读且可编辑，不再提供 `/memory` 系列斜杠命令；用户直接查看/编辑文件即可。
- **不提供 LLM 读取记忆工具**：记忆已在系统提示词注入，LLM 无需额外工具读取；仅提供 `add_memory` / `replace_memory` / `remove_memory` 三个写入工具。

### 与 Hermes 的对比与取舍

| 维度 | Hermes | 本方案 |
|------|--------|--------|
| 文件 | MEMORY.md + USER.md | 同（USER.md + MEMORY.md） |
| 格式 | Markdown | Markdown |
| 字符上限 | 有 | 有（两文件分别可配，USER.md 默认 2000 / MEMORY.md 默认 4000） |
| 超限处理 | LLM 自主整理 | LLM 反思整理（超限时触发） |
| 写入工具 | add/replace/remove | 同（add_memory/replace_memory/remove_memory） |
| 注入时机 | 会话开始 | 会话开始，并冻结以命中 prefix cache |
| 读取工具 | 提供 | 不提供（已注入系统提示词） |

### 为何不选其他方案

- **纯向量库/RAG 检索**：需引入 embedding 模型与向量库依赖，违背「本地文件、简单」定位；且检索式记忆无法保证稳定注入。本方案用「全量精简 Markdown 注入」替代，零外部依赖。
- **Letta 操作系统式自主管理**：分层记忆（核心/回忆/档案）实现复杂、维护成本高。本方案以双文件 Markdown 承载，足够覆盖偏好/项目/环境/经验四类需求。
- **知识图谱（Zep/Graphiti）**：需图数据库与本体维护，过重。
- **JSON 结构化存储**：人不可读、编辑不便；改用 Markdown 后用户可直接编辑，且元数据（id/时间/分类）非必要，聚焦内容本身。
- **每轮强制 LLM 提取**：成本高且非每轮都有值得记忆的内容；改用 LLM 自主提取（工具调用），仅在有价值时写入，零额外 LLM 调用。
- **仅 append 单工具**：用户偏好或事实变化时只能追加，旧/矛盾信息在两次反思整理之间持续存在，跨会话污染注入。改用 add/replace/remove 三工具，LLM 可即时更新或删除过时内容。

## Why

SAgent 当前具备会话内上下文管理（压缩、持久化）与跨会话历史，但**跨会话的长期记忆缺失**：每次新会话，Agent 对用户偏好、项目约定、运行环境、过往经验一无所知，需要用户重复说明。引入长期记忆可让 Agent 跨会话保持个性化与连续性，减少重复沟通，并从交互中持续积累可复用知识。

## What Changes

- 新增 `src/sagent/memory/` 包：Markdown 文件存储、记忆管理器（注入构建/反思整理）、提示词（含记忆使用引导 `MEMORY_GUIDE_PROMPT`、反思整理 `REFLECT_SYSTEM_PROMPT`、注入前缀构造 `build_injection_prefix`）。
- 新增 `src/sagent/tools/memory_tool.py`：`add_memory` / `replace_memory` / `remove_memory` 三个工具（与 `file_tools.py`、`shell_tool.py` 同级，继承 `Tool` 基类）。
- 新增 `MemoryConfig` 配置模型并挂载到 `AppConfig.memory`（缺省可用，默认启用）。
- 新增 `AddMemoryTool` / `ReplaceMemoryTool` / `RemoveMemoryTool`（均继承 `Tool` 基类），由 CLI 层构建并注册入工具表，LLM 在 ReAct 推理中自主调用。
- `cli/app.py` 集成：构建 `MemoryManager`；会话开始前用记忆构建系统提示词前缀并冻结（整个会话复用同一 system_prompt）；注册三个记忆工具。
- 不再扩展斜杠命令（移除原方案 `/memory` 系列）。
- `config.example.yaml` 增加 `memory` 段示例。
- 新增单元测试覆盖：Markdown 存储读写与字符上限、反思整理（FakeLLMClient 回放）、注入构建与会话冻结、三个记忆工具调用。

## Impact

- **Affected specs**: `add-session-management`（记忆与会话持久化为并列的两个持久化层，互不依赖）、`add-context-management`（记忆注入复用 system 消息保留机制，不修改压缩策略）。
- **Affected code**:
  - 新增：`src/sagent/memory/{__init__,store,manager,prompts}.py`、`src/sagent/tools/memory_tool.py`
  - 修改：`src/sagent/config/models.py`（新增 `MemoryConfig` 与 `AppConfig.memory`）、`src/sagent/cli/app.py`（构建/注入冻结/注册工具）、`config.example.yaml`（示例段）。
  - 不修改：`context/`、`session/`、`core/` 引擎（引擎通过自定义 system_prompt 接收记忆前缀）、`tools/registry.py`（不改，仅注册新工具实例）、`tools/base.py`（不改，仅继承）。

## ADDED Requirements

### Requirement: 长期记忆 Markdown 持久化

系统 SHALL 提供基于本地 Markdown 文件的非结构化长期记忆持久化，使用 `USER.md`（用户偏好与环境信息）与 `MEMORY.md`（项目上下文与学习到的经验）两个文件，文件内容为自由格式 Markdown 文本，无 id/时间/分类等结构化元数据。

#### Scenario: 启动加载

- **WHEN** 记忆管理器初始化且文件存在
- **THEN** 加载文件全文内容到内存
- **WHEN** 文件不存在
- **THEN** 以空字符串初始化，不报错

#### Scenario: 写入与原子落盘

- **WHEN** 记忆工具写入新内容
- **THEN** 追加到对应文件（以换行分隔），先写临时文件再 rename，避免中途异常导致文件损坏

### Requirement: 字符上限与反思整理

系统 SHALL 对每个记忆文件施加字符上限，超限时触发 LLM 反思整理。

#### Scenario: 超限触发反思

- **WHEN** 写入后文件内容长度超过对应上限（`user_max_chars` 或 `memory_max_chars`）
- **THEN** 调用 LLM 对文件全文进行反思整理：去重、合并冗余、精简表述，输出不超过上限的整理后内容
- **AND** 将整理后内容写回文件（原子写入）

#### Scenario: 反思失败不阻断

- **WHEN** 反思整理的 LLM 调用失败或输出非法
- **THEN** 记录日志并保留写入前内容，不阻断主流程

### Requirement: LLM 自主提取（add/replace/remove 三工具）

系统 SHALL 提供 `add_memory` / `replace_memory` / `remove_memory` 三个工具供 LLM 在推理过程中自主调用，替代每轮强制提取。工具描述需引导 LLM 判断目标文件：用户偏好/环境信息写入 `USER.md`，项目上下文/学习经验写入 `MEMORY.md`。系统 SHALL 在会话注入前缀中包含记忆使用引导提示词（`MEMORY_GUIDE_PROMPT`），引导 LLM 何时调用记忆工具、什么内容值得记忆、什么该跳过及写入原则，参考 Hermes Agent 的记忆引导实践。

#### Scenario: 新增记忆（add_memory）

- **WHEN** LLM 在 ReAct 推理中判断本轮交互存在值得记忆的新内容
- **THEN** LLM 调用 `add_memory` 工具，指定目标文件（user/memory）与内容
- **AND** 工具将内容追加到文件，并在超限时触发反思整理

#### Scenario: 更新记忆（replace_memory）

- **WHEN** LLM 判断已有记忆内容需要更新（如用户偏好或事实发生变化）
- **THEN** LLM 调用 `replace_memory` 工具，指定目标文件、待替换文本（old）与新文本（new）
- **AND** 工具在文件中查找 old 并替换为 new，未找到则返回提示；替换后在超限时触发反思整理

#### Scenario: 删除记忆（remove_memory）

- **WHEN** LLM 判断已有记忆内容已过时或不再相关
- **THEN** LLM 调用 `remove_memory` 工具，指定目标文件与待删除文本（content）
- **AND** 工具在文件中查找并删除该文本，未找到则返回提示

#### Scenario: 无值得记忆内容时不调用

- **WHEN** LLM 判断本轮交互无值得记忆的内容
- **THEN** 不调用任何记忆工具，零额外 LLM 开销

### Requirement: 会话开始前注入并冻结

系统 SHALL 在会话开始前将记忆使用引导提示词与当前记忆内容一同注入系统提示词前缀，并在整个会话期间冻结不变，以命中 LLM 服务商的 prefix cache。

#### Scenario: 会话开始注入冻结

- **WHEN** 会话启动
- **THEN** 加载 `USER.md` 与 `MEMORY.md` 内容，与记忆使用引导提示词（`MEMORY_GUIDE_PROMPT`）一同拼装为系统提示词前缀（结构：`MEMORY_GUIDE_PROMPT` + `[长期记忆]` + `## 用户档案\n...` + `## 记忆\n...`，空文件省略对应小节）
- **AND** 该前缀在整个会话期间保持不变，即使磁盘文件被记忆工具更新

#### Scenario: 记忆为空也注入引导提示词

- **WHEN** 会话启动且两个记忆文件均为空（或仅含空白）
- **THEN** 仍注入记忆使用引导提示词（`MEMORY_GUIDE_PROMPT`）作为前缀，使 LLM 知道记忆工具可用及何时调用
- **AND** 不含 `## 用户档案` / `## 记忆` 小节

#### Scenario: 注入内容在压缩中被保留

- **WHEN** 上下文触发分层压缩
- **THEN** 记忆作为开头连续 system 消息的一部分被 `_split_system_and_body` 分离保留，不被裁剪

#### Scenario: 更新于下次会话生效

- **WHEN** 会话期间记忆工具更新了磁盘文件
- **THEN** 当前会话已注入的前缀不变，更新内容于下次会话加载时生效

### Requirement: 记忆配置

系统 SHALL 通过 `MemoryConfig` 控制记忆功能，挂载到 `AppConfig.memory` 且缺省可用。

#### Scenario: 缺省启用

- **WHEN** 配置文件未提供 `memory` 段
- **THEN** 使用默认值（enabled=True、user_file="USER.md"、memory_file="MEMORY.md"、user_max_chars=2000、memory_max_chars=4000）
