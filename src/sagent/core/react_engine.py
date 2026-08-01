"""ReAct 执行引擎。

实现「推理 -> 工具调用 -> 执行 -> 观察 -> 继续」的循环，直到 LLM 给出最终答案
或达到最大迭代次数。
"""

from __future__ import annotations

from typing import Any, Callable

from ..config.models import AgentConfig
from ..context.context_manager import ContextManager
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
        context_manager: ContextManager | None = None,
    ) -> None:
        """初始化引擎。

        参数:
            llm: LLM 客户端。
            registry: 工具注册表。
            agent_config: Agent 配置（含最大迭代次数）。
            on_event: 可选的过程回调，用于向 CLI 输出中间过程（思考/工具调用/观察）。
            context_manager: 可选的上下文管理器，注入后实现跨轮次上下文持久化。
        """
        self.llm = llm
        self.registry = registry
        self.config = agent_config
        self._on_event = on_event
        self._context_manager = context_manager

    def _emit(self, text: str) -> None:
        """输出过程事件。"""
        if self._on_event:
            self._on_event(text)

    def run(self, task: str, memory_prefix: str | None = None) -> str:
        """执行一个任务并返回最终答案。

        参数:
            task: 用户任务/输入。
            memory_prefix: 可选的记忆注入前缀，叠加到 ReAct 系统提示词之前
                （而非替换）。会话开始时由调用方冻结构建一次，整个会话复用。

        返回:
            最终答案文本。
        """
        prompt = REACT_SYSTEM_PROMPT
        if memory_prefix:
            prompt = memory_prefix + "\n\n" + prompt
        tools = self.registry.to_openai_schemas()

        logger.info(
            "ReAct 开始执行",
            extra={"event": "react_start", "max_iterations": self.config.max_iterations},
        )

        # 注入了上下文管理器时，走跨轮次上下文持久化路径
        if self._context_manager is not None:
            return self._run_with_context(task, prompt, tools)

        # 无上下文管理器时，保持原有逻辑
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": task},
        ]

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

    def _run_with_context(self, task: str, prompt: str, tools: list[dict[str, Any]]) -> str:
        """使用上下文管理器执行任务，实现跨轮次上下文持久化。

        参数:
            task: 用户任务/输入。
            prompt: 系统提示词。
            tools: 工具 schema 列表。

        返回:
            最终答案文本。
        """
        cm = self._context_manager
        # 确保系统提示词在上下文中，并添加用户消息
        cm.ensure_system_prompt(prompt)
        cm.add_message({"role": "user", "content": task})

        for iteration in range(1, self.config.max_iterations + 1):
            logger.debug(
                "ReAct 迭代（上下文模式）",
                extra={"event": "react_iteration", "iteration": iteration},
            )
            messages = cm.get_messages()
            response = self.llm.chat(messages, tools=tools)
            cm.record_llm_usage(response.usage)

            # 没有工具调用，视为最终答案
            if not response.has_tool_calls:
                cm.add_message({"role": "assistant", "content": response.content})
                if response.content:
                    self._emit(f"[最终答案] {response.content}")
                logger.info(
                    "ReAct 得到最终答案",
                    extra={"event": "react_final", "iteration": iteration},
                )
                return response.content or "（模型未返回内容）"

            # 记录助手消息（包含 tool_calls）到上下文
            cm.add_message(self._assistant_message(response))

            # 依次执行工具调用并追加观察结果到上下文
            for call in response.tool_calls:
                self._emit(f"[行动] 调用工具 {call['name']}，参数: {call['arguments']}")
                observation = self.registry.execute(call["name"], call["arguments"])
                self._emit(f"[观察] {observation}")
                cm.add_message(
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
        messages = cm.get_messages()
        final = self.llm.chat(messages, tools=None)
        cm.record_llm_usage(final.usage)
        cm.add_message({"role": "assistant", "content": final.content})
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
