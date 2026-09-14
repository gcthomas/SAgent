"""终端输入取消、恢复与资源所有权测试。"""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from sagent.terminal_input import TerminalInput


@pytest.fixture
def terminal():
    with create_pipe_input() as pipe:
        with TerminalInput(input=pipe, output=DummyOutput()) as reader:
            yield reader, pipe


def assert_idle(reader):
    assert not reader._session.app.is_running
    assert reader._session.app.future is None
    assert not asyncio.all_tasks(reader._loop)


def test_timeout_finishes_cancellation_and_next_line_is_readable(terminal):
    reader, pipe = terminal
    with pytest.raises(asyncio.TimeoutError):
        reader.read("审批 > ", timeout=0.05)
    assert_idle(reader)
    pipe.send_text("下一行\n")
    assert reader.read("你 > ", timeout=1) == "下一行"
    assert_idle(reader)


def test_repeated_timeouts(terminal):
    reader, pipe = terminal
    for _ in range(3):
        with pytest.raises(asyncio.TimeoutError):
            reader.read("审批 > ", timeout=0.03)
        assert_idle(reader)
    pipe.send_text("y\n")
    assert reader.read("你 > ", timeout=1) == "y"


def test_partial_line_does_not_leak_to_next_prompt(terminal):
    reader, pipe = terminal
    pipe.send_text("old partial")
    with pytest.raises(asyncio.TimeoutError):
        reader.read("审批 > ", timeout=0.05)
    assert_idle(reader)
    pipe.send_text("new line\n")
    assert reader.read("你 > ", timeout=1) == "new line"


@pytest.mark.parametrize("key,error", [("\x04", EOFError), ("\x03", KeyboardInterrupt)])
def test_eof_and_interrupt_restore_input(terminal, key, error):
    reader, pipe = terminal
    pipe.send_text(key)
    with pytest.raises(error):
        reader.read("审批 > ", timeout=1)
    assert_idle(reader)
    pipe.send_text("recovered\n")
    assert reader.read("你 > ", timeout=1) == "recovered"


def test_zero_timeout_does_not_consume_input(terminal):
    reader, pipe = terminal
    pipe.send_text("next\n")
    with pytest.raises(asyncio.TimeoutError):
        reader.read("审批 > ", timeout=0)
    assert_idle(reader)
    assert reader.read("你 > ", timeout=1) == "next"


def test_close_is_idempotent_and_borrowed_input_remains_open():
    with create_pipe_input() as pipe:
        reader = TerminalInput(input=pipe, output=DummyOutput())
        pipe.send_text("ok\n")
        assert reader.read("", timeout=1) == "ok"
        reader.close()
        reader.close()
        assert reader._loop.is_closed()
        assert not pipe.closed
        with pytest.raises(RuntimeError, match="已关闭"):
            reader.read("")


def test_owned_input_is_closed(monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr("sagent.terminal_input.create_input", lambda: pipe)
        with TerminalInput(output=DummyOutput()) as reader:
            pipe.send_text("ok\n")
            assert reader.read("", timeout=1) == "ok"
        assert pipe.closed
        assert reader._loop.is_closed()
