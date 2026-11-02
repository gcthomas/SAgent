"""Skill 正文加载器单元测试。

覆盖合法加载渲染、空引用拒绝、未知技能拒绝、正文超预算拒绝、
正文恰好等于预算不报错、成功路径 skill_loaded 日志事件。
全部使用 tmp_path 做隔离，不依赖真实 LLM。
"""

from __future__ import annotations

import logging

from sagent.config.models import SkillConfig
from sagent.skills.loader import SkillLoader
from sagent.skills.manager import SkillManager


def _make_skill(root, name: str, description: str = "测试技能", body: str | None = None) -> None:
    """在根目录下创建一个合法的 Skill 子目录。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    body_text = body if body is not None else f"{description}\n"
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body_text}",
        encoding="utf-8",
    )


def _make_loader(tmp_path, **kwargs) -> tuple[SkillLoader, object]:
    """构建项目级技能目录下含一个技能的加载器，返回 (loader, skill 目录)。"""
    project_root = tmp_path / "project"
    project_skills = project_root / ".sagent" / "skills"
    _make_skill(project_skills, "code-review", body="这是技能正文内容。\n")
    manager = SkillManager(
        SkillConfig(),
        project_root=project_root,
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager, **kwargs)
    return loader, project_skills / "code-review"


# ========== 合法加载与渲染 ==========


def test_load_renders_qualified_name_body_and_resource_notes(tmp_path):
    """合法引用返回渲染文本，含限定名、来源、目录与正文及资源目录说明。"""
    loader, skill_dir = _make_loader(tmp_path)
    rendered = loader.load("code-review")
    assert "[技能] project:code-review" in rendered
    assert "来源: project" in rendered
    assert str(skill_dir) in rendered
    assert "--- 技能正文 ---" in rendered
    assert "这是技能正文内容。" in rendered
    assert "references/" in rendered
    assert "scripts/" in rendered
    assert "assets/" in rendered


def test_load_by_qualified_name(tmp_path):
    """来源限定名引用同样命中并渲染。"""
    loader, _ = _make_loader(tmp_path)
    rendered = loader.load("project:code-review")
    assert "[技能] project:code-review" in rendered


# ========== 失败路径 ==========


def test_load_blank_reference_returns_error(tmp_path):
    """空引用与纯空白引用返回错误字符串。"""
    loader, _ = _make_loader(tmp_path)
    assert loader.load("").startswith("错误:")
    assert loader.load("   ").startswith("错误:")


def test_injected_token_counter_changes_estimation(tmp_path):
    """注入自定义计数函数后 estimate_tokens 委托该函数，与上下文侧口径一致。"""
    loader, _ = _make_loader(
        tmp_path,
        token_counter=lambda text, **kwargs: 99999,
        token_counter_kwargs={"model": "test", "method": "heuristic"},
    )
    assert loader.estimate_tokens("任意文本") == 99999
    # 正文超预算拒绝路径同样按注入的计数结果判定
    result = loader.load("code-review")
    assert result.startswith("错误:")
    assert "预算" in result


def test_load_unknown_skill_returns_error_without_files(tmp_path):
    """未知技能返回错误字符串，且不在项目技能目录下创建任何文件。"""
    project_root = tmp_path / "project"
    manager = SkillManager(
        SkillConfig(),
        project_root=project_root,
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager)
    result = loader.load("no-such-skill")
    assert result.startswith("错误:")
    assert not (project_root / ".sagent" / "skills" / "no-such-skill").exists()


def test_load_over_budget_body_rejected(tmp_path):
    """正文估算 token 超过预算时拒绝，错误信息含预算字样且不返回正文。"""
    long_body = "很长的正文" * 100
    loader, _ = _make_loader(tmp_path, max_body_tokens=1)
    result = loader.load("code-review")
    assert result.startswith("错误:")
    assert "预算" in result
    assert "很长的正文" not in result
    # 长正文场景复核：把长正文技能放进来也按预算拒绝
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    _make_skill(project_skills, "long-skill", body=long_body)
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    long_loader = SkillLoader(manager, max_body_tokens=1)
    long_result = long_loader.load("long-skill")
    assert long_result.startswith("错误:")
    assert long_body not in long_result


def test_load_body_exactly_at_budget_succeeds(tmp_path):
    """正文估算 token 恰好等于预算时不报错，正常返回正文。"""
    body = "字" * 20
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    _make_skill(project_skills, "exact-skill", body=body)
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager, max_body_tokens=10)
    assert loader.estimate_tokens(body) == 10
    rendered = loader.load("exact-skill")
    assert "--- 技能正文 ---" in rendered
    assert body in rendered


def test_load_rendered_fulltext_over_output_budget_rejected(tmp_path):
    """body 通过校验但渲染后全文超过输出预算时拒绝，不返回正文。"""
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    _make_skill(project_skills, "output-budget", body="短正文。")
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager, max_body_tokens=2000, max_output_tokens=1)
    result = loader.load("output-budget")
    assert result.startswith("错误:技能渲染后超过输出预算上限")
    assert "很长的正文" not in result
    assert "短正文" not in result
    # 渲染全文恰好不超预算时正常返回（默认预算远大于本技能渲染文本）
    ok_loader = SkillLoader(manager, max_body_tokens=2000, max_output_tokens=2000)
    assert "--- 技能正文 ---" in ok_loader.load("output-budget")


# ========== 日志事件 ==========


def test_load_success_logs_skill_loaded_event(tmp_path, caplog):
    """成功路径记录 skill_loaded 日志事件，字段齐全。"""
    loader, _ = _make_loader(tmp_path)
    with caplog.at_level(logging.INFO, logger="sagent.skills.loader"):
        loader.load("code-review")
    records = [r for r in caplog.records if r.__dict__.get("event") == "skill_loaded"]
    assert len(records) == 1
    extra = records[0].__dict__
    assert extra["skill_name"] == "code-review"
    assert extra["source"] == "project"
    assert extra["body_chars"] > 0
    assert extra["estimated_tokens"] > 0
