"""统一 Agent 编排入口。"""

from __future__ import annotations

import json
from typing import Any

from ..config.models import AgentConfig, PlanningConfig
from ..context.context_manager import ContextManager
from ..llm.client import LLMClient
from ..tools.registry import ToolRegistry
from .events import AgentEvent, EventHandler
from .plan_engine import PlanEngine
from .prompts import PLAN_DECISION_PROMPT
from .react_engine import ReActEngine


class AgentEngine(ReActEngine):
    """按计划策略编排 ReAct 与 Plan 子流程。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        agent_config: AgentConfig,
        planning_config: PlanningConfig | None = None,
        on_event: EventHandler | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        super().__init__(llm, registry, agent_config, on_event, context_manager)
        self.planning_config = planning_config or PlanningConfig()
        self._plan = PlanEngine(
            llm,
            registry,
            agent_config,
            on_event=on_event,
            context_manager=context_manager,
            planning_config=self.planning_config,
        )
        self._session_manager = None
        # 记录本轮实际执行路径（react/plan），供可观测性指标使用
        self.last_mode: str = "react"

    def run(self, task: str, memory_prefix: str | None = None) -> str:
        """按配置选择普通 ReAct 或计划子流程。"""
        strategy = self.planning_config.strategy
        self._emit(AgentEvent(event_type="plan_decision", content=f"计划策略: {strategy}"))
        if strategy == "never":
            self.last_mode = "react"
            if self._session_manager is not None:
                self._session_manager.set_active_plan(None)
            return super().run(task, memory_prefix=memory_prefix)
        if strategy == "always":
            self.last_mode = "plan"
            return self._run_plan(task, memory_prefix)
        if self._needs_plan(task):
            self.last_mode = "plan"
            return self._run_plan(task, memory_prefix)
        self.last_mode = "react"
        if self._session_manager is not None:
            self._session_manager.set_active_plan(None)
        return super().run(task, memory_prefix=memory_prefix)

    def _run_plan(self, task: str, memory_prefix: str | None) -> str:
        """运行受控计划流程，并清理已完成的活动状态。"""
        try:
            if self._session_manager is not None:
                self._plan._on_plan_state_change = self._session_manager.set_active_plan
                restored = self._session_manager.get_active_plan()
                if restored is not None:
                    self._plan.plan_state = restored
            result = self._plan.run(task, memory_prefix=memory_prefix)
            status = self._plan.plan_state.status if self._plan.plan_state is not None else "completed"
            message = {
                "completed": "计划执行完成",
                "blocked": "计划执行受阻",
                "failed": "计划执行失败",
            }.get(status, f"计划状态: {status}")
            self._emit(
                AgentEvent(
                    event_type="plan_completed",
                    content=message,
                    mode="plan",
                    plan_status=status,
                )
            )
            return result
        finally:
            if self._plan.plan_state is not None:
                self._plan.plan_state.current_step = None
                if self._session_manager is not None:
                    self._session_manager.set_active_plan(None)
                if self._plan.plan_state.status not in ("blocked", "failed"):
                    self._plan.plan_state.status = "completed"

    def _needs_plan(self, task: str) -> bool:
        """调用结构化判断协议，解析失败时按 ReAct 继续。"""
        response = self.llm.chat(
            [
                {"role": "system", "content": PLAN_DECISION_PROMPT},
                {"role": "user", "content": task},
            ],
            tools=None,
            on_event=None,
        )
        try:
            content = (response.content or "").strip()
            if content.startswith("```"):
                content = content.strip("`")
                newline = content.find("\n")
                if newline >= 0:
                    content = content[newline + 1 :]
            data: Any = json.loads(content)
            if not isinstance(data, dict) or not isinstance(data.get("needs_plan"), bool):
                raise ValueError("needs_plan 缺失或类型错误")
            self._emit(
                AgentEvent(
                    event_type="plan_decision",
                    content=f"自适应判断: {'需要计划' if data['needs_plan'] else '直接执行'}",
                )
            )
            return data["needs_plan"]
        except (json.JSONDecodeError, ValueError, TypeError):
            self._emit(AgentEvent(event_type="notice", content="计划判断结果无法解析，直接执行 ReAct。"))
            return False
