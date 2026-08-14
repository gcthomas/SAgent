"""MCP 工具提供者。

连接 MCP 服务器，发现工具，经 ToolFilter 过滤后返回 MCPTool 列表。
实现 ToolProvider 接口，供 ToolRegistry.register_provider() 接入。
"""

from __future__ import annotations

from typing import Any

from ...config.models import MCPServerConfig
from ...observability import get_logger
from ..base import Tool, ToolProvider
from .filtering import ToolFilter
from .tool import MCPTool

logger = get_logger(__name__)


class MCPToolProvider(ToolProvider):
    """MCP 工具提供者。

    实现 ToolProvider 接口，在 provide_tools() 中完成连接、发现、过滤并返回 MCPTool 列表。
    会话生命周期管理（关闭）由 MCPSessionManager 独立负责。

    参数:
        config: MCP 服务器配置
        session_manager: MCPSessionManager 实例
    """

    def __init__(self, config: MCPServerConfig, session_manager: Any) -> None:
        self._config = config
        self._session_manager = session_manager

    def provide_tools(self) -> list[Tool]:
        """连接服务器、发现工具、过滤并返回 MCPTool 列表。

        连接失败时返回空列表，不抛异常。
        每个工具名强制添加 mcp_{server}_{tool} 前缀。
        """
        # 连接并发现工具
        mcp_tools = self._session_manager.connect_server(self._config)
        if not mcp_tools:
            return []

        # 构建工具过滤器
        tool_filter = ToolFilter(
            allow=self._config.tool_filter.allow,
            deny=self._config.tool_filter.deny,
        )

        # 过滤工具名
        tool_names = [t.name for t in mcp_tools]
        filtered_names = set(tool_filter.filter_tools(tool_names))

        # 为每个通过过滤的工具创建 MCPTool
        tools: list[Tool] = []
        for t in mcp_tools:
            if t.name in filtered_names:
                tool = MCPTool(
                    tool_name=t.name,
                    description=t.description or "",
                    input_schema=t.input_schema or {},
                    session_manager=self._session_manager,
                    server_name=self._config.name,
                )
                tools.append(tool)

        logger.info(
            "MCP 工具注册",
            extra={
                "event": "mcp_tools_registered",
                "server": self._config.name,
                "total": len(mcp_tools),
                "registered": len(tools),
            },
        )

        return tools
