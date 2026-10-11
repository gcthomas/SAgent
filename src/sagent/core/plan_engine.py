"""Plan 执行引擎。

先由 LLM 将复杂任务拆解为有序步骤，再逐步执行（每步复用 ReAct 引擎），最后汇总结果。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config.models import AgentConfig, PlanningConfig
from .plan_state import PlanState, PlanStepState
from ..context.context_manager import ContextManager
from ..llm.client import LLMClient, LLMStreamEvent
from .events import AgentEvent, EventHandler
from ..observability import Span, get_logger
from ..tools.registry import ToolRegistry
from .prompts import PLAN_DECOMPOSE_PROMPT, PLAN_REPLAN_PROMPT, PLAN_SUMMARY_PROMPT, REACT_SYSTEM_PROMPT
from .react_engine import ReActEngine

logger = get_logger(__name__)


class PlanEngine:
    """Plan 执行引擎。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        agent_config: AgentConfig,
        on_event: EventHandler | None = None,
        context_manager: ContextManager | None = None,
        planning_config: PlanningConfig | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.config = agent_config
        self._on_event = on_event
        self._context_manager = context_manager
        self._planning_config = planning_config or PlanningConfig()
        self.plan_state: PlanState | None = None
        self._outcome = "completed"
        self._step_context: tuple[int, int, str] | None = None
        self._recent_observation = ""
        self._on_plan_state_change = None
        # 每个步骤复用 ReAct 引擎执行，传入 context_manager 实现上下文持久化
        self._react = ReActEngine(
            llm,
            registry,
            agent_config,
            self._forward_react_event,
            context_manager=context_manager,
        )

    def _emit(self, event: AgentEvent) -> None:
        if self._on_event:
            self._on_event(event)

    def _on_llm_event(self, event: LLMStreamEvent) -> None:
        """将 Plan 顶层流式事件转为结构化 Agent 事件。"""
        if event.event_type == "text_delta":
            self._emit(AgentEvent(event_type="llm_text_delta", content=event.content, mode="plan"))
        elif event.event_type == "completed":
            self._emit(
                AgentEvent(
                    event_type="llm_completed",
                    content=event.response.content if event.response else None,
                    mode="plan",
                )
            )

    def _chat(self, messages: list[dict[str, Any]], emit_stream: bool = True):
        """发起 Plan 请求，流式配置下由客户端发送增量事件。"""
        callback = self._on_llm_event if emit_stream else None
        return self.llm.chat(messages, tools=None, on_event=callback)

    def _forward_react_event(self, event: AgentEvent) -> None:
        """为 Plan 步骤内的 ReAct 事件补充步骤上下文。"""
        if self._step_context is None:
            event = AgentEvent(
                event_type=event.event_type,
                content=event.content,
                mode="plan",
                scope="plan_fallback",
                iteration=event.iteration,
                tool_name=event.tool_name,
                tool_arguments=event.tool_arguments,
                tool_call_id=event.tool_call_id,
            )
        else:
            index, total, step = self._step_context
            event = event.with_plan_context(index=index, total=total, step=step)
        self._emit(event)

    def run(self, task: str, memory_prefix: str | None = None) -> str:
        """执行 Plan 模式任务：拆解 -> 分步执行 -> 汇总。

        参数:
            task: 用户任务/输入。
            memory_prefix: 可选的记忆注入前缀，叠加到 ReAct 系统提示词之前
                （而非替换）。会话开始时由调用方冻结构建一次，整个会话复用。
        """
        prompt = REACT_SYSTEM_PROMPT
        if memory_prefix:
            prompt = memory_prefix + "\n\n" + prompt
        # 恢复状态需在写入用户任务前判定，用于避免重复写入上下文。
        restored = self.plan_state is not None and self.plan_state.status == "executing"
        # 注入了上下文管理器时，确保系统提示词在上下文中并添加用户原始任务
        if self._context_manager is not None:
            self._context_manager.ensure_system_prompt(prompt)
            # 恢复时用户任务已存在于历史，避免重复写入。
            if not restored:
                self._context_manager.add_message({"role": "user", "content": task})

        if restored:
            if self.plan_state.task != task:
                raise ValueError("恢复的活动计划与当前任务不一致")
            steps = [step.description for step in self.plan_state.steps]
            for step in self.plan_state.steps:
                # 中断时的 running 不代表完成，恢复为 pending 后重新执行。
                if step.status == "running":
                    step.status = "pending"
            decompose_span = None
        else:
            with Span("plan.decompose") as decompose_span:
                steps = self._decompose(task)
            steps = steps[: self._planning_config.max_steps]
        if not steps:
            # 拆解失败时回退为直接用 ReAct 执行整个任务
            self._emit(
                AgentEvent(
                    event_type="notice",
                    content="未能拆解出步骤，直接执行整个任务。",
                    mode="plan",
                    scope="plan_fallback",
                )
            )
            logger.warning("Plan 拆解失败，回退 ReAct", extra={"event": "plan_fallback"})
            self._outcome = "plan_fallback"
            return self._react.run(
                task,
                memory_prefix=memory_prefix,
                persist_task=self._context_manager is None,
            )

        if decompose_span is not None:
            decompose_span.set_attribute("sagent.step_count", len(steps))
        if not restored:
            self.plan_state = PlanState(
                version=1,
                task=task,
                steps=[PlanStepState(description=step) for step in steps],
                status="executing",
            )
        self.plan_state.status = "executing"
        self._recent_observation = self.plan_state.recent_observation
        if self._on_plan_state_change:
            self._on_plan_state_change(self.plan_state)
        self._emit(
            AgentEvent(
                event_type="plan_started",
                content=f"[计划] 共拆解为 {len(steps)} 个步骤：",
                mode="plan",
            )
        )
        logger.info(
            "Plan 拆解完成",
            extra={"event": "plan_decomposed", "step_count": len(steps), "steps": steps},
        )
        for idx, step in enumerate(steps, start=1):
            if self.plan_state.steps[idx - 1].status == "completed":
                continue
            self._emit(
                AgentEvent(
                    event_type="notice",
                    content=f"{idx}. {step}",
                    mode="plan",
                    plan_step_index=idx,
                    plan_step_total=len(steps),
                    plan_step=step,
                )
            )

        # 恢复活动计划时已完成步骤不再执行，但需将有界摘要带入最终汇总；
        # 仅取 summary，避免把步骤描述等冗长文本写入汇总输入。
        step_results: list[str] = [
            f"步骤 {index} 结果:\n{step.summary}"
            for index, step in enumerate(self.plan_state.steps, start=1)
            if step.status == "completed"
        ]
        idx = 1
        while idx <= len(self.plan_state.steps):
            step = self.plan_state.steps[idx - 1].description
            # 保留已完成步骤的状态和摘要，恢复时不重复执行。
            if self.plan_state.steps[idx - 1].status == "completed":
                idx += 1
                continue
            with Span("plan.step") as step_span:
                step_span.set_attribute("sagent.step_index", idx)
                step_span.set_attribute("sagent.step_total", len(steps))
                self._emit(
                    AgentEvent(
                        event_type="plan_step_started",
                        mode="plan",
                        plan_step_index=idx,
                        plan_step_total=len(steps),
                        plan_step=step,
                    )
                )
                logger.info(
                    "Plan 执行步骤",
                    extra={"event": "plan_step", "index": idx, "total": len(steps), "step": step},
                )
                # 为每个步骤提供原始任务作为背景
                self.plan_state.current_step = idx
                # 重规划后在当前索引重试新步骤，不提前推进到下一步。
                while True:
                    step = self.plan_state.steps[idx - 1].description
                    steps = [item.description for item in self.plan_state.steps]
                    step_task = (
                        f"原始任务: {task}\n"
                        f"当前需要完成的步骤: {step}\n"
                        f"请完成该步骤并给出结果。"
                    )
                    self._step_context = (idx, len(steps), step)
                    self.plan_state.steps[idx - 1].status = "running"
                    try:
                        # 投影只进入请求；共享上下文已有原始任务，不再持久化子步骤提示。
                        result = self._react.run(
                            step_task,
                            memory_prefix=memory_prefix,
                            plan_projection=self.plan_state.projection(self._recent_observation),
                            persist_task=self._context_manager is None,
                        )
                    except Exception as exc:
                        # 异常与错误结果共享计划级重规划上限，耗尽后标记 blocked。
                        if self.plan_state.replans < self._planning_config.max_replans:
                            self._replan(task, idx, str(exc))
                            steps = [item.description for item in self.plan_state.steps]
                            continue
                        self.plan_state.steps[idx - 1].status = "blocked"
                        self.plan_state.failure = str(exc)
                        self.plan_state.blocked = f"步骤 {idx} 重规划次数已达到上限。"
                        self.plan_state.status = "blocked"
                        self.plan_state.recent_observation = str(exc)[-1200:]
                        self._recent_observation = self.plan_state.recent_observation
                        if self._on_plan_state_change:
                            self._on_plan_state_change(self.plan_state)
                        result = None
                    finally:
                        self._step_context = None
                    if result is None:
                        break
                    # 工具约定的错误结果同样触发重规划，不作为成功步骤保存。
                    if result.startswith("错误:"):
                        self.plan_state.recent_observation = result[-1200:]
                        self._recent_observation = self.plan_state.recent_observation
                        if self.plan_state.replans < self._planning_config.max_replans:
                            self._replan(task, idx, result)
                            steps = [item.description for item in self.plan_state.steps]
                            continue
                        self.plan_state.steps[idx - 1].status = "blocked"
                        self.plan_state.failure = result[:1200]
                        self.plan_state.blocked = f"步骤 {idx} 重规划次数已达到上限。"
                        self.plan_state.status = "blocked"
                        step_results.append(f"步骤 {idx}（{step}）未完成:\n{result[:1200]}")
                        if self._on_plan_state_change:
                            self._on_plan_state_change(self.plan_state)
                        break
                    self.plan_state.steps[idx - 1].status = "completed"
                    # 快照只保留有界摘要与最近观察，完整结果留给本轮汇总。
                    self.plan_state.steps[idx - 1].summary = result[:500]
                    self.plan_state.recent_observation = result[-1200:]
                    self._recent_observation = self.plan_state.recent_observation
                    refs = re.findall(r"(?:file://|https?://)\S+|[A-Za-z]:\\\\\S+", result)
                    # 产物仅保存引用，不保存正文；限制步骤和计划的引用数量。
                    self.plan_state.steps[idx - 1].artifact_refs = refs[:10]
                    self.plan_state.artifact_refs = (self.plan_state.artifact_refs + refs)[:20]
                    step_results.append(f"步骤 {idx}（{step}）结果:\n{result}")
                    if self._on_plan_state_change:
                        self._on_plan_state_change(self.plan_state)
                    break
                # 受阻后停止后续步骤，进入部分结果收尾。
                if self.plan_state.status == "blocked":
                    break
                idx += 1

        with Span("plan.summarize") as summarize_span:
            # blocked 明确报告未完成及已有部分，不包装成成功完成。
            if self.plan_state.status == "blocked":
                partial = self._summarize(task, step_results) if step_results else "尚无步骤成功完成。"
                summary = f"计划未完全完成：{self.plan_state.blocked}\n已完成部分：\n{partial}"
            else:
                summary = self._summarize(task, step_results)

        if self.plan_state is not None:
            self.plan_state.completion_summary = summary[:1000]
            self.plan_state.current_step = None
            # 收尾清除当前步骤，但保留 blocked 状态供调用方判断。
            if self.plan_state.status != "blocked":
                self.plan_state.status = "completed"
            # 汇总需报告最终状态与重规划次数；Span 支持 with 退出后设置属性。
            summarize_span.set_attribute("sagent.plan_status", self.plan_state.status)
            summarize_span.set_attribute("sagent.step_count", len(self.plan_state.steps))
            summarize_span.set_attribute("sagent.replan_count", self.plan_state.replans)
            logger.info(
                "Plan 汇总结果",
                extra={
                    "event": "plan_summarize",
                    "plan_status": self.plan_state.status,
                    "step_count": len(self.plan_state.steps),
                    "replan_count": self.plan_state.replans,
                },
            )
            if self._on_plan_state_change:
                self._on_plan_state_change(self.plan_state)
        # 汇总完成后，将最终结果添加到上下文作为 assistant 消息
        if self._context_manager is not None:
            self._context_manager.add_message({"role": "assistant", "content": summary})

        return summary

    def _replan(self, task: str, failed_index: int, observation: str) -> None:
        state = self.plan_state
        completed = [step for step in state.steps if step.status == "completed"]
        remaining = [step.description for step in state.steps[failed_index - 1 :]]
        response = self._chat(
            [
                {"role": "system", "content": PLAN_REPLAN_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"原始任务:\n{task}\n已完成步骤:\n"
                        f"{[step.description + ': ' + step.summary for step in completed]}\n"
                        f"剩余步骤:\n{remaining}\n失败观察:\n{observation[-1200:]}"
                    ),
                },
            ],
            emit_stream=False,
        )
        revised = self._parse_steps((response.content or "").strip())
        if not revised:
            revised = remaining
        revised = revised[: self._planning_config.max_steps]
        state.steps = completed + [PlanStepState(description=step) for step in revised]
        state.version += 1
        state.replans += 1
        state.failure = observation[-1200:]
        state.status = "executing"
        self._emit(
            AgentEvent(
                event_type="plan_replanned",
                content=f"根据失败观察调整计划（第 {state.replans} 次，共 {len(state.steps)} 步）。",
                mode="plan",
                plan_step_index=failed_index,
                plan_step_total=len(state.steps),
                plan_step=state.steps[len(completed)].description if len(state.steps) > len(completed) else None,
            )
        )
        with Span("plan.replan") as replan_span:
            replan_span.set_attribute("sagent.replan_count", state.replans)
            replan_span.set_attribute("sagent.step_count", len(state.steps))
            replan_span.set_attribute("sagent.plan_status", state.status)
        logger.info(
            "Plan 重规划",
            extra={
                "event": "plan_replanned",
                "replan_count": state.replans,
                "step_count": len(state.steps),
                "plan_status": state.status,
            },
        )
        if self._on_plan_state_change:
            self._on_plan_state_change(state)

    def _decompose(self, task: str) -> list[str]:
        """调用 LLM 将任务拆解为步骤列表。解析失败返回空列表。"""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PLAN_DECOMPOSE_PROMPT},
            {"role": "user", "content": task},
        ]
        response = self._chat(messages, emit_stream=False)
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
        response = self._chat(messages)
        return response.content or joined
