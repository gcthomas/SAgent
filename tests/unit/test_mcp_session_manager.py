"""MCPSessionManager 单元测试。

重点覆盖 stdio 传输连接时对子进程 stderr 目标（errlog）的处理：
默认 errlog 为 sys.stderr，在交互式控制台下会导致 stdio 握手永久挂起，
因此 _connect 必须显式传入非控制台的 errlog。

测试全部离线运行，通过 monkeypatch 替换 mcp SDK 的 stdio_client 与 ClientSession，
不启动真实 MCP 子进程，也不依赖真实 LLM。
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from sagent.config.models import MCPServerConfig
from sagent.tools.mcp.session_manager import MCPSessionManager


class FakeMCPTool:
    """模拟 MCP SDK 的 Tool 对象。"""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeTransportCM:
    """模拟 stdio_client 返回的传输层异步上下文管理器。"""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return ("fake-read-stream", "fake-write-stream")

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class FakeStdioClient:
    """记录 stdio_client 的调用参数，返回预设传输上下文。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.transport_cm = FakeTransportCM()

    def __call__(self, server, errlog=None):
        self.calls.append({"server": server, "errlog": errlog})
        return self.transport_cm


class FakeListToolsResult:
    """模拟 list_tools 的返回结果。"""

    def __init__(self, tools: list[Any]) -> None:
        self.tools = tools


class FakeSession:
    """模拟 ClientSession，记录握手与工具发现。"""

    def __init__(self, read, write) -> None:
        self.read = read
        self.write = write
        self.initialized = False
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> FakeListToolsResult:
        return FakeListToolsResult([FakeMCPTool("ping")])


class TestGetErrlog:
    """_get_errlog 惰性打开并复用非控制台 stderr 目标。"""

    def test_lazy_open_and_reused(self):
        manager = MCPSessionManager()
        # 初始未打开
        assert manager._errlog is None
        first = manager._get_errlog()
        second = manager._get_errlog()
        try:
            # 惰性打开后复用同一句柄
            assert first is second
            # 必须是非控制台目标，不能是 sys.stderr / sys.stdout
            assert first is not sys.stderr
            assert first is not sys.stdout
            assert not first.closed
            assert first.name == os.devnull
        finally:
            first.close()

    def test_devnull_not_console(self):
        manager = MCPSessionManager()
        errlog = manager._get_errlog()
        try:
            assert errlog.name == os.devnull
        finally:
            errlog.close()


class TestConnectStdioErrlog:
    """stdio 连接必须向 stdio_client 传入非控制台 errlog。"""

    def test_stdio_connect_passes_non_console_errlog(self, monkeypatch):
        import mcp
        import mcp.client.stdio

        fake_stdio = FakeStdioClient()
        monkeypatch.setattr(mcp.client.stdio, "stdio_client", fake_stdio)
        monkeypatch.setattr(mcp, "ClientSession", FakeSession)

        manager = MCPSessionManager()
        config = MCPServerConfig(
            name="agent-search",
            command="npx",
            args=["-y", "agent-search-mcp"],
        )
        try:
            tools = asyncio.run(manager._connect(config))
        finally:
            if manager._errlog is not None:
                manager._errlog.close()

        # 只调用一次 stdio_client
        assert len(fake_stdio.calls) == 1
        errlog = fake_stdio.calls[0]["errlog"]
        # 显式传入非控制台 errlog，绝不能是默认的 sys.stderr
        assert errlog is not None
        assert errlog is not sys.stderr
        assert errlog.name == os.devnull
        # 参数正确透传
        assert fake_stdio.calls[0]["server"].command == "npx"
        assert fake_stdio.calls[0]["server"].args == ["-y", "agent-search-mcp"]
        # 返回发现的工具
        assert len(tools) == 1
        assert tools[0].name == "ping"

    def test_stdio_connect_stores_session_and_context(self, monkeypatch):
        import mcp
        import mcp.client.stdio

        fake_stdio = FakeStdioClient()
        monkeypatch.setattr(mcp.client.stdio, "stdio_client", fake_stdio)
        monkeypatch.setattr(mcp, "ClientSession", FakeSession)

        manager = MCPSessionManager()
        config = MCPServerConfig(
            name="fs",
            command="npx",
            connect_timeout=12.0,
            call_timeout=34.0,
        )
        try:
            asyncio.run(manager._connect(config))
        finally:
            if manager._errlog is not None:
                manager._errlog.close()

        # 会话、调用超时与上下文均已记录
        assert "fs" in manager._sessions
        assert manager._call_timeouts["fs"] == 34.0
        assert len(manager._contexts) == 1
        connect_timeout, transport_cm, session_cm = manager._contexts[0]
        assert connect_timeout == 12.0
        assert transport_cm is fake_stdio.transport_cm
        assert isinstance(session_cm, FakeSession)
        assert session_cm.initialized is True

    def test_stdio_connect_reuses_single_errlog_handle(self, monkeypatch):
        import mcp
        import mcp.client.stdio

        fake_stdio = FakeStdioClient()
        monkeypatch.setattr(mcp.client.stdio, "stdio_client", fake_stdio)
        monkeypatch.setattr(mcp, "ClientSession", FakeSession)

        manager = MCPSessionManager()
        try:
            asyncio.run(manager._connect(MCPServerConfig(name="a", command="npx")))
            asyncio.run(manager._connect(MCPServerConfig(name="b", command="npx")))
        finally:
            if manager._errlog is not None:
                manager._errlog.close()

        errlogs = [call["errlog"] for call in fake_stdio.calls]
        assert len(errlogs) == 2
        assert errlogs[0] is errlogs[1]


class TestShutdownErrlog:
    """shutdown 关闭 stderr 目标句柄并复位状态。"""

    def test_shutdown_closes_errlog(self):
        manager = MCPSessionManager()
        manager.start()
        try:
            handle = manager._get_errlog()
            assert not handle.closed
            manager.shutdown()
            assert handle.closed
            assert manager._errlog is None
        finally:
            # 兜底清理，避免测试异常时残留句柄
            if manager._errlog is not None:
                manager._errlog.close()

    def test_shutdown_without_errlog_is_safe(self):
        manager = MCPSessionManager()
        manager.start()
        manager.shutdown()
        assert manager._errlog is None
