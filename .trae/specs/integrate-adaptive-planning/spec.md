# ReAct 自适应计划执行 Spec

## Why
当前 ReAct 与 Plan 是两个相互切换的顶层执行引擎，默认 ReAct 在遇到复杂任务时不会主动进入计划流程，用户必须预先选择计划策略。需要将计划能力融合到 ReAct 中，由模型在当前任务确实需要时生成并执行计划，并通过配置统一控制执行策略。

## 推荐方案
采用“统一 Agent 编排入口 + 可控的计划策略 + Plan 作为 ReAct 的受控子流程”设计：

- 默认策略为 `adaptive`：先由 ReAct 判断任务是否需要计划；简单任务直接按现有 ReAct 循环执行，复杂任务进入计划阶段。
- 提供 `always` 与 `never` 两种策略：`always` 等价于当前 Plan 模式，`never` 等价于纯 ReAct。
- 计划不是一次性静态脚本，而是有限步骤的结构化执行状态：每步复用现有 ReAct 工具循环，记录步骤状态和结果；执行中允许根据观察结果重新规划或提前结束。
- 计划状态与对话消息分层管理：PlanState 是当前任务的唯一计划事实源，独立保存计划版本、步骤状态、步骤摘要、产物引用和重规划次数；计划事件不作为普通对话消息无限追加。
- 工具调用所需的 assistant/tool 消息仍按 OpenAI 协议追加到 ContextManager；计划状态更新不重复生成这些消息。
- 每次步骤 LLM 调用时，在请求级临时注入一段有限长度的 Plan Projection（不写回消息历史）；当前步骤和最近观察保留完整信息，已完成步骤只保留结构化摘要和必要产物引用。
- 计划完成后仅持久化最终计划摘要和必要恢复状态；新用户任务开始时清理活动 PlanState，避免把旧计划投影带入新任务。
- 默认不向用户暴露完整内部思维，只展示计划步骤、当前步骤、工具调用和结果等可审计过程信息。
- 不新增计划策略命令行参数，统一通过配置文件中的 `planning.strategy` 控制策略；仅移除当前已有的 `--mode` 参数。

## What Changes
- 新增 Agent 计划策略配置：`adaptive`、`always`、`never`，默认 `adaptive`。
- 新增自适应计划判断协议，要求模型以结构化结果返回是否需要计划及原因摘要。
- 在统一执行入口中编排 ReAct、计划生成、步骤执行、必要时重新规划和最终汇总。
- 复用现有 `PlanEngine` 的步骤解析、步骤进度事件和 `ReActEngine` 的工具执行逻辑，避免重复实现。
- 新增计划步骤数量、重规划次数等边界配置，防止计划失控和额外 LLM 调用无限增长。
- 移除当前已有的 `--mode` 启动参数，不新增 `--planning` 参数；计划策略仅由配置文件控制。这是 CLI 参数层面的不兼容变更。
- 增加配置、策略判断、计划执行和重规划测试。

## Impact
- Affected specs: agent-core、cli、config、context、observability。
- Affected code:
  - `src/sagent/config/models.py`
  - `src/sagent/core/react_engine.py`
  - `src/sagent/core/plan_engine.py`
  - `src/sagent/core/prompts.py`
  - `src/sagent/core/events.py`
  - `src/sagent/cli/app.py`
  - `config.example.yaml`
  - 相关 `tests/unit/`、`tests/engines/` 测试

## ADDED Requirements

### Requirement: 计划策略配置
系统 SHALL 支持 `adaptive`、`always`、`never` 三种计划策略，默认使用 `adaptive`。计划策略只能通过配置项 `planning.strategy` 指定。

#### Scenario: 默认自适应
- **WHEN** 配置未声明计划策略
- **THEN** Agent 使用 `adaptive`，简单任务直接执行，复杂任务先生成计划

#### Scenario: 强制计划
- **WHEN** 用户通过配置设置 `planning.strategy=always`
- **THEN** 每个用户任务先生成有序计划，再逐步执行

