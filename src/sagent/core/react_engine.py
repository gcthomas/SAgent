"""ReAct 执行引擎。

实现「推理 -> 工具调用 -> 执行 -> 观察 -> 继续」的循环，直到 LLM 给出最终答案
或达到最大迭代次数。
"""

from __future__ import annotations

from typing import Any, Callable

from ..config.models import AgentConfig
from ..llm.client import LLMClient
from ..observability import get_logger
from ..tools.registry import ToolRegistry
from .prompts import REACT_SYSTEM_PROMPT

logger = get_logger(__name__)


class ReActEngine:
    """ReAct 执行引擎。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        agent_config: AgentConfig,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        """初始化引擎。

        参数:
            llm: LLM 客户端。
            registry: 工具注册表。
            agent_config: Agent 配置（含最大迭代次数）。
            on_event: 可选的过程回调，用于向 CLI 输出中间过程（思考/工具调用/观察）。
        """
        self.llm = llm
        self.registry = registry
        self.config = agent_config
        self._on_event = on_event

    def _emit(self, text: str) -> None:
        """输出过程事件。"""
        if self._on_event:
            self._on_event(text)

    def run(self, task: str, system_prompt: str | None = None) -> str:
        """执行一个任务并返回最终答案。

        参数:
            task: 用户任务/输入。
            system_prompt: 可选的自定义系统提示词，默认使用 ReAct 系统提示词。

        返回:
            最终答案文本。
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt or REACT_SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        tools = self.registry.to_openai_schemas()

        logger.info(
            "ReAct 开始执行",
            extra={"event": "react_start", "max_iterations": self.config.max_iterations},
        )

        for iteration in range(1, self.config.max_iterations + 1):
            logger.debug(
                "ReAct 迭代",
                extra={"event": "react_iteration", "iteration": iteration},
            )
            response = self.llm.chat(messages, tools=tools)

            # 没有工具调用，视为最终答案
            if not response.has_tool_calls:
                if response.content:
                    self._emit(f"[最终答案] {response.content}")
                logger.info(
                    "ReAct 得到最终答案",
                    extra={"event": "react_final", "iteration": iteration},
                )
                return response.content or "（模型未返回内容）"

            # 记录助手消息（包含 tool_calls），供后续工具结果对齐
            messages.append(self._assistant_message(response))

            # 依次执行工具调用并追加观察结果
            for call in response.tool_calls:
                self._emit(f"[行动] 调用工具 {call['name']}，参数: {call['arguments']}")
                observation = self.registry.execute(call["name"], call["arguments"])
                self._emit(f"[观察] {observation}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": observation,
                    }
                )

        # 达到最大迭代次数仍未结束，进行最后一次无工具的收尾请求
        self._emit(f"[提示] 已达到最大迭代次数 {self.config.max_iterations}，尝试给出当前结论。")
        logger.warning(
            "ReAct 达到最大迭代次数",
            extra={"event": "react_max_iterations", "max_iterations": self.config.max_iterations},
        )
        final = self.llm.chat(messages, tools=None)
        return (
            final.content
            or f"未能在 {self.config.max_iterations} 轮内完成任务，请尝试拆分任务或增大 max_iterations。"
        )

    @staticmethod
    def _assistant_message(response: Any) -> dict[str, Any]:
        """将 LLM 响应转换为可加入消息历史的 assistant 消息（含 tool_calls）。"""
        tool_calls = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in response.tool_calls
        ]
        return {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": tool_calls,
        }
