"""MCP 工具包装。

将单个 MCP 工具包装为本地 Tool 实例，覆写 schema 生成与参数校验以适配 MCP 的原生 JSON schema。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..base import Tool


class MCPTool(Tool):
    """MCP 工具包装类。

    将 MCP 服务器暴露的单个工具包装为 SAgent 本地 Tool 实例。
    覆写 to_openai_schema() 使用原始 MCP JSON schema（不依赖 pydantic model_json_schema）。
    覆写 validate_args() 直接返回原始 dict（MCP 服务端负责校验）。
    工具名强制添加 mcp_{server}_{tool} 前缀。

    参数:
        tool_name: MCP 工具原始名称（不带前缀）
        description: 工具描述
        input_schema: MCP 工具的原始 JSON schema（dict）
        session_manager: MCPSessionManager 实例
        server_name: 所属 MCP 服务器名称
    """

    def __init__(
        self,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        session_manager: Any,
        server_name: str,
    ) -> None:
        # 强制添加 mcp_{server}_{tool} 前缀
        self.name = f"mcp_{server_name}_{tool_name}"
        self.description = description or ""
        self._input_schema = input_schema or {}
        self._session_manager = session_manager
        self._server_name = server_name
        self._tool_name = tool_name

    def to_openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 所需的 tool schema。

        直接使用 MCP 原始 JSON schema，不依赖 pydantic model_json_schema。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._input_schema,
            },
        }

    def validate_args(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """直接返回原始 dict，MCP 服务端负责参数校验。"""
        return arguments

    def run(self, args: dict[str, Any]) -> str:
        """执行 MCP 工具调用，委托 session_manager.call_tool()。

        参数:
            args: 工具参数字典（由 validate_args 返回的原始 dict）
        """
        return self._session_manager.call_tool(self._server_name, self._tool_name, args)
