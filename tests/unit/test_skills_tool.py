"""load_skill 工具单元测试。

覆盖工具元信息（name/description/args_schema）、OpenAI function schema 输出、
run() 参数委托 SkillLoader.load。
全部离线运行，不依赖真实 LLM。
"""

from __future__ import annotations

from sagent.config.models import SkillConfig
from sagent.skills.loader import LoadSkillArgs, SkillLoader
from sagent.skills.manager import SkillManager
from sagent.tools.skill_tool import LoadSkillTool


class _FakeLoader:
    """记录 load 调用参数并返回固定文本的假加载器。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def load(self, ref: str) -> str:
        self.calls.append(ref)
        return f"loaded:{ref}"


def _make_real_loader(tmp_path) -> SkillLoader:
    """构建含一个项目级技能的真实加载器。"""
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    directory = project_skills / "code-review"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: 测试技能\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    return SkillLoader(manager)


# ========== 工具元信息与 schema ==========


def test_tool_metadata_and_openai_schema():
    """name/description/args_schema 齐全，OpenAI schema 输出 load_skill 函数名。"""
    tool = LoadSkillTool(_FakeLoader())
    assert tool.name == "load_skill"
    assert tool.description
    assert tool.args_schema is LoadSkillArgs
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "load_skill"
    assert "parameters" in schema["function"]


def test_args_schema_requires_name():
    """args_schema 的 name 字段必填，且 description 说明用途。"""
    fields = LoadSkillArgs.model_fields
    assert fields["name"].is_required()
    assert fields["name"].description


# ========== run 委托 ==========


def test_run_delegates_to_loader():
    """run() 把参数模型的 name 委托给 loader.load 并透传返回值。"""
    fake = _FakeLoader()
    tool = LoadSkillTool(fake)
    args = LoadSkillArgs(name="code-review")
    result = tool.run(args)
    assert result == "loaded:code-review"
    assert fake.calls == ["code-review"]


def test_run_with_real_loader_returns_rendered_text(tmp_path):
    """使用真实 SkillLoader 时，run() 返回含正文与资源说明的渲染文本。"""
    loader = _make_real_loader(tmp_path)
    tool = LoadSkillTool(loader)
    result = tool.run(LoadSkillArgs(name="code-review"))
    assert "[技能] project:code-review" in result
    assert "--- 技能正文 ---" in result
    assert "正文内容" in result
