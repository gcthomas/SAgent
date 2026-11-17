"""UI Message Stream 适配器与事件桥单元测试。

覆盖：
- 文本增量：start 只出现一次、text-start 只出现一次、多个 text-delta 可解析；
- 工具调用：tool-input-available 输入为 dict、tool-output-available、状态推进；
- 审批：data-approval 的 pending / approved / denied 与运行状态切换；
- 用量：单次值与 run_total 累计、latency_ms 透传；
- 计划：data-plan 的任务、步骤、当前步骤与总数；
- 终态唯一：close 两次只产生一次终态与一次 [DONE]，close 后 handle 返回空；
- EventBridge：线程投递、关闭结束、关闭后丢弃、事件循环已关闭不抛异常。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

from sagent.core.events import AgentEvent
from sagent.core.plan_state import PlanState, PlanStepState
from sagent.server.event_bridge import EventBridge
from sagent.server.stream_adapter import UIStreamAdapter

DONE_MARKER = "[DONE]"


def parse_frames(frames: list[str]) -> list[Any]:
    """把 SSE 文本帧解析为 JSON 部件列表，[DONE] 保留为字符串标记。"""
    parts: list[Any] = []
    for frame in frames:
        assert frame.startswith("data: "), frame
        assert frame.endswith("\n\n"), frame
        payload = frame[len("data: ") :].strip()
        if payload == DONE_MARKER:
            parts.append(DONE_MARKER)
        else:
            parts.append(json.loads(payload))
    return parts


def of_type(parts: list[Any], type_name: str) -> list[dict[str, Any]]:
    """按部件类型过滤。"""
    return [
        part
        for part in parts
        if isinstance(part, dict) and part.get("type") == type_name
    ]


def statuses(parts: list[Any]) -> list[str]:
    """按顺序取出所有 data-run-status 的 status 值。"""
    return [part["data"]["status"] for part in of_type(parts, "data-run-status")]


def test_text_delta_frames():
    adapter = UIStreamAdapter(run_id="run-1", session_id="sess-1")
    frames = adapter.handle(AgentEvent("llm_text_delta", content="你"))
    frames += adapter.handle(AgentEvent("llm_text_delta", content="好"))
    parts = parse_frames(frames)

    assert of_type(parts, "start") == [{"type": "start", "messageId": "run-1"}]
    assert len(of_type(parts, "text-start")) == 1
    assert of_type(parts, "text-start")[0]["id"] == "run-1"
    deltas = of_type(parts, "text-delta")
    assert [item["delta"] for item in deltas] == ["你", "好"]
    assert all(item["id"] == "run-1" for item in deltas)

    first_status = of_type(parts, "data-run-status")[0]["data"]
    assert first_status["run_id"] == "run-1"
    assert first_status["session_id"] == "sess-1"
    assert first_status["status"] == "thinking"


def test_tool_call_and_result_frames():
    adapter = UIStreamAdapter(run_id="run-2")
    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "tool_call",
                tool_name="read_file",
                tool_arguments='{"path": "a.txt"}',
                tool_call_id="call-1",
                iteration=1,
            )
        )
    )

    tool_input = of_type(parts, "tool-input-available")[0]
    assert tool_input["toolCallId"] == "call-1"
    assert tool_input["toolName"] == "read_file"
    assert tool_input["input"] == {"path": "a.txt"}
    assert statuses(parts) == ["thinking", "tool_calling", "executing"]

    # tool-input-available 夹在 tool_calling 与 executing 之间
    frame_types = [
        f"status:{part['data']['status']}"
        if isinstance(part, dict) and part.get("type") == "data-run-status"
        else part.get("type")
        for part in parts
    ]
    assert frame_types == [
        "start",
        "status:thinking",
        "status:tool_calling",
        "tool-input-available",
        "status:executing",
    ]

    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "tool_result",
                content="文件内容",
                tool_name="read_file",
                tool_call_id="call-1",
                iteration=1,
            )
        )
    )
    assert of_type(parts, "tool-output-available") == [
        {"type": "tool-output-available", "toolCallId": "call-1", "output": "文件内容"}
    ]
    assert statuses(parts) == ["thinking"]


def test_approval_flow():
    adapter = UIStreamAdapter(run_id="run-3", session_id="sess-3")
    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "approval_required",
                content="需要确认",
                tool_name="write_file",
                tool_arguments={"path": "a.txt"},
                approval_id="ap-1",
            )
        )
    )

    assert of_type(parts, "data-approval")[0]["data"] == {
        "approval_id": "ap-1",
        "tool_name": "write_file",
        "arguments": {"path": "a.txt"},
        "reason": "需要确认",
        "status": "pending",
        "expires_at": None,
    }
    assert statuses(parts)[-1] == "waiting_approval"

    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "approval_resolved",
                content="审批已通过",
                tool_name="write_file",
                tool_arguments={"path": "a.txt"},
                approval_id="ap-1",
                approval_action="approve",
            )
        )
    )
    assert of_type(parts, "data-approval")[0]["data"]["status"] == "approved"
    assert statuses(parts)[-1] == "executing"


@pytest.mark.parametrize(
    ("action", "expected"),
    [("approve", "approved"), ("always", "approved"), ("deny", "denied")],
)
def test_approval_resolved_status_mapping(action: str, expected: str):
    adapter = UIStreamAdapter(run_id="run-approval")
    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "approval_resolved",
                tool_name="run_shell",
                tool_arguments={"command": "dir"},
                approval_id="ap-2",
                approval_action=action,
            )
        )
    )
    assert of_type(parts, "data-approval")[0]["data"]["status"] == expected


def test_usage_accumulates_run_total():
    adapter = UIStreamAdapter(run_id="run-4")
    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "llm_completed",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                latency_ms=120.5,
            )
        )
    )
    data = of_type(parts, "data-usage")[0]["data"]
    assert (
        data["prompt_tokens"],
        data["completion_tokens"],
        data["total_tokens"],
    ) == (10, 5, 15)
    assert data["latency_ms"] == 120.5
    assert data["run_total"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
        "latency_ms": 120.5,
    }

    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "llm_completed",
                usage={"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
                latency_ms=80.0,
            )
        )
    )
    data = of_type(parts, "data-usage")[0]["data"]
    assert data["latency_ms"] == 80.0
    assert data["run_total"] == {
        "prompt_tokens": 30,
        "completion_tokens": 12,
        "total_tokens": 42,
        "cached_tokens": 0,
        "latency_ms": 200.5,
    }

    # 未携带 usage 时不产生 data-usage
    parts = parse_frames(adapter.handle(AgentEvent("llm_completed")))
    assert of_type(parts, "data-usage") == []


def test_context_snapshot_frames():
    """提供 context_snapshot 时，llm_completed 与 close 各下发一次 data-context。"""
    snapshots = [{"used_tokens": 1200, "max_tokens": 128000}]
    adapter = UIStreamAdapter(run_id="run-ctx", context_snapshot=lambda: snapshots[0])
    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "llm_completed",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                latency_ms=10.0,
            )
        )
    )
    assert of_type(parts, "data-context") == [
        {
            "type": "data-context",
            "data": {"run_id": "run-ctx", "used_tokens": 1200, "max_tokens": 128000},
        }
    ]

    # 本轮结束后上下文继续增长，close 时下发最新占用
    snapshots[0] = {"used_tokens": 3400, "max_tokens": 128000}
    parts = parse_frames(adapter.close("completed"))
    assert of_type(parts, "data-context")[-1]["data"]["used_tokens"] == 3400


def test_context_snapshot_absent_emits_nothing():
    """未提供 context_snapshot 时不下发 data-context。"""
    adapter = UIStreamAdapter(run_id="run-no-ctx")
    parts = parse_frames(adapter.handle(AgentEvent("llm_completed")))
    assert of_type(parts, "data-context") == []
    parts = parse_frames(adapter.close("completed"))
    assert of_type(parts, "data-context") == []


def _build_plan_state() -> PlanState:
    """构造 3 步计划：第 1 步完成、第 2 步执行中、第 3 步待执行。"""
    return PlanState(
        version=1,
        task="部署服务",
        steps=[
            PlanStepState(
                title="安装依赖",
                description="安装项目依赖",
                status="completed",
                summary="已完成安装",
            ),
            PlanStepState(
                title="启动服务",
                description="启动后端服务",
                status="running",
            ),
            PlanStepState(
                title="验证服务",
                description="验证健康检查接口",
                status="pending",
            ),
        ],
        current_step=2,
        replans=1,
        status="executing",
    )


def test_plan_snapshot_frames():
    adapter = UIStreamAdapter(run_id="run-5", plan_snapshot=_build_plan_state)
    parts = parse_frames(
        adapter.handle(
            AgentEvent(
                "plan_step_started",
                plan_step_index=2,
                plan_step_total=3,
                plan_step="启动服务",
            )
        )
    )

    plans = of_type(parts, "data-plan")
    assert len(plans) == 1
    plan = plans[0]["data"]
    assert plan["task"] == "部署服务"
    assert plan["status"] == "executing"
    assert plan["current_step"] == 2
    assert plan["total_steps"] == 3
    assert plan["replans"] == 1
    assert [step["index"] for step in plan["steps"]] == [1, 2, 3]
    assert [step["title"] for step in plan["steps"]] == ["安装依赖", "启动服务", "验证服务"]
    assert [step["status"] for step in plan["steps"]] == [
        "completed",
        "running",
        "pending",
    ]
    assert plan["steps"][0]["description"] == "安装项目依赖"
    assert plan["steps"][0]["summary"] == "已完成安装"

    status_data = of_type(parts, "data-run-status")[-1]["data"]
    assert status_data["status"] == "executing"
    assert status_data["plan_step_index"] == 2
    assert status_data["plan_step_total"] == 3
    assert status_data["plan_step_title"] == "启动服务"


def test_plan_snapshot_missing_skips_data_plan():
    adapter = UIStreamAdapter(run_id="run-6")
    parts = parse_frames(adapter.handle(AgentEvent("plan_started", content="计划开始")))
    assert of_type(parts, "data-plan") == []

    adapter = UIStreamAdapter(run_id="run-6b", plan_snapshot=lambda: None)
    parts = parse_frames(adapter.handle(AgentEvent("plan_started", content="计划开始")))
    assert of_type(parts, "data-plan") == []


def test_close_emits_terminal_once():
    adapter = UIStreamAdapter(run_id="run-7")
    adapter.handle(AgentEvent("llm_text_delta", content="hi"))
    frames = adapter.close("completed")
    frames += adapter.close("failed", "不应输出")
    frames += adapter.handle(AgentEvent("llm_text_delta", content="x"))
    parts = parse_frames(frames)

    terminal = [
        status
        for status in statuses(parts)
        if status in ("completed", "failed", "cancelled")
    ]
    assert terminal == ["completed"]
    assert len(of_type(parts, "text-end")) == 1
    assert len(of_type(parts, "finish")) == 1
    assert parts.count(DONE_MARKER) == 1
    types = [part.get("type") if isinstance(part, dict) else part for part in parts]
    assert types[-4:] == ["data-run-status", "data-turn-usage", "finish", DONE_MARKER]

    # close 后 handle 直接返回空
    assert adapter.handle(AgentEvent("llm_text_delta", content="y")) == []


def test_close_without_handle_emits_start_and_terminal():
    adapter = UIStreamAdapter(run_id="run-8")
    parts = parse_frames(adapter.close("cancelled", "用户取消"))

    assert of_type(parts, "start") == [{"type": "start", "messageId": "run-8"}]
    terminal = [
        part
        for part in of_type(parts, "data-run-status")
        if part["data"]["status"] in ("completed", "failed", "cancelled")
    ]
    assert len(terminal) == 1
    assert terminal[0]["data"]["status"] == "cancelled"
    assert terminal[0]["data"]["message"] == "用户取消"
    assert len(of_type(parts, "finish")) == 1
    assert parts.count(DONE_MARKER) == 1


def test_event_bridge_forwards_from_thread_and_closes():
    async def scenario() -> list[AgentEvent]:
        loop = asyncio.get_running_loop()
        bridge = EventBridge(loop)
        received: list[AgentEvent] = []

        def worker() -> None:
            bridge.emit(AgentEvent("notice", content="第一"))
            bridge.emit(AgentEvent("notice", content="第二"))
            bridge.close()

        thread = threading.Thread(target=worker)
        thread.start()
        async for event in bridge.events():
            received.append(event)
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert bridge.closed is True
        return received

    received = asyncio.run(scenario())
    assert [event.content for event in received] == ["第一", "第二"]


def test_event_bridge_drops_events_after_close():
    async def scenario() -> list[AgentEvent]:
        loop = asyncio.get_running_loop()
        bridge = EventBridge(loop)
        bridge.close()
        bridge.emit(AgentEvent("notice", content="迟到事件"))
        bridge.close()
        return [event async for event in bridge.events()]

    assert asyncio.run(scenario()) == []


def test_event_bridge_ignores_closed_loop():
    loop = asyncio.new_event_loop()
    loop.close()
    bridge = EventBridge(loop)

    # 事件循环已关闭：emit / close 均不得抛异常
    bridge.emit(AgentEvent("notice", content="丢弃"))
    bridge.close()
    assert bridge.closed is True
