"""MCPTool 工具包装单元测试。

测试 to_openai_schema、validate_args、run 方法与工具名前缀格式。
使用 fake session_manager 避免真实 MCP 连接。
"""

from __future__ import annotations

from typing import Any

from sagent.tools.mcp.tool import MCPTool


class FakeSessionManager:
    """模拟 MCPSessionManager，记录调用并返回预设结果。"""

    def __init__(self, result: str = "fake result"):
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._result = result

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((server_name, tool_name, arguments))
        return self._result


class TestMCPToolNamePrefix:
    """工具名前缀格式 mcp_{server}_{tool}。"""

    def test_name_format(self):
        sm = FakeSessionManager()
        tool = MCPTool("read_file", "Read a file", {}, sm, "filesystem")
        assert tool.name == "mcp_filesystem_read_file"

    def test_name_with_multi_word_server(self):
        sm = FakeSessionManager()
        tool = MCPTool("search", "Search", {}, sm, "my_server")
        assert tool.name == "mcp_my_server_search"


class TestMCPToolSchema:
    """to_openai_schema 使用原始 MCP JSON schema。"""

    def test_schema_uses_input_schema(self):
        sm = FakeSessionManager()
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path"}},
            "required": ["path"],
        }
        tool = MCPTool("read_file", "Read a file", schema, sm, "fs")
        result = tool.to_openai_schema()
        assert result["type"] == "function"
        assert result["function"]["name"] == "mcp_fs_read_file"
        assert result["function"]["description"] == "Read a file"
        assert result["function"]["parameters"] == schema

    def test_empty_description(self):
        sm = FakeSessionManager()
        tool = MCPTool("tool", "", {}, sm, "server")
        assert tool.description == ""

    def test_none_input_schema_becomes_empty(self):
        sm = FakeSessionManager()
        tool = MCPTool("tool", "desc", None, sm, "server")
        assert tool.to_openai_schema()["function"]["parameters"] == {}


class TestMCPToolValidateArgs:
    """validate_args 直接返回原始 dict。"""

    def test_returns_raw_dict(self):
        sm = FakeSessionManager()
        tool = MCPTool("read_file", "desc", {}, sm, "fs")
        args = {"path": "/tmp/test.txt", "encoding": "utf-8"}
        result = tool.validate_args(args)
        assert result is args  # 直接返回原始 dict 引用


class TestMCPToolRun:
    """run 方法委托 session_manager.call_tool。"""

    def test_run_delegates_to_session_manager(self):
        sm = FakeSessionManager(result="file content here")
        tool = MCPTool("read_file", "desc", {}, sm, "filesystem")
        result = tool.run({"path": "/tmp/test.txt"})
        assert result == "file content here"
        assert sm.calls == [("filesystem", "read_file", {"path": "/tmp/test.txt"})]

    def test_run_with_empty_args(self):
        sm = FakeSessionManager(result="ok")
        tool = MCPTool("ping", "Ping", {}, sm, "server")
        result = tool.run({})
        assert result == "ok"
        assert sm.calls == [("server", "ping", {})]
