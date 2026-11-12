"""SessionStore Web 能力单元测试。

覆盖旧库自动升级（turns 表）、跨线程并发写入、会话联合搜索
（标题 + 消息内容、FTS5 与 LIKE 降级）以及轮次记录往返读写。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading

import pytest

from sagent.session.models import SessionMessage, SessionMeta, TurnRecord
from sagent.session.store import SessionStore


def _make_meta(session_id="s1", title="测试会话", updated_at="2026-01-01T00:00:00"):
    """构造 SessionMeta 辅助函数。"""
    return SessionMeta(
        id=session_id,
        title=title,
        created_at="2026-01-01T00:00:00",
        updated_at=updated_at,
        mode="react",
        message_count=0,
        persisted_seq=0,
    )


def _make_msg(role="user", content="hello", seq=1):
    """构造 SessionMessage 辅助函数。"""
    return SessionMessage(role=role, content=content, seq=seq)


@pytest.fixture
def db_path():
    """临时数据库文件路径，测试后删除。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        os.unlink(path)


@pytest.fixture
def store(db_path):
    """基于临时数据库的 SessionStore，测试后关闭连接。"""
    s = SessionStore(db_path)
    try:
        yield s
    finally:
        s._conn.close()


def _create_old_schema_database(path: str) -> None:
    """按旧版本 schema（不含 turns 表）建库并写入会话与消息。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                persisted_seq INTEGER NOT NULL DEFAULT 0,
                plan_state TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tool_calls TEXT,
                tool_call_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                summary TEXT NOT NULL,
                covered_from_seq INTEGER NOT NULL,
                covered_to_seq INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_messages_session_seq
            ON messages(session_id, seq)
            """
        )
        conn.execute(
            """
            INSERT INTO sessions
                (id, title, created_at, updated_at, mode, message_count, persisted_seq, plan_state)
            VALUES ('old1', '旧会话', '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'react', 2, 2, NULL)
            """
        )
        conn.executemany(
            """
            INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at)
            VALUES (?, ?, ?, ?, NULL, NULL, '2026-01-01T00:00:00')
            """,
            [
                ("old1", 1, "user", "旧消息一"),
                ("old1", 2, "assistant", "旧消息二"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_open_old_database_creates_turns_table(db_path):
    """使用旧 schema 建库后打开，自动创建 turns 表且既有数据可读。"""
    _create_old_schema_database(db_path)
    store = SessionStore(db_path)
    try:
        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='turns'"
        )
        assert cur.fetchone() is not None
        cur.close()
        # 旧数据不受影响
        meta = store.get_session("old1")
        assert meta is not None
        assert meta.title == "旧会话"
        messages = store.load_messages("old1")
        assert [m.content for m in messages] == ["旧消息一", "旧消息二"]
    finally:
        store._conn.close()


def test_concurrent_append_messages_from_two_threads(store):
    """两个线程并发写入不同会话，无异常且消息完整。"""
    store.create_session(_make_meta(session_id="t1", title="线程一"))
    store.create_session(_make_meta(session_id="t2", title="线程二"))
    errors: list[BaseException] = []

    def writer(session_id: str) -> None:
        try:
            for index in range(1, 21):
                store.append_messages(
                    session_id,
                    [_make_msg(content=f"{session_id}-消息-{index}", seq=index)],
                    "2026-01-02T00:00:00",
                )
        except BaseException as exc:  # 收集线程内异常，供断言使用
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=("t1",)),
        threading.Thread(target=writer, args=("t2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for session_id in ("t1", "t2"):
        messages = store.load_messages(session_id)
        assert [m.seq for m in messages] == list(range(1, 21))
        meta = store.get_session(session_id)
        assert meta.message_count == 20
        assert meta.persisted_seq == 20


def test_search_sessions_title_content_and_dedup(store):
    """标题命中与内容命中的会话都能返回，同一会话只出现一次。"""
    # s1 标题命中，且消息内容也命中（应标记为 both 且只返回一次）
    store.create_session(_make_meta(session_id="s1", title="deploy notes"))
    store.append_messages(
        "s1",
        [
            _make_msg(content="how to deploy", seq=1),
            _make_msg(content="deploy again", seq=2),
        ],
        "2026-01-02T00:00:00",
    )
    # s2 仅消息内容命中
    store.create_session(_make_meta(session_id="s2", title="其他会话"))
    store.append_messages(
        "s2", [_make_msg(content="deploy checklist", seq=1)], "2026-01-03T00:00:00"
    )

    hits = store.search_sessions("deploy")
    ids = [hit.session.id for hit in hits]
    assert ids == ["s2", "s1"]
    assert len(ids) == len(set(ids))
    by_id = {hit.session.id: hit for hit in hits}
    assert by_id["s1"].match_source == "both"
    assert by_id["s2"].match_source == "content"
    assert by_id["s2"].snippet is not None
    assert "deploy" in by_id["s2"].snippet


def test_search_sessions_title_only_hit(store):
    """仅标题命中时命中来源为 title，摘要使用标题。"""
    store.create_session(_make_meta(session_id="s1", title="部署脚本说明"))
    hits = store.search_sessions("部署脚本")
    assert len(hits) == 1
    assert hits[0].match_source == "title"
    assert hits[0].snippet == "部署脚本说明"


def test_search_sessions_without_fts(db_path):
    """FTS5 关闭时降级为 LIKE，标题与消息内容均可命中。"""
    store = SessionStore(db_path, enable_fts=False)
    try:
        store.create_session(_make_meta(session_id="s1", title="deploy notes"))
        store.create_session(_make_meta(session_id="s2", title="其他会话"))
        store.append_messages(
            "s2", [_make_msg(content="部署前先 deploy 再验证", seq=1)], "2026-01-03T00:00:00"
        )
        hits = store.search_sessions("deploy")
        by_id = {hit.session.id: hit for hit in hits}
        assert set(by_id) == {"s1", "s2"}
        assert by_id["s1"].match_source == "title"
        assert by_id["s2"].match_source == "content"
        assert "deploy" in by_id["s2"].snippet

        # 中文子串在 LIKE 降级路径下也能命中消息内容
        hits_cn = store.search_sessions("部署")
        assert [hit.session.id for hit in hits_cn] == ["s2"]
    finally:
        store._conn.close()


def test_append_and_list_turns_roundtrip(store):
    """append_turn 写入后 list_turns 按 turn_index 升序还原。"""
    store.create_session(_make_meta(session_id="s1"))
    turn2 = TurnRecord(
        session_id="s1",
        turn_index=2,
        started_at="2026-01-01T00:00:02",
        finished_at="2026-01-01T00:00:04",
        duration_ms=2000,
        prompt_tokens=30,
        completion_tokens=10,
        total_tokens=40,
        outcome="completed",
        mode="react",
    )
    turn1 = TurnRecord(
        session_id="s1",
        turn_index=1,
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:01",
        duration_ms=1000,
        prompt_tokens=20,
        completion_tokens=5,
        total_tokens=25,
        outcome="cancelled",
        mode="plan",
    )
    store.append_turn(turn2)
    store.append_turn(turn1)

    turns = store.list_turns("s1")
    assert [t.turn_index for t in turns] == [1, 2]
    assert turns[0] == turn1
    assert turns[1] == turn2
