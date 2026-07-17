"""上下文管理相关提示词。"""

from __future__ import annotations

# LLM 摘要压缩系统提示词
# 使用 {max_tokens} 占位符，在 summarize() 调用时格式化为实际 token 上限
SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要助手。请把以下对话历史压缩成一段事实性摘要。\n"
    "要求：\n"
    "- 保留：用户目标、已确认的关键事实、已完成的动作与结果、未解决的约束。\n"
    "- 丢弃：寒暄、重复内容、已被推翻的中间结论、工具调用的原始长输出。\n"
    "- 使用简洁的中文，摘要不超过 {max_tokens} 个 token。\n"
    "- 输出纯文本摘要，不要输出 JSON 或其他格式。"
)
