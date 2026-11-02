"""Skill 核心数据模型。

定义 Skill 来源枚举、校验诊断、通过校验的 Skill 实体与一次扫描的完整结果。
本模块为纯数据结构，不包含解析与扫描逻辑，也不依赖 LLM 与配置模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SkillSource(str, Enum):
    """Skill 来源：project=项目根目录、user=用户主目录、extra=配置额外目录。"""

    PROJECT = "project"
    USER = "user"
    EXTRA = "extra"


@dataclass
class SkillIssue:
    """单个 Skill 目录的校验失败诊断。

    属性:
        directory: 触发问题的目录（或文件）路径。
        reason: 中文可读原因，如 "缺少 SKILL.md"、"frontmatter 解析失败: ..."。
    """

    directory: Path
    reason: str


@dataclass
class Skill:
    """一个通过校验的 Skill。

    属性:
        name: frontmatter.name，与所在目录名一致。
        description: frontmatter.description，供 LLM 理解 Skill 用途。
        source: Skill 来源，标记该 Skill 来自哪个根目录类型。
        root_dir: 所属 Skill 根目录（如 <项目>/.sagent/skills）。
        directory: Skill 自身目录（root_dir / name）。
        body: SKILL.md frontmatter 之后的 Markdown 正文，原样保留换行结构，
            仅去掉紧随开头的空行。
        qualifier: 限定名前缀，由发现器按根目录填充；多个额外目录时使用
            extra1、extra2 等编号，用于区分同名技能。
    """

    name: str
    description: str
    source: SkillSource
    root_dir: Path
    directory: Path
    body: str
    qualifier: str

    @property
    def qualified_name(self) -> str:
        """来源限定名，格式 "qualifier:name"，如 "project:code-review"。"""
        return f"{self.qualifier}:{self.name}"


@dataclass
class DiscoveryResult:
    """一次 Skill 扫描的完整结果。

    属性:
        skills: 全部通过校验的 Skill（含跨来源重名，不做覆盖）。
        issues: 诊断信息（目录不可读、缺 SKILL.md、校验失败、被过滤等）。
    """

    skills: list[Skill]
    issues: list[SkillIssue]
