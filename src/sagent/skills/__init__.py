"""Skill 模块公开接口。

提供 Skill 核心数据模型、SKILL.md 解析校验与多根目录发现能力，
供后续配置装配、加载工具与 CLI 命令使用。
"""

from __future__ import annotations

from .discovery import SkillDiscovery, SkillRoot
from .loader import LoadSkillArgs, ReadSkillResourceArgs, SkillLoader
from .manager import SkillManager, SkillNotFoundError
from .models import DiscoveryResult, Skill, SkillIssue, SkillSource
from .parser import SkillParseError, parse_skill_md, validate_skill_name
from .prompts import build_skill_catalog_prefix

__all__ = [
    # models
    "Skill",
    "SkillSource",
    "SkillIssue",
    "DiscoveryResult",
    # discovery
    "SkillRoot",
    "SkillDiscovery",
    # manager
    "SkillManager",
    "SkillNotFoundError",
    # loader
    "SkillLoader",
    "LoadSkillArgs",
    "ReadSkillResourceArgs",
    # parser
    "SkillParseError",
    "parse_skill_md",
    "validate_skill_name",
    # prompts
    "build_skill_catalog_prefix",
]
