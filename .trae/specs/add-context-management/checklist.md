# Checklist

## Token 估算
- [x] token_counter.py 实现了 count_tokens(messages, model, method) -> int 接口
- [x] 支持 auto/tiktoken/heuristic 三种模式，由 token_counter_method 配置控制
- [x] auto 模式：tiktoken 可用时精确计数，不可用时回退字符启发式
- [x] heuristic 模式：始终字符启发式估算，不依赖 tiktoken
- [x] tiktoken 强制模式不可用时回退启发式并记录 WARNING 日志
- [x] tiktoken 已添加到 requirements.txt（标注为可选依赖）
- [x] 混合校准：record_llm_usage 记录 API 返回的 prompt_tokens 作为精确基准，token_count 返回基准 + delta
- [x] use_api_calibration 配置控制是否启用混合校准，默认 True
- [x] 压缩触发时自动重置校准基准
- [x] API 不返回 usage 时自动回退全量估算

## 配置模型
- [x] ContextConfig 包含字段：max_context_tokens、compression_threshold、safe_threshold、keep_recent_messages、max_tool_output_tokens、token_counter_method、use_api_calibration、enable_summary、summary_max_tokens
- [x] AppConfig 添加 context 字段，带默认工厂（配置缺省时使用默认值）
- [x] config.example.yaml 包含 context 配置段示例

## 压缩策略
- [x] 第一层 ToolOutputTruncation：工具结果超 max_tool_output_tokens 时截断为头部+尾部+截断标记
- [x] 第二层 ToolMessageOffload：旧工具调用/结果对替换为简短引用摘要
- [x] 第三层 LLMSummaryCompression：调用 LLM 生成结构化摘要替换旧消息，支持层级摘要，先于裁剪执行以保留语义
- [x] summary_max_tokens 实际注入摘要提示词约束 LLM 输出长度（非仅存储不使用）
- [x] 第四层 SlidingWindowPruning：保留最近 keep_recent_messages 条，丢弃更早消息（系统提示词除外），作为兜底
- [x] 第四层裁剪边界对齐：切点落在 tool 结果消息上时向前扩展以包含父 assistant(tool_calls)，避免孤儿 tool 消息
- [x] 摘要提示词定义在 context/prompts.py 中
- [x] 各策略依次执行（低成本操作 → 语义保留 → 兜底裁剪），token 降至安全线以下即停止

## ContextManager
- [x] add_message(msg) 方法正常添加消息（含第一层内联截断）
- [x] get_messages() 方法返回当前消息列表，超阈值时自动压缩
- [x] 系统提示词（role=system 的首条消息）始终保留不被压缩或丢弃
- [x] 压缩后 token 降至 safe_threshold（安全线）以下或用尽所有策略

## 引擎集成
- [x] ReActEngine 支持可选 context_manager 参数，默认 None
- [x] 有 context_manager 时用其管理消息（add/get），无时保持原有行为
- [x] 每次 LLM 调用后调用 cm.record_llm_usage(response.usage) 记录精确 token 数
- [x] PlanEngine 支持可选 context_manager 并传递给内部 ReActEngine
- [x] 无 context_manager 时两个引擎行为与改造前完全一致（向后兼容）

## CLI 多轮对话
- [x] CLI 交互循环中创建 ContextManager 并跨轮次复用
- [x] 每轮用户输入追加到 context_manager，engine.run 返回后助手回复也追加
- [x] 长对话累积时自动触发压缩

## 测试
- [x] tests/unit/test_token_counter.py 覆盖 auto/tiktoken/heuristic 三种模式的计数逻辑与回退行为
- [x] tests/unit/test_strategies.py 覆盖四层压缩策略各自逻辑
- [x] tests/unit/test_context_manager.py 覆盖 ContextManager 整体流程
- [x] tests/engines/test_context_integration.py 覆盖引擎注入后的工具调用与压缩
- [x] 无 context_manager 时引擎行为不变测试通过
- [x] 多轮对话场景测试通过
- [x] python -m pytest 全部用例通过
