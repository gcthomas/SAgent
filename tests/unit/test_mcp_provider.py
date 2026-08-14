"""MCPToolProvider 提供者单元测试。

测试 provide_tools 的过滤逻辑与名称前缀，使用 fake session_manager 返回预设工具列表。
"""

from __future__ import annotations

from typing import Any

from sagent.config.models import MCPServerConfig, ToolFilterConfig
from sagent.tools.mcp.provider import MCPToolProvider


class FakeMCPTool:
    """模拟 MCP SDK 的 Tool 对象。"""

    def __init__(self, name: str, description: str = "", input_schema: dict[str, Any] | None = None):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {}


class FakeSessionManager:
    """模拟 MCPSessionManager，返回预设工具列表。"""

    def __init__(self, tools: list[Any] | None = None):
        self._tools = tools if tools is not None else []
        self.connect_calls: list[Any] = []

    def connect_server(self, config: MCPServerConfig) -> list[Any]:
        self.connect_calls.append(config)
        return self._tools


class TestMCPToolProviderNoFilter:
    """无过滤配置时所有工具通过。"""

    def test_all_tools_registered(self):
        tools = [
            FakeMCPTool("read_file", "Read", {"type": "object"}),
            FakeMCPTool("write_file", "Write", {"type": "object"}),
        ]
        sm = FakeSessionManager(tools)
        config = MCPServerConfig(name="filesystem", command="test")
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert len(result) == 2
        assert result[0].name == "mcp_filesystem_read_file"
        assert result[1].name == "mcp_filesystem_write_file"

    def test_tool_descriptions_preserved(self):
        tools = [FakeMCPTool("read_file", "Read a file", {})]
        sm = FakeSessionManager(tools)
        config = MCPServerConfig(name="fs", command="test")
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert result[0].description == "Read a file"

    def test_tool_schemas_preserved(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        tools = [FakeMCPTool("read_file", "Read", schema)]
        sm = FakeSessionManager(tools)
        config = MCPServerConfig(name="fs", command="test")
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert result[0].to_openai_schema()["function"]["parameters"] == schema


class TestMCPToolProviderAllowFilter:
    """白名单过滤。"""

    def test_only_allowlisted_tools(self):
        tools = [
            FakeMCPTool("read_file"),
            FakeMCPTool("write_file"),
            FakeMCPTool("delete_file"),
        ]
        sm = FakeSessionManager(tools)
        config = MCPServerConfig(
            name="fs",
            command="test",
            tool_filter=ToolFilterConfig(allow=["read_file", "write_file"]),
        )
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert len(result) == 2
        names = [t.name for t in result]
        assert "mcp_fs_read_file" in names
        assert "mcp_fs_write_file" in names
        assert "mcp_fs_delete_file" not in names


class TestMCPToolProviderDenyFilter:
    """黑名单过滤。"""

    def test_denylisted_tools_excluded(self):
        tools = [
            FakeMCPTool("read_file"),
            FakeMCPTool("write_file"),
            FakeMCPTool("delete_file"),
        ]
        sm = FakeSessionManager(tools)
        config = MCPServerConfig(
            name="fs",
            command="test",
            tool_filter=ToolFilterConfig(deny=["delete_file"]),
        )
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert len(result) == 2
        names = [t.name for t in result]
        assert "mcp_fs_read_file" in names
        assert "mcp_fs_write_file" in names
        assert "mcp_fs_delete_file" not in names


class TestMCPToolProviderCombinedFilter:
    """白名单+黑名单组合过滤。"""

    def test_whitelist_then_exclude_deny(self):
        tools = [
            FakeMCPTool("a"),
            FakeMCPTool("b"),
            FakeMCPTool("c"),
            FakeMCPTool("d"),
        ]
        sm = FakeSessionManager(tools)
        config = MCPServerConfig(
            name="srv",
            command="test",
            tool_filter=ToolFilterConfig(allow=["a", "b", "c"], deny=["b"]),
        )
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert len(result) == 2
        names = [t.name for t in result]
        assert "mcp_srv_a" in names
        assert "mcp_srv_c" in names
        assert "mcp_srv_b" not in names
        assert "mcp_srv_d" not in names


class TestMCPToolProviderConnectionFailure:
    """连接失败时返回空列表。"""

    def test_empty_tools_returns_empty_list(self):
        sm = FakeSessionManager([])  # 空列表表示连接失败
        config = MCPServerConfig(name="failed", command="test")
        provider = MCPToolProvider(config, sm)
        result = provider.provide_tools()
        assert result == []

    def test_no_exception_raised(self):
        sm = FakeSessionManager([])
        config = MCPServerConfig(name="failed", command="test")
        provider = MCPToolProvider(config, sm)
        # 确保不抛异常
        try:
            provider.provide_tools()
        except Exception:
            assert False, "provide_tools 不应在连接失败时抛异常"
