# Tasks

- [x] Task 1: 设计并实现计划策略配置
  - [x] SubTask 1.1: 在配置模型中移除旧 `agent.mode`，新增 `planning.strategy`、最大步骤数和最大重规划次数等最小配置
  - [x] SubTask 1.2: 从 CLI 移除当前已有的 `--mode` 参数；不新增 `--planning`，对传入的 `--mode` 提供明确错误，提示使用配置项 `planning.strategy`
  - [x] SubTask 1.3: 更新示例配置、启动提示及配置加载测试，确认策略只从配置读取

- [x] Task 2: 增加结构化的自适应计划判断
  - [x] SubTask 2.1: 定义计划判断的结构化响应模型与提示词，要求返回是否需要计划及简短理由
  - [x] SubTask 2.2: 在统一 Agent 编排入口中实现 `never`、`always`、`adaptive` 三种分支
  - [x] SubTask 2.3: 覆盖判断结果解析成功、解析失败和简单任务直通 ReAct 的测试

- [x] Task 3: 将 Plan 能力整合为可复用的受控子流程
  - [x] SubTask 3.1: 抽取或调整计划生成、步骤执行和最终汇总的边界，继续复用 ReAct 工具循环
  - [x] SubTask 3.2: 增加独立 PlanState，保存计划版本、步骤状态、步骤摘要、产物引用和重规划次数，不将计划事件无界追加到 messages
  - [x] SubTask 3.3: 为每次步骤 LLM 调用在 messages 副本中临时注入有限长度的 Plan Projection，不写回 ContextManager；当前步骤与最近观察保留更完整信息，已完成步骤使用结构化摘要
  - [x] SubTask 3.4: 确保 ContextManager、权限闸门、记忆前缀和会话保存链路继续生效

- [x] Task 4: 支持基于观察结果的有限重规划
  - [x] SubTask 4.1: 定义步骤状态与重规划触发条件，保留已完成步骤结果
  - [x] SubTask 4.2: 实现最大重规划次数限制，并在达到上限时进行可解释收尾
  - [x] SubTask 4.3: 增加重规划成功、重规划上限和提前完成测试

- [x] Task 5: 完善事件、展示和可观测性
  - [x] SubTask 5.1: 增加计划判断、策略选择、重规划和计划完成事件，接入现有事件展示器
  - [x] SubTask 5.2: 补充计划策略、步骤数量、重规划次数和最终状态的 Span/日志字段
  - [x] SubTask 5.3: 验证 CLI 不展示隐藏思维链，只展示计划摘要、工具行动、观察和最终结果

- [x] Task 6: 集成验证与回归
  - [x] SubTask 6.1: 补充 ReAct、Plan、自适应策略和会话上下文的单元/引擎测试，并覆盖当前已有 `--mode` 被移除后的错误提示
  - [x] SubTask 6.2: 运行受影响测试，再运行完整离线测试集
  - [x] SubTask 6.3: 根据 checklist 核对参数移除、安全权限和中文编码要求

- [x] Task 7: 修复代码检视发现的实现缺陷
  - [x] SubTask 7.1: `store.create_session` 对 `plan_state` 做 `json.dumps(..., ensure_ascii=False)`，与 `save_plan_state` 序列化方式保持一致
  - [x] SubTask 7.2: 修复可观测性 `mode` 维度回归：`AgentEngine` 记录本轮实际执行路径（react/plan），`app.py` 设置 `sagent.mode` 并保留 `sagent.planning_strategy`；`metrics.py` 与 `tests/unit/test_observability.py` 保持读 `sagent.mode` 不变
  - [x] SubTask 7.3: 补齐 `plan.summarize` Span 属性与 `plan_summarize`、`plan_replanned` 日志的最终状态与重规划次数字段（落实 SubTask 5.2）
  - [x] SubTask 7.4: 修复 `PlanEngine.run` 拆解失败回退分支未传 `persist_task`，避免 ContextManager 下重复添加用户任务消息
  - [x] SubTask 7.5: 恢复活动计划时用已完成步骤摘要预填 `step_results`，保证最终汇总包含已完成步骤结果
  - [x] SubTask 7.6: 修正 `tests/engines/test_plan_engine.py` 中因运算符优先级而恒真的投影不写回断言（改为传入 ContextManager 后断言）
  - [x] SubTask 7.7: 评估并处理恢复计划时重复写入用户任务消息的问题

# Task Dependencies
- Task 2 依赖 Task 1
- Task 3 依赖 Task 1 和 Task 2
- Task 4 依赖 Task 3
- Task 5 可与 Task 3、Task 4 的部分工作并行，但事件字段需在集成前确定
- Task 6 依赖 Task 1 至 Task 5
- Task 7 依赖 Task 1 至 Task 6，用于修复检视发现的缺陷
