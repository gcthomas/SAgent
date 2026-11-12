"""Web 对话流式、运行取消与审批决策接口单元测试。

使用临时 SQLite 会话库与假 LLM 客户端构造应用，覆盖消息校验、会话存在性、
并发冲突、正常 SSE 流、取消流程、审批决策与轮次落盘；不发起任何真实 LLM 请求。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import FakeLLMClient, make_tool_call, text_response, tool_response
from sagent.config.models import AppConfig
from sagent.permissions import PermissionRequest, build_default_policy
from sagent.server.app import create_app
from sagent.server.runtime import RuntimeRegistry

# 测试等待上限，避免依赖真实时钟长等待
_WAIT_TIMEOUT = 5.0


class BlockingLLMClient:
    """可阻塞的假 LLM 客户端，用于制造运行窗口。

    ``chat`` 在首次调用时通知 ``entered`` 并等待 ``release``，从而让测试
    在运行进行中发送取消或并发请求。
    """

    def __init__(self, response: Any) -> None:
        self.config = type("Config", (), {"stream": False})()
        self._response = response
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, on_event=None):  # noqa: ANN001
        """记录调用参数，阻塞到测试放行后返回预设响应。"""
        self.calls.append({"messages": messages, "tools": tools})
        self.entered.set()
        if not self.release.wait(timeout=_WAIT_TIMEOUT):
            raise AssertionError("测试等待假 LLM 放行超时")
        return self._response


def _make_config(tmp_path: Path) -> AppConfig:
    """构造不依赖真实 LLM 与外部资源的测试配置。"""
    return AppConfig(
        llm={"model": "gpt-4o-mini", "api_key": "test"},
        context={"enable_summary": False},
        planning={"strategy": "never"},
        memory={"enabled": False},
        skills={"enabled": False},
        session={"db_path": str(tmp_path / "sessions.db"), "enable_fts": False},
        permissions={"enabled": False},
        server={"static_dir": str(tmp_path / "missing-dist")},
    )


def _make_app(tmp_path: Path, llm: Any) -> FastAPI:
    """构造应用，并把共享 LLM 与运行时注册表替换为测试替身。"""
    config = _make_config(tmp_path)
    app = create_app(config)
    store = app.state.store
    app.state.llm = llm
    app.state.runtimes = RuntimeRegistry(config=config, store=store, llm=llm)
    return app


def _create_session(client: TestClient) -> str:
    """通过会话接口创建一个测试会话并返回其 id。"""
    response = client.post("/api/sessions", json={"title": "测试会话"})
    assert response.status_code == 201
    return response.json()["id"]


def _parse_frames(body: str) -> list[dict[str, Any]]:
    """解析 SSE 响应体为 JSON 部件列表，[DONE] 记为 ``{"_done": True}``。"""
    frames: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        line = block.strip()
        if not line.startswith("data: "):
            continue
        data = line[len("data: ") :]
        if data == "[DONE]":
            frames.append({"_done": True})
            continue
        frames.append(json.loads(data))
    return frames


def _run_statuses(frames: list[dict[str, Any]]) -> list[str]:
    """提取全部 data-run-status 的状态值。"""
    return [
        frame["data"]["status"]
        for frame in frames
        if frame.get("type") == "data-run-status"
    ]


def _wait_until(predicate: Callable[[], Any], message: str) -> Any:
    """轮询等待条件成立，超时抛出断言错误。"""
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(message)


def test_empty_message_returns_400_without_creating_run(tmp_path: Path) -> None:
    """空白消息返回 400，且不创建会话运行时。"""
    app = _make_app(tmp_path, FakeLLMClient([]))
    with TestClient(app) as client:
        session_id = _create_session(client)

        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "   ", "message_id": None},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "bad_request"
        assert app.state.runtimes.get(session_id) is None


def test_unknown_session_returns_404(tmp_path: Path) -> None:
    """不存在的会话返回 404。"""
    app = _make_app(tmp_path, FakeLLMClient([]))
    with TestClient(app) as client:
        response = client.post(
            "/api/sessions/not-exist/messages",
            json={"message": "你好"},
        )

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


def test_stream_completes_and_persists_turn(tmp_path: Path) -> None:
    """正常流式对话输出文本增量、finish 与 [DONE]，并落盘轮次记录。"""
    llm = FakeLLMClient(
        [text_response("你好，我是助手", usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})],
        stream=True,
    )
    app = _make_app(tmp_path, llm)
    with TestClient(app) as client:
        session_id = _create_session(client)

        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "你好"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-vercel-ai-data-stream"] == "v1"

        frames = _parse_frames(response.text)
        types = [frame.get("type") for frame in frames]
        assert "text-delta" in types
        assert "finish" in types
        assert frames[-1] == {"_done": True}
        assert _run_statuses(frames)[-1] == "completed"

        store = app.state.store
        turns = _wait_until(
            lambda: store.list_turns(session_id),
            "等待轮次记录落盘超时",
        )
        assert len(turns) == 1
        assert turns[0].outcome == "completed"
        assert turns[0].total_tokens >= 0

        # 用户消息由引擎写入上下文，并由会话自动保存到存储
        messages = store.load_messages(session_id)
        assert any(message.role == "user" and message.content == "你好" for message in messages)


def test_second_concurrent_request_returns_409(tmp_path: Path) -> None:
    """同一会话已有进行中的运行时，第二个请求返回 409。"""
    llm = BlockingLLMClient(text_response("完成"))
    app = _make_app(tmp_path, llm)
    with TestClient(app) as client:
        session_id = _create_session(client)
        results: list[Any] = []

        def _first_request() -> None:
            results.append(
                client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"message": "第一次"},
                )
            )

        thread = threading.Thread(target=_first_request, name="first-chat")
        thread.start()
        try:
            assert llm.entered.wait(_WAIT_TIMEOUT), "等待首个运行进入 LLM 调用超时"
            second = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"message": "第二次"},
            )
            assert second.status_code == 409
            assert second.json()["code"] == "conflict"
        finally:
            llm.release.set()
            thread.join(timeout=_WAIT_TIMEOUT)
        assert results and results[0].status_code == 200


def test_cancel_running_request_marks_turn_cancelled(tmp_path: Path) -> None:
    """运行中调用取消接口，终态为 cancelled，且轮次记录保留。"""
    llm = BlockingLLMClient(
        tool_response([make_tool_call("read_file", {"path": "不存在.txt"})])
    )
    app = _make_app(tmp_path, llm)
    with TestClient(app) as client:
        session_id = _create_session(client)
        results: list[Any] = []

        def _first_request() -> None:
            results.append(
                client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"message": "读取文件"},
                )
            )

        thread = threading.Thread(target=_first_request, name="cancel-chat")
        thread.start()
        try:
            assert llm.entered.wait(_WAIT_TIMEOUT), "等待运行进入 LLM 调用超时"
            runtime = app.state.runtimes.get(session_id)
            assert runtime is not None
            run_id = _wait_until(
                lambda: runtime.active_run,
                "等待活动运行登记超时",
            ).run_id

            cancel = client.post(f"/api/runs/{run_id}/cancel")
            assert cancel.status_code == 202
            assert cancel.json() == {"status": "cancelling"}

            # 再次取消已取消的运行返回 409
            again = client.post(f"/api/runs/{run_id}/cancel")
            assert again.status_code == 409
        finally:
            llm.release.set()
            thread.join(timeout=_WAIT_TIMEOUT)

        assert results and results[0].status_code == 200
        frames = _parse_frames(results[0].text)
        assert _run_statuses(frames)[-1] == "cancelled"

        store = app.state.store
        turns = _wait_until(
            lambda: [turn for turn in store.list_turns(session_id) if turn.outcome == "cancelled"],
            "等待取消轮次落盘超时",
        )
        assert len(turns) == 1


def test_cancel_unknown_run_returns_404(tmp_path: Path) -> None:
    """取消不存在的运行返回 404。"""
    app = _make_app(tmp_path, FakeLLMClient([]))
    with TestClient(app) as client:
        response = client.post("/api/runs/not-exist/cancel")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


def _wait_for_pending(handler, timeout: float = _WAIT_TIMEOUT) -> str:  # noqa: ANN001
    """等待审批器登记待决策审批并返回其 id。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending_ids = handler.pending_ids
        if pending_ids:
            return pending_ids[0]
        time.sleep(0.01)
    raise AssertionError("等待审批登记超时")


