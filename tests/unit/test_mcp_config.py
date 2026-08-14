"""MCP 配置模型单元测试。

测试 MCPConfig / MCPServerConfig / ToolFilterConfig 的默认值与基本校验。
"""

from __future__ import annotations

import pytest
from sagent.config.models import MCPServerConfig, ToolFilterConfig, MCPConfig


class TestToolFilterConfig:
    """工具过滤配置默认值。"""

    def test_defaults(self):
        config = ToolFilterConfig()
        assert config.allow == []
        assert config.deny == []

    def test_with_values(self):
        config = ToolFilterConfig(allow=["a", "b"], deny=["c"])
        assert config.allow == ["a", "b"]
        assert config.deny == ["c"]


class TestMCPServerConfig:
    """MCP 服务器配置默认值。"""

    def test_defaults(self):
        config = MCPServerConfig(name="test", command="test")
        assert config.name == "test"
        assert config.transport == "stdio"
        assert config.command == "test"
        assert config.args == []
        assert config.env == {}
        assert config.cwd == ""
        assert config.url == ""
        assert config.enabled is True
        assert isinstance(config.tool_filter, ToolFilterConfig)
        assert config.connect_timeout == 30.0
        assert config.call_timeout == 60.0

    def test_stdio_transport(self):
        config = MCPServerConfig(
            name="filesystem",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
            env={"FOO": "bar"},
            cwd="/tmp",
        )
        assert config.command == "npx"
        assert config.args == ["-y", "@modelcontextprotocol/server-filesystem"]
        assert config.env == {"FOO": "bar"}
        assert config.cwd == "/tmp"

    def test_sse_transport(self):
        config = MCPServerConfig(name="remote", transport="sse", url="http://localhost:8080/sse")
        assert config.transport == "sse"
        assert config.url == "http://localhost:8080/sse"

    def test_streamable_http_transport(self):
        config = MCPServerConfig(
            name="http", transport="streamable_http", url="http://localhost:8080/mcp"
        )
        assert config.transport == "streamable_http"
        assert config.url == "http://localhost:8080/mcp"

    def test_disabled(self):
        config = MCPServerConfig(name="disabled", command="test", enabled=False)
        assert config.enabled is False

    def test_custom_timeouts(self):
        config = MCPServerConfig(name="test", command="test", connect_timeout=5.0, call_timeout=15.0)
        assert config.connect_timeout == 5.0
        assert config.call_timeout == 15.0

    def test_custom_tool_filter(self):
        config = MCPServerConfig(
            name="test",
            command="test",
            tool_filter=ToolFilterConfig(allow=["a"], deny=["b"]),
        )
        assert config.tool_filter.allow == ["a"]
        assert config.tool_filter.deny == ["b"]


class TestMCPServerConfigValidator:
    """MCPServerConfig 传输方式必填字段校验。"""

    def test_stdio_without_command_raises(self):
        with pytest.raises(ValueError, match="command"):
            MCPServerConfig(name="test")

    def test_sse_without_url_raises(self):
        with pytest.raises(ValueError, match="url"):
            MCPServerConfig(name="test", transport="sse")

    def test_streamable_http_without_url_raises(self):
        with pytest.raises(ValueError, match="url"):
            MCPServerConfig(name="test", transport="streamable_http")

    def test_stdio_with_command_passes(self):
        config = MCPServerConfig(name="test", command="npx")
        assert config.command == "npx"

    def test_sse_with_url_passes(self):
        config = MCPServerConfig(name="test", transport="sse", url="http://localhost:8080")
        assert config.url == "http://localhost:8080"


class TestMCPConfig:
    """MCP 顶层配置默认值。"""

    def test_defaults(self):
        config = MCPConfig()
        assert config.enabled is False
        assert config.servers == []

    def test_enabled_with_servers(self):
        config = MCPConfig(
            enabled=True,
            servers=[
                MCPServerConfig(name="fs", command="npx"),
                MCPServerConfig(name="remote", transport="sse", url="http://localhost:8080"),
            ],
        )
        assert config.enabled is True
        assert len(config.servers) == 2
        assert config.servers[0].name == "fs"
        assert config.servers[1].name == "remote"


class TestAppConfigMCPField:
    """AppConfig 中 mcp 字段缺省可用。"""

    def test_default_mcp_disabled(self):
        from sagent.config.models import AppConfig, LLMConfig

        config = AppConfig(llm=LLMConfig(model="test"))
        assert config.mcp.enabled is False
        assert config.mcp.servers == []
