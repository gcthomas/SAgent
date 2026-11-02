"""SkillConfig 配置模型单元测试。

覆盖默认值、max_body_tokens 非正数校验失败、AppConfig 缺省包含 skills 字段、
从 dict 构造 AppConfig 时 skills 段生效。不依赖真实 LLM。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagent.config.models import AppConfig, SkillConfig


# ========== SkillConfig 默认值 ==========


def test_skill_config_defaults():
    """SkillConfig 全字段默认值正确。"""
    config = SkillConfig()
    assert config.enabled is True
    assert config.extra_dirs == []
    assert config.allow == []
    assert config.deny == []
    assert config.max_body_tokens == 2000
    assert config.max_resource_tokens == 2000


# ========== 字段校验 ==========


def test_skill_config_max_body_tokens_must_be_positive():
    """max_body_tokens 小于等于 0 时校验失败。"""
    with pytest.raises(ValidationError):
        SkillConfig(max_body_tokens=0)
    with pytest.raises(ValidationError):
        SkillConfig(max_body_tokens=-1)


def test_skill_config_max_resource_tokens_must_be_positive():
    """max_resource_tokens 小于等于 0 时校验失败。"""
    with pytest.raises(ValidationError):
        SkillConfig(max_resource_tokens=0)
    with pytest.raises(ValidationError):
        SkillConfig(max_resource_tokens=-1)


# ========== AppConfig 接入 ==========


def test_app_config_has_default_skills():
    """AppConfig 缺省包含 skills 字段且为 SkillConfig 实例。"""
    config = AppConfig(llm={"model": "test-model"})
    assert isinstance(config.skills, SkillConfig)
    assert config.skills.enabled is True


def test_app_config_skills_section_from_dict():
    """从 dict 构造 AppConfig 时 skills 段生效，含 extra_dirs 列表。"""
    config = AppConfig(
        llm={"model": "test-model"},
        skills={
            "enabled": True,
            "extra_dirs": [".agents/skills", "~/shared-agent-skills"],
            "allow": ["code-review"],
            "deny": ["legacy-skill"],
            "max_body_tokens": 3000,
        },
    )
    assert config.skills.enabled is True
    assert config.skills.extra_dirs == [".agents/skills", "~/shared-agent-skills"]
    assert config.skills.allow == ["code-review"]
    assert config.skills.deny == ["legacy-skill"]
    assert config.skills.max_body_tokens == 3000
    assert config.skills.max_resource_tokens == 2000
