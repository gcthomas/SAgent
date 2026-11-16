# Checklist

## 事件与引擎

- [x] `AgentEvent` 新增字段与事件类型后，既有字段语义与 `with_plan_context` 行为未改变
- [x] `EventPresenter` 遇到未知事件类型静默忽略，CLI 一轮对话输出与改动前一致
- [x] 取消令牌缺省为 `None` 时，引擎行为与既有实现一致
- [x] 取消在工具调用前生效，工具未被执行，并抛出 `RunCancelled`
- [x] `llm_completed` 事件携带该次请求的 usage 与耗时
- [x] `PlanEngine` 在步骤边界与重规划前检查取消令牌

## 存储与统计

- [x] `SessionStore` 支持跨线程调用，无 SQLite 线程错误，并发写入数据完整
- [x] 旧版 `sessions.db` 启动后自动创建 `turns` 表，既有数据不受影响
- [x] `search_sessions` 能同时命中会话标题与消息内容，并按会话去重
- [x] FTS5 不可用时搜索降级为 LIKE 且不报错
- [x] CLI 的 `/search` 行为未改变
- [x] `ContextManager` 用量快照为只读，累计与压缩逻辑未改变

## 审批

- [x] 需要审批时生成 `approval_id` 并下发 `data-approval` 部件
- [x] 批准后工具继续执行，状态从 `waiting_approval` 恢复
- [x] 拒绝后工具不执行，拒绝说明作为观察结果回写上下文
- [x] `always` 在会话内对相同工具与参数不再询问
- [x] 审批超时按 `non_interactive` 收敛（默认拒绝），前端展示超时
- [x] 对过期 `approval_id` 提交返回 409
- [x] 审批结果写入既有权限审计日志

## 后端服务

- [x] `python -m sagent.server` 可按配置启动，`GET /api/health` 返回 200
- [x] 缺少 `server` 配置段时使用默认值启动
- [x] 未构建前端时 `/` 返回明确的"前端未构建"提示，而非 500
- [x] 每个会话拥有独立运行时，两会话并行 run 不串流
- [x] 同一会话重复提交 run 返回 409
- [x] 同一会话连续多轮复用运行时，不重复加载历史
- [x] 删除进行中的会话会先取消 run，随后访问返回 404

## 流式协议

- [x] 响应为 `text/event-stream`，含 `x-vercel-ai-data-stream: v1` 响应头
- [x] 文本以 `text-start` / `text-delta` / `text-end` 增量下发
- [x] 工具调用以 `tool-input-available` / `tool-output-available` 下发，含 `toolCallId`
- [x] 流结束前必有一个终态（`completed` / `failed` / `cancelled`），且仅一个
- [x] 引擎异常映射为 `failed` 并携带错误摘要
- [x] `RunCancelled` 映射为 `cancelled`
- [x] 空消息返回 400，不产生 run

## 会话与轨迹 API

- [x] 会话列表按更新时间降序，字段含 id、title、updated_at、message_count
- [x] 创建、重命名、删除接口返回结构符合 spec
- [x] `GET /api/sessions/{id}/trace` 返回系统初始提示词、轮次列表与总用量
- [x] `GET /api/sessions/{id}/messages` 返回会话累计用量，无轮次记录时为 null
- [x] 历史无 `system` 消息时回退为当前引擎默认提示词
- [x] 轮次按真实用户输入切分（计划步骤指令不计为单独轮次），工具调用与结果按 `tool_call_id` 关联
- [x] 压缩事件在轨迹对应位置显示被覆盖的 seq 区间
- [x] 不存在的会话在消息、轨迹、重命名、删除接口上均返回 404

## 前端会话侧栏

- [x] 侧栏展示会话 id 与名称，当前会话高亮
- [x] 新建会话后自动选中并显示空对话欢迎态
- [x] 切换会话后中间区域加载该会话历史消息
- [x] 搜索框实时过滤，清空后恢复完整列表
- [x] 重命名与删除均有二次确认

## 前端对话视图

- [x] Enter 发送、Shift+Enter 换行
- [x] 执行中发送按钮切换为停止，点击可取消 run
- [x] 助手消息逐字流式渲染，代码块正确高亮
- [x] 状态条正确显示运行状态中文标签，本轮结束后隐藏
- [x] 工具调用卡片展示名称、参数与结果
- [x] 审批卡片展示工具名、参数摘要与三个动作，决策后从对话区移除
- [x] 计划模式展示第几步 / 总步数与步骤列表；ReAct 模式不渲染
- [x] 每轮展示耗时与 token 用量，消息区下方常驻展示会话累计用量且与状态分行
- [x] 会话累计用量展示千分位总 token 与缓存命中率，点击摘要展开输入 / 命中缓存 / 未命中缓存 / 输出明细
- [x] 无轮次记录时显示"未记录"，不虚构数值
- [x] 事件流断开时提示中断并提供重新加载入口

## 前端轨迹视图

- [x] 系统提示词可折叠展示，复制按钮可用
- [x] 轮次概览展示总轮数、总耗时、总 token
- [x] 逐轮区块可展开/收起，包含用户输入、助手回答、工具明细、耗时与 token
- [x] 压缩事件以分隔条插入

## 构建与文档

- [x] `npm run build` 产物位于 `web/dist`
- [x] 后端按 `server.static_dir` 挂载静态资源并提供 SPA 回退
- [x] 直接刷新前端子路由可正常渲染
- [x] 静态目录缺失时 `/api/*` 全部可用
- [x] `README.md` 补充后端、前端开发与生产构建的启动方式

## 回归

- [x] `python -m pytest` 全部通过
- [x] CLI 启动、斜杠会话命令、Skill 命令行为与改动前一致
- [x] 配置示例与加载逻辑、测试三者保持一致