#### Scenario: 禁用计划
- **WHEN** 用户通过配置设置 `planning.strategy=never`
- **THEN** Agent 不调用计划判断和计划生成流程，保持纯 ReAct 行为

### Requirement: 计划策略命令行参数移除
系统 SHALL 不新增用于选择计划策略的命令行参数，并 SHALL 移除当前已有的 `--mode` 参数。

#### Scenario: 仅使用配置控制
- **WHEN** 用户启动 CLI
- **THEN** CLI 从配置文件读取 `planning.strategy`，不提供计划策略命令行覆盖

#### Scenario: 传入当前已移除参数
- **WHEN** 用户指定当前 CLI 中已有但本次变更移除的 `--mode`
- **THEN** CLI 拒绝启动，并明确提示计划策略需通过配置项 `planning.strategy=adaptive|always|never` 设置

#### Scenario: 传入未实现的设计参数
- **WHEN** 用户指定从未在当前 CLI 实现过的 `--planning`
- **THEN** 该参数不属于本次兼容或错误提示范围，按 CLI 未知参数处理

### Requirement: 自适应计划判断
在 `adaptive` 策略下，系统 SHALL 让 LLM 判断任务是否需要多步骤计划，并使用结构化结果驱动后续流程。

#### Scenario: 简单任务
- **WHEN** 判断结果为不需要计划
- **THEN** 系统直接进入现有 ReAct 循环，且不额外生成计划

#### Scenario: 复杂任务
- **WHEN** 判断结果为需要计划
- **THEN** 系统生成有序步骤并进入计划执行流程

#### Scenario: 判断结果不可解析
- **WHEN** LLM 返回不符合约定结构的判断结果
- **THEN** 系统记录可观测事件并按配置的确定性策略继续；默认直接进入 ReAct，不因判断格式问题使会话崩溃

### Requirement: 计划执行状态
系统 SHALL 以独立的结构化 PlanState 管理计划生命周期：判断、生成、执行、重规划或完成。PlanState 不等同于对话消息列表，也不要求将每个计划事件追加到 messages。

#### Scenario: 计划状态与消息历史分层
- **WHEN** 计划进入执行阶段
- **THEN** 系统在 PlanState 中保存计划版本、步骤列表、每步状态、步骤摘要、必要产物引用和重规划次数；ContextManager 继续维护用户、assistant、tool 等对话消息

#### Scenario: 生成计划上下文投影
- **WHEN** ReAct 为当前步骤调用 LLM
- **THEN** 系统仅在本次请求的 messages 副本中注入有限长度的 Plan Projection，包括原始任务摘要、当前步骤、已完成步骤摘要、剩余步骤和最近观察；该投影不写回 ContextManager，不重复注入完整历史计划

#### Scenario: 普通 ReAct 请求不携带旧计划
- **WHEN** 当前任务未进入计划流程，或计划已经完成并开始新的用户任务
- **THEN** 系统不注入 Plan Projection，ReAct 只使用正常的 ContextManager 消息历史

#### Scenario: 步骤完成后的状态更新
- **WHEN** 当前步骤执行完成
- **THEN** 系统先更新 PlanState 的步骤状态和结构化摘要，再生成下一次 LLM 请求需要的计划投影；不因状态更新而追加一条普通 assistant 消息

#### Scenario: 计划完成后的清理
- **WHEN** 计划成功完成、提前完成或因上限收尾
- **THEN** 系统保留最终计划摘要和结果引用供最终回答使用，并清理当前活动计划状态，避免后续用户任务继承旧计划

#### Scenario: 按步骤执行
- **WHEN** 计划生成成功
- **THEN** 系统按顺序执行步骤，每个步骤复用 ReAct 工具调用能力，并输出步骤开始与结果事件

#### Scenario: 计划完成后汇总
- **WHEN** 所有步骤成功完成
- **THEN** 系统基于原始任务、计划和步骤结果生成最终回答

