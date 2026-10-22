"""计划状态投影单元测试。"""

from __future__ import annotations

from sagent.core.plan_state import PlanState, PlanStepState


def _build_state(recent_observation: str = "") -> PlanState:
    """构造 4 步计划：步骤 1 执行中，后 3 步待执行。"""
    return PlanState(
        version=1,
        task="调研 A 公司的工商与招聘情况",
        steps=[
            PlanStepState(
                title="收集基础资料",
                description="通过搜索引擎收集 A 公司的公开资料",
                status="running",
            ),
            PlanStepState(
                title="核对工商信息",
                description="通过天眼查和企查查查询股东结构",
            ),
            PlanStepState(
                title="整理招聘信息",
                description="在招聘网站统计在招岗位数量",
            ),
            PlanStepState(
                title="输出调研报告",
                description="生成 Markdown 报告并保存到本地文件",
            ),
        ],
        current_step=1,
        recent_observation=recent_observation,
    )


def test_projection_hides_pending_descriptions():
    projection = _build_state().projection()
    assert "通过天眼查和企查查查询股东结构" not in projection
    assert "在招聘网站统计在招岗位数量" not in projection
    assert "生成 Markdown 报告并保存到本地文件" not in projection


def test_projection_exposes_only_next_step_title():
    projection = _build_state().projection()
    assert "下一步: 核对工商信息" in projection
    assert "整理招聘信息" not in projection
    assert "输出调研报告" not in projection


def test_projection_reports_progress_and_boundary():
    projection = _build_state().projection()
    assert "共 4 步，已完成 0 步，待执行 3 步" in projection
    assert "不得提前执行" in projection
    assert "剩余步骤" not in projection


def test_projection_omits_current_step_and_keeps_observation():
    projection = _build_state(recent_observation="上一步观察：已获取官网公开信息").projection()
    assert "当前步骤:" not in projection
    assert "通过搜索引擎收集 A 公司的公开资料" not in projection
    assert "上一步观察：已获取官网公开信息" in projection
    assert "原始任务: 调研 A 公司的工商与招聘情况" in projection


def test_projection_without_pending_step():
    state = _build_state()
    for step in state.steps:
        step.status = "completed"
        step.summary = f"{step.title}完成"
    projection = state.projection()
    assert "下一步: 无" in projection
    assert "共 4 步，已完成 4 步，待执行 0 步" in projection
