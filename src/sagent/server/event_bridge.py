"""同步事件到 asyncio 事件循环的桥接。

Agent 引擎在后台工作线程中同步执行，通过 ``EventHandler`` 回调产生
``AgentEvent``；SSE 响应则由事件循环协程消费。本模块提供线程安全的
``EventBridge``：工作线程只负责把事件投递到绑定的 asyncio 队列，协程侧
通过 ``events()`` 异步迭代取出，二者之间不共享业务状态。

关闭语义：
- ``close()`` 线程安全地投递哨兵，``events()`` 收到哨兵后正常结束；
- 重复 ``close()`` 幂等，只投递一次哨兵；
- 关闭后再调用 ``emit()`` 静默丢弃，不抛异常打断引擎线程；
- 事件循环已关闭时（线程退出竞态）投递失败同样丢弃，仅记录调试日志。

本模块只做事件搬运，不做任何运行状态推导；状态推导由
``stream_adapter.UIStreamAdapter`` 负责。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator

from ..core.events import AgentEvent
from ..observability import get_logger

logger = get_logger(__name__)

# 队列结束哨兵；与 AgentEvent 类型区分，保证不会误判为业务事件
_SENTINEL = object()


class EventBridge:
    """把工作线程产生的同步事件安全投递到指定 asyncio 事件循环。"""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """初始化事件桥。

        参数:
            loop: 消费事件的 asyncio 事件循环，通常为当前请求所在的循环。
        """
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False
        self._lock = threading.Lock()

    @property
    def closed(self) -> bool:
        """事件桥是否已关闭。"""
        with self._lock:
            return self._closed

    def emit(self, event: AgentEvent) -> None:
        """线程安全地投递一个事件。

        参数:
            event: Agent 产生的结构化事件。
        """
        with self._lock:
            if self._closed:
                # 关闭后的迟到事件直接丢弃，避免阻断引擎线程退出
                return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            # 事件循环已关闭：属于线程退出竞态，丢弃事件而不是打扰引擎
            logger.debug(
                "事件循环已关闭，丢弃事件",
                extra={"event": "event_bridge_loop_closed"},
                exc_info=True,
            )

    def close(self) -> None:
        """线程安全地投递结束哨兵，重复调用幂等。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, _SENTINEL)
        except RuntimeError:
            logger.debug(
                "事件循环已关闭，无法投递结束哨兵",
                extra={"event": "event_bridge_close_loop_closed"},
                exc_info=True,
            )

    async def events(self) -> AsyncIterator[AgentEvent]:
        """异步迭代事件，收到结束哨兵后返回。"""
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            yield item