#### Scenario: 步骤提前结束
- **WHEN** 某一步已获得足以回答原始任务的结果
- **THEN** 系统可以提前结束剩余步骤并生成最终回答

### Requirement: 有限重规划
系统 SHALL 支持基于步骤执行观察结果的有限重规划，并受最大重规划次数约束。

#### Scenario: 需要调整计划
- **WHEN** 工具结果表明原计划不可行或前置条件发生变化
- **THEN** 系统更新未执行步骤，保留已完成结果并继续执行新计划

#### Scenario: 达到重规划上限
- **WHEN** 重规划次数达到配置上限
- **THEN** 系统停止继续重规划，使用当前结果进行收尾并明确提示计划未完全完成

### Requirement: 安全与可审计输出
系统 SHALL 对计划步骤、工具调用和重规划行为发出结构化事件，并遵循现有工具权限控制。

#### Scenario: 高风险工具调用
- **WHEN** 计划步骤调用受权限控制的工具
- **THEN** 工具调用仍经过现有权限闸门，不因进入计划流程而绕过审批或拒绝规则

#### Scenario: 过程展示
- **WHEN** CLI 运行自适应或强制计划策略
- **THEN** 用户可看到是否进入计划、步骤进度、工具行动和最终结果，但不要求展示隐藏思维链

### Requirement: 上下文与会话兼容
系统 SHALL 将计划状态与计划执行消息纳入现有上下文管理与会话保存机制，但必须区分独立 PlanState 和普通对话消息：PlanState 负责活动计划的结构化状态，ContextManager 负责对话消息的 token 预算与压缩。

#### Scenario: 长计划上下文控制
- **WHEN** 计划执行导致上下文接近预算
- **THEN** ContextManager 按既有策略压缩普通对话消息；PlanState 不作为无界消息追加，已完成步骤通过结构化摘要压缩，当前步骤及最近观察按计划投影保留

#### Scenario: 计划步骤消息归属
- **WHEN** 步骤执行产生 assistant/tool 消息
- **THEN** 这些消息仍进入 ContextManager 并遵守工具调用消息配对规则；计划步骤状态、计划版本和摘要只更新 PlanState，不重复写入 messages

#### Scenario: 会话恢复
- **WHEN** 用户在启用会话管理时继续已有会话
- **THEN** 会话保存 PlanState 的可恢复摘要和必要状态；加载后可恢复活动计划或明确标记为需要重新规划，新的用户任务不会继承已完成的旧计划

## MODIFIED Requirements

### Requirement: ReAct 执行模式
ReAct SHALL 继续负责单步任务的推理、工具调用和观察循环；在 `adaptive` 策略下，它还负责触发计划判断并将复杂任务交给计划编排流程。`never` 策略下保持现有纯 ReAct 行为。

### Requirement: Plan 执行模式
Plan SHALL 从独立顶层模式扩展为可由统一 Agent 入口调用的计划子流程；其步骤执行继续复用 ReAct，不得复制工具调用逻辑。

### Requirement: Agent 配置
Agent 配置 SHALL 移除旧的顶层 `mode` 字段，并新增独立的 `planning` 配置；未显式配置时保持默认启动行为为统一 Agent 入口下的自适应策略。计划策略不提供命令行覆盖。

## REMOVED Requirements

### Requirement: 通过 `--mode` 选择顶层执行模式
**Reason**: 顶层 mode 参数与计划策略表达同一维度，且计划策略统一由配置控制。
**Migration**: 使用配置项 `planning.strategy=always|never|adaptive`。

### Requirement: 通过 `--planning` 覆盖计划策略
**Reason**: `--planning` 仅存在于本规格的设计过程，从未成为当前项目的 CLI 参数，因此不应作为需要移除或兼容处理的现有功能。
**Migration**: 无；计划策略直接使用配置文件中的 `planning.strategy=adaptive|always|never`。
