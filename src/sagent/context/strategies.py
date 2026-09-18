"""分层压缩策略实现。

依次提供四层压缩策略（低成本操作 → 语义保留 → 兜底裁剪），保证发送给 LLM 的消息长度在安全范围内。

四层策略依次为：
1. ToolOutputTruncation：截断过长的工具输出内容（信息无损，仅丢弃冗余长文本）
2. ToolMessageOffload：将已过期的工具调用与结果消息替换为简短摘要（轻度信息损失）
3. LLMSummaryCompression：调用 LLM 对旧消息生成摘要，用摘要消息替换旧消息（信息有损但语义保留）
4. SlidingWindowPruning：滑动窗口裁剪，仅保留系统消息与最近若干条消息（重度信息损失，兜底）
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from ..observability import get_logger
from .prompts import SUMMARY_SYSTEM_PROMPT
from .token_counter import count_text_tokens

logger = get_logger(__name__)

# 摘要输入中各角色消息的预览字符预算
_TEXT_PREVIEW_CHARS = 200  # user/assistant/system 普通文本
_TOOL_RESULT_PREVIEW_CHARS = 500  # tool 结果（承载事实输出，预算更大）


class CompressionStrategy(ABC):
    """压缩策略抽象基类。

    所有具体策略需实现 compress 方法，对消息列表执行压缩并返回新的列表。
    """

    @abstractmethod
    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对消息列表执行压缩，返回压缩后的新列表。

        参数:
            messages: OpenAI 格式的消息列表
        返回:
            压缩后的消息列表（新对象，不修改原列表）
        """
        ...


class ToolOutputTruncation(CompressionStrategy):
    """第一层：工具输出截断。

    遍历消息列表，对 role=="tool" 且 content 的 token 数超过 max_tokens 的消息，
    截断为「头部（约 40% max_tokens） + 尾部（约 40% max_tokens） + 截断标记」。
    """

    def __init__(self, max_tokens: int, model: str = "", method: str = "auto") -> None:
        """初始化工具输出截断策略。

        参数:
            max_tokens: 单条工具结果允许的最大 token 数
            model: 模型名称，用于 token 计数
            method: token 计数方式，auto/tiktoken/heuristic
        """
        self._max_tokens = max_tokens
        self._model = model
        self._method = method

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对消息列表中的工具结果消息执行截断。

        参数:
            messages: 消息列表
        返回:
            截断后的新消息列表
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") != "tool":
                result.append(dict(msg))
                continue
            content = msg.get("content") or ""
            if not content:
                result.append(dict(msg))
                continue
            token_count = count_text_tokens(content, self._model, self._method)
            if token_count <= self._max_tokens:
                result.append(dict(msg))
                continue
            truncated = self._truncate_content(content)
            new_msg = dict(msg)
            new_msg["content"] = truncated
            result.append(new_msg)
            logger.debug(
                "工具输出已截断",
                extra={
                    "event": "tool_output_truncated",
                    "original_tokens": token_count,
                    "original_chars": len(content),
                },
            )
        return result

    def _truncate_content(self, content: str) -> str:
        """截断文本内容为头部 + 尾部 + 截断标记。

        参数:
            content: 原始文本
        返回:
            截断后的文本
        """
        original_chars = len(content)
        # 头部与尾部各保留约 40% 的 token 预算
        head_budget = int(self._max_tokens * 0.4)
        tail_budget = int(self._max_tokens * 0.4)

        # 按字符比例估算截断位置（token 与字符近似线性）
        # 先取头部
        head = content[: self._approx_char_count(content, head_budget)]
        # 再取尾部
        tail_content = content[len(content) - self._approx_char_count_for_tail(content, tail_budget) :]
        kept_chars = len(head) + len(tail_content)
        marker = f"\n...[已截断，原始 {original_chars} 字符，当前保留约 {kept_chars} 字符]"
        truncated = head + marker + tail_content

        # 确保截断后未超过预算，若超出则进一步裁剪尾部
        while count_text_tokens(truncated, self._model, self._method) > self._max_tokens and len(tail_content) > 0:
            tail_content = tail_content[1:]
            kept_chars = len(head) + len(tail_content)
            marker = f"\n...[已截断，原始 {original_chars} 字符，当前保留约 {kept_chars} 字符]"
            truncated = head + marker + tail_content
        return truncated

    def _approx_char_count(self, text: str, token_budget: int) -> int:
        """根据 token 预算估算对应的字符数（用于头部截断）。

        参数:
            text: 原始文本
            token_budget: token 预算
        返回:
            估算的字符数
        """
        if not text:
            return 0
        # 统计中文字符与非中文字符比例，估算每 token 对应的字符数
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        non_cjk = len(text) - cjk
        # 每 token 约对应 (cjk/2 + non_cjk/4) / len(text) 个字符的倒数
        total_tokens = cjk // 2 + non_cjk // 4
        if total_tokens == 0:
            return len(text)
        ratio = len(text) / total_tokens
        return int(token_budget * ratio)

    def _approx_char_count_for_tail(self, text: str, token_budget: int) -> int:
        """根据 token 预算估算尾部应保留的字符数。

        参数:
            text: 原始文本
            token_budget: token 预算
        返回:
            估算的字符数
        """
        return self._approx_char_count(text, token_budget)


