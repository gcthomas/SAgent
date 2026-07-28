"""SessionStore 单元测试。

覆盖建表与初始化、会话 CRUD、增量追加（seq 游标）、消息加载与无损还原、
压缩事件、build_working_context 还原、FTS5 检索与降级等核心逻辑。
"""

from __future__ import annotations

import os
import tempfile

import pytest

from sagent.session.models import CompactionEvent, SessionMessage, SessionMeta
from sagent.session.store import SessionStore


@pytest.fixture
def store():
    """创建临时数据库的 SessionStore，每个测试用例独立，测试后删除文件。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        s = SessionStore(path)
        yield s
    finally:
        # 确保连接关闭后再删除文件
        s._conn.close()
        os.unlink(path)


def _make_meta(id="s1", title="测试会话", mode="react", persisted_seq=0):
    """构造 SessionMeta 辅助函数。"""
    return SessionMeta(
        id=id,
        title=title,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        mode=mode,
        message_count=0,
        persisted_seq=persisted_seq,
    )


def _make_msg(role="user", content="hello", seq=1, tool_calls=None, tool_call_id=None):
    """构造 SessionMessage 辅助函数。"""
    return SessionMessage(
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        seq=seq,
    )


# --- 1. 建表与初始化 ---


def test_init_creates_tables(store):
    """首次连接自动创建 sessions、messages、session_events 表。"""
    cur = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
        ("sessions", "messages", "session_events"),
    )
    names = {row["name"] for row in cur.fetchall()}
    cur.close()
    assert "sessions" in names
    assert "messages" in names
    assert "session_events" in names


def test_wal_mode_enabled(store):
    """连接启用 WAL 模式，PRAGMA journal_mode 返回 'wal'。"""
    cur = store._conn.execute("PRAGMA journal_mode")
    mode = cur.fetchone()[0]
    cur.close()
    assert mode == "wal"


# --- 2. 会话 CRUD ---


def test_create_and_get_session(store):
    """create_session 后 get_session 返回相同元数据。"""
    meta = _make_meta()
    store.create_session(meta)
    got = store.get_session("s1")
    assert got is not None
    assert got.id == meta.id
    assert got.title == meta.title
    assert got.created_at == meta.created_at
    assert got.updated_at == meta.updated_at
    assert got.mode == meta.mode
    assert got.message_count == meta.message_count
    assert got.persisted_seq == meta.persisted_seq


def test_list_sessions_ordered_by_updated_at_desc(store):
    """创建多个会话（不同 updated_at），list_sessions 按 updated_at DESC 排序。"""
    m1 = _make_meta(id="s1")
    m1.updated_at = "2026-01-01T00:00:00"
    m2 = _make_meta(id="s2")
    m2.updated_at = "2026-01-03T00:00:00"
    m3 = _make_meta(id="s3")
    m3.updated_at = "2026-01-02T00:00:00"
    store.create_session(m1)
    store.create_session(m2)
    store.create_session(m3)
    sessions = store.list_sessions()
    assert len(sessions) == 3
    # 按 updated_at 降序：s2 > s3 > s1
    assert sessions[0].id == "s2"
    assert sessions[1].id == "s3"
    assert sessions[2].id == "s1"


def test_rename_session(store):
    """rename_session 后 get_session 返回新标题与更新时间。"""
    meta = _make_meta()
    store.create_session(meta)
    result = store.rename_session("s1", "新标题", "2026-01-02T00:00:00")
    assert result is True
    got = store.get_session("s1")
    assert got.title == "新标题"
    assert got.updated_at == "2026-01-02T00:00:00"


def test_rename_nonexistent_session(store):
    """rename 不存在的会话返回 False。"""
    result = store.rename_session("nonexistent", "标题", "2026-01-02T00:00:00")
    assert result is False


def test_delete_session_cascades_messages(store):
    """delete_session 后 get_session 返回 None，且 messages 被级联删除。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1", [_make_msg(content="hello", seq=1)], "2026-01-01T00:01:00"
    )
    store.delete_session("s1")
    assert store.get_session("s1") is None
    assert store.load_messages("s1") == []


# --- 3. 增量追加（seq 游标） ---


