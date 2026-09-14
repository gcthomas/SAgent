"""同步终端输入接口，确保超时取消完成后才交还输入所有权。"""

from __future__ import annotations

import asyncio
from types import TracebackType

from prompt_toolkit import PromptSession
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.output import Output, create_output


class TerminalInput:
    """管理输入会话和事件循环；外部注入的输入对象由调用方关闭。"""

    def __init__(self, *, input: Input | None = None, output: Output | None = None) -> None:
        self._owns_input = input is None
        self._input = create_input() if input is None else input
        self._loop = asyncio.new_event_loop()
        self._closed = False
        try:
            self._session: PromptSession[str] = PromptSession(
                input=self._input,
                output=create_output() if output is None else output,
            )
        except BaseException:
            self.close()
            raise

    async def _read(self, prompt: str, timeout: float | None) -> str | KeyboardInterrupt:
        # KeyboardInterrupt 必须在子任务内转换，避免事件循环提前退出清理流程。
        async def prompt_value() -> str | KeyboardInterrupt:
            try:
                return await self._session.prompt_async(prompt)
            except KeyboardInterrupt as exc:
                return exc

        result = await asyncio.wait_for(prompt_value(), timeout)
        if isinstance(result, KeyboardInterrupt):
            return result
        return result

    def read(self, prompt: str, timeout: float | None = None) -> str:
        """读取一行；超时抛出 asyncio.TimeoutError，EOF 和中断原样传递。"""
        if self._closed:
            raise RuntimeError("终端输入已关闭")
        task = self._loop.create_task(self._read(prompt, timeout))
        try:
            result = self._loop.run_until_complete(task)
        finally:
            if not task.done():
                task.cancel()
                self._loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        if isinstance(result, KeyboardInterrupt):
            raise result
        return result

    def close(self) -> None:
        """释放自有输入与事件循环，可重复调用。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        finally:
            try:
                self._loop.close()
            finally:
                if self._owns_input:
                    self._input.close()

    def __enter__(self) -> TerminalInput:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
