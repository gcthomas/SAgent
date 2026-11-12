"""Web 审批器单元测试。

覆盖：
- 页面批准 / 拒绝时返回对应 ConfirmAction，并发出 approval_required 与 approval_resolved；
- always 记忆：相同工具与规范化参数不再询问、不再发审批请求事件；
- 超时收敛：non_interactive 为 deny / allow 时的返回值与事件内容；
- 过期 / 不存在 / 已决策 / 非法动作的 resolve 异常；
- cancel_pending 以拒绝唤醒全部等待；
- 审计日志字段与级别。
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from sagent.permissions import ConfirmAction, PermissionRequest, build_default_policy
from sagent.server.approval import (
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalResolvedError,
    WebApprovalHandler,
)


class EventRecorder:
    """线程安全的事件记录器。"""

    def __init__(self) -> None:
        self.events = []
        self._lock = threading.Lock()

    def __call__(self, event) -> None:
        with self._lock:
            self.events.append(event)

    def of_type(self, event_type: str) -> list:
        """按事件类型过滤。"""
        with self._lock:
            return [event for event in self.events if event.event_type == event_type]


def _write_file_request(path: str = "a.txt", content: str = "x") -> PermissionRequest:
    """构造一个 write_file 权限请求（默认策略为 ask）。"""
    return PermissionRequest(tool="write_file", args={"path": path, "content": content})


def _wait_for_pending(handler: WebApprovalHandler, timeout: float = 2.0) -> str:
    """轮询等待审批登记，返回第一个待决策的 approval_id。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = handler.pending_ids
        if ids:
            return ids[0]
        time.sleep(0.005)
    raise AssertionError("等待审批登记超时")


def _start_approve(
    handler: WebApprovalHandler,
    request: PermissionRequest,
    results: list[ConfirmAction],
    errors: list[BaseException],
) -> threading.Thread:
    """在后台线程执行 approve，并收集结果与异常。"""

    def _run() -> None:
        try:
            results.append(handler.approve(request, build_default_policy()))
        except BaseException as exc:  # 记录线程内异常供主线程断言
            errors.append(exc)

    thread = threading.Thread(target=_run, name="web-approval-test")
    thread.start()
    return thread


def test_approve_returns_approved_and_emits_events():
    recorder = EventRecorder()
    handler = WebApprovalHandler(timeout=5.0, on_event=recorder)
    results: list[ConfirmAction] = []
    errors: list[BaseException] = []

    thread = _start_approve(handler, _write_file_request(), results, errors)
    approval_id = _wait_for_pending(handler)
    assert handler.resolve(approval_id, "approve") is ConfirmAction.APPROVED
    thread.join(timeout=3.0)

    assert errors == []
    assert results == [ConfirmAction.APPROVED]
    assert handler.pending_ids == []

    required = recorder.of_type("approval_required")
    assert len(required) == 1
    assert required[0].approval_id == approval_id
    assert required[0].tool_name == "write_file"
    assert required[0].tool_arguments == {"path": "a.txt", "content": "x"}
    assert required[0].status == "waiting_approval"
    assert "write_file" in required[0].content

    resolved = recorder.of_type("approval_resolved")
    assert len(resolved) == 1
    assert resolved[0].approval_id == approval_id
    assert resolved[0].approval_action == "approve"
    assert "通过" in resolved[0].content


def test_approve_returns_denied_and_emits_events():
    recorder = EventRecorder()
    handler = WebApprovalHandler(timeout=5.0, on_event=recorder)
    results: list[ConfirmAction] = []
    errors: list[BaseException] = []

    thread = _start_approve(handler, _write_file_request(), results, errors)
    approval_id = _wait_for_pending(handler)
    assert handler.resolve(approval_id, "deny") is ConfirmAction.DENIED
    thread.join(timeout=3.0)

    assert errors == []
    assert results == [ConfirmAction.DENIED]
    resolved = recorder.of_type("approval_resolved")
    assert len(resolved) == 1
    assert resolved[0].approval_action == "deny"
    assert "拒绝" in resolved[0].content


def test_always_cached_for_same_tool_and_canonical_args():
    recorder = EventRecorder()
    handler = WebApprovalHandler(timeout=5.0, on_event=recorder)
    results: list[ConfirmAction] = []
    errors: list[BaseException] = []

    thread = _start_approve(handler, _write_file_request(content="x"), results, errors)
    approval_id = _wait_for_pending(handler)
    assert handler.resolve(approval_id, "always") is ConfirmAction.ALLOWED_ALWAYS
    thread.join(timeout=3.0)

    assert errors == []
    assert results == [ConfirmAction.ALLOWED_ALWAYS]
    assert len(recorder.of_type("approval_required")) == 1

    # 相同工具与规范化参数（content 不参与白名单键）：直接放行且不再发审批请求
    assert (
        handler.approve(_write_file_request(content="y"), build_default_policy())
        is ConfirmAction.ALLOWED_ALWAYS
    )
    assert handler.pending_ids == []
    assert len(recorder.of_type("approval_required")) == 1