def test_append_messages_only_new(store):
    """首次追加 seq=1,2 返回 2；再次追加 seq=1,2,3 应只追加 seq=3（返回 1），历史行不被删改。"""
    meta = _make_meta()
    store.create_session(meta)
    count1 = store.append_messages(
        "s1",
        [_make_msg(content="msg1", seq=1), _make_msg(content="msg2", seq=2)],
        "2026-01-01T00:01:00",
    )
    assert count1 == 2
    count2 = store.append_messages(
        "s1",
        [
            _make_msg(content="msg1-new", seq=1),
            _make_msg(content="msg2-new", seq=2),
            _make_msg(content="msg3", seq=3),
        ],
        "2026-01-01T00:02:00",
    )
    assert count2 == 1
    # 验证历史行不被删改
    msgs = store.load_messages("s1")
    assert len(msgs) == 3
    assert msgs[0].seq == 1
    assert msgs[0].content == "msg1"
    assert msgs[1].seq == 2
    assert msgs[1].content == "msg2"
    assert msgs[2].seq == 3
    assert msgs[2].content == "msg3"


def test_append_messages_skips_already_persisted(store):
    """追加 seq <= persisted_seq 的消息应返回 0。"""
    meta = _make_meta(persisted_seq=5)
    store.create_session(meta)
    count = store.append_messages(
        "s1",
        [_make_msg(seq=1), _make_msg(seq=5)],
        "2026-01-01T00:01:00",
    )
    assert count == 0


def test_append_messages_updates_session_meta(store):
    """追加后 message_count 与 persisted_seq 正确更新，updated_at 更新。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1",
        [_make_msg(seq=1), _make_msg(seq=2), _make_msg(seq=3)],
        "2026-01-02T00:00:00",
    )
    got = store.get_session("s1")
    assert got.message_count == 3
    assert got.persisted_seq == 3
    assert got.updated_at == "2026-01-02T00:00:00"


# --- 4. 消息加载与无损还原 ---


def test_load_messages_ordered_by_seq(store):
    """按 seq 升序返回消息。"""
    meta = _make_meta()
    store.create_session(meta)
    # 故意乱序追加，验证加载时按 seq 排序
    store.append_messages(
        "s1",
        [_make_msg(seq=3), _make_msg(seq=1), _make_msg(seq=2)],
        "2026-01-01T00:01:00",
    )
    msgs = store.load_messages("s1")
    assert len(msgs) == 3
    assert [m.seq for m in msgs] == [1, 2, 3]


def test_load_messages_preserves_tool_calls(store):
    """含 tool_calls 的 assistant 消息和含 tool_call_id 的 tool 消息能无损还原。"""
    meta = _make_meta()
    store.create_session(meta)
    tool_calls = [
        {"id": "call_1", "name": "read_file", "arguments": '{"path": "a.txt"}'}
    ]
    msgs = [
        _make_msg(role="user", content="读取文件", seq=1),
        _make_msg(role="assistant", content="", seq=2, tool_calls=tool_calls),
        _make_msg(role="tool", content="文件内容", seq=3, tool_call_id="call_1"),
    ]
    store.append_messages("s1", msgs, "2026-01-01T00:01:00")
    loaded = store.load_messages("s1")
    assert len(loaded) == 3
    # user 消息
    assert loaded[0].role == "user"
    assert loaded[0].content == "读取文件"
    # assistant 消息，tool_calls 无损还原
    assert loaded[1].role == "assistant"
    assert loaded[1].tool_calls == tool_calls
    # tool 消息，tool_call_id 无损还原
    assert loaded[2].role == "tool"
    assert loaded[2].tool_call_id == "call_1"
    assert loaded[2].content == "文件内容"


def test_load_messages_empty_session(store):
    """空会话返回空列表。"""
    meta = _make_meta()
    store.create_session(meta)
    assert store.load_messages("s1") == []


# --- 5. 压缩事件 ---


def test_append_compaction_event_does_not_modify_messages(store):
    """追加 compaction 事件后 messages 表行数不变。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1",
        [_make_msg(content="msg1", seq=1), _make_msg(content="msg2", seq=2)],
        "2026-01-01T00:01:00",
    )
    before = len(store.load_messages("s1"))
    event = CompactionEvent(
        type="compaction",
        summary="这是摘要",
        covered_from_seq=1,
        covered_to_seq=2,
        created_at="2026-01-01T00:02:00",
    )
    store.append_compaction_event("s1", event)
    after = len(store.load_messages("s1"))
    assert before == after
    assert before == 2


