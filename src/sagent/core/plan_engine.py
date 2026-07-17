"""Plan 执行引擎。

先由 LLM 将复杂任务拆解为有序步骤，再逐步执行（每步复用 ReAct 引擎），最后汇总结果。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..config.models import AgentConfig
from ..context.context_manager import ContextManager
from ..llm.client import LLMClient
from ..observability import get_logger
from ..tools.registry import ToolRegistry
from .prompts import PLAN_DECOMPOSE_PROMPT, PLAN_SUMMARY_PROMPT, REACT_SYSTEM_PROMPT
from .react_engine import ReActEngine

logger = get_logger(__name__)


class PlanEngine:
    """Plan 执行引擎。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        agent_config: AgentConfig,
        on_event: Callable[[str], None] | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.config = agent_config
        self._on_event = on_event
        self._context_manager = context_manager
        # 每个步骤复用 ReAct 引擎执行，传入 context_manager 实现上下文持久化
        self._react = ReActEngine(llm, registry, agent_config, on_event, context_manager=context_manager)

    def _emit(self, text: str) -> None:
        if self._on_event:
            self._on_event(text)

    def run(self, task: str) -> str:
        """执行 Plan 模式任务：拆解 -> 分步执行 -> 汇总。"""
        # 注入了上下文管理器时，确保系统提示词在上下文中并添加用户原始任务
        if self._context_manager is not None:
            self._context_manager.ensure_system_prompt(REACT_SYSTEM_PROMPT)
            self._context_manager.add_message({"role": "user", "content": task})

        steps = self._decompose(task)
        if not steps:
            # 拆解失败时回退为直接用 ReAct 执行整个任务
            self._emit("[提示] 未能拆解出步骤，直接执行整个任务。")
            logger.warning("Plan 拆解失败，回退 ReAct", extra={"event": "plan_fallback"})
            return self._react.run(task)

        self._emit(f"[计划] 共拆解为 {len(steps)} 个步骤：")
        logger.info(
            "Plan 拆解完成",
            extra={"event": "plan_decomposed", "step_count": len(steps), "steps": steps},
        )
        for idx, step in enumerate(steps, start=1):
            self._emit(f"  {idx}. {step}")

        step_results: list[str] = []
        for idx, step in enumerate(steps, start=1):
            self._emit(f"\n[执行步骤 {idx}/{len(steps)}] {step}")
            logger.info(
                "Plan 执行步骤",
                extra={"event": "plan_step", "index": idx, "total": len(steps), "step": step},
            )
            # 为每个步骤提供原始任务作为背景
            step_task = (
                f"原始任务: {task}\n"
                f"当前需要完成的步骤: {step}\n"
                f"请完成该步骤并给出结果。"
            )
            result = self._react.run(step_task)
            step_results.append(f"步骤 {idx}（{step}）结果:\n{result}")

        logger.info("Plan 汇总结果", extra={"event": "plan_summarize"})
        summary = self._summarize(task, step_results)

        # 汇总完成后，将最终结果添加到上下文作为 assistant 消息
        if self._context_manager is not None:
            self._context_manager.add_message({"role": "assistant", "content": summary})

        return summary

    def _decompose(self, task: str) -> list[str]:
        """调用 LLM 将任务拆解为步骤列表。解析失败返回空列表。"""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PLAN_DECOMPOSE_PROMPT},
            {"role": "user", "content": task},
        ]
        response = self.llm.chat(messages, tools=None)
        content = (response.content or "").strip()
        return self._parse_steps(content)

    @staticmethod
    def _parse_steps(content: str) -> list[str]:
        """从 LLM 输出中解析步骤列表。

        兼容返回内容被 ```json 代码块包裹的情况。
        """
        text = content
        if text.startswith("```"):
            # 去掉代码块围栏
            text = text.strip("`")
            # 可能形如 json\n{...}
            newline = text.find("\n")
            if newline != -1 and text[:newline].strip().lower() in ("json", ""):
                text = text[newline + 1 :]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, list):
            return []
        return [str(s).strip() for s in steps if str(s).strip()]

    def _summarize(self, task: str, step_results: list[str]) -> str:
        """基于各步骤结果生成最终汇总答案。"""
        joined = "\n\n".join(step_results)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PLAN_SUMMARY_PROMPT},
            {
                "role": "user",
                "content": f"用户原始任务:\n{task}\n\n各步骤执行结果:\n{joined}",
            },
        ]
        response = self.llm.chat(messages, tools=None)
        return response.content or joined
