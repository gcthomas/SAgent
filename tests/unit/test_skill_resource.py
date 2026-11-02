"""read_skill_resource 工具与 SkillLoader.load_resource 单元测试。

覆盖正常读取、目录逃逸拒绝、绝对路径拒绝、符号链接拒绝、
技能目录上级链接放行、目标不存在、目标是目录、内容超资源预算、
非 UTF-8 文本、同名技能歧义引用，以及工具元信息与 run() 委托。
全部使用 tmp_path 做隔离，不依赖真实 LLM。
"""

from __future__ import annotations

import os

import pytest

from sagent.config.models import SkillConfig
from sagent.skills.loader import ReadSkillResourceArgs, SkillLoader
from sagent.skills.manager import SkillManager
from sagent.tools.skill_tool import ReadSkillResourceTool


def _make_skill(root, name: str, description: str = "测试技能") -> object:
    """在根目录下创建一个合法的 Skill 子目录，附带 references 资源文件。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n技能正文。\n",
        encoding="utf-8",
    )
    references = directory / "references"
    references.mkdir(exist_ok=True)
    (references / "api.md").write_text("资源文件内容第一行。\n", encoding="utf-8")
    return directory


def _make_loader(tmp_path, **kwargs) -> tuple[SkillLoader, object]:
    """构建项目级技能目录下含一个技能的加载器，返回 (loader, skill 目录)。"""
    project_root = tmp_path / "project"
    project_skills = project_root / ".sagent" / "skills"
    directory = _make_skill(project_skills, "code-review")
    manager = SkillManager(
        SkillConfig(),
        project_root=project_root,
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager, **kwargs)
    return loader, directory


# ========== 正常读取 ==========


def test_load_resource_returns_file_content(tmp_path):
    """正常路径读取技能目录内资源文件，返回含限定名与文件内容的文本。"""
    loader, skill_dir = _make_loader(tmp_path)
    result = loader.load_resource("code-review", "references/api.md")
    assert result.startswith("[技能资源] project:code-review")
    assert "references/api.md" in result
    assert "资源文件内容第一行。" in result


# ========== 路径逃逸防护 ==========


def test_load_resource_dotdot_escape_rejected(tmp_path):
    """../ 逃逸到技能目录之外时拒绝读取。"""
    loader, _ = _make_loader(tmp_path)
    result = loader.load_resource("code-review", "references/../../secret.txt")
    assert result.startswith("错误:")
    assert "越出技能目录" in result


def test_load_resource_absolute_path_rejected(tmp_path):
    """绝对路径（技能目录之外的文件）拒绝读取。"""
    loader, skill_dir = _make_loader(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("机密内容", encoding="utf-8")
    result = loader.load_resource("code-review", str(outside))
    assert result.startswith("错误:")
    assert "越出技能目录" in result


def test_load_resource_symlink_inside_skill_rejected(tmp_path):
    """技能目录内的符号链接即使指向目录内部文件也拒绝读取。"""
    loader, skill_dir = _make_loader(tmp_path)
    link = skill_dir / "references" / "link.md"
    try:
        os.symlink(str((skill_dir / "references" / "api.md").resolve()), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("当前环境无权限创建符号链接")
    result = loader.load_resource("code-review", "references/link.md")
    assert result.startswith("错误:")
    assert "符号链接或 junction" in result


def test_load_resource_allows_symlinked_ancestor_of_skill_dir(tmp_path):
    """技能目录自身的上级目录是符号链接时（如链接的项目根）仍可正常读取。"""
    real_project = tmp_path / "real-project"
    _make_skill(real_project / ".sagent" / "skills", "code-review")
    link_project = tmp_path / "link-project"
    try:
        os.symlink(str(real_project), str(link_project), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境无权限创建符号链接")
    manager = SkillManager(
        SkillConfig(),
        project_root=link_project,
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager)
    result = loader.load_resource("code-review", "references/api.md")
    assert result.startswith("[技能资源] project:code-review")
    assert "资源文件内容第一行。" in result


# ========== 目标校验 ==========


def test_load_resource_missing_file_rejected(tmp_path):
    """资源文件不存在时返回错误字符串。"""
    loader, _ = _make_loader(tmp_path)
    result = loader.load_resource("code-review", "references/no-such.md")
    assert result.startswith("错误:")
    assert "不存在或不是普通文件" in result


def test_load_resource_directory_target_rejected(tmp_path):
    """目标为目录（而非普通文件）时返回错误字符串。"""
    loader, _ = _make_loader(tmp_path)
    result = loader.load_resource("code-review", "references")
    assert result.startswith("错误:")
    assert "不存在或不是普通文件" in result


def test_load_resource_over_output_budget_rejected(tmp_path):
    """资源内容超过资源预算时返回错误，不返回内容。"""
    loader, skill_dir = _make_loader(tmp_path, max_resource_tokens=1)
    result = loader.load_resource("code-review", "references/api.md")
    assert result.startswith("错误:")
    assert "资源预算" in result
    assert "资源文件内容第一行。" not in result


def test_load_resource_binary_file_rejected(tmp_path):
    """资源文件不是 UTF-8 文本时返回可读错误，而不是抛出异常。"""
    loader, skill_dir = _make_loader(tmp_path)
    binary = skill_dir / "references" / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    result = loader.load_resource("code-review", "references/blob.bin")
    assert result.startswith("错误:")
    assert "UTF-8" in result


def test_load_resource_blank_args_rejected(tmp_path):
    """空技能引用与空资源路径返回错误字符串。"""
    loader, _ = _make_loader(tmp_path)
    assert loader.load_resource("", "references/api.md").startswith("错误:")
    assert loader.load_resource("code-review", "  ").startswith("错误:")


# ========== 技能引用解析 ==========


def test_load_resource_ambiguous_ref_rejected(tmp_path):
    """同名短名命中多个技能时返回含候选列表的错误字符串。"""
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    user_skills = tmp_path / "home" / ".sagent" / "skills"
    _make_skill(project_skills, "code-review")
    _make_skill(user_skills, "code-review")
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    loader = SkillLoader(manager)
    result = loader.load_resource("code-review", "references/api.md")
    assert result.startswith("错误:")
    assert "歧义" in result
    assert "请使用来源限定名" in result


# ========== 工具封装 ==========


def test_read_skill_resource_tool_delegates_to_loader(tmp_path):
    """run() 把参数模型的 name/path 委托给 loader.load_resource。"""
    loader, _ = _make_loader(tmp_path)
    tool = ReadSkillResourceTool(loader)
    assert tool.name == "read_skill_resource"
    assert tool.args_schema is ReadSkillResourceArgs
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_skill_resource"
    fields = ReadSkillResourceArgs.model_fields
    assert fields["name"].is_required()
    assert fields["path"].is_required()

    result = tool.run(ReadSkillResourceArgs(name="code-review", path="references/api.md"))
    assert "资源文件内容第一行。" in result
