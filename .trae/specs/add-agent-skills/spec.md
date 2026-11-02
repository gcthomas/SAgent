# Agent Skills 能力 Spec

## Why
当前 SAgent 已支持内置 Tool 和 MCP Tool，但缺少可复用的领域知识、操作流程和参考资源封装能力。需要引入与 Agent Skills 开放规范兼容的 Skill，使 Agent 能够在任务相关时按需加载指导内容，同时继续复用现有 ReAct、Plan、ToolRegistry 和权限体系。

## What Changes
- 新增基于 `SKILL.md` 的 Skill 目录格式，兼容 Agent Skills 开放规范的核心 frontmatter。
- 新增 Skill 发现、元数据解析、校验、来源管理和按需正文加载能力。
- 启动时仅向模型暴露 Skill 的名称、描述和来源；正文与资源按需加载。
- 新增统一的 `load_skill` 工具，不为每个 Skill 动态注册独立 Tool。
- Skill 正文加载后继续通过现有 ToolRegistry、PermissionPolicy 和 ContextManager 执行后续操作。
- 统一使用 `.sagent/skills` 作为 SAgent 的默认 Skill 目录：项目根目录下的 `.sagent/skills` 和用户主目录下的 `.sagent/skills`；`.agents/skills` 不作为默认扫描目录，跨 Agent 技能通过配置指定的额外目录接入。
- 对项目级、用户级和配置指定的 Skill 进行明确来源管理，避免同名 Skill 静默覆盖。
- 新增 `/skills` 查看命令和 `/skill <name> [instruction]` 显式调用入口。
- 新增 Skill 相关配置、权限、日志、Span 和测试覆盖。
- 不在本次变更中实现 Skill 市场、自动安装、自动生成/修改 Skill、动态命令插值或 Skill 专属工具注册。

## Impact
- Affected specs: Skill discovery, progressive disclosure, tool execution, permissions, context management, CLI commands, observability.
- Affected code: `src/sagent/skills/`, `src/sagent/tools/`, `src/sagent/core/`, `src/sagent/config/`, `src/sagent/permissions/`, `src/sagent/cli/`, `src/sagent/observability/`, `tests/unit/`, `tests/engines/`, `config.example.yaml`.

## ADDED Requirements

### Requirement: Skill 目录格式
系统 SHALL 支持一个包含 `SKILL.md` 的 Skill 目录。

#### Scenario: 有效 Skill
- **WHEN** Skill 目录包含合法的 `SKILL.md`
- **THEN** 系统 SHALL 解析该 Skill 的名称、描述、来源路径和可选元数据
- **AND** SHALL 将 Markdown 正文作为 Skill 指令保存，供后续按需加载

#### Scenario: Skill 目录结构
- **WHEN** Skill 包含 `scripts/`、`references/` 或 `assets/` 子目录
- **THEN** 系统 SHALL 将这些目录视为资源目录
- **AND** SHALL 不在发现阶段读取或执行其中的文件

### Requirement: Agent Skills 元数据校验
系统 SHALL 校验核心 frontmatter 字段。

#### Scenario: 元数据合法
- **WHEN** `name` 和 `description` 存在且满足开放规范约束
- **THEN** Skill SHALL 进入可用列表

#### Scenario: 元数据非法
- **WHEN** 缺少必填字段、名称格式非法、名称与目录不一致或字段超过限制
- **THEN** 系统 SHALL 忽略该 Skill 的执行资格
- **AND** SHALL 记录可诊断的校验失败原因
- **AND** SHALL 不影响其他合法 Skill 加载

### Requirement: 多来源 Skill 发现
系统 SHALL 支持并明确区分以下 Skill 根目录：

- 项目级目录：当前项目根目录下的 `.sagent/skills`；
- 用户级目录：当前用户主目录下的 `.sagent/skills`；
- 配置目录：由 `skills.extra_dirs` 指定的一个或多个额外目录；该目录用于显式接入跨 Agent 共享技能，例如 `.agents/skills`。

`.agents/skills` SHALL NOT 作为 SAgent 的默认扫描目录。系统 SHALL 只扫描上述已启用 Skill 根目录的直接子目录；每个直接子目录 SHALL 以其目录名作为 Skill 标识候选，并通过其中的 `SKILL.md` 完成校验。

#### Scenario: 来源优先级
- **WHEN** 多个来源包含同名 Skill
- **THEN** 系统 SHALL 保留来源信息并避免静默覆盖
- **AND** 默认简写名称仅在名称唯一时可用
- **AND** 名称冲突时 SHALL 要求通过来源限定名选择

