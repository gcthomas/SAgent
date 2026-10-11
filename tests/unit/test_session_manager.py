"""SessionManager 单元测试。

覆盖会话的创建、切换、增量保存、工作上下文还原桥接、压缩事件记录、
删除保护等核心逻辑，验证 SessionManager 与 SessionStore、ContextManager
之间的协作行为。
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from sagent.config.models import ContextConfig
from sagent.context.context_manager import ContextManager
from sagent.core.plan_state import PlanState, PlanStepState
from sagent.session.manager import SessionManager
from sagent.session.models import CompactionEvent, SessionMessage, SessionMeta
from sagent.session.store import SessionStore


@pytest.fixture
def manager():
    """创建独立的 store + context_manager + session_manager。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = SessionStore(path)
        cm = ContextManager(ContextConfig(token_counter_method="heuristic"))
        mgr = SessionManager(store, cm, mode="react")
        yield mgr, store, cm
    finally:
        store._conn.close()
        os.unlink(path)


# --- 创建与切换 ---


def test_ensure_current_session_creates_new(manager):
    """无 session_id 时创建新会话，返回 SessionMeta，当前会话 id 非空。"""
    mgr, store, cm = manager
    meta = mgr.ensure_current_session()
    assert isinstance(meta, SessionMeta)
    assert mgr._current_session_id is not None
    assert mgr._current_session_id == meta.id


def test_ensure_current_session_loads_existing(manager):
    """指定已存在 session_id 时切换回该会话，当前会话 id 等于该 id。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    sid = meta.id
    # 新建一个 SessionManager，复用同一存储
    cm2 = ContextManager(ContextConfig(token_counter_method="heuristic"))
    mgr2 = SessionManager(store, cm2, mode="react")
    result = mgr2.ensure_current_session(sid)
    assert result is not None
    assert mgr2._current_session_id == sid


def test_new_session_resets_context(manager):
    """创建新会话后上下文管理器消息为空。"""
    mgr, store, cm = manager
    cm.add_message({"role": "user", "content": "before"})
    assert len(cm.get_messages()) == 1
    mgr.new_session()
    assert len(cm.get_messages()) == 0


def test_switch_session_loads_working_context(manager):
    """会话1保存消息后切换到会话2，再切回会话1，上下文包含会话1历史。"""
    mgr, store, cm = manager
    m1 = mgr.new_session()
    cm.add_message({"role": "user", "content": "session1-msg1"})
    cm.add_message({"role": "assistant", "content": "session1-msg2"})
    mgr.save_current()
    # 切换到会话2
    mgr.new_session()
    assert len(cm.get_messages()) == 0
    # 切换回会话1
    mgr.switch_session(m1.id)
    msgs = cm.get_messages()
    assert len(msgs) == 2
    assert msgs[0]["content"] == "session1-msg1"
    assert msgs[1]["content"] == "session1-msg2"


def test_active_plan_is_persisted_and_restored_on_session_switch(manager):
    mgr, store, cm = manager
    meta = mgr.new_session()
    plan = PlanState(
        version=1,
        task="恢复任务",
        steps=[PlanStepState(description="继续执行", status="running")],
        current_step=1,
        status="executing",
        recent_observation="上一步观察",
        artifact_refs=["https://example.test/result"],
    )
    mgr.set_active_plan(plan)
    mgr.save_current()
    mgr.new_session()

    restored_manager = SessionManager(store, ContextManager(ContextConfig(token_counter_method="heuristic")))
    restored_manager.switch_session(meta.id)
    restored = restored_manager.get_active_plan()

    assert restored is not None
    assert restored.task == "恢复任务"
    assert restored.recent_observation == "上一步观察"
    assert restored.artifact_refs == ["https://example.test/result"]


def test_clear_absent_active_plan_skips_database_operations(manager):
    """无活动计划时重复清空不调用存储，也不执行 SQL。"""
    mgr, store, cm = manager
    mgr.new_session()
    statements = []
    store._conn.set_trace_callback(statements.append)
    try:
        with patch.object(store, "save_plan_state", wraps=store.save_plan_state) as save_plan:
            mgr.set_active_plan(None)
            mgr.set_active_plan(None)
            save_plan.assert_not_called()
        assert statements == []
        assert mgr.get_active_plan() is None
    finally:
        store._conn.set_trace_callback(None)


def test_clear_active_plan_removes_persisted_snapshot(manager):
    """清空已有计划写入空快照，重新加载会话不会恢复旧计划。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    plan = PlanState(
        version=1, task="待完成任务", steps=[PlanStepState(description="继续执行")],
        status="executing",
    )
    mgr.set_active_plan(plan)
    assert store.get_session(meta.id).plan_state == plan.to_dict()

    with patch.object(store, "save_plan_state", wraps=store.save_plan_state) as save_plan:
        mgr.set_active_plan(None)
        mgr.set_active_plan(None)
        save_plan.assert_called_once_with(meta.id, None)

    assert mgr.get_active_plan() is None
    assert store.get_session(meta.id).plan_state is None
    restored_manager = SessionManager(store, ContextManager(ContextConfig(token_counter_method="heuristic")))
    restored_manager.switch_session(meta.id)
    assert restored_manager.get_active_plan() is None