def test_approval_decision_and_repeat_returns_409(tmp_path: Path) -> None:
    """审批接口返回 approved，重复提交返回 409，非法动作返回 400。"""
    app = _make_app(tmp_path, FakeLLMClient([]))
    with TestClient(app) as client:
        session_id = _create_session(client)
        runtime = app.state.runtimes.get_or_create(session_id)
        results: list[Any] = []

        def _request_approval() -> None:
            results.append(
                runtime.approval.approve(
                    PermissionRequest(tool="write_file", args={"path": "a.txt", "content": "x"}),
                    build_default_policy(),
                )
            )

        thread = threading.Thread(target=_request_approval, name="approval-waiter")
        thread.start()
        approval_id = _wait_for_pending(runtime.approval)

        response = client.post(
            f"/api/approvals/{approval_id}",
            json={"action": "approve"},
        )
        assert response.status_code == 200
        assert response.json() == {"approval_id": approval_id, "status": "approved"}
        thread.join(timeout=_WAIT_TIMEOUT)
        assert not thread.is_alive()

        repeat = client.post(
            f"/api/approvals/{approval_id}",
            json={"action": "approve"},
        )
        assert repeat.status_code == 409
        assert repeat.json()["code"] == "conflict"

        invalid = client.post(
            f"/api/approvals/{approval_id}",
            json={"action": "bogus"},
        )
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "bad_request"

        missing = client.post(
            "/api/approvals/not-exist",
            json={"action": "approve"},
        )
        assert missing.status_code == 404
