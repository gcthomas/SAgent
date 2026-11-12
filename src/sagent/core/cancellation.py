"""协作式取消令牌。

引擎在执行循环的迭代边界与工具调用前主动检查令牌，
从而实现不强制中断线程的可控取消。
"""

from __future__ import annotations

import threading


class RunCancelled(Exception):
    """运行被取消时抛出的专用异常。"""


class CancelToken:
    """线程安全的协作式取消令牌。"""

    def __init__(self) -> None:
        """初始化未取消状态的令牌。"""
        self._event = threading.Event()

    def cancel(self) -> None:
        """置位取消标记。"""
        self._event.set()

    def is_cancelled(self) -> bool:
        """是否已被取消。"""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """已取消时抛出 RunCancelled。"""
        if self._event.is_set():
            raise RunCancelled("运行已取消")
