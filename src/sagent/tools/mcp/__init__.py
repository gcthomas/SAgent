"""MCP 工具提供者子包。

提供 MCP 客户端集成：会话管理、工具包装、工具过滤与提供者实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .filtering import ToolFilter
from .provider import MCPToolProvider
from .session_manager import MCPSessionManager
from .tool import MCPTool

if TYPE_CHECKING:
    from ...config.models import MCPConfig


def build_mcp_providers(config: MCPConfig, session_manager: MCPSessionManager) -> list[MCPToolProvider]:
    """遍历 enabled 服务器创建 MCPToolProvider 列表。

    参数:
        config: MCP 配置
        session_manager: MCP 会话管理器

    返回:
        MCPToolProvider 列表（仅包含 enabled=true 的服务器）
    """
    providers: list[MCPToolProvider] = []
    for server_config in config.servers:
        if server_config.enabled:
            providers.append(MCPToolProvider(server_config, session_manager))
    return providers


__all__ = [
    "MCPToolProvider",
    "MCPTool",
    "MCPSessionManager",
    "ToolFilter",
    "build_mcp_providers",
]
