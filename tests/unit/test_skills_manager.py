"""Skill 管理器单元测试。

覆盖根目录装配顺序与来源标记、extra_dirs 相对路径按运行目录解析与 ~ 展开、
默认根目录缺失不记诊断、extra 目录缺失记诊断、discover 结果缓存、
skills/issues 属性自动触发扫描、resolve 的短名/限定名/歧义/未知/空引用/非法来源解析。
全部使用 tmp_path 与 monkeypatch 做隔离，不依赖真实 LLM。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sagent.config.models import SkillConfig
from sagent.skills.manager import SkillManager, SkillNotFoundError
from sagent.skills.models import SkillSource


def _make_skill(root: Path, name: str, description: str = "测试技能") -> Path:
    """在根目录下创建一个合法的 Skill 子目录并返回其路径。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n",
        encoding="utf-8",
    )
    return directory


# ========== 根目录装配 ==========


def test_roots_assembled_in_fixed_order_with_sources(tmp_path):
    """项目级 -> 用户级 -> extra 目录按固定顺序装配，来源与根目录标记正确。"""
    project_root = tmp_path / "project"
    user_home = tmp_path / "home"
    extra_root = tmp_path / "extra" / "skills"
    project_skills = project_root / ".sagent" / "skills"
    user_skills = user_home / ".sagent" / "skills"
    _make_skill(project_skills, "p-skill")
    _make_skill(user_skills, "u-skill")
    _make_skill(extra_root, "e-skill")
    manager = SkillManager(
        SkillConfig(extra_dirs=[str(extra_root)]),
        project_root=project_root,
        user_home=user_home,
    )
    result = manager.discover()
    assert result.issues == []
    assert [skill.qualified_name for skill in result.skills] == [
        "project:p-skill",
        "user:u-skill",
        "extra:e-skill",
    ]
    root_by_qualified = {skill.qualified_name: skill.root_dir for skill in result.skills}
    assert root_by_qualified["project:p-skill"] == project_skills
    assert root_by_qualified["user:u-skill"] == user_skills
    assert root_by_qualified["extra:e-skill"] == extra_root


def test_missing_default_roots_not_reported_but_missing_extra_is(tmp_path):
    """项目级/用户级目录未创建属正常不记诊断；显式配置的 extra 目录缺失仍记诊断。"""
    project_root = tmp_path / "project"
    user_home = tmp_path / "home"
    extra_root = tmp_path / "extra" / "skills"
    manager = SkillManager(
        SkillConfig(extra_dirs=[str(extra_root)]),
        project_root=project_root,
        user_home=user_home,
    )
    result = manager.discover()
    assert result.skills == []
    assert [issue.directory for issue in result.issues] == [extra_root]


