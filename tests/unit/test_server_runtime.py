"""会话级运行时注册表单元测试。

全部用例使用临时目录的 SessionStore 与不联网的假 LLM，只验证运行时装配、
会话内串行、跨会话并行、取消审批以及删除/释放语义，不驱动 engine.run。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from conftest import FakeLLMClient

from sagent.config.models import AppConfig
from sagent.permissions import PermissionRequest, build_default_policy
from sagent.server.runtime import (
    RunFinishedError,
    RunNotFoundError,
    RuntimeRegistry,
    SessionBusyError,
    SessionNotFoundError,
)
from sagent.session.models import SessionMeta
from sagent.session.store import SessionStore


def _make_config(tmp_path: Path) -> AppConfig:
    """构造不依赖真实 LLM 与外部资源的测试配置。

    参数:
        tmp_path: 测试临时目录，用于存放会话数据库。

    返回:
        测试用 AppConfig。
    """
    return AppConfig(
        llm={"model": "gpt-4o-mini", "api_key": "test"},
        context={"enable_summary": False},
        memory={"enabled": False},
        skills={"enabled": False},
        session={"db_path": str(tmp_path / "sessions.db"), "enable_fts": False},
        permissions={"ask_timeout": 5.0},
    )


def _create_session(store: SessionStore, session_id: str, title: str = "测试会话") -> None:
    """直接向存储写入一条会话元数据。"""
    now = datetime.now().isoformat()
    store.create_session(
        SessionMeta(
            id=session_id,
            title=title,
            created_at=now,
            updated_at=now,
            mode="react",
            message_count=0,
            persisted_seq=0,
        )
    )


def _make_registry(tmp_path: Path) -> tuple[RuntimeRegistry, SessionStore]:
    """构造测试用运行时注册表与其底层存储。"""
    store = SessionStore(str(tmp_path / "sessions.db"), enable_fts=False)
    registry = RuntimeRegistry(
        config=_make_config(tmp_path),
        store=store,
        llm=FakeLLMClient([]),
    )
    return registry, store


def _wait_until(predicate, timeout: float = 2.0) -> None:
    """轮询等待条件成立，超时则断言失败。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待条件超时")


def test_begin_run_rejects_second_run_until_finished(tmp_path):
    """同一会话第二次 begin_run 抛 SessionBusyError，结束后可再次开始。"""
    registry, store = _make_registry(tmp_path)
    _create_session(store, "s1")
    runtime = registry.get_or_create("s1")

    first = runtime.begin_run()
    with pytest.raises(SessionBusyError):
        runtime.begin_run()
    assert runtime.active_run is first

    runtime.finish_run(first.run_id, "completed")
    assert runtime.active_run is None

    second = runtime.begin_run()
    assert second.run_id != first.run_id
    runtime.finish_run(second.run_id, "completed")

    # 重复结束与未知运行分别对应 409 与 404
    with pytest.raises(RunFinishedError):
        runtime.finish_run(second.run_id, "completed")
    with pytest.raises(RunNotFoundError):
        runtime.finish_run("unknown-run", "completed")


def test_two_sessions_run_independently(tmp_path):
    """两个会话可各自持有进行中的运行，互不影响。"""
    registry, store = _make_registry(tmp_path)
    _create_session(store, "s1")
    _create_session(store, "s2")
    runtime_a = registry.get_or_create("s1")
    runtime_b = registry.get_or_create("s2")

    run_a = runtime_a.begin_run()
    run_b = runtime_b.begin_run()

    assert runtime_a is not runtime_b
    assert run_a.engine is not run_b.engine
    assert runtime_a.active_run is run_a
    assert runtime_b.active_run is run_b

    runtime_a.finish_run(run_a.run_id, "completed")
    runtime_b.finish_run(run_b.run_id, "completed")


def test_cancel_run_clears_pending_approvals(tmp_path):
    """取消运行会以拒绝唤醒并清空该会话的等待审批。"""
    registry, store = _make_registry(tmp_path)
    _create_session(store, "s1")
    runtime = registry.get_or_create("s1")
    run = runtime.begin_run()

    policy = build_default_policy()
    request = PermissionRequest(tool="write_file", args={"path": "a.txt"})
    worker = threading.Thread(target=runtime.approval.approve, args=(request, policy))
    worker.start()
    _wait_until(lambda: bool(runtime.approval.list_pending()))

    runtime.cancel_run(run.run_id)
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert runtime.approval.list_pending() == []
    assert run.cancel_token.is_cancelled()
    runtime.finish_run(run.run_id, "cancelled")


def test_release_drops_runtime(tmp_path):
    """release 后注册表不再持有该运行时。"""
    registry, store = _make_registry(tmp_path)
    _create_session(store, "s1")
    runtime = registry.get_or_create("s1")
    assert registry.get("s1") is runtime

    registry.release("s1")

    assert registry.get("s1") is None
    # 释放后再次访问会重建运行时
    assert registry.get_or_create("s1") is not runtime


def test_delete_session_removes_runtime_and_record(tmp_path):
    """delete_session 删除运行时与会话记录，不存在时抛 SessionNotFoundError。"""
    registry, store = _make_registry(tmp_path)
    _create_session(store, "s1")
    registry.get_or_create("s1")

    registry.delete_session("s1")

    assert registry.get("s1") is None
    assert store.get_session("s1") is None
    with pytest.raises(SessionNotFoundError):
        registry.delete_session("s1")


def test_get_or_create_unknown_session_raises(tmp_path):
    """会话记录不存在时拒绝创建运行时。"""
    registry, _ = _make_registry(tmp_path)

    with pytest.raises(SessionNotFoundError):
        registry.get_or_create("missing")
    assert registry.get("missing") is None