def test_append_compaction_event_stored(store):
    """压缩事件被持久化（通过 build_working_context 间接验证）。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1",
        [
            _make_msg(content="msg1", seq=1),
            _make_msg(content="msg2", seq=2),
            _make_msg(content="msg3", seq=3),
        ],
        "2026-01-01T00:01:00",
    )
    event = CompactionEvent(
        type="compaction",
        summary="这是摘要",
        covered_from_seq=1,
        covered_to_seq=2,
        created_at="2026-01-01T00:02:00",
    )
    store.append_compaction_event("s1", event)
    # 通过 build_working_context 间接验证事件已持久化
    ctx = store.build_working_context("s1")
    # 摘要消息应在首位，证明事件已存储
    assert ctx[0].role == "system"
    assert "[历史摘要]" in ctx[0].content
    assert "这是摘要" in ctx[0].content


# --- 6. build_working_context 还原 ---


def test_build_working_context_with_compaction(store):
    """有 compaction 事件时返回摘要消息 + 其后 seq > covered_to_seq 的原始消息。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1",
        [
            _make_msg(content="msg1", seq=1),
            _make_msg(content="msg2", seq=2),
            _make_msg(content="msg3", seq=3),
            _make_msg(content="msg4", seq=4),
        ],
        "2026-01-01T00:01:00",
    )
    event = CompactionEvent(
        type="compaction",
        summary="摘要内容",
        covered_from_seq=1,
        covered_to_seq=2,
        created_at="2026-01-01T00:02:00",
    )
    store.append_compaction_event("s1", event)
    ctx = store.build_working_context("s1")
    # 首条为摘要消息
    assert ctx[0].role == "system"
    assert ctx[0].content == "[历史摘要] 摘要内容"
    assert ctx[0].tool_calls is None
    assert ctx[0].tool_call_id is None
    # 其后为 seq > covered_to_seq(=2) 的原始消息
    rest = ctx[1:]
    assert len(rest) == 2
    assert rest[0].seq == 3
    assert rest[0].content == "msg3"
    assert rest[1].seq == 4
    assert rest[1].content == "msg4"


def test_build_working_context_without_compaction(store):
    """无 compaction 事件时返回全部消息。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1",
        [_make_msg(content="msg1", seq=1), _make_msg(content="msg2", seq=2)],
        "2026-01-01T00:01:00",
    )
    ctx = store.build_working_context("s1")
    assert len(ctx) == 2
    assert ctx[0].content == "msg1"
    assert ctx[1].content == "msg2"


def test_build_working_context_summary_seq(store):
    """摘要消息的 seq 为 covered_to_seq+1。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1",
        [
            _make_msg(content="msg1", seq=1),
            _make_msg(content="msg2", seq=2),
            _make_msg(content="msg3", seq=3),
        ],
        "2026-01-01T00:01:00",
    )
    event = CompactionEvent(
        type="compaction",
        summary="摘要",
        covered_from_seq=1,
        covered_to_seq=2,
        created_at="2026-01-01T00:02:00",
    )
    store.append_compaction_event("s1", event)
    ctx = store.build_working_context("s1")
    # 摘要消息 seq = covered_to_seq(2) + 1 = 3
    assert ctx[0].seq == 3


# --- 7. FTS5 检索与降级 ---


def test_search_fts5_available(store):
    """FTS5 可用时，search 返回命中结果，结果含 snippet 字段。"""
    if not store._fts_available:
        pytest.skip("FTS5 不可用，跳过 FTS5 检索测试")
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1", [_make_msg(content="hello world", seq=1)], "2026-01-01T00:01:00"
    )
    results = store.search("hello")
    assert len(results) == 1
    assert results[0]["session_id"] == "s1"
    assert results[0]["seq"] == 1
    assert results[0]["role"] == "user"
    assert "snippet" in results[0]
    assert "hello" in results[0]["content"]
    # 外部内容表的 snippet() 应返回实际摘要文本而非 None
    assert results[0]["snippet"] is not None
    assert "hello" in results[0]["snippet"]


def test_search_fts5_degradation():
    """FTS5 不可用时降级为 LIKE 查询，仍返回结果。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        s = SessionStore(path, enable_fts=False)
        # 强制降级：模拟 FTS5 不可用，走 LIKE 查询路径
        s._fts_available = False
        meta = _make_meta()
        s.create_session(meta)
        s.append_messages(
            "s1", [_make_msg(content="hello world", seq=1)], "2026-01-01T00:01:00"
        )
        results = s.search("hello")
        assert len(results) == 1
        assert results[0]["session_id"] == "s1"
        assert "hello" in results[0]["snippet"]
        s._conn.close()
    finally:
        os.unlink(path)


def test_search_no_match(store):
    """无匹配关键词时返回空列表。"""
    meta = _make_meta()
    store.create_session(meta)
    store.append_messages(
        "s1", [_make_msg(content="hello world", seq=1)], "2026-01-01T00:01:00"
    )
    results = store.search("nomatch")
    assert results == []
