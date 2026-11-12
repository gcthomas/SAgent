"""会话与轨迹 API 单元测试。

使用临时 SQLite 会话库构造应用，覆盖会话增删改查、搜索、历史消息与轨迹
聚合逻辑；不发起任何真实 LLM 请求。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sagent.config.models import AppConfig
from sagent.core.plan_engine import PLAN_STEP_TASK_PREFIX
from sagent.core.prompts import REACT_SYSTEM_PROMPT
from sagent.server.app import create_app
from sagent.session.models import (
    CompactionEvent,
    SessionMessage,
    SessionMeta,
    TurnRecord,
)
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
        server={"static_dir": str(tmp_path / "missing-dist")},
    )


@pytest.fixture
def client(tmp_path: Path):
    """提供启用了会话存储的测试客户端。"""
    with TestClient(create_app(_make_config(tmp_path))) as test_client:
        yield test_client


def _insert_session(
    store: SessionStore,
    session_id: str,
    title: str,
    updated_at: str = "2026-01-01T00:00:00",
) -> None:
    """直接写入一个会话元数据，便于构造固定数据。

    参数:
        store: 会话存储。
        session_id: 会话唯一标识。
        title: 会话标题。
        updated_at: 更新时间。
    """
    store.create_session(
        SessionMeta(
            id=session_id,
            title=title,
            created_at="2026-01-01T00:00:00",
            updated_at=updated_at,
            mode="react",
            message_count=0,
            persisted_seq=0,
        )
    )


def test_list_sessions_returns_meta_with_pagination(client):
    store = client.app.state.store
    _insert_session(store, "sess0001", "旧会话", "2026-01-01T00:00:00")
    _insert_session(store, "sess0002", "新会话", "2026-01-02T00:00:00")

    response = client.get("/api/sessions", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["sessions"]) == 1
    first = body["sessions"][0]
    assert set(first) == {
        "id",
        "title",
        "created_at",
        "updated_at",
        "mode",
        "message_count",
    }
    assert first["id"] == "sess0002"

    second_page = client.get("/api/sessions", params={"limit": 1, "offset": 1}).json()
    assert second_page["sessions"][0]["id"] == "sess0001"


def test_create_session_returns_201_meta(client):
    response = client.post("/api/sessions", json={"title": "  部署脚本  "})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "部署脚本"
    assert body["mode"] == "react"
    assert body["message_count"] == 0
    assert len(body["id"]) == 8

    listed = client.get("/api/sessions").json()
    assert listed["total"] == 1
    assert listed["sessions"][0]["id"] == body["id"]

    default_title = client.post("/api/sessions", json={"title": None}).json()
    assert default_title["title"] == "新会话"


def test_rename_session(client):
    session_id = client.post("/api/sessions", json={"title": "旧标题"}).json()["id"]

    response = client.patch(f"/api/sessions/{session_id}", json={"title": "新标题"})
    assert response.status_code == 200
    assert response.json()["title"] == "新标题"

    listed = client.get("/api/sessions").json()
    assert listed["sessions"][0]["title"] == "新标题"


def test_rename_blank_title_returns_400(client):
    session_id = client.post("/api/sessions", json={"title": "标题"}).json()["id"]

    response = client.patch(f"/api/sessions/{session_id}", json={"title": "   "})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


def test_rename_missing_session_returns_404(client):
    response = client.patch("/api/sessions/missing0", json={"title": "新标题"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_delete_session_releases_runtime_and_removes_record(client):
    store = client.app.state.store
    session_id = client.post("/api/sessions", json={"title": "待删除"}).json()["id"]
    client.app.state.runtimes.get_or_create(session_id)

    response = client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.app.state.runtimes.get(session_id) is None
    assert store.get_session(session_id) is None
    assert client.get(f"/api/sessions/{session_id}/messages").status_code == 404


def test_delete_missing_session_returns_404(client):
    response = client.delete("/api/sessions/missing0")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_search_matches_title_and_content(client):
    store = client.app.state.store
    _insert_session(store, "title001", "部署脚本说明")
    _insert_session(store, "content1", "无关标题")
    _insert_session(store, "both0001", "部署策略")
    store.append_messages(
        "content1",
        [SessionMessage(role="user", content="请检查部署流程", seq=1)],
        "2026-01-03T00:00:00",
    )
    store.append_messages(
        "both0001",
        [SessionMessage(role="user", content="部署失败如何排查", seq=1)],
        "2026-01-04T00:00:00",
    )

    response = client.get("/api/sessions/search", params={"q": "部署"})
    assert response.status_code == 200
    sessions = {item["id"]: item for item in response.json()["sessions"]}
    assert sessions["title001"]["match_source"] == "title"
    assert sessions["content1"]["match_source"] == "content"
    assert sessions["both0001"]["match_source"] == "both"
    assert sessions["title001"]["title"] == "部署脚本说明"
    assert "部署" in sessions["content1"]["snippet"]


def test_search_empty_keyword_returns_empty_list(client):
    response = client.get("/api/sessions/search", params={"q": "   "})
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def _seed_trace_session(store: SessionStore) -> str:
    """写入一条包含多轮、工具调用与压缩事件的会话。

    参数:
        store: 会话存储。

    返回:
        会话唯一标识。
    """
    session_id = "trace001"
    _insert_session(store, session_id, "轨迹会话")
    store.append_messages(
        session_id,
        [
            SessionMessage(role="system", content="自定义系统提示", seq=1),
            SessionMessage(role="user", content="第一问", seq=2),
            SessionMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{\"path\": \"a.txt\"}"},
                    }
                ],
                seq=3,
            ),
            SessionMessage(role="tool", content="文件内容", tool_call_id="call_1", seq=4),
            SessionMessage(role="assistant", content="第一答", seq=5),
            SessionMessage(role="user", content="第二问", seq=6),
            SessionMessage(
                role="assistant",
                content="第二答",
                tool_calls=[
                    {
                        "id": "call_missing",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    }
                ],
                seq=7,
            ),
            SessionMessage(role="user", content="第三问", seq=8),
            SessionMessage(role="assistant", content="第三答", seq=9),
        ],
        "2026-01-05T12:00:00",
    )
    store.append_turn(
        TurnRecord(
            session_id=session_id,
            turn_index=1,
            started_at="2026-01-05T12:00:01",
            finished_at="2026-01-05T12:00:03",
            duration_ms=2000,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            outcome="completed",
            mode="react",
        )
    )
    store.append_compaction_event(
        session_id,
        CompactionEvent(
            type="compaction",
            summary="第一轮摘要",
            covered_from_seq=2,
            covered_to_seq=4,
            created_at="2026-01-05T12:00:02",
        ),
    )
    store.append_compaction_event(
        session_id,
        CompactionEvent(
            type="compaction",
            summary="第二轮摘要",
            covered_from_seq=6,
            covered_to_seq=7,
            created_at="2026-01-05T12:00:04",
        ),
    )
    store.append_compaction_event(
        session_id,
        CompactionEvent(
            type="compaction",
            summary="无法归属的摘要",
            covered_from_seq=999,
            covered_to_seq=999,
            created_at="2026-01-05T12:00:05",
        ),
    )
    return session_id


def test_list_messages_returns_full_rows(client):
    store = client.app.state.store
    session_id = _seed_trace_session(store)

    response = client.get(f"/api/sessions/{session_id}/messages")
    assert response.status_code == 200
    body = response.json()
    messages = body["messages"]
    assert len(messages) == 9
    assert [message["seq"] for message in messages] == list(range(1, 10))
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "自定义系统提示"
    assert messages[0]["tool_calls"] is None
    assert isinstance(messages[0]["id"], int)
    assert messages[0]["created_at"] == "2026-01-05T12:00:00"

    assistant_with_tool = messages[2]
    assert assistant_with_tool["role"] == "assistant"
    assert assistant_with_tool["tool_calls"][0]["id"] == "call_1"
    assert assistant_with_tool["tool_calls"][0]["function"]["name"] == "read_file"

    tool_message = messages[3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"

    # 会话累计用量取自 turns 表，供前端用量栏在历史会话中展示
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_list_messages_without_turns_returns_no_usage(client):
    store = client.app.state.store
    _insert_session(store, "noTurn01", "无轮次会话")
    store.append_messages(
        "noTurn01",
        [SessionMessage(role="user", content="你好", seq=1)],
        "2026-01-06T12:00:00",
    )

    response = client.get("/api/sessions/noTurn01/messages")
    assert response.status_code == 200
    body = response.json()
    assert len(body["messages"]) == 1
    assert body["usage"] is None


def test_list_messages_missing_session_returns_404(client):
    response = client.get("/api/sessions/missing0/messages")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_trace_builds_turns_usage_and_compactions(client):
    store = client.app.state.store
    session_id = _seed_trace_session(store)

    response = client.get(f"/api/sessions/{session_id}/trace")
    assert response.status_code == 200
    body = response.json()

    assert body["session"]["id"] == session_id
    assert body["system_prompt"] == "自定义系统提示"
    assert body["system_prompt_source"] == "stored"
    assert body["total_turns"] == 3
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }

    first, second, third = body["turns"]

    assert first["turn_index"] == 1
    assert first["started_at"] == "2026-01-05T12:00:01"
    assert first["finished_at"] == "2026-01-05T12:00:03"
    assert first["duration_ms"] == 2000
    assert first["mode"] == "react"
    assert first["outcome"] == "completed"
    assert first["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert first["user"] == {
        "seq": 2,
        "content": "第一问",
        "created_at": "2026-01-05T12:00:00",
    }
    assert first["assistant_text"] == "第一答"
    assert first["tool_calls"] == [
        {
            "tool_call_id": "call_1",
            "name": "read_file",
            "arguments": "{\"path\": \"a.txt\"}",
            "result": "文件内容",
        }
    ]
    assert first["compactions"] == [
        {
            "type": "compaction",
            "summary": "第一轮摘要",
            "covered_from_seq": 2,
            "covered_to_seq": 4,
            "created_at": "2026-01-05T12:00:02",
        }
    ]

    assert second["turn_index"] == 2
    assert second["started_at"] is None
    assert second["duration_ms"] is None
    assert second["mode"] is None
    assert second["outcome"] is None
    assert second["usage"] is None
    assert second["user"]["seq"] == 6
    assert second["assistant_text"] == "第二答"
    # 工具结果缺失时保留 result 为 None 的独立条目
    assert second["tool_calls"] == [
        {
            "tool_call_id": "call_missing",
            "name": "list_dir",
            "arguments": "{}",
            "result": None,
        }
    ]
    assert second["compactions"][0]["covered_to_seq"] == 7

    assert third["turn_index"] == 3
    assert third["user"]["seq"] == 8
    # 无法归属的压缩事件归入最后一轮
    assert third["compactions"] == [
        {
            "type": "compaction",
            "summary": "无法归属的摘要",
            "covered_from_seq": 999,
            "covered_to_seq": 999,
            "created_at": "2026-01-05T12:00:05",
        }
    ]


def test_trace_ignores_plan_step_instructions(client):
    store = client.app.state.store
    session_id = "trace002"
    _insert_session(store, session_id, "计划轨迹会话")
    store.append_messages(
        session_id,
        [
            SessionMessage(role="user", content="生成今日日报", seq=1),
            SessionMessage(
                role="user",
                content=f"{PLAN_STEP_TASK_PREFIX}查询资讯\n请完成该步骤并给出结果。",
                seq=2,
            ),
            SessionMessage(role="assistant", content="查询结果", seq=3),
            SessionMessage(
                role="user",
                content=f"{PLAN_STEP_TASK_PREFIX}汇总输出\n请完成该步骤并给出结果。",
                seq=4,
            ),
            SessionMessage(role="assistant", content="最终日报", seq=5),
        ],
        "2026-01-07T00:00:00",
    )
    store.append_turn(
        TurnRecord(
            session_id=session_id,
            turn_index=1,
            started_at="2026-01-07T00:00:01",
            finished_at="2026-01-07T00:00:09",
            duration_ms=8000,
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            outcome="completed",
            mode="plan",
        )
    )

    response = client.get(f"/api/sessions/{session_id}/trace")
    assert response.status_code == 200
    body = response.json()
    # 计划步骤指令不产生额外的交互轮次
    assert body["total_turns"] == 1
    turn = body["turns"][0]
    assert turn["user"]["content"] == "生成今日日报"
    assert turn["mode"] == "plan"
    assert turn["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
    }
    assert turn["assistant_text"] == "查询结果\n\n最终日报"


def test_trace_uses_default_system_prompt_when_not_stored(client):
    store = client.app.state.store
    _insert_session(store, "plain001", "无系统提示")
    store.append_messages(
        "plain001",
        [
            SessionMessage(role="user", content="你好", seq=1),
            SessionMessage(role="assistant", content="你好，有什么可以帮你", seq=2),
        ],
        "2026-01-06T00:00:00",
    )

    response = client.get("/api/sessions/plain001/trace")
    assert response.status_code == 200
    body = response.json()
    assert body["system_prompt_source"] == "default"
    assert body["system_prompt"] == REACT_SYSTEM_PROMPT
    assert body["total_turns"] == 1
    assert body["usage"] is None
    assert body["turns"][0]["usage"] is None
    assert body["turns"][0]["assistant_text"] == "你好，有什么可以帮你"


def test_trace_missing_session_returns_404(client):
    response = client.get("/api/sessions/missing0/trace")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
