"""计划执行的结构化状态模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


StepStatus = Literal["pending", "running", "completed", "failed", "blocked", "skipped"]
PlanStatus = Literal["planning", "executing", "completed", "failed", "blocked"]


@dataclass
class PlanStepState:
    """单个计划步骤的可审计状态。"""

    description: str
    status: StepStatus = "pending"
    summary: str = ""
    artifact_refs: list[str] = field(default_factory=list)


@dataclass
class PlanState:
    """当前任务的有限计划状态，不写入普通消息历史。"""

    version: int
    task: str
    steps: list[PlanStepState]
    current_step: int | None = None
    completion_summary: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    recent_observation: str = ""
    failure: str | None = None
    blocked: str | None = None
    replans: int = 0
    status: PlanStatus = "planning"

    @property
    def replanning_count(self) -> int:
        return self.replans

    @replanning_count.setter
    def replanning_count(self, value: int) -> None:
        self.replans = value

    def projection(self, recent_observation: str = "") -> str:
        """生成有界计划投影，供单次 LLM 请求使用。"""
        completed = [step.summary[:300] for step in self.steps if step.status == "completed"]
        remaining = [step.description for step in self.steps if step.status == "pending"]
        current = self.steps[self.current_step - 1].description if self.current_step else ""
        observation = recent_observation or self.recent_observation
        artifacts = self.artifact_refs[-10:]
        return (
            "[计划投影]\n"
            f"原始任务: {self.task[:500]}\n"
            f"当前步骤: {current[:500]}\n"
            f"已完成摘要: {' | '.join(completed[-5:])[:1200]}\n"
            f"剩余步骤: {' | '.join(remaining[:10])[:1200]}\n"
            f"最近观察: {observation[-1200:]}\n"
            f"产物引用: {' | '.join(artifacts)[:800]}"
        )

    def to_dict(self) -> dict[str, Any]:
        """导出受限的结构化计划快照。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanState":
        """从会话快照恢复计划状态。"""
        steps = [PlanStepState(**step) for step in data.get("steps", [])]
        values = {key: value for key, value in data.items() if key != "steps"}
        return cls(steps=steps, **values)

