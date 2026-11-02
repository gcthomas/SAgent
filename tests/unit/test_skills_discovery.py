"""Skill 发现器单元测试。

覆盖项目/用户/extra 三根目录扫描与来源标记、根目录缺失或不是目录时继续扫描、
子目录缺少 SKILL.md 记录诊断、单个非法 Skill 不影响其他、allow/deny 过滤、
同名跨来源全部保留与限定名正确、只扫描直接子目录。
全部使用 tmp_path 做文件隔离，不依赖真实 LLM。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sagent.skills.discovery import SkillDiscovery, SkillRoot
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


# ========== 多根目录扫描与来源标记 ==========


def test_discover_three_roots_with_source_labels(tmp_path):
    """项目/用户/extra 三根目录均被扫描，来源与路径信息正确。"""
    project = tmp_path / "project" / ".sagent" / "skills"
    user = tmp_path / "user" / ".sagent" / "skills"
    extra = tmp_path / "extra" / "skills"
    _make_skill(project, "code-review")
    _make_skill(user, "write-docs")
    _make_skill(extra, "deploy")
    discovery = SkillDiscovery(
        [
            SkillRoot(path=project, source=SkillSource.PROJECT),
            SkillRoot(path=user, source=SkillSource.USER),
            SkillRoot(path=extra, source=SkillSource.EXTRA),
        ]
    )
    result = discovery.discover()
    assert result.issues == []
    assert [skill.qualified_name for skill in result.skills] == [
        "project:code-review",
        "user:write-docs",
        "extra:deploy",
    ]
    by_qualified_name = {skill.qualified_name: skill for skill in result.skills}
    review = by_qualified_name["project:code-review"]
    assert review.source == SkillSource.PROJECT
    assert review.root_dir == project
    assert review.directory == project / "code-review"
    assert by_qualified_name["user:write-docs"].source == SkillSource.USER
    assert by_qualified_name["extra:deploy"].source == SkillSource.EXTRA


def test_discover_missing_root_records_issue_and_continues(tmp_path):
    """某个根目录不存在时记一条诊断，其余根目录继续扫描。"""
    existing = tmp_path / "existing" / "skills"
    missing = tmp_path / "missing" / "skills"
    _make_skill(existing, "demo")
    discovery = SkillDiscovery(
        [
            SkillRoot(path=missing, source=SkillSource.PROJECT),
            SkillRoot(path=existing, source=SkillSource.USER),
        ]
    )
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["demo"]
    assert len(result.issues) == 1
    assert result.issues[0].directory == missing
    assert "不存在" in result.issues[0].reason


def test_discover_optional_missing_root_skipped_without_issue(tmp_path):
    """optional 根目录（隐式默认目录）不存在时跳过且不记诊断。"""
    existing = tmp_path / "existing" / "skills"
    missing = tmp_path / "missing" / "skills"
    _make_skill(existing, "demo")
    discovery = SkillDiscovery(
        [
            SkillRoot(path=missing, source=SkillSource.PROJECT, optional=True),
            SkillRoot(path=existing, source=SkillSource.USER, optional=True),
        ]
    )
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["demo"]
    assert result.issues == []


def test_discover_root_being_file_records_issue(tmp_path):
    """根目录路径实际是文件时记诊断且不抛异常。"""
    file_root = tmp_path / "not-a-dir"
    file_root.write_text("普通文件", encoding="utf-8")
    discovery = SkillDiscovery([SkillRoot(path=file_root, source=SkillSource.PROJECT)])
    result = discovery.discover()
    assert result.skills == []
    assert len(result.issues) == 1
    assert "不是目录" in result.issues[0].reason


# ========== 单个子目录失败隔离 ==========


def test_missing_skill_md_records_issue(tmp_path):
    """子目录缺少 SKILL.md 时记诊断并跳过，不影响其他 Skill。"""
    root = tmp_path / "skills"
    _make_skill(root, "demo")
    empty_dir = root / "empty-dir"
    empty_dir.mkdir()
    discovery = SkillDiscovery([SkillRoot(path=root, source=SkillSource.PROJECT)])
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["demo"]
    assert len(result.issues) == 1
    assert result.issues[0].directory == empty_dir
    assert result.issues[0].reason == "缺少 SKILL.md"


def test_invalid_skill_does_not_affect_others(tmp_path):
    """单个 Skill 的 SKILL.md 非法时记诊断，其余 Skill 正常发现。"""
    root = tmp_path / "skills"
    _make_skill(root, "good-skill")
    bad_dir = root / "bad-skill"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text(
        "---\nname: [未闭合\n---\n正文\n", encoding="utf-8"
    )
    discovery = SkillDiscovery([SkillRoot(path=root, source=SkillSource.PROJECT)])
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["good-skill"]
    assert len(result.issues) == 1
    assert result.issues[0].directory == bad_dir
    assert "frontmatter 解析失败" in result.issues[0].reason


# ========== allow / deny 过滤 ==========


def test_allow_filter_keeps_only_matched(tmp_path):
    """allow 非空时仅保留命中的 Skill，未命中记入诊断。"""
    root = tmp_path / "skills"
    dir_a = _make_skill(root, "alpha")
    dir_b = _make_skill(root, "beta")
    discovery = SkillDiscovery(
        [SkillRoot(path=root, source=SkillSource.PROJECT)],
        allow=["alpha"],
    )
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["alpha"]
    assert len(result.issues) == 1
    assert result.issues[0].directory == dir_b
    assert "未命中 skills.allow 过滤" in result.issues[0].reason
    assert dir_a not in [issue.directory for issue in result.issues]


def test_deny_filter_removes_matched(tmp_path):
    """deny 非空时移除命中的 Skill，被过滤项记入诊断。"""
    root = tmp_path / "skills"
    dir_a = _make_skill(root, "alpha")
    _make_skill(root, "beta")
    discovery = SkillDiscovery(
        [SkillRoot(path=root, source=SkillSource.PROJECT)],
        deny=["alpha"],
    )
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["beta"]
    assert len(result.issues) == 1
    assert result.issues[0].directory == dir_a
    assert "被 skills.deny 过滤" in result.issues[0].reason


def test_discover_log_records_filtered_count(caplog, tmp_path):
    """扫描完成日志记录被 allow/deny 过滤掉的技能数量。"""
    root = tmp_path / "skills"
    _make_skill(root, "alpha")
    _make_skill(root, "beta")
    discovery = SkillDiscovery(
        [SkillRoot(path=root, source=SkillSource.PROJECT)],
        allow=["alpha"],
    )
    with caplog.at_level(logging.INFO, logger="sagent.skills.discovery"):
        discovery.discover()
    record = next(
        item for item in caplog.records if getattr(item, "event", None) == "skills_discovered"
    )
    assert record.total == 1
    assert record.filtered_count == 1
    assert record.issue_count == 1


def test_deny_wins_when_in_both_allow_and_deny(tmp_path):
    """先 allow 后 deny：同时命中 allow 与 deny 的 Skill 被 deny 移除。"""
    root = tmp_path / "skills"
    dir_a = _make_skill(root, "alpha")
    _make_skill(root, "beta")
    discovery = SkillDiscovery(
        [SkillRoot(path=root, source=SkillSource.PROJECT)],
        allow=["alpha", "beta"],
        deny=["alpha"],
    )
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["beta"]
    assert [issue.directory for issue in result.issues] == [dir_a]
    assert "被 skills.deny 过滤" in result.issues[0].reason


def test_filter_applies_per_name_across_sources(tmp_path):
    """过滤按 name 匹配，命中名称在所有来源下都被过滤。"""
    project = tmp_path / "project" / "skills"
    user = tmp_path / "user" / "skills"
    _make_skill(project, "alpha")
    _make_skill(user, "alpha")
    discovery = SkillDiscovery(
        [
            SkillRoot(path=project, source=SkillSource.PROJECT),
            SkillRoot(path=user, source=SkillSource.USER),
        ],
        deny=["alpha"],
    )
    result = discovery.discover()
    assert result.skills == []
    assert len(result.issues) == 2


# ========== 同名跨来源 ==========


def test_same_name_across_sources_all_kept(tmp_path):
    """同名 Skill 跨来源全部保留，不做覆盖，限定名正确。"""
    project = tmp_path / "project" / "skills"
    user = tmp_path / "user" / "skills"
    _make_skill(project, "shared", description="项目版本")
    _make_skill(user, "shared", description="用户版本")
    discovery = SkillDiscovery(
        [
            SkillRoot(path=project, source=SkillSource.PROJECT),
            SkillRoot(path=user, source=SkillSource.USER),
        ]
    )
    result = discovery.discover()
    assert result.issues == []
    assert len(result.skills) == 2
    assert {skill.qualified_name for skill in result.skills} == {
        "project:shared",
        "user:shared",
    }
    descriptions = {skill.qualified_name: skill.description for skill in result.skills}
    assert descriptions["project:shared"] == "项目版本"
    assert descriptions["user:shared"] == "用户版本"


# ========== 扫描边界 ==========


def test_only_direct_children_scanned(tmp_path):
    """只扫描直接子目录：嵌套子目录不作为 Skill，根目录普通文件跳过。"""
    root = tmp_path / "skills"
    _make_skill(root, "outer")
    # outer 的嵌套子目录 inner 不被当作 Skill 扫描
    nested_dir = root / "outer" / "inner"
    nested_dir.mkdir(parents=True)
    (nested_dir / "SKILL.md").write_text(
        "---\nname: inner\ndescription: 嵌套定义\n---\n\n正文\n",
        encoding="utf-8",
    )
    # 嵌套链路上间层目录缺少 SKILL.md，仅该间层记诊断，更深层不被扫描
    orphan_parent = root / "nested-root"
    orphan_parent.mkdir()
    _make_skill(orphan_parent, "deep-skill")
    # 根目录下的普通文件直接跳过，不产生诊断
    (root / "README.md").write_text("说明文档", encoding="utf-8")
    discovery = SkillDiscovery([SkillRoot(path=root, source=SkillSource.PROJECT)])
    result = discovery.discover()
    assert [skill.name for skill in result.skills] == ["outer"]
    assert len(result.issues) == 1
    assert result.issues[0].directory == orphan_parent
    assert result.issues[0].reason == "缺少 SKILL.md"