#### Scenario: 项目级目录
- **WHEN** 当前项目根目录存在 `.sagent/skills/<skill-name>/SKILL.md`
- **THEN** 系统 SHALL 将该 Skill 标记为项目级 Skill
- **AND** 项目级 Skill 的来源标识 SHALL 包含 `project`

#### Scenario: 用户级目录
- **WHEN** 当前用户主目录存在 `.sagent/skills/<skill-name>/SKILL.md`
- **THEN** 系统 SHALL 将该 Skill 标记为用户级 Skill
- **AND** 用户级 Skill 的来源标识 SHALL 包含 `user`

#### Scenario: 目录不可访问
- **WHEN** 某个 Skill 根目录不存在或不可读
- **THEN** 系统 SHALL 记录该目录不可用
- **AND** SHALL 继续发现其他来源中的 Skill

### Requirement: 渐进式披露
系统 SHALL 按三个层级向 Agent 提供 Skill 内容。

#### Scenario: 启动阶段
- **WHEN** Agent 会话初始化
- **THEN** 系统 SHALL 只注入可用 Skill 的名称、描述和来源摘要
- **AND** SHALL 不将所有 `SKILL.md` 正文注入系统提示词

#### Scenario: 按需加载正文
- **WHEN** Agent 判断任务需要某个 Skill，或用户显式调用某个 Skill
- **THEN** 系统 SHALL 通过统一的 `load_skill` 能力加载该 Skill 的完整正文
- **AND** SHALL 返回 Skill 根目录和资源引用规则

#### Scenario: 按需加载资源
- **WHEN** 已加载的 Skill 指令引用 `references/`、`scripts/` 或 `assets/` 中的文件
- **THEN** Agent SHALL 使用现有文件工具或明确的资源读取能力按需访问
- **AND** 系统 SHALL 不因加载 Skill 自动执行脚本、安装依赖或调用 MCP

### Requirement: Skill 加载工具
系统 SHALL 提供名称为 `load_skill` 的统一 Tool。

#### Scenario: 成功加载
- **WHEN** 使用合法且可用的 Skill 名称调用 `load_skill`
- **THEN** 工具 SHALL 返回正文、来源、根目录和资源访问约束
- **AND** SHALL 记录 Skill 名称、来源、正文大小和加载结果

#### Scenario: Skill 不存在
- **WHEN** 调用未发现或不可用的 Skill
- **THEN** 工具 SHALL 返回以 `错误:` 开头的结果
- **AND** SHALL 不读取任意用户指定路径

#### Scenario: 正文超过预算
- **WHEN** Skill 正文超过配置的单 Skill 正文预算
- **THEN** 系统 SHALL 拒绝加载并返回拆分参考文件的明确错误
- **AND** SHALL 不静默截断 Skill 流程指令

### Requirement: Tool、MCP、Skill 边界
系统 SHALL 保持以下边界：Skill 只提供知识、流程和资源引用，Tool 和 MCP Tool 提供实际执行能力。

#### Scenario: Skill 指导工具执行
- **WHEN** Skill 要求读写文件、执行命令或调用 MCP
- **THEN** 后续操作 SHALL 继续通过现有 ToolRegistry 执行
- **AND** SHALL 继续执行现有参数校验、权限审批、错误转换和 Span 记录

#### Scenario: Skill 不增加权限
- **WHEN** Skill 声明 `allowed-tools` 或正文要求使用某个工具
- **THEN** 该声明 SHALL 不自动授予权限
- **AND** 实际工具调用 SHALL 仍受 PermissionPolicy 控制

### Requirement: ReAct 与 Plan 一致可用
系统 SHALL 在 ReAct 和 Plan 两种执行模式下提供一致的 Skill 目录和加载能力。

#### Scenario: ReAct 模式
- **WHEN** Agent 以 ReAct 模式处理任务
- **THEN** Agent SHALL 能够发现、显式加载并按需使用 Skill

#### Scenario: Plan 模式
- **WHEN** Agent 以 Plan 模式拆分任务并执行步骤
- **THEN** 每个计划步骤 SHALL 能够访问同一组 Skill 元数据
- **AND** Skill 加载 SHALL 不改变计划状态语义

### Requirement: CLI Skill 命令
系统 SHALL 支持 Skill 查询和显式调用命令。

#### Scenario: 查看 Skill
- **WHEN** 用户输入 `/skills`
- **THEN** CLI SHALL 展示 Skill 名称、描述、来源和可用状态
- **AND** SHALL 展示校验失败或目录不可用的诊断信息