def test_same_active_plan_object_persists_updated_snapshot(manager):
    """同一可变计划对象的状态更新仍保存独立快照。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    plan = PlanState(
        version=1, task="执行任务", steps=[PlanStepState(description="执行步骤")],
        status="executing",
    )
    with patch.object(store, "save_plan_state", wraps=store.save_plan_state) as save_plan:
        mgr.set_active_plan(plan)
        initial_snapshot = save_plan.call_args.args[1]
        plan.steps[0].status = "completed"
        plan.steps[0].summary = "步骤已完成"
        plan.status = "completed"
        mgr.set_active_plan(plan)

        assert save_plan.call_count == 2
        save_plan.assert_called_with(meta.id, plan.to_dict())
        assert initial_snapshot["steps"][0]["status"] == "pending"
        assert initial_snapshot["status"] == "executing"

    assert mgr.get_active_plan() is plan
    assert store.get_session(meta.id).plan_state == plan.to_dict()


def test_non_executing_plan_is_not_restored_as_active(manager):
    mgr, store, cm = manager
    meta = mgr.new_session()
    plan = PlanState(version=1, task="已结束", steps=[], status="blocked")
    mgr.set_active_plan(plan)

    restored_manager = SessionManager(store, ContextManager(ContextConfig(token_counter_method="heuristic")))
    restored_manager.switch_session(meta.id)

    assert restored_manager.get_active_plan() is None


def test_switch_nonexistent_returns_none(manager):
    """切换到不存在的会话返回 None，当前会话不变。"""
    mgr, store, cm = manager
    current = mgr.new_session()
    result = mgr.switch_session("nonexistent")
    assert result is None
    assert mgr._current_session_id == current.id


# --- 增量保存 ---


def test_save_current_increments_persisted_seq(manager):
    """增量保存推进持久化游标，返回新增条数。"""
    mgr, store, cm = manager
    mgr.new_session()
    cm.add_message({"role": "user", "content": "msg1"})
    cm.add_message({"role": "assistant", "content": "msg2"})
    assert mgr.save_current() == 2
    assert mgr._persisted_seq == 2
    cm.add_message({"role": "user", "content": "msg3"})
    assert mgr.save_current() == 1
    assert mgr._persisted_seq == 3


def test_save_current_no_new_messages(manager):
    """无新消息时增量保存返回 0。"""
    mgr, store, cm = manager
    mgr.new_session()
    assert mgr.save_current() == 0


def test_save_current_persists_to_store(manager):
    """增量保存后存储可加载到相同消息。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    cm.add_message({"role": "user", "content": "persisted-msg"})
    mgr.save_current()
    msgs = store.load_messages(meta.id)
    assert len(msgs) == 1
    assert isinstance(msgs[0], SessionMessage)
    assert msgs[0].role == "user"
    assert msgs[0].content == "persisted-msg"


# --- 工作上下文还原桥接 ---


def test_switch_restores_messages_to_context_manager(manager):
    """切换会话时将历史消息按 seq 顺序还原到上下文管理器。"""
    mgr, store, cm = manager
    m1 = mgr.new_session()
    cm.add_message({"role": "user", "content": "first"})
    cm.add_message({"role": "assistant", "content": "second"})
    cm.add_message({"role": "user", "content": "third"})
    mgr.save_current()
    # 切换到会话2再切回
    mgr.new_session()
    mgr.switch_session(m1.id)
    msgs = cm.get_messages()
    assert len(msgs) == 3
    assert [m["content"] for m in msgs] == ["first", "second", "third"]


# --- 压缩后已落盘历史不被删改 ---


def test_compaction_does_not_delete_persisted_messages(manager):
    """记录压缩事件后，已落盘的原始消息不被删除或修改。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    for i in range(5):
        cm.add_message({"role": "user", "content": f"msg-{i}"})
    mgr.save_current()
    # 记录压缩事件
    mgr.record_compaction("summary", 1, 3)
    # 已落盘消息数量不变
    persisted = store.load_messages(meta.id)
    assert len(persisted) == 5
    # session_events 表有一条 compaction 事件
    cur = store._conn.execute(
        "SELECT COUNT(*) FROM session_events WHERE session_id=?", (meta.id,)
    )
    assert cur.fetchone()[0] == 1


# --- 删除当前会话保护 ---


def test_delete_current_session_rejected(manager):
    """删除当前会话返回 False，会话仍存在。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    result = mgr.delete_session(meta.id)
    assert result is False
    assert store.get_session(meta.id) is not None


def test_delete_other_session_succeeds(manager):
    """删除非当前会话返回 True，会话被删除。"""
    mgr, store, cm = manager
    m1 = mgr.new_session()
    m2 = mgr.new_session()
    # 切回会话1，使当前会话为会话1
    mgr.switch_session(m1.id)
    # 在会话1中删除会话2
    result = mgr.delete_session(m2.id)
    assert result is True
    assert store.get_session(m2.id) is None


# --- 压缩回调桥接 ---


def test_compaction_callback_registered(manager):
    """SessionManager 构造后上下文管理器的压缩回调已设置。"""
    mgr, store, cm = manager
    assert cm._on_compaction is not None


def test_record_compaction_via_callback(manager):
    """通过压缩回调记录压缩事件，session_events 表有对应事件。"""
    mgr, store, cm = manager
    meta = mgr.new_session()
    # 直接调用压缩回调，模拟 ContextManager 第三层摘要后调用
    mgr._on_compaction("sum", 1, 2)
    cur = store._conn.execute(
        "SELECT COUNT(*) FROM session_events WHERE session_id=?", (meta.id,)
    )
    assert cur.fetchone()[0] == 1