def test_timeout_denies_and_marks_expired():
    recorder = EventRecorder()
    handler = WebApprovalHandler(timeout=0.05, non_interactive="deny", on_event=recorder)

    assert handler.approve(_write_file_request(), build_default_policy()) is ConfirmAction.DENIED
    assert handler.pending_ids == []

    required = recorder.of_type("approval_required")
    assert len(required) == 1
    resolved = recorder.of_type("approval_resolved")
    assert len(resolved) == 1
    assert resolved[0].approval_action == "deny"
    assert "超时" in resolved[0].content
    assert "拒绝" in resolved[0].content

    # 过期后提交决策：抛 ApprovalExpiredError，且不改变运行状态
    with pytest.raises(ApprovalExpiredError):
        handler.resolve(required[0].approval_id, "approve")


def test_timeout_allows_when_non_interactive_allow():
    recorder = EventRecorder()
    handler = WebApprovalHandler(timeout=0.05, non_interactive="allow", on_event=recorder)

    assert handler.approve(_write_file_request(), build_default_policy()) is ConfirmAction.APPROVED
    resolved = recorder.of_type("approval_resolved")
    assert len(resolved) == 1
    assert resolved[0].approval_action == "approve"
    assert "超时" in resolved[0].content
    assert "允许" in resolved[0].content


def test_resolve_unknown_id_raises_not_found():
    handler = WebApprovalHandler(timeout=1.0)
    with pytest.raises(ApprovalNotFoundError):
        handler.resolve("missing", "approve")


def test_resolve_invalid_action_raises_value_error():
    handler = WebApprovalHandler(timeout=1.0)
    # 非法动作优先于 id 校验，直接抛 ValueError
    with pytest.raises(ValueError):
        handler.resolve("missing", "maybe")


def test_resolve_twice_raises_resolved_error():
    handler = WebApprovalHandler(timeout=5.0)
    results: list[ConfirmAction] = []
    errors: list[BaseException] = []

    thread = _start_approve(handler, _write_file_request(), results, errors)
    approval_id = _wait_for_pending(handler)
    handler.resolve(approval_id, "approve")
    thread.join(timeout=3.0)

    with pytest.raises(ApprovalResolvedError):
        handler.resolve(approval_id, "deny")


def test_cancel_pending_denies_and_emits_event():
    recorder = EventRecorder()
    handler = WebApprovalHandler(timeout=5.0, on_event=recorder)
    results: list[ConfirmAction] = []
    errors: list[BaseException] = []

    thread = _start_approve(handler, _write_file_request(), results, errors)
    approval_id = _wait_for_pending(handler)
    handler.cancel_pending("运行已取消")
    thread.join(timeout=3.0)

    assert errors == []
    assert results == [ConfirmAction.DENIED]
    assert handler.pending_ids == []

    resolved = recorder.of_type("approval_resolved")
    assert len(resolved) == 1
    assert resolved[0].approval_id == approval_id
    assert resolved[0].approval_action == "deny"
    assert "取消" in resolved[0].content

    with pytest.raises(ApprovalExpiredError):
        handler.resolve(approval_id, "approve")


def test_approve_without_event_handler():
    handler = WebApprovalHandler(timeout=0.02, non_interactive="deny")
    assert handler.approve(_write_file_request(), build_default_policy()) is ConfirmAction.DENIED


def test_configure_updates_settings():
    handler = WebApprovalHandler(timeout=10.0, non_interactive="allow")
    handler.configure(timeout=0.02, non_interactive="deny")
    assert handler.approve(_write_file_request(), build_default_policy()) is ConfirmAction.DENIED
    # 参数缺省（None）时保持当前值不变
    handler.configure()
    assert handler.approve(_write_file_request(), build_default_policy()) is ConfirmAction.DENIED


def test_audit_log_records_approval_decision(caplog):
    caplog.set_level(logging.INFO, logger="sagent.server.approval")

    results: list[ConfirmAction] = []
    errors: list[BaseException] = []
    approved_handler = WebApprovalHandler(timeout=5.0)
    thread = _start_approve(approved_handler, _write_file_request(), results, errors)
    approval_id = _wait_for_pending(approved_handler)
    approved_handler.resolve(approval_id, "approve")
    thread.join(timeout=3.0)

    record = caplog.records[-1]
    assert record.event == "permission_decision"
    assert record.levelno == logging.INFO
    assert record.decision == "ask"
    assert record.tool == "write_file"
    assert record.approval_id == approval_id
    assert record.expired is False

    # 超时拒绝：WARNING，expired 为 True
    timeout_handler = WebApprovalHandler(timeout=0.02, non_interactive="deny")
    timeout_handler.approve(_write_file_request(), build_default_policy())
    record = caplog.records[-1]
    assert record.event == "permission_decision"
    assert record.levelno == logging.WARNING
    assert record.expired is True
    assert record.tool_args == {"path": "a.txt"}
