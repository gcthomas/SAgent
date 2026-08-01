"""内置工具：长期记忆读写。

提供 add_memory / replace_memory / remove_memory 三个工具，供 LLM 在 ReAct
推理过程中自主调用，向 USER.md 或 MEMORY.md 写入记忆。三个工具均继承 Tool
基类，参数用 pydantic 模型定义 schema，执行委托 MemoryManager 对应方法。

目标文件判断约定（写入工具描述中引导 LLM 选择）：
- target="user"：用户偏好与环境信息，写入 USER.md。
- target="memory"：项目上下文与学习经验，写入 MEMORY.md。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..memory.manager import MemoryManager
from .base import Tool

# 目标文件描述片段，用于工具参数说明
_TARGET_DESC = (
    '目标记忆文件：user 对应用户偏好与环境信息（USER.md），'
    'memory 对应项目上下文与学习经验（MEMORY.md）'
)


class AddMemoryArgs(BaseModel):
    """新增记忆参数。"""

    target: Literal["user", "memory"] = Field(..., description=_TARGET_DESC)
    content: str = Field(..., description="要追加的记忆内容")


class AddMemoryTool(Tool):
    """向长期记忆追加新内容。"""

    name = "add_memory"
    description = (
        "向长期记忆文件追加一条新记忆。当本轮交互存在值得记忆的新内容时使用。"
        "用户偏好或环境信息选 target=user（写入 USER.md），"
        "项目上下文或学习经验选 target=memory（写入 MEMORY.md）。"
    )
    args_schema = AddMemoryArgs

    def __init__(self, manager: MemoryManager) -> None:
        """初始化新增记忆工具。

        参数:
            manager: 记忆管理器实例，执行委托给它。
        """
        self._manager = manager

    def run(self, args: AddMemoryArgs) -> str:
        return self._manager.add(args.target, args.content)


class ReplaceMemoryArgs(BaseModel):
    """替换记忆参数。"""

    target: Literal["user", "memory"] = Field(..., description=_TARGET_DESC)
    old: str = Field(..., description="待替换的原文（需在现有记忆中精确匹配）")
    new: str = Field(..., description="替换后的新文本")


class ReplaceMemoryTool(Tool):
    """更新长期记忆中已有内容。"""

    name = "replace_memory"
    description = (
        "更新长期记忆中已有内容：在目标文件中查找 old 文本并替换为 new。"
        "用于用户偏好或事实发生变化时更新已有记忆，而非追加新条目。"
        "未找到 old 时返回提示，不会写入。"
    )
    args_schema = ReplaceMemoryArgs

    def __init__(self, manager: MemoryManager) -> None:
        """初始化替换记忆工具。

        参数:
            manager: 记忆管理器实例，执行委托给它。
        """
        self._manager = manager

    def run(self, args: ReplaceMemoryArgs) -> str:
        return self._manager.replace(args.target, args.old, args.new)


class RemoveMemoryArgs(BaseModel):
    """删除记忆参数。"""

    target: Literal["user", "memory"] = Field(..., description=_TARGET_DESC)
    content: str = Field(..., description="要删除的记忆原文（需在现有记忆中精确匹配）")


class RemoveMemoryTool(Tool):
    """从长期记忆中删除过时内容。"""

    name = "remove_memory"
    description = (
        "从长期记忆文件中删除过时或不再相关的内容：在目标文件中查找并移除指定 content。"
        "用于记忆已失效或不再适用时清理。未找到 content 时返回提示，不会报错。"
    )
    args_schema = RemoveMemoryArgs

    def __init__(self, manager: MemoryManager) -> None:
        """初始化删除记忆工具。

        参数:
            manager: 记忆管理器实例，执行委托给它。
        """
        self._manager = manager

    def run(self, args: RemoveMemoryArgs) -> str:
        return self._manager.remove(args.target, args.content)
