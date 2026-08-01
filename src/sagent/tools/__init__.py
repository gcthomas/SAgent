"""工具系统公开接口。"""

from .base import Tool, ToolProvider
from .file_tools import ReadFileTool, WriteFileTool
from .memory_tool import AddMemoryTool, RemoveMemoryTool, ReplaceMemoryTool
from .registry import ToolRegistry
from .shell_tool import ShellTool


def build_default_registry() -> ToolRegistry:
    """构建并返回包含内置工具的默认注册表。"""
    registry = ToolRegistry()
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
