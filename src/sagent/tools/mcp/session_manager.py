"""MCP 会话管理器。

通过后台事件循环线程桥接异步 MCP SDK 与同步项目代码，管理持久化会话生命周期。
负责创建传输连接、执行 MCP 握手、保持会话存活、同步包装工具调用、优雅关闭。

所有异步操作（连接、调用、关闭）通过命令队列分发到单个 worker task 执行，
确保 anyio cancel scope 的进入和退出在同一 task 中完成。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from typing import Any

from ...config.models import MCPServerConfig
from ...observability import Span, get_logger

logger = get_logger(__name__)


class MCPSessionManager:
    """MCP 会话管理器。

    通过后台事件循环线程运行异步 MCP SDK，为同步项目代码提供同步调用接口。
    管理多个 MCP 服务器的持久化会话，支持连接、工具调用、优雅关闭。

    所有异步操作通过命令队列分发到单个 worker task 执行，
    确保 anyio cancel scope 的进入和退出在同一 task 中完成。

    使用方式：
        manager = MCPSessionManager()
        manager.start()
        tools = manager.connect_server(config)  # 同步调用
        result = manager.call_tool("server", "tool", {...})  # 同步调用
        manager.shutdown()
    """

    def __init__(self) -> None:
        # 后台事件循环与线程
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # 服务器名 -> ClientSession（存活会话）
        self._sessions: dict[str, Any] = {}
        # 服务器名 -> call_timeout（工具调用超时）
        self._call_timeouts: dict[str, float] = {}
        # 上下文管理器列表 [(connect_timeout, transport_cm, session_cm), ...] 用于优雅关闭
        self._contexts: list[tuple[float, Any, Any]] = []
        # 命令队列与 worker task
        self._cmd_queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        """启动后台事件循环线程（daemon 线程）。"""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-event-loop")
        self._thread.start()
        # 在事件循环中初始化命令队列和 worker task
        init_future = asyncio.run_coroutine_threadsafe(self._init_worker(), self._loop)
        init_future.result(timeout=5.0)

    def _run_loop(self) -> None:
        """在后台线程中运行事件循环。"""
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _init_worker(self) -> None:
        """初始化命令队列并启动 worker task。"""
        self._cmd_queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self) -> None:
        """单 worker task，处理所有 MCP 操作。

        所有 __aenter__/__aexit__ 调用都在此 task 中执行，
        满足 anyio 要求 cancel scope 在同一 task 中进出的约束。
        """
        assert self._cmd_queue is not None
        while True:
            cmd_type, payload, result_future = await self._cmd_queue.get()
            if cmd_type == "shutdown":
                try:
                    await self._shutdown_all()
                    result_future.set_result(None)
                except Exception as exc:
                    result_future.set_exception(exc)
                break
            try:
                if cmd_type == "connect":
                    result = await self._connect(payload)
                    result_future.set_result(result)
                elif cmd_type == "call":
                    server_name, tool_name, arguments = payload
                    result = await self._call_tool(server_name, tool_name, arguments)
                    result_future.set_result(result)
            except Exception as exc:
                result_future.set_exception(exc)

    def _submit(self, cmd_type: str, payload: Any, timeout: float) -> Any:
        """提交命令到 worker task 并同步等待结果。

        通过命令队列将操作分发到 worker task，确保在同一 task 中执行。
        使用 concurrent.futures.Future 实现跨线程结果传递与超时等待。
        """
        assert self._loop is not None and self._cmd_queue is not None
        result_future: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(
            self._cmd_queue.put_nowait, (cmd_type, payload, result_future)
        )
        return result_future.result(timeout=timeout)

    def connect_server(self, config: MCPServerConfig) -> list:
        """同步连接 MCP 服务器并发现工具。

        通过命令队列将连接任务提交到 worker task，同步等待结果。
        连接失败或超时返回空列表，不抛异常。

        参数:
            config: MCP 服务器配置

        返回:
            MCP Tool 对象列表（连接失败时为空列表）
        """
        with Span("mcp.connect") as span:
            span.set_attribute("mcp.server.name", config.name)
            span.set_attribute("mcp.transport", config.transport)
            logger.info(
                "MCP 服务器连接",
                extra={"event": "mcp_connect_start", "server": config.name, "transport": config.transport},
            )
            try:
                tools = self._submit("connect", config, config.connect_timeout)
                logger.info(
                    "MCP 服务器连接完成",
                    extra={"event": "mcp_connect_done", "server": config.name, "tool_count": len(tools)},
                )
                return tools
            except TimeoutError:
                span.set_attribute("error.type", "timeout")
                span.set_status("error")
                logger.error(
                    "MCP 服务器连接超时",
                    extra={"event": "mcp_connect_error", "server": config.name, "error": "timeout"},
                )
                return []
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status("error")
                logger.error(
                    "MCP 服务器连接失败",
                    extra={"event": "mcp_connect_error", "server": config.name, "error": str(exc)},
                )
                return []

    async def _connect(self, config: MCPServerConfig) -> list:
        """异步连接 MCP 服务器。

        手动管理异步上下文管理器生命周期，保持会话存活。
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamable_http_client

        transport_cm = None
        session_cm = None
        try:
            # 按传输方式创建传输层
            if config.transport == "stdio":
                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                    cwd=config.cwd or None,
                )
                transport_cm = stdio_client(params)
            elif config.transport == "sse":
                transport_cm = sse_client(config.url)
            elif config.transport == "streamable_http":
                transport_cm = streamable_http_client(config.url)
            else:
                raise ValueError(f"不支持的传输方式: {config.transport}")

            # 手动进入传输层上下文（不使用 async with，保持连接存活）
            read, write = await transport_cm.__aenter__()

            # 创建并进入 ClientSession 上下文
            session_cm = ClientSession(read, write)
            session = await session_cm.__aenter__()

            # MCP 握手
            await session.initialize()

            # 发现工具
            result = await session.list_tools()

            # 存储会话与上下文供后续使用
            self._sessions[config.name] = session
            self._call_timeouts[config.name] = config.call_timeout
            self._contexts.append((config.connect_timeout, transport_cm, session_cm))

            return result.tools
        except Exception:
            # 连接失败时清理已进入的上下文
            if session_cm is not None:
                try:
                    await session_cm.__aexit__(None, None, None)
                except Exception as exc:
                    logger.warning(
                        "清理 MCP 会话上下文失败",
                        extra={"event": "mcp_cleanup_error", "server": config.name,
                               "context": "session", "error": str(exc)},
                    )
            if transport_cm is not None:
                try:
                    await transport_cm.__aexit__(None, None, None)
                except Exception as exc:
                    logger.warning(
                        "清理 MCP 传输层上下文失败",
                        extra={"event": "mcp_cleanup_error", "server": config.name,
                               "context": "transport", "error": str(exc)},
                    )
            raise

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """同步调用 MCP 工具。

        通过命令队列将调用任务提交到 worker task，同步等待结果。
        超时或失败返回以"错误:"开头的字符串，不抛异常。

        参数:
            server_name: MCP 服务器名称
            tool_name: MCP 工具名称（不带前缀的原始名称）
            arguments: 工具参数字典

        返回:
            工具执行结果字符串；错误时返回以"错误:"开头的字符串
        """
        with Span("mcp.tool_call") as span:
            span.set_attribute("mcp.server.name", server_name)
            span.set_attribute("mcp.tool.name", tool_name)
            logger.info(
                "MCP 工具调用",
                extra={
                    "event": "mcp_tool_call",
                    "server": server_name,
                    "tool": tool_name,
                    "tool_args": arguments,
                },
            )
            timeout = self._call_timeouts.get(server_name, 60.0)
            start = time.perf_counter()
            try:
                result = self._submit("call", (server_name, tool_name, arguments), timeout)
                latency_ms = round((time.perf_counter() - start) * 1000, 1)
                span.set_attribute("mcp.latency_ms", latency_ms)
                span.set_attribute("mcp.result_length", len(result))
                logger.info(
                    "MCP 工具调用完成",
                    extra={
                        "event": "mcp_tool_result",
                        "server": server_name,
                        "tool": tool_name,
                        "result_length": len(result),
                        "latency_ms": latency_ms,
                    },
                )
                return result
            except TimeoutError:
                span.set_attribute("error.type", "timeout")
                span.set_status("error")
                return f"错误: MCP 工具 '{server_name}.{tool_name}' 调用超时"
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status("error")
                return f"错误: MCP 工具 '{server_name}.{tool_name}' 调用失败: {exc}"

    async def _call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """异步调用 MCP 工具并提取文本结果。"""
        session = self._sessions.get(server_name)
        if session is None:
            return f"错误: MCP 服务器 '{server_name}' 未连接"
        result = await session.call_tool(tool_name, arguments)
        return self._extract_text(result)

    @staticmethod
    def _extract_text(result: Any) -> str:
        """从 MCP CallToolResult 提取文本内容。

        遍历 content 列表，提取所有 TextContent 的 text 字段并拼接。
        非文本内容跳过，无文本内容时返回占位提示。
        """
        texts: list[str] = []
        content = getattr(result, "content", None) or []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
        if texts:
            return "\n".join(texts)
        return "[无文本结果]"

    def shutdown(self) -> None:
        """关闭所有 MCP 会话与子进程，停止后台事件循环。"""
        assert self._loop is not None
        # 关闭超时与连接超时对等：取所有已连接服务器的 connect_timeout 之和
        shutdown_timeout = sum(t for t, _, _ in self._contexts) or 10.0
        # 提交关闭命令到 worker task（在同一 task 中清理所有上下文）
        try:
            self._submit("shutdown", None, shutdown_timeout)
        except TimeoutError:
            logger.warning(
                "MCP 关闭任务超时",
                extra={"event": "mcp_shutdown_error", "error": "timeout",
                       "timeout": shutdown_timeout},
            )
        except Exception as exc:
            logger.warning(
                "MCP 关闭任务异常",
                extra={"event": "mcp_shutdown_error", "error": str(exc)},
            )
        # 停止事件循环
        self._loop.call_soon_threadsafe(self._loop.stop)
        # 等待线程结束
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None

    async def _shutdown_all(self) -> None:
        """异步关闭所有会话上下文（逆序退出）。

        每个上下文的关闭超时与连接超时对等，防止单个服务器挂起阻塞整体关闭。
        """
        for connect_timeout, transport_cm, session_cm in reversed(self._contexts):
            try:
                await asyncio.wait_for(
                    session_cm.__aexit__(None, None, None), timeout=connect_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "关闭 MCP 会话上下文超时",
                    extra={"event": "mcp_shutdown_context_timeout",
                           "context": "session", "timeout": connect_timeout},
                )
            except Exception as exc:
                logger.warning(
                    "关闭 MCP 会话上下文失败",
                    extra={"event": "mcp_shutdown_context_error",
                           "context": "session", "error": str(exc)},
                )
            try:
                await asyncio.wait_for(
                    transport_cm.__aexit__(None, None, None), timeout=connect_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "关闭 MCP 传输层上下文超时",
                    extra={"event": "mcp_shutdown_context_timeout",
                           "context": "transport", "timeout": connect_timeout},
                )
            except Exception as exc:
                logger.warning(
                    "关闭 MCP 传输层上下文失败",
                    extra={"event": "mcp_shutdown_context_error",
                           "context": "transport", "error": str(exc)},
                )
        self._contexts.clear()
        self._sessions.clear()
        self._call_timeouts.clear()
