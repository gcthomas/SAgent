# 通用 CLI Agent 应用 Spec

## Why
需要一个简单、通用、可扩展的 Python Agent 应用：以 CLI 方式与用户交互，基于 LLM 进行任务推理与工具调用，支持 ReAct 与 Plan 两种执行模式，并为未来 MCP / skill 扩展预留接口。

## What Changes
- 新增基于 Python 3.10+ 的 Agent 项目骨架（可通过 CLI 运行）。
- 使用 openai SDK 调用 LLM，封装统一的 LLM 客户端。
- 使用 pydantic 定义配置模型与工具参数 schema。
- 实现工具注册机制（Tool Registry），首批内置「文件读写」「Shell/命令执行」两类 function call 工具。
- 实现 ReAct 模式（思考-行动-观察循环）执行引擎。
- 实现 Plan 模式（先拆解计划，再分步执行）执行引擎。
- 通过 CLI 参数/命令在 ReAct 与 Plan 模式间切换。
- 配置采用 YAML 文件加载（模型、API Key、base_url、模式、最大迭代次数等）。
- 为 MCP 客户端与 skill 预留抽象接口（本阶段不实现具体逻辑）。

## Impact
- Affected specs: agent-core、tool-system、llm-client、cli、config。
- Affected code: 全新项目，主要文件位于 `src/sagent/` 下，配置 `config.yaml`，入口 `main.py`。

## ADDED Requirements

### Requirement: 配置加载
系统从 YAML 配置文件加载运行所需参数，并使用 pydantic 进行校验。

#### Scenario: 加载有效配置
- **WHEN** 程序启动并指定 `--config config.yaml`（或使用默认路径）
- **THEN** 系统读取 YAML 并解析为 pydantic 配置对象，缺失必填项时给出明确错误提示

#### Scenario: 环境变量覆盖 API Key
- **WHEN** 配置文件中 api_key 为空但设置了环境变量 `LLM_API_KEY`
- **THEN** 系统使用环境变量中的值

### Requirement: LLM 客户端
系统 SHALL 通过 openai SDK 调用大模型，支持 function calling。

#### Scenario: 发起带工具的对话请求
- **WHEN** Agent 携带消息历史与工具 schema 调用 LLM
- **THEN** LLM 客户端返回助手消息，可能包含文本回答或 tool_calls

### Requirement: 工具注册与调用
系统 SHALL 提供工具注册机制，工具参数使用 pydantic 定义 schema，并可转换为 OpenAI function calling 格式。

#### Scenario: 注册并调用内置工具
- **WHEN** 内置工具（文件读写 / Shell 执行）被注册到 Tool Registry
- **THEN** Registry 能输出所有工具的 function schema，并能按名称与参数执行对应工具，返回结果字符串

#### Scenario: 调用不存在的工具
- **WHEN** LLM 请求调用一个未注册的工具名
- **THEN** 系统返回错误信息给 LLM 而不崩溃

### Requirement: ReAct 执行模式
系统 SHALL 实现 ReAct 循环：LLM 推理 -> 请求工具 -> 执行工具 -> 观察结果 -> 继续，直到给出最终回答或达到最大迭代次数。

#### Scenario: 通过工具完成任务
- **WHEN** 用户提出需要工具协助的任务
- **THEN** Agent 在若干轮 thought/action/observation 后返回最终答案

#### Scenario: 达到最大迭代次数
- **WHEN** 循环次数超过配置的最大迭代次数仍未完成
- **THEN** 系统停止并返回当前可得的结果与提示

### Requirement: Plan 执行模式
系统 SHALL 支持 Plan 模式：先由 LLM 将复杂任务拆解为有序步骤，再逐步执行（每步可调用 ReAct 循环），最后汇总结果。

#### Scenario: 拆解并执行复杂任务
- **WHEN** 用户在 Plan 模式下提交复杂任务
- **THEN** Agent 先生成步骤列表，逐步执行并展示进度，最终输出汇总结果

### Requirement: CLI 交互
系统 SHALL 提供 CLI 入口，接收用户输入并输出回答，支持通过参数选择执行模式。

#### Scenario: 选择模式运行
- **WHEN** 用户运行 `python main.py --mode react`（或 `--mode plan`）
- **THEN** 系统进入对应模式，进入交互循环，接收输入并给出回答，支持退出命令

### Requirement: MCP 与 Skill 扩展接口（预留）
系统 SHALL 定义 MCP 客户端与 skill 的抽象接口/占位，使工具系统可在未来接入而无需重构核心。

#### Scenario: 预留接口存在
- **WHEN** 查看工具系统模块
- **THEN** 存在明确的抽象基类或接口定义（如 ToolProvider），说明未来 MCP / skill 的接入点，本阶段不含具体实现