#### Scenario: 显式调用 Skill
- **WHEN** 用户输入 `/skill <name> [instruction]`
- **THEN** CLI SHALL 校验 Skill 名称并确保 Skill 正文被加载
- **AND** SHALL 将剩余 instruction 作为当前任务交给 Agent
- **AND** 无效 Skill 名称 SHALL 在进入 LLM 执行前返回错误

### Requirement: Skill 配置
系统 SHALL 增加 `skills` 配置段。

#### Scenario: 基本配置
- **WHEN** `skills.enabled` 为 false
- **THEN** 系统 SHALL 不发现、不注入 Skill 元数据且不注册 `load_skill`

#### Scenario: 目录与过滤
- **WHEN** Skill 功能启用
- **THEN** 系统 SHALL 支持配置额外目录、allow/deny 过滤、正文最大 token 数和资源读取限制
- **AND** 配置加载失败 SHALL 遵循现有配置错误处理逻辑

### Requirement: 权限与安全边界
系统 SHALL 将 Skill 加载视为受限的只读能力，但不改变其他工具的默认权限。

#### Scenario: 可信 Skill 目录
- **WHEN** `load_skill` 访问已配置且通过校验的 Skill
- **THEN** 系统 SHALL 允许读取其元数据和正文
- **AND** SHALL 限制路径解析在 Skill 根目录内

#### Scenario: 路径逃逸
- **WHEN** Skill 资源引用解析到 Skill 根目录之外
- **THEN** 系统 SHALL 拒绝读取并返回错误

#### Scenario: 不可信或被过滤 Skill
- **WHEN** Skill 来源被 deny 规则过滤或 Skill 未通过校验
- **THEN** 系统 SHALL 不将其提供给模型调用

### Requirement: 上下文预算与会话行为
系统 SHALL 将 Skill 正文视为普通上下文内容参与 token 预算和压缩。

#### Scenario: Skill 内容进入上下文
- **WHEN** `load_skill` 返回正文
- **THEN** 正文 SHALL 通过现有 ContextManager 写入当前上下文
- **AND** SHALL 受到现有 token 统计、压缩和会话持久化规则约束

#### Scenario: 上下文压缩后继续执行
- **WHEN** Skill 正文因上下文压缩不再保留
- **THEN** Agent SHALL 能够再次调用 `load_skill` 获取正文
- **AND** 系统 SHALL 不假设已加载 Skill 永久存在于上下文

### Requirement: 可观测性
系统 SHALL 为 Skill 生命周期提供结构化日志和 Span。

#### Scenario: 发现与加载
- **WHEN** 系统完成 Skill 扫描或执行 `load_skill`
- **THEN** SHALL 记录发现数量、过滤数量、失败原因、Skill 名称、来源、正文大小和结果
- **AND** SHALL 创建 `skill.load` 或等价 Span
- **AND** 默认 SHALL 不记录 Skill 正文全文

### Requirement: 测试与兼容性
系统 SHALL 为 Skill 功能增加离线测试，并保持现有 Tool、MCP、Memory、Plan 功能不变。

#### Scenario: 单元测试
- **WHEN** 运行受影响范围的 pytest
- **THEN** SHALL 覆盖 frontmatter 校验、发现、重名、过滤、预算、路径边界和 `load_skill` 工具

#### Scenario: 引擎测试
- **WHEN** 使用 FakeLLMClient 运行 ReAct 和 Plan 引擎测试
- **THEN** SHALL 覆盖元数据注入、显式加载和上下文压缩后的重新加载
- **AND** SHALL 按预期 LLM 调用轮次配置 FakeLLMClient

## MODIFIED Requirements

### Requirement: 工具注册与权限执行
现有工具执行 SHALL 继续由 ToolRegistry 作为唯一入口。Skill 仅新增 `load_skill` 工具注册，不得绕过现有参数校验、PermissionPolicy、Span 和错误返回约定。

### Requirement: 系统提示词装配
现有记忆前缀 SHALL 保持记忆语义。Skill 目录摘要 SHALL 以独立的系统提示词片段装配，并与记忆前缀共同作用于 ReAct 和 Plan 路径；不得将 Skill 正文全量固化到启动提示词。

### Requirement: CLI 启动信息
现有工具、MCP、Memory 和权限状态展示 SHALL 保持不变。Skill 启用时可追加技能数量和不可用数量，不得移除原有状态。

## REMOVED Requirements

无。
