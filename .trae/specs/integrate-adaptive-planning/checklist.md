# Checklist

- [x] 默认启动使用统一 Agent 入口，并采用 `adaptive` 计划策略
- [x] `adaptive` 能对简单任务直通 ReAct，对复杂任务生成并执行有序计划
- [x] `always` 能复现当前 Plan 模式，`never` 能复现当前纯 ReAct 模式
- [x] 计划策略仅从 `planning.strategy` 配置读取，当前已有的 `--mode` 已移除且传入时明确提示配置项；未实现过的 `--planning` 按未知参数处理
- [x] 自适应判断使用结构化响应，解析失败不会导致会话崩溃
- [x] 计划步骤有数量上限，执行过程复用 ReAct 和现有工具权限控制
- [x] 计划使用独立 PlanState 管理，普通 assistant/tool 消息仍由 ContextManager 管理，不把计划事件无界追加到 messages
- [x] 每次步骤 LLM 调用只在 messages 副本中临时注入有限长度 Plan Projection，不写回 ContextManager；已完成步骤使用摘要，当前步骤和最近观察保留完整信息
- [x] 未进入计划流程或开始新任务时，不注入旧的 Plan Projection
- [x] PlanState 的可恢复摘要和必要状态纳入会话保存链路，且新任务不会继承已完成旧计划
- [x] 重规划次数受上限约束，达到上限时给出可解释的最终收尾
- [x] CLI 仅展示计划摘要、步骤、工具调用、观察和最终回答，不输出隐藏思维链
- [x] 事件、日志和 Span 能区分策略选择、计划执行、重规划和完成状态
- [x] 受影响测试与完整离线测试通过，新增代码中文注释 UTF-8 且无乱码、无 emoji
