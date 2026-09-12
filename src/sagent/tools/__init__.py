"""工具系统公开接口。"""

from ..permissions import PermissionEnforcer
from .base import Tool, ToolProvider
from .file_tools import ReadFileTool, WriteFileTool
from .memory_tool import AddMemoryTool, RemoveMemoryTool, ReplaceMemoryTool
from .registry import ToolRegistry
from .shell_tool import ShellTool


def build_default_registry(permission: PermissionEnforcer | None = None) -> ToolRegistry:
    """构建并返回包含内置工具的默认注册表。

    参数:
        permission: 可选的权限执行器；注入后每次工具执行前统一授权
            （缺省 None 保持全自动执行，向后兼容）。
    """
    registry = ToolRegistry(permission=permission)
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(ShellTool())
    return registry


__all__ = [
    "Tool",
    "ToolProvider",
    "ToolRegistry",
    "ReadFileTool",
    "WriteFileTool",
    "ShellTool",
    "AddMemoryTool",
    "ReplaceMemoryTool",
    "RemoveMemoryTool",
    "build_default_registry",
]
