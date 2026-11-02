"""SKILL.md 解析与校验单元测试。

覆盖合法 SKILL.md（含可选字段、未知字段与 BOM）、必填字段缺失、name 非法、
name 与目录不一致、字段长度超限、frontmatter 缺失或未闭合、YAML 非法、
根节点非映射、metadata 类型错误、正文提取与 UTF-8 中文内容。
全部使用 tmp_path 做文件隔离，不依赖真实 LLM。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sagent.skills.parser import (
    SKILL_NAME_MAX_CHARS,
    SkillParseError,
    parse_skill_md,
    validate_skill_name,
)


def _write_skill_md(tmp_path: Path, directory_name: str, content: str) -> Path:
    """在临时目录下写入一个 SKILL.md 并返回其路径。"""
    directory = tmp_path / directory_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


# ========== 合法解析 ==========


def test_parse_valid_skill_with_optional_fields(tmp_path):
    """合法 SKILL.md（含可选字段与未知字段）解析成功，字段正确。"""
    content = (
        "---\n"
        "name: code-review\n"
        "description: 审查代码质量与规范\n"
        'compatibility: ">=1.0"\n'
        "metadata:\n"
        "  author: sagent\n"
        "  version: 1\n"
        "license: MIT\n"
        "allowed-tools:\n"
        "  - read_file\n"
        "unknown-field: 任意扩展值\n"
        "---\n"
        "\n"
        "# 代码审查\n"
        "\n"
        "按步骤审查代码。\n"
    )
    path = _write_skill_md(tmp_path, "code-review", content)
    skill = parse_skill_md(path)
    assert skill.name == "code-review"
    assert skill.description == "审查代码质量与规范"
    assert skill.directory == path.parent
    # source 为占位值，由发现器覆盖
    assert skill.source.value == "project"
    assert skill.qualified_name == "project:code-review"


def test_parse_supports_bom(tmp_path):
    """带 BOM 的 UTF-8 文件可以正常解析。"""
    content = "---\nname: demo\ndescription: 测试\n---\n\n正文\n"
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "SKILL.md").write_text(content, encoding="utf-8-sig")
    skill = parse_skill_md(directory / "SKILL.md")
    assert skill.name == "demo"


def test_parse_utf8_chinese_content(tmp_path):
    """UTF-8 中文内容解析正确，无乱码。"""
    content = (
        "---\n"
        "name: demo\n"
        "description: 中文描述：审查与修复\n"
        "---\n"
        "\n"
        "# 中文标题\n"
        "\n"
        "内容含中文、标点与换行。\n"
    )
    path = _write_skill_md(tmp_path, "demo", content)
    skill = parse_skill_md(path)
    assert skill.description == "中文描述：审查与修复"
    assert skill.body == "# 中文标题\n\n内容含中文、标点与换行。\n"


# ========== body 提取 ==========


def test_parse_body_strips_leading_blank_lines_only(tmp_path):
    """body 仅去掉紧随 frontmatter 的开头空行，保留其余换行结构。"""
    content = "---\nname: demo\ndescription: 测试\n---\n\n\n# 标题\n\n正文段落。\n"
    path = _write_skill_md(tmp_path, "demo", content)
    skill = parse_skill_md(path)
    assert skill.body == "# 标题\n\n正文段落。\n"


def test_parse_body_empty(tmp_path):
    """frontmatter 之后无正文时 body 为空字符串。"""
    content = "---\nname: demo\ndescription: 测试\n---\n"
    path = _write_skill_md(tmp_path, "demo", content)
    skill = parse_skill_md(path)
    assert skill.body == ""


# ========== 必填字段校验 ==========


def test_parse_missing_name(tmp_path):
    """缺少 name 时报错，消息为中文。"""
    path = _write_skill_md(tmp_path, "demo", "---\ndescription: 测试\n---\n正文\n")
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "name" in str(exc_info.value)


def test_parse_missing_description(tmp_path):
    """缺少 description 时报错。"""
    path = _write_skill_md(tmp_path, "demo", "---\nname: demo\n---\n正文\n")
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "description" in str(exc_info.value)


def test_parse_empty_description(tmp_path):
    """description 为空字符串时报错。"""
    content = '---\nname: demo\ndescription: ""\n---\n正文\n'
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError):
        parse_skill_md(path)


def test_parse_non_string_name(tmp_path):
    """name 不是字符串（如数字）时报错。"""
    content = "---\nname: 123\ndescription: 测试\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError):
        parse_skill_md(path)


# ========== name 校验 ==========


def test_parse_name_with_invalid_chars(tmp_path):
    """name 含大写、下划线等非法字符时报错。"""
    for bad_name in ("Code-Review", "code_review", "has space"):
        content = f"---\nname: {bad_name}\ndescription: 测试\n---\n正文\n"
        path = _write_skill_md(tmp_path, bad_name.replace(" ", "-"), content)
        with pytest.raises(SkillParseError):
            parse_skill_md(path)


def test_parse_name_directory_mismatch(tmp_path):
    """name 与所在目录名不一致时报错。"""
    content = "---\nname: other-name\ndescription: 测试\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "不一致" in str(exc_info.value)


def test_parse_name_too_long(tmp_path):
    """name 超过默认长度上限时报错。"""
    long_name = "a" * (SKILL_NAME_MAX_CHARS + 1)
    content = f"---\nname: {long_name}\ndescription: 测试\n---\n正文\n"
    path = _write_skill_md(tmp_path, long_name, content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "超长" in str(exc_info.value)


def test_parse_name_exceeds_custom_max(tmp_path):
    """调用方收紧 name 长度上限时按自定义上限校验。"""
    content = "---\nname: abcdef\ndescription: 测试\n---\n正文\n"
    path = _write_skill_md(tmp_path, "abcdef", content)
    with pytest.raises(SkillParseError):
        parse_skill_md(path, max_name_chars=5)


# ========== description 长度校验 ==========


def test_parse_description_too_long(tmp_path):
    """description 超过默认长度上限时报错。"""
    long_description = "描" * 1025
    content = f"---\nname: demo\ndescription: {long_description}\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "超长" in str(exc_info.value)


def test_parse_description_exceeds_custom_max(tmp_path):
    """调用方收紧 description 长度上限时按自定义上限校验。"""
    content = "---\nname: demo\ndescription: 这是一段中文描述\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError):
        parse_skill_md(path, max_description_chars=5)


# ========== frontmatter 结构校验 ==========


def test_parse_no_frontmatter(tmp_path):
    """文件不以 --- 开头时报错。"""
    path = _write_skill_md(tmp_path, "demo", "# 普通文档\n正文\n")
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "frontmatter" in str(exc_info.value)


def test_parse_empty_file(tmp_path):
    """空文件报错。"""
    path = _write_skill_md(tmp_path, "demo", "")
    with pytest.raises(SkillParseError):
        parse_skill_md(path)


def test_parse_unclosed_frontmatter(tmp_path):
    """frontmatter 缺少结束标记时报错。"""
    content = "---\nname: demo\ndescription: 测试\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "未闭合" in str(exc_info.value)


def test_parse_invalid_yaml(tmp_path):
    """YAML 解析失败时报错。"""
    content = "---\nname: [未闭合\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "frontmatter 解析失败" in str(exc_info.value)


def test_parse_yaml_root_not_mapping(tmp_path):
    """YAML 根节点不是映射（如列表）时报错。"""
    content = "---\n- a\n- b\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "映射" in str(exc_info.value)


def test_parse_metadata_not_mapping(tmp_path):
    """metadata 存在但不是映射时报错。"""
    content = "---\nname: demo\ndescription: 测试\nmetadata: 不是映射\n---\n正文\n"
    path = _write_skill_md(tmp_path, "demo", content)
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(path)
    assert "metadata" in str(exc_info.value)


# ========== validate_skill_name 直接测试 ==========


def test_validate_skill_name_accepts_valid_names():
    """合法名称返回 None。"""
    assert validate_skill_name("code-review", "code-review") is None
    assert validate_skill_name("a", "a") is None
    assert validate_skill_name("a-1-b", "a-1-b") is None


def test_validate_skill_name_rejects_invalid_names():
    """非法名称返回中文原因字符串。"""
    assert validate_skill_name("Code-Review", "Code-Review") is not None
    assert validate_skill_name("code_review", "code_review") is not None
    assert validate_skill_name("-lead", "-lead") is not None
    assert validate_skill_name("trail-", "trail-") is not None
    assert validate_skill_name("dou--ble", "dou--ble") is not None
    assert validate_skill_name("a" * 65, "a" * 65) is not None


def test_validate_skill_name_checks_directory_match():
    """name 与目录名不一致时返回原因字符串。"""
    reason = validate_skill_name("demo", "other")
    assert reason is not None
    assert "不一致" in reason


def test_validate_skill_name_respects_custom_max_chars():
    """调用方收紧长度上限时按自定义上限校验并返回超长原因。"""
    assert validate_skill_name("abcdef", "abcdef") is None
    reason = validate_skill_name("abcdef", "abcdef", max_name_chars=5)
    assert reason is not None
    assert "超长" in reason
