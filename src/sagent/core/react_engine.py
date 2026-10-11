"""ReAct 执行引擎。

实现「推理 -> 工具调用 -> 执行 -> 观察 -> 继续」的循环，直到 LLM 给出最终答案
或达到最大迭代次数。
"""

from __future__ import annotations

import json
from typing import Any

from ..config.models import AgentConfig
from ..context.context_manager import ContextManager
from ..llm.client import LLMClient, LLMStreamEvent
from .events import AgentEvent, EventHandler
from ..observability import Span, get_logger
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
        on_event: EventHandler | None = None,
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
        self._outcome = "completed"

    def _emit(self, event: AgentEvent) -> None:
        """输出结构化过程事件。"""
        if self._on_event:
            self._on_event(event)

    def _on_llm_event(self, event: LLMStreamEvent) -> None:
        """将 LLM 流式事件转换为 Agent 事件。"""
        if event.event_type == "text_delta":
            self._emit(AgentEvent(event_type="llm_text_delta", content=event.content))
        elif event.event_type == "completed":
            self._emit(
                AgentEvent(
                    event_type="llm_completed",
                    content=event.response.content if event.response else None,
                )
            )

    def _chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        emit_stream: bool = True,
    ) -> Any:
        """发起 LLM 请求，流式配置下由客户端发送增量事件。"""
        callback = self._on_llm_event if emit_stream else None
        return self.llm.chat(messages, tools=tools, on_event=callback)

    def run(
        self,
        task: str,
        memory_prefix: str | None = None,
        plan_projection: str | None = None,
        persist_task: bool = True,
    ) -> str:
        """执行一个任务并返回最终答案。

        参数:
            task: 用户任务/输入。
            memory_prefix: 可选的记忆注入前缀，叠加到 ReAct 系统提示词之前
                （而非替换）。会话开始时由调用方冻结构建一次，整个会话复用。
            plan_projection: 可选的计划状态投影，仅作为本次执行中迭代请求的
                临时 system 消息，不写入上下文历史，也不持久化计划快照。
            persist_task: 是否将 task 作为 user 消息加入上下文管理器，默认 True。
                False 用于避免 Plan 子步骤提示写入历史，不影响助手消息和工具
                结果的记录；无上下文管理器时仍将 task 用于请求，此参数不生效。

        返回:
            最终答案文本。
        """
        prompt = REACT_SYSTEM_PROMPT
        if memory_prefix:
            prompt = memory_prefix + "\n\n" + prompt
        tools = self.registry.to_openai_schemas()

        with Span("agent.react") as span:
            span.set_attribute("sagent.max_iterations", self.config.max_iterations)
            logger.info(
                "ReAct 开始执行",
                extra={"event": "react_start", "max_iterations": self.config.max_iterations},
            )

            # 注入了上下文管理器时，走跨轮次上下文持久化路径
            if self._context_manager is not None:
                return self._run_with_context(task, prompt, tools, plan_projection, persist_task)

            # 无上下文管理器时，保持原有逻辑
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": task},
            ]
            if plan_projection:
                messages.append({"role": "system", "content": plan_projection})

            for iteration in range(1, self.config.max_iterations + 1):
                span.add_event("iteration", {"index": iteration})
                logger.debug(
                    "ReAct 迭代",
                    extra={"event": "react_iteration", "iteration": iteration},
                )
                response = self._chat(messages, tools=tools)

                # 没有工具调用，视为最终答案
                if not response.has_tool_calls:
                    if response.content:
                        self._emit(
                            AgentEvent(
                                event_type="react_completed",
                                content=response.content,
                            )
                        )
                    logger.info(
                        "ReAct 得到最终答案",
                        extra={"event": "react_final", "iteration": iteration},
                    )
                    return response.content or "（模型未返回内容）"

                # 记录助手消息（包含 tool_calls），供后续工具结果对齐
                messages.append(self._assistant_message(response))

                # 依次执行工具调用并追加观察结果
                for call in response.tool_calls:
                    self._emit(
                        AgentEvent(
                            event_type="tool_call",
                            iteration=iteration,
                            tool_name=call["name"],
                            tool_arguments=self._parse_tool_arguments(call["arguments"]),
                            tool_call_id=call["id"],
                        )
                    )
                    observation = self.registry.execute(call["name"], call["arguments"])
                    self._emit(
                        AgentEvent(
                            event_type="tool_result",
                            content=observation,
                            iteration=iteration,
                            tool_name=call["name"],
                            tool_call_id=call["id"],
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": observation,
                        }
                    )

            # 达到最大迭代次数仍未结束，进行最后一次无工具的收尾请求
            span.set_attribute("sagent.outcome", "max_iterations")
            self._outcome = "max_iterations"
            with Span("agent.finalize") as finalize_span:
                finalize_span.set_attribute("sagent.max_iterations", self.config.max_iterations)
                self._emit(
                    AgentEvent(
                        event_type="notice",
                        content=f"已达到最大迭代次数 {self.config.max_iterations}，尝试给出当前结论。",
                    )
                )
                logger.warning(
                    "ReAct 达到最大迭代次数",
                    extra={"event": "react_max_iterations", "max_iterations": self.config.max_iterations},
                )
                final = self._chat(messages, tools=None)
            return (
                final.content
                or f"未能在 {self.config.max_iterations} 轮内完成任务，请尝试拆分任务或增大 max_iterations。"
            )

    def _run_with_context(
        self,
        task: str,
        prompt: str,
        tools: list[dict[str, Any]],
        plan_projection: str | None = None,
        persist_task: bool = True,
    ) -> str:
        """使用上下文管理器执行任务，实现跨轮次上下文持久化。

        参数:
            task: 用户任务/输入。
            prompt: 系统提示词。
            tools: 工具 schema 列表。
            plan_projection: 可选的计划状态投影，仅追加到迭代请求的消息副本，
                不调用 add_message 写回历史；达到迭代上限的收尾请求不附加投影。
            persist_task: 是否将 task 作为 user 消息加入上下文历史。False 仅跳过
                该输入的记录，助手消息、工具调用及结果仍照常加入上下文。

        持久化边界:
            本方法只维护 ContextManager 的消息历史；会话落盘由调用方另行保存，
            结构化计划快照由会话管理器保存，不通过计划投影写入消息历史。

        返回:
            最终答案文本。
        """
        cm = self._context_manager
        # 确保系统提示词在上下文中，并添加用户消息
        cm.ensure_system_prompt(prompt)
        if persist_task:
            cm.add_message({"role": "user", "content": task})

        with Span("agent.react") as span:
            span.set_attribute("sagent.max_iterations", self.config.max_iterations)

            for iteration in range(1, self.config.max_iterations + 1):
                span.add_event("iteration", {"index": iteration})
                logger.debug(
                    "ReAct 迭代（上下文模式）",
                    extra={"event": "react_iteration", "iteration": iteration},
                )
                messages = cm.get_messages()
                if plan_projection:
                    messages = [
                        *messages,
                        {"role": "system", "content": plan_projection},
                    ]
                response = self._chat(messages, tools=tools)
                cm.record_llm_usage(response.usage)

                # 没有工具调用，视为最终答案
                if not response.has_tool_calls:
                    cm.add_message({"role": "assistant", "content": response.content})
                    if response.content:
                        self._emit(
                            AgentEvent(
                                event_type="react_completed",
                                content=response.content,
                            )
                        )
                    logger.info(
                        "ReAct 得到最终答案",
                        extra={"event": "react_final", "iteration": iteration},
                    )
                    return response.content or "（模型未返回内容）"

                # 记录助手消息（包含 tool_calls）到上下文
                cm.add_message(self._assistant_message(response))

                # 依次执行工具调用并追加观察结果到上下文
                for call in response.tool_calls:
                    self._emit(
                        AgentEvent(
                            event_type="tool_call",
                            tool_name=call["name"],
                            tool_arguments=self._parse_tool_arguments(call["arguments"]),
                            tool_call_id=call["id"],
                        )
                    )
                    observation = self.registry.execute(call["name"], call["arguments"])
                    self._emit(
                        AgentEvent(
                            event_type="tool_result",
                            content=observation,
                            tool_name=call["name"],
                            tool_call_id=call["id"],
                        )
                    )
                    cm.add_message(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": observation,
                        }
                    )

            # 达到最大迭代次数仍未结束，进行最后一次无工具的收尾请求
            span.set_attribute("sagent.outcome", "max_iterations")
            self._outcome = "max_iterations"
            with Span("agent.finalize") as finalize_span:
                finalize_span.set_attribute("sagent.max_iterations", self.config.max_iterations)
                self._emit(
                    AgentEvent(
                        event_type="notice",
                        content=f"已达到最大迭代次数 {self.config.max_iterations}，尝试给出当前结论。",
                    )
                )
                logger.warning(
                    "ReAct 达到最大迭代次数",
                    extra={"event": "react_max_iterations", "max_iterations": self.config.max_iterations},
                )
                messages = cm.get_messages()
                final = self._chat(messages, tools=None)
                cm.record_llm_usage(final.usage)
                cm.add_message({"role": "assistant", "content": final.content})
            return (
                final.content
                or f"未能在 {self.config.max_iterations} 轮内完成任务，请尝试拆分任务或增大 max_iterations。"
            )

    @staticmethod
    def _parse_tool_arguments(arguments: Any) -> Any:
        """将工具参数解析为结构化值。"""
        if not isinstance(arguments, str):
            return arguments
        try:
            return json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return arguments

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