class ToolMessageOffload(CompressionStrategy):
    """第二层：工具消息卸载。

    识别已过期的工具消息对（assistant 含 tool_calls + 紧跟的 tool 结果），
    将过期对的 assistant 替换为简短摘要（移除 tool_calls），并删除对应的
    tool 结果消息（父消息 tool_calls 已移除，保留会形成孤儿 tool 消息）。
    """

    def __init__(self, keep_recent: int) -> None:
        """初始化工具消息卸载策略。

        参数:
            keep_recent: 保留最近多少条消息不卸载
        """
        self._keep_recent = keep_recent

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对消息列表执行工具消息卸载。

        参数:
            messages: 消息列表
        返回:
            卸载后的新消息列表
        """
        if len(messages) <= 1:
            return [dict(m) for m in messages]

        result: list[dict[str, Any]] = []
        # 第一条 system 消息始终跳过（原样保留）
        idx = 0
        if messages[0].get("role") == "system":
            result.append(dict(messages[0]))
            idx = 1

        # 计算保护范围：最后 keep_recent 条消息不卸载
        total = len(messages)
        protected_from = total - self._keep_recent if total > self._keep_recent else 0

        # 遍历剩余消息，识别过期工具消息对
        while idx < total:
            msg = messages[idx]
            role = msg.get("role", "")
            # assistant 消息含 tool_calls，且不在保护范围内
            if role == "assistant" and msg.get("tool_calls") and idx < protected_from:
                # 收集紧随其后的连续 tool 结果消息
                tool_call_ids = set()
                tool_names: list[str] = []
                arg_summaries: list[str] = []
                for tc in msg.get("tool_calls", []):
                    if isinstance(tc, dict):
                        func = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                        name = func.get("name", "") or tc.get("name", "") or ""
                        args_str = func.get("arguments", "") or tc.get("arguments", "") or ""
                        tool_names.append(name)
                        arg_summaries.append(self._summarize_args(args_str))
                        call_id = tc.get("id", "")
                        if call_id:
                            tool_call_ids.add(call_id)

                # 收集紧跟的 tool 结果消息
                tool_results: list[dict[str, Any]] = []
                j = idx + 1
                while j < total and messages[j].get("role") == "tool":
                    tool_results.append(messages[j])
                    j += 1

                # 构造卸载摘要
                tool_name_str = ", ".join(tool_names) if tool_names else "unknown"
                arg_str = "; ".join(arg_summaries) if arg_summaries else ""
                total_result_chars = sum(len(r.get("content") or "") for r in tool_results)

                offloaded_assistant = dict(msg)
                offloaded_assistant["content"] = (
                    f"[已压缩: {tool_name_str}({arg_str}), 结果 {total_result_chars} 字符]"
                )
                offloaded_assistant.pop("tool_calls", None)
                result.append(offloaded_assistant)

                # 不保留对应的 tool 结果消息：父消息的 tool_calls 已被移除，
                # 保留 role="tool" 的消息会形成孤儿 tool 消息，导致严格端点返回 400。
                # 工具名/参数摘要与结果字符数已包含在上方 assistant 占位文本中。
                idx = j
                logger.debug(
                    "工具消息对已卸载",
                    extra={
                        "event": "tool_message_offloaded",
                        "tool_names": tool_names,
                        "result_chars": total_result_chars,
                    },
                )
            else:
                result.append(dict(msg))
                idx += 1

        return result

    def _summarize_args(self, args_str: str) -> str:
        """将工具参数 JSON 字符串摘要为简短文本。

        参数:
            args_str: 工具参数的 JSON 字符串
        返回:
            简短的参数摘要
        """
        if not args_str:
            return ""
        try:
            args = json.loads(args_str)
            if isinstance(args, dict):
                parts: list[str] = []
                for k, v in args.items():
                    val_str = str(v)
                    if len(val_str) > 30:
                        val_str = val_str[:30] + "..."
                    parts.append(f"{k}={val_str}")
                return ", ".join(parts)
            return str(args)[:50]
        except (json.JSONDecodeError, TypeError):
            return args_str[:50]


class SlidingWindowPruning(CompressionStrategy):
    """第四层：滑动窗口裁剪。

    保留第一条 system 消息与最后 keep_recent 条消息，丢弃中间所有消息。
    裁剪边界会自动对齐到 tool_call/tool_result pair 的安全边界，
    避免产生孤儿 tool 结果消息（缺少对应 assistant(tool_calls) 父消息）。
    """

    def __init__(self, keep_recent: int) -> None:
        """初始化滑动窗口裁剪策略。

        参数:
            keep_recent: 保留最近多少条消息
        """
        self._keep_recent = keep_recent

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """执行滑动窗口裁剪。

        参数:
            messages: 消息列表
        返回:
            裁剪后的新消息列表
        """
        if len(messages) <= self._keep_recent + 1:
            return [dict(m) for m in messages]

        # 提取开头连续的 system 消息
        system_msgs: list[dict[str, Any]] = []
        body = list(messages)
        while body and body[0].get("role") == "system":
            system_msgs.append(dict(body.pop(0)))

        # 保留最后 keep_recent 条
        cut = max(0, len(body) - self._keep_recent)

        # 对齐裁剪边界：若切点落在 tool 结果消息上，向前扩展以包含其
        # 父 assistant(tool_calls) 消息，避免孤儿 tool 消息导致 API 报错
        while cut > 0 and body[cut].get("role") == "tool":
            cut -= 1

        recent = [dict(m) for m in body[cut:]]
        result = system_msgs + recent

        logger.debug(
            "滑动窗口裁剪完成",
            extra={
                "event": "sliding_window_pruned",
                "original_count": len(messages),
                "pruned_count": len(result),
            },
        )
        return result


class LLMSummaryCompression(CompressionStrategy):
    """第三层：LLM 摘要压缩。

    调用 LLM 对旧消息生成摘要，用摘要 system 消息替换旧消息。
    相比纯裁剪，摘要保留了语义信息。
    """

    def __init__(
        self,
        llm: Any,
        summary_max_tokens: int,
        model: str = "",
        keep_recent: int = 20,
    ) -> None:
        """初始化 LLM 摘要压缩策略。

        参数:
            llm: LLM 客户端，需有 chat(messages, tools=None) 方法
            summary_max_tokens: 摘要最大 token 数（用于提示词约束）
            model: 模型名称
            keep_recent: 保留最近多少条消息不参与摘要
        """
        self._llm = llm
        self._summary_max_tokens = summary_max_tokens
        self._model = model
        self._keep_recent = keep_recent

    def summarize(self, old_messages: list[dict[str, Any]], existing_summary: str | None = None) -> str:
        """调用 LLM 对旧消息生成摘要。

        参数:
            old_messages: 需要摘要的旧消息列表
            existing_summary: 已有的历史摘要，若有则一并发给 LLM 做增量摘要
        返回:
            LLM 生成的摘要文本
        """
        # 将旧消息格式化为文本，每条一行
        lines: list[str] = []
        if existing_summary:
            lines.append(f"[已有摘要] {existing_summary}")
        for msg in old_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            # 角色差异化预览预算：tool 结果承载事实输出，保留更长预览
            budget = _TOOL_RESULT_PREVIEW_CHARS if role == "tool" else _TEXT_PREVIEW_CHARS
            preview = content[:budget]
            if len(content) > budget:
                preview += "..."
            lines.append(f"[{role}] {preview}")
            # assistant 的 tool_calls 是关键执行记录（工具名/参数），需呈现给摘要 LLM；
            # 兼容嵌套（OpenAI 标准）与扁平（conftest.make_tool_call）两种格式，
            # 与 ToolMessageOffload 的解析方式保持一致
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    name = func.get("name", "") or tc.get("name", "") or ""
                    args_str = func.get("arguments", "") or tc.get("arguments", "") or ""
                    if not name:
                        continue
                    args_preview = args_str[:_TEXT_PREVIEW_CHARS]
                    if len(args_str) > _TEXT_PREVIEW_CHARS:
                        args_preview += "..."
                    lines.append(f"[assistant 工具调用] {name}({args_preview})")
        formatted_text = "\n".join(lines)

        # 构造 LLM 请求消息，将 summary_max_tokens 注入提示词约束摘要长度
        system_prompt = SUMMARY_SYSTEM_PROMPT.format(max_tokens=self._summary_max_tokens)
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": formatted_text},
        ]

        logger.info(
            "发起摘要压缩 LLM 请求",
            extra={
                "event": "summary_llm_request",
                "old_message_count": len(old_messages),
                "has_existing_summary": existing_summary is not None,
                "summary_max_tokens": self._summary_max_tokens,
            },
        )

        response = self._llm.chat(llm_messages, tools=None)
        summary = response.content or ""
        # 硬校验摘要长度：LLM 不遵守提示词约束时按比例截断
        summary = self._enforce_summary_limit(summary)
        logger.info(
            "摘要压缩 LLM 响应已接收",
            extra={
                "event": "summary_llm_response",
                "summary_length": len(summary),
            },
        )
        return summary

    def _enforce_summary_limit(self, summary: str) -> str:
        """硬校验摘要长度：超过 summary_max_tokens 时按比例截断。

        参数:
            summary: LLM 生成的摘要文本
        返回:
            满足 token 上限的摘要文本（截断时追加标记）
        """
        if not summary:
            return summary
        marker = "\n...[摘要已截断至 token 上限]"
        tokens = count_text_tokens(summary + marker, self._model)
        if tokens <= self._summary_max_tokens:
            return summary
        logger.warning(
            "摘要超过 token 上限，已截断",
            extra={
                "event": "summary_truncated",
                "summary_max_tokens": self._summary_max_tokens,
                "original_tokens": tokens,
            },
        )
        # 按 token 占比估算字符保留量，每轮预留 10% 余量，循环收缩直至满足上限
        while tokens > self._summary_max_tokens and summary:
            keep_chars = max(1, int(len(summary) * self._summary_max_tokens / tokens * 0.9))
            summary = summary[:keep_chars]
            tokens = count_text_tokens(summary + marker, self._model)
            if keep_chars == 1:
                break
        return summary + marker

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对消息列表执行 LLM 摘要压缩。

        分离系统消息与正文，将正文中超出 keep_recent 的旧消息替换为摘要 system 消息。

        参数:
            messages: 消息列表
        返回:
            压缩后的新消息列表
        """
        # 提取开头连续的 system 消息
        system_msgs: list[dict[str, Any]] = []
        body = list(messages)
        while body and body[0].get("role") == "system":
            system_msgs.append(body.pop(0))

        # 分离旧消息与近期消息
        if len(body) <= self._keep_recent:
            return [dict(m) for m in messages]

        # 切分边界对齐：若切点落在 tool 结果消息上，向前扩展以包含其
        # 父 assistant(tool_calls) 消息，避免摘要后残留孤儿 tool 消息
        cut = max(0, len(body) - self._keep_recent)
        while cut > 0 and body[cut].get("role") == "tool":
            cut -= 1

        recent = body[cut:]
        old = body[:cut]

        if not old:
            return [dict(m) for m in messages]

        # 调用 LLM 生成摘要
        summary = self.summarize(old, existing_summary=None)
        summary_msg = {"role": "system", "content": f"[历史摘要] {summary}"}

        result = [dict(m) for m in system_msgs] + [summary_msg] + [dict(m) for m in recent]
        return result
