"""上下文管理器。

维护消息历史，在 token 超阈值时自动执行分层压缩，保证发送给 LLM 的消息长度在安全范围内。

压缩依次应用四层策略（低成本操作 → 语义保留 → 兜底裁剪）：
1. 工具输出截断（add_message 时内联执行，_compress 时全量扫描补充）
2. 工具消息卸载（替换过期工具消息对为简短摘要）
3. LLM 摘要压缩（保留语义信息，先于裁剪执行）
4. 滑动窗口裁剪（兜底，丢弃旧消息）

每层执行后检查是否已降至安全线以下，达到则停止。
"""

from __future__ import annotations

from typing import Any

from ..config.models import ContextConfig
from ..observability import get_logger
from .strategies import (
    LLMSummaryCompression,
    SlidingWindowPruning,
    ToolMessageOffload,
    ToolOutputTruncation,
)
from .token_counter import count_tokens

logger = get_logger(__name__)


class ContextManager:
    """上下文管理器，维护消息历史并自动压缩。"""

    def __init__(
        self,
        config: ContextConfig,
        llm: Any | None = None,
        model: str = "",
    ) -> None:
        """初始化上下文管理器。

        参数:
            config: 上下文管理配置
            llm: LLM 客户端，用于第三层摘要压缩；enable_summary 为 False 时可为 None
            model: 模型名称，用于 token 计数
        """
        self._config = config
        self._llm = llm
        self._model = model
        self._messages: list[dict[str, Any]] = []
        self._existing_summary: str | None = None  # 已有的历史摘要

        # 混合校准：记录 LLM API 返回的精确 prompt_tokens 及基准时刻的消息数
        self._calibrated_tokens: int | None = None
        self._calibrated_count: int = 0

        # 初始化策略实例
        self._truncation = ToolOutputTruncation(
            config.max_tool_output_tokens, model, config.token_counter_method
        )
        self._offload = ToolMessageOffload(config.keep_recent_messages)
        self._pruning = SlidingWindowPruning(config.keep_recent_messages)
        if config.enable_summary and llm is not None:
            self._summarizer = LLMSummaryCompression(
                llm, config.summary_max_tokens, model, config.keep_recent_messages
            )
        else:
            self._summarizer = None

    def add_message(self, message: dict[str, Any]) -> None:
        """添加一条消息到历史。对 tool 结果消息立即应用第一层内联截断。

        参数:
            message: OpenAI 格式的消息字典
        """
        # 如果是 tool 消息，应用第一层截断
        if message.get("role") == "tool":
            truncated = self._truncation.compress([message])
            if truncated:
                message = truncated[0]
        self._messages.append(message)

    def get_messages(self) -> list[dict[str, Any]]:
        """获取当前消息列表。如果 token 超阈值，自动触发压缩后返回。

        返回:
            消息列表的副本
        """
        if self._is_over_threshold():
            self._compress()
        return list(self._messages)

    def record_llm_usage(self, usage: dict[str, int] | None) -> None:
        """从 LLM API 响应中记录实际的 prompt token 用量，用于混合校准。

        启用 use_api_calibration 后，每次 LLM 调用返回的 prompt_tokens 作为精确基准，
        后续新增消息仅估算 delta，总量 = 精确基准 + 估算 delta。

        参数:
            usage: LLM API 返回的 usage dict，含 prompt_tokens 等字段；None 时跳过
        """
        if not self._config.use_api_calibration or usage is None:
            return
        prompt_tokens = usage.get("prompt_tokens")
        if prompt_tokens is not None:
            self._calibrated_tokens = prompt_tokens
            self._calibrated_count = len(self._messages)
            logger.info(
                "混合校准基准已记录",
                extra={
                    "event": "calibration_recorded",
                    "calibrated_tokens": prompt_tokens,
                    "calibrated_count": self._calibrated_count,
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
            )

    @property
    def token_count(self) -> int:
        """当前消息列表的 token 估算数。

        启用混合校准且有 API 数据时，返回「精确基准 + 新增消息估算 delta」；
        否则回退到对全量消息的估算。
        """
        if self._calibrated_tokens is not None:
            new_msgs = self._messages[self._calibrated_count:]
            if new_msgs:
                delta = count_tokens(new_msgs, self._model, self._config.token_counter_method)
                total = self._calibrated_tokens + delta
                logger.debug(
                    "混合校准 token 计数（基准 + delta）",
                    extra={
                        "event": "calibration_delta",
                        "calibrated_base": self._calibrated_tokens,
                        "calibrated_count": self._calibrated_count,
                        "new_msg_count": len(new_msgs),
                        "delta_tokens": delta,
                        "total_tokens": total,
                    },
                )
                return total
            logger.debug(
                "混合校准 token 计数（无新增消息，直接使用基准）",
                extra={
                    "event": "calibration_base_only",
                    "calibrated_base": self._calibrated_tokens,
                    "calibrated_count": self._calibrated_count,
                    "total_tokens": self._calibrated_tokens,
                },
            )
            return self._calibrated_tokens
        return count_tokens(self._messages, self._model, self._config.token_counter_method)

    def ensure_system_prompt(self, prompt: str) -> None:
        """确保系统提示词是消息列表的第一条。如果不存在则插入到开头。

        参数:
            prompt: 系统提示词文本
        """
        if not self._messages or self._messages[0].get("role") != "system":
            self._messages.insert(0, {"role": "system", "content": prompt})

    def _is_over_threshold(self) -> bool:
        """判断当前 token 是否超过压缩触发阈值。"""
        threshold = int(self._config.max_context_tokens * self._config.compression_threshold)
        return self.token_count > threshold

    def _is_below_safe(self) -> bool:
        """判断当前 token 是否已降至安全线以下。"""
        safe = int(self._config.max_context_tokens * self._config.safe_threshold)
        return self.token_count <= safe

    def _compress(self) -> None:
        """执行分层压缩，依次应用策略（低成本操作 → 语义保留 → 兜底裁剪），直到 token 降至安全线以下。"""
        # 压缩会改变消息列表，重置混合校准基准（下次 LLM 调用后重新校准）
        if self._calibrated_tokens is not None:
            logger.info(
                "压缩触发，混合校准基准重置",
                extra={
                    "event": "calibration_reset",
                    "previous_base": self._calibrated_tokens,
                    "previous_count": self._calibrated_count,
                },
            )
        self._calibrated_tokens = None
        self._calibrated_count = 0

        logger.info(
            "触发上下文压缩",
            extra={"event": "context_compress_start", "before_tokens": self.token_count},
        )

        # 第一层：工具输出截断（add_message 时已内联执行，这里再做一次全量扫描确保覆盖）
        self._messages = self._truncation.compress(self._messages)
        if self._is_below_safe():
            logger.info(
                "压缩完成（第一层）",
                extra={"event": "context_compress_done", "layer": 1, "after_tokens": self.token_count},
            )
            return

        # 第二层：大体积工具消息卸载
        self._messages = self._offload.compress(self._messages)
        if self._is_below_safe():
            logger.info(
                "压缩完成（第二层）",
                extra={"event": "context_compress_done", "layer": 2, "after_tokens": self.token_count},
            )
            return

        # 分离旧消息与近期消息
        system_msgs, body = self._split_system_and_body()
        keep_count = self._config.keep_recent_messages
        recent = body[-keep_count:] if len(body) > keep_count else body
        old = body[:-keep_count] if len(body) > keep_count else []

        if old and self._summarizer is not None:
            # 第三层：LLM 摘要压缩（先于裁剪执行，保留语义信息）
            try:
                summary = self._summarizer.summarize(old, self._existing_summary)
                self._existing_summary = summary
                summary_msg = {"role": "system", "content": f"[历史摘要] {summary}"}
                self._messages = system_msgs + [summary_msg] + recent
                logger.info(
                    "压缩完成（第三层摘要）",
                    extra={"event": "context_compress_done", "layer": 3, "after_tokens": self.token_count},
                )
                if self._is_below_safe():
                    return
            except Exception:
                logger.exception(
                    "LLM 摘要压缩失败",
                    extra={"event": "context_summary_error"},
                )

        # 第四层：滑动窗口裁剪（兜底）
        self._messages = self._pruning.compress(self._messages)
        logger.info(
            "压缩完成（第四层）",
            extra={"event": "context_compress_done", "layer": 4, "after_tokens": self.token_count},
        )

    def _split_system_and_body(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """将消息列表分离为系统消息（开头连续的 system 消息）和其余消息。

        返回:
            (system_msgs, body): system_msgs 为开头连续的系统消息列表，body 为其余消息列表
        """
        system_msgs: list[dict[str, Any]] = []
        body = list(self._messages)
        while body and body[0].get("role") == "system":
            system_msgs.append(body.pop(0))
        return system_msgs, body
