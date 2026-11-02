"""Skill 日志事件可观测性单元测试。

覆盖 SkillLoader.load 未知技能时的 skill_load_rejected 事件，
与 SkillDiscovery.discover 空根目录时的 skills_discovered 事件。
全部使用 tmp_path 做隔离，不依赖真实 LLM。
"""

from __future__ import annotations

import logging

from sagent.skills.discovery import SkillDiscovery, SkillRoot
from sagent.skills.loader import SkillLoader
from sagent.skills.manager import SkillManager
from sagent.skills.models import SkillSource
from sagent.config.models import SkillConfig


def test_load_unknown_skill_logs_rejected_event(tmp_path, caplog):
    """加载未知技能时记录 skill_load_rejected 日志事件，字段齐全。"""
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager)
    with caplog.at_level(logging.DEBUG, logger="sagent.skills.loader"):
        result = loader.load("no-such-skill")
    assert result.startswith("错误:")
    records = [r for r in caplog.records if r.__dict__.get("event") == "skill_load_rejected"]
    assert len(records) == 1
    extra = records[0].__dict__
    assert extra["skill_ref"] == "no-such-skill"
    assert extra["reason"] == "not_found"
    assert extra["detail"]


def test_discover_empty_root_logs_discovered_event(tmp_path, caplog):
    """空根目录扫描记录 skills_discovered 日志事件，extra 含 total 与 issue_count。"""
    missing_root = tmp_path / "skills"
    discovery = SkillDiscovery(
        [SkillRoot(path=missing_root, source=SkillSource.PROJECT)],
    )
    with caplog.at_level(logging.INFO, logger="sagent.skills.discovery"):
        result = discovery.discover()
    assert result.skills == []
    assert len(result.issues) == 1
    records = [r for r in caplog.records if r.__dict__.get("event") == "skills_discovered"]
    assert len(records) == 1
    extra = records[0].__dict__
    assert extra["total"] == 0
    assert extra["issue_count"] == 1
