"""SKILL.md frontmatter 解析与校验。

负责把一个 SKILL.md 文件解析为 Skill 实体：
- 使用 utf-8-sig 读取（兼容带 BOM 的 UTF-8 文件）；
- 提取首个独占一行 ``---`` 与下一个独占一行 ``---`` 之间的 YAML frontmatter；
- 校验必填字段 name / description、名称合法性、字段长度上限与 metadata 类型；
- 提取 frontmatter 之后的 Markdown 正文（仅去掉紧随开头的空行，其余原样保留）。

解析或校验失败时抛出 SkillParseError，消息为中文原因；
可选字段 compatibility、license、allowed-tools 与未知字段不报错。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..observability import get_logger
from .models import Skill, SkillSource

logger = get_logger(__name__)

# name 合法格式：小写字母、数字与连字符，且不以连字符开头或结尾、无连续连字符
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# frontmatter 起止标记，必须独占一行
_FRONTMATTER_DELIMITER = "---"

# validate_skill_name 默认使用的 name 长度上限
SKILL_NAME_MAX_CHARS = 64


class SkillParseError(Exception):
    """SKILL.md 解析或校验失败异常，异常消息为中文原因。"""


def validate_skill_name(
    name: str,
    directory_name: str,
    max_name_chars: int = SKILL_NAME_MAX_CHARS,
) -> str | None:
    """校验 Skill 名称合法性。

    规则：仅允许小写字母、数字、连字符（无连续连字符、不以连字符开头或结尾），
    长度不超过 max_name_chars，且必须与所在目录名一致。

    参数:
        name: frontmatter 中的 name 字段值。
        directory_name: Skill 所在目录名。
        max_name_chars: name 长度上限，默认 SKILL_NAME_MAX_CHARS。

    返回:
        合法返回 None；非法返回中文原因字符串。
    """
    if not _NAME_PATTERN.match(name):
        return (
            f"name 非法: {name!r}，仅允许小写字母、数字与连字符，"
            "且不以连字符开头或结尾、无连续连字符"
        )
    if len(name) > max_name_chars:
        return f"name 超长: 长度 {len(name)} 超过上限 {max_name_chars}"
    if name != directory_name:
        return f"name 与目录名不一致: name={name!r}，目录名={directory_name!r}"
    return None


def parse_skill_md(
    path: Path,
    *,
    max_name_chars: int = SKILL_NAME_MAX_CHARS,
    max_description_chars: int = 1024,
) -> Skill:
    """解析一个 SKILL.md 文件并返回 Skill。

    source 与 root_dir 由调用方（发现器）填充，此处先填 PROJECT 与文件父目录占位。
    解析或校验失败抛 SkillParseError，单个文件失败不影响其他文件。

    参数:
        path: SKILL.md 文件路径。
        max_name_chars: name 长度上限，默认 SKILL_NAME_MAX_CHARS（64）。
        max_description_chars: description 长度上限，默认 1024。

    返回:
        解析得到的 Skill 实例（source 为占位值，需调用方覆盖）。

    异常:
        SkillParseError: 文件不可读、缺少或未闭合 frontmatter、YAML 解析失败、
        必填字段缺失或非法、字段长度超限、metadata 类型错误。
    """
    try:
        # utf-8-sig 兼容带 BOM 的 UTF-8 文件
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillParseError(f"读取 SKILL.md 失败: {exc}") from exc

    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        raise SkillParseError("缺少 frontmatter：文件必须以独占一行的 --- 开头")

    # 查找下一个独占一行的结束标记
    end_index = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_DELIMITER:
            end_index = index
            break
    if end_index == -1:
        raise SkillParseError("frontmatter 未闭合：缺少独占一行的结束标记 ---")

    frontmatter_text = "\n".join(lines[1:end_index])
    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"frontmatter 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillParseError("frontmatter 根节点必须是键值映射")

    name = _require_string(data, "name")
    description = _require_string(data, "description")

    # name 长度上限由 validate_skill_name 统一校验（支持调用方收紧）
    invalid_reason = validate_skill_name(
        name,
        directory_name=path.parent.name,
        max_name_chars=max_name_chars,
    )
    if invalid_reason is not None:
        raise SkillParseError(invalid_reason)

    if len(description) > max_description_chars:
        raise SkillParseError(
            f"description 超长: 长度 {len(description)} 超过上限 {max_description_chars}"
        )

    # 可选字段 metadata：存在时必须是映射
    if "metadata" in data and not isinstance(data["metadata"], dict):
        raise SkillParseError("metadata 必须是键值映射")

    body = _extract_body(lines[end_index + 1 :], keep_trailing_newline=text.endswith("\n"))
    logger.debug(
        "SKILL.md 解析成功",
        extra={"event": "skill_parsed", "skill_path": str(path), "skill_name": name},
    )
    return Skill(
        name=name,
        description=description,
        # 占位值：由调用方（发现器）覆盖为真实来源与根目录
        source=SkillSource.PROJECT,
        root_dir=path.parent,
        directory=path.parent,
        body=body,
        qualifier=SkillSource.PROJECT.value,
    )


def _require_string(data: dict[str, Any], key: str) -> str:
    """从 frontmatter 映射中取必填的非空字符串字段。

    参数:
        data: frontmatter 解析出的映射。
        key: 字段名。

    返回:
        字段值。

    异常:
        SkillParseError: 字段缺失、不是字符串或为空字符串。
    """
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillParseError(f"缺少必填字段 {key} 或其不是非空字符串")
    return value


def _extract_body(body_lines: list[str], *, keep_trailing_newline: bool) -> str:
    """提取 frontmatter 结束标记之后的正文。

    仅去掉紧随开头的空行（含纯空白行），其余换行结构与内容原样保留；
    正文非空且原文件以换行结尾时保留结尾换行符。

    参数:
        body_lines: frontmatter 结束标记之后的行列表。
        keep_trailing_newline: 原文件是否以换行符结尾。

    返回:
        正文文本，允许为空字符串。
    """
    start = 0
    while start < len(body_lines) and body_lines[start].strip() == "":
        start += 1
    body = "\n".join(body_lines[start:])
    if body and keep_trailing_newline:
        body += "\n"
    return body