def test_extra_dirs_relative_resolved_against_cwd(tmp_path, monkeypatch):
    """extra_dirs 相对路径按当前运行目录解析。"""
    run_dir = tmp_path / "run"
    extra_root = run_dir / ".agents" / "skills"
    _make_skill(extra_root, "rel-skill")
    monkeypatch.chdir(run_dir)
    manager = SkillManager(
        SkillConfig(extra_dirs=[".agents/skills"]),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    result = manager.discover()
    extra_skills = [skill for skill in result.skills if skill.source == SkillSource.EXTRA]
    assert [skill.name for skill in extra_skills] == ["rel-skill"]
    assert extra_skills[0].root_dir == extra_root


def test_extra_dirs_tilde_expansion_records_issue_when_missing(tmp_path):
    """~ 开头的 extra 目录按用户主目录展开；目录不存在时不抛错、只记诊断。"""
    tilde_dir = "~/.sagent-selftest-no-such-skills-dir"
    expected_path = Path.home() / ".sagent-selftest-no-such-skills-dir"
    manager = SkillManager(
        SkillConfig(extra_dirs=[tilde_dir]),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    result = manager.discover()
    assert result.skills == []
    issue_dirs = [issue.directory for issue in result.issues]
    assert expected_path in issue_dirs
    assert expected_path.is_absolute()


def test_multiple_extra_dirs_numbered_qualified_names(tmp_path):
    """多个额外目录中的同名技能按配置顺序编号，限定名可分别解析出正确目录。"""
    extra_a = tmp_path / "extra-a"
    extra_b = tmp_path / "extra-b"
    _make_skill(extra_a, "shared", description="A 版本")
    _make_skill(extra_b, "shared", description="B 版本")
    manager = SkillManager(
        SkillConfig(extra_dirs=[str(extra_a), str(extra_b)]),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    assert [skill.qualified_name for skill in manager.skills] == [
        "extra1:shared",
        "extra2:shared",
    ]
    assert manager.resolve("extra1:shared").description == "A 版本"
    assert manager.resolve("extra2:shared").description == "B 版本"
    # 短名歧义提示列出互不相同的限定名，用户可据此消歧
    with pytest.raises(SkillNotFoundError) as exc_info:
        manager.resolve("shared")
    message = str(exc_info.value)
    assert "extra1:shared" in message
    assert "extra2:shared" in message
    # 多个额外目录时不再接受不带编号的 extra 前缀
    with pytest.raises(SkillNotFoundError) as extra_exc:
        manager.resolve("extra:shared")
    assert "extra1" in str(extra_exc.value)


# ========== 项目根默认解析 ==========


def test_default_project_root_found_from_nested_subdirectory(tmp_path, monkeypatch):
    """未显式传 project_root 时，从子目录启动仍能向上找到含 .sagent 的项目根。"""
    project_root = tmp_path / "project"
    project_skills = project_root / ".sagent" / "skills"
    _make_skill(project_skills, "nested-skill")
    nested = project_root / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    manager = SkillManager(SkillConfig(), user_home=tmp_path / "home")
    assert [skill.qualified_name for skill in manager.skills] == ["project:nested-skill"]
    assert manager.skills[0].root_dir == project_skills


def test_default_project_root_falls_back_to_cwd_without_sagent(tmp_path, monkeypatch):
    """向上未找到 .sagent 时，项目根回退为当前运行目录，且缺失目录不记为诊断。"""
    run_dir = tmp_path / "plain-run-dir"
    run_dir.mkdir()
    monkeypatch.chdir(run_dir)
    manager = SkillManager(SkillConfig(), user_home=tmp_path / "home")
    assert manager.skills == []
    assert manager.issues == []


# ========== discover 缓存与属性自动触发 ==========


def test_discover_result_cached_between_calls(tmp_path):
    """两次 discover 返回同一缓存实例。"""
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    first = manager.discover()
    second = manager.discover()
    assert first is second


def test_skills_and_issues_properties_trigger_discover(tmp_path):
    """skills / issues 属性在未扫描时自动触发 discover，结果为同一缓存。"""
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    _make_skill(project_skills, "auto-skill")
    missing_extra = tmp_path / "missing-extra"
    manager = SkillManager(
        SkillConfig(extra_dirs=[str(missing_extra)]),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    skills = manager.skills
    assert [skill.qualified_name for skill in skills] == ["project:auto-skill"]
    # 属性访问已自动触发扫描：显式配置但缺失的 extra 目录记一条诊断
    issues = manager.issues
    assert len(issues) == 1
    assert issues[0].directory == missing_extra
    # 属性访问后 discover 返回同一缓存实例
    assert manager.discover().skills is skills
    assert manager.discover().issues is issues


# ========== resolve 名称解析 ==========


def _make_manager_for_resolve(tmp_path) -> SkillManager:
    """构建含跨来源同名技能的管理器：project 与 user 均有 shared，user 另有 write-docs。"""
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    user_skills = tmp_path / "home" / ".sagent" / "skills"
    _make_skill(project_skills, "shared", description="项目版本")
    _make_skill(user_skills, "shared", description="用户版本")
    _make_skill(user_skills, "write-docs")
    return SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )


def test_resolve_unique_short_name(tmp_path):
    """短名唯一命中时直接返回对应 Skill。"""
    manager = _make_manager_for_resolve(tmp_path)
    skill = manager.resolve("write-docs")
    assert skill.qualified_name == "user:write-docs"


def test_resolve_ambiguous_short_name_raises_with_qualified_names(tmp_path):
    """短名跨来源命中多个时抛错，消息列出全部限定名并提示使用来源限定名。"""
    manager = _make_manager_for_resolve(tmp_path)
    with pytest.raises(SkillNotFoundError) as exc_info:
        manager.resolve("shared")
    message = str(exc_info.value)
    assert "project:shared" in message
    assert "user:shared" in message
    assert "来源限定名" in message


def test_resolve_qualified_name_hits_exact_skill(tmp_path):
    """来源限定名精确命中对应来源的 Skill。"""
    manager = _make_manager_for_resolve(tmp_path)
    assert manager.resolve("project:shared").description == "项目版本"
    assert manager.resolve("user:shared").description == "用户版本"


def test_resolve_unknown_name_raises(tmp_path):
    """短名与限定名均未命中时抛错，消息包含引用内容。"""
    manager = _make_manager_for_resolve(tmp_path)
    with pytest.raises(SkillNotFoundError) as short_exc:
        manager.resolve("no-such-skill")
    assert "no-such-skill" in str(short_exc.value)
    with pytest.raises(SkillNotFoundError) as qualified_exc:
        manager.resolve("project:no-such-skill")
    assert "project:no-such-skill" in str(qualified_exc.value)


def test_resolve_blank_reference_raises(tmp_path):
    """空引用与纯空白引用抛错。"""
    manager = _make_manager_for_resolve(tmp_path)
    with pytest.raises(SkillNotFoundError):
        manager.resolve("")
    with pytest.raises(SkillNotFoundError):
        manager.resolve("   ")


def test_resolve_invalid_source_raises(tmp_path):
    """来源限定名的来源值非法时抛错，消息说明合法来源。"""
    manager = _make_manager_for_resolve(tmp_path)
    with pytest.raises(SkillNotFoundError) as exc_info:
        manager.resolve("foo:shared")
    assert "来源" in str(exc_info.value)
