"""Skill 目录提示词构建单元测试。

覆盖空目录返回 None、manager 为 None 返回 None、单技能目录内容
（标题、load_skill 指引、限定名与描述条目）与跨来源重名技能的
逐行渲染。全部使用 tmp_path 做隔离，不依赖真实 LLM。
"""

from __future__ import annotations

from sagent.config.models import SkillConfig
from sagent.skills.manager import SkillManager
from sagent.skills.prompts import build_skill_catalog_prefix


def _make_skill(root, name: str, description: str) -> None:
    """在根目录下创建一个合法的 Skill 子目录。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n",
        encoding="utf-8",
    )


def _make_manager(
    tmp_path,
    project_skills: list[tuple[str, str]] | None = None,
    user_skills: list[tuple[str, str]] | None = None,
) -> SkillManager:
    """构建隔离的 Skill 管理器，可分别注入项目级与用户级技能。"""
    project_root = tmp_path / "project"
    user_home = tmp_path / "home"
    placements = (
        (project_root / ".sagent" / "skills", project_skills or []),
        (user_home / ".sagent" / "skills", user_skills or []),
    )
    for root, skills in placements:
        for name, description in skills:
            _make_skill(root, name, description)
    return SkillManager(SkillConfig(), project_root=project_root, user_home=user_home)


# ========== 返回 None 路径 ==========


def test_no_skills_returns_none(tmp_path):
    """manager 无可用技能时返回 None，调用方保持默认系统提示词。"""
    manager = _make_manager(tmp_path)
    assert build_skill_catalog_prefix(manager) is None


def test_none_manager_returns_none():
    """manager 为 None 时返回 None，调用方可安全透传未启用场景。"""
    assert build_skill_catalog_prefix(None) is None


# ========== 目录渲染 ==========


def test_single_skill_contains_qualified_name_description_and_guidance(tmp_path):
    """单技能目录含标题、load_skill 指引与限定名及描述条目。"""
    manager = _make_manager(tmp_path, project_skills=[("demo", "演示技能用途")])
    prefix = build_skill_catalog_prefix(manager)
    assert prefix is not None
    assert prefix.startswith("[可用技能]")
    # 调用约束指引：先加载正文再执行、不覆盖系统规则与权限策略
    assert "load_skill" in prefix
    assert "不能覆盖系统规则、用户要求与权限策略" in prefix
    assert "不会自动执行脚本或安装依赖" in prefix
    assert "## 技能" in prefix
    assert "- project:demo: 演示技能用途" in prefix


def test_duplicate_names_render_two_lines(tmp_path):
    """跨来源重名技能按 manager.skills 顺序各渲染一行，限定名互不相同。"""
    manager = _make_manager(
        tmp_path,
        project_skills=[("demo", "项目级演示")],
        user_skills=[("demo", "用户级演示")],
    )
    prefix = build_skill_catalog_prefix(manager)
    assert prefix is not None
    assert "- project:demo: 项目级演示" in prefix
    assert "- user:demo: 用户级演示" in prefix
    # 两行均位于技能小节标题之后
    section_index = prefix.index("## 技能")
    assert prefix.index("- project:demo:") > section_index
    assert prefix.index("- user:demo:") > section_index
