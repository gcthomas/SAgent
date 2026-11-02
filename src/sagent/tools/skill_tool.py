"""load_skill 与 read_skill_resource 工具。

按需加载已发现技能的完整正文与资源说明，供 Agent 在任务匹配技能目录描述时调用；
read_skill_resource 仅可读取技能目录内的资源文件，路径校验由 SkillLoader 完成。
工具仅做参数委托，正文渲染、预算校验与路径逃逸防护由 SkillLoader 完成。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..skills.loader import LoadSkillArgs, ReadSkillResourceArgs, SkillLoader
from .base import Tool


class LoadSkillTool(Tool):
    """按需加载技能正文的工具（与记忆工具一致的显式注册方式）。"""

    name = "load_skill"
    description = (
        "按需加载技能的完整正文与资源说明。仅可加载已发现的技能；"
        "在任务与系统提示词中的技能目录描述匹配时先调用本工具再执行。"
    )
    args_schema = LoadSkillArgs

    def __init__(self, loader: SkillLoader) -> None:
        """初始化工具。

        参数:
            loader: Skill 正文加载器。
        """
        self._loader = loader

    def run(self, args: BaseModel) -> str:
        """执行工具逻辑，委托 SkillLoader.load 完成加载与渲染。

        参数:
            args: 已通过 pydantic 校验的参数模型实例（LoadSkillArgs）。
        """
        return self._loader.load(args.name)


class ReadSkillResourceTool(Tool):
    """读取技能目录内资源文件的受限工具（与记忆工具一致的显式注册方式）。"""

    name = "read_skill_resource"
    description = (
        "读取已发现技能目录内的资源文件内容（如 references/ 下的参考文档）。"
        "仅可读取该技能目录内的普通文件；路径越出技能目录、包含符号链接或 junction、"
        "目标不存在或是目录时将被拒绝。"
    )
    args_schema = ReadSkillResourceArgs

    def __init__(self, loader: SkillLoader) -> None:
        """初始化工具。

        参数:
            loader: Skill 正文加载器，同时承担资源路径校验与读取。
        """
        self._loader = loader

    def run(self, args: BaseModel) -> str:
        """执行工具逻辑，委托 SkillLoader.load_resource 完成读取与校验。

        参数:
            args: 已通过 pydantic 校验的参数模型实例（ReadSkillResourceArgs）。
        """
        return self._loader.load_resource(args.name, args.path)
