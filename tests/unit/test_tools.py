"""内置工具与注册表单元测试。"""

from __future__ import annotations

import datetime
import sys

from sagent.permissions import (
    ApprovalHandler,
    ConfirmAction,
    Decision,
    PermissionEnforcer,
    PermissionPolicy,
    PermissionRequest,
    PermissionRule,
    build_default_policy,
)
from sagent.tools import build_default_registry
from sagent.tools.file_tools import ReadFileTool, WriteFileTool
from sagent.tools.registry import ToolRegistry
from sagent.tools.shell_tool import ShellArgs, ShellTool


def test_to_openai_schema_shape():
    tool = ReadFileTool()
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert "parameters" in schema["function"]
    # pydantic 生成的 JSON schema 应包含 path 属性
    assert "path" in schema["function"]["parameters"]["properties"]


def test_write_then_read_roundtrip(tmp_path):
    registry = build_default_registry()
    target = tmp_path / "sub" / "note.txt"

    write_result = registry.execute(
        "write_file", {"path": str(target), "content": "你好，世界"}
    )
    assert "写入文件" in write_result
    # 自动创建父目录并写入成功
    assert target.exists()

    read_result = registry.execute("read_file", {"path": str(target)})
    assert read_result == "你好，世界"


def test_append_mode(tmp_path):
    registry = build_default_registry()
    target = tmp_path / "log.txt"
    registry.execute("write_file", {"path": str(target), "content": "a"})
    registry.execute(
        "write_file", {"path": str(target), "content": "b", "mode": "append"}
    )
    assert registry.execute("read_file", {"path": str(target)}) == "ab"


def test_read_missing_file_returns_error(tmp_path):
    registry = build_default_registry()
    result = registry.execute("read_file", {"path": str(tmp_path / "none.txt")})
    assert result.startswith("错误")


def test_unknown_tool_returns_error():
    registry = ToolRegistry()
    result = registry.execute("no_such_tool", {})
    assert result.startswith("错误")
    assert "no_such_tool" in result


def test_invalid_json_arguments_returns_error(tmp_path):
    registry = build_default_registry()
    # 非法 JSON 字符串
    result = registry.execute("read_file", "{not json}")
    assert result.startswith("错误")


def test_arg_validation_error_returns_error():
    registry = build_default_registry()
    # 缺少必填的 path 参数
    result = registry.execute("read_file", {})
    assert result.startswith("错误")


def test_register_empty_name_raises():
    registry = ToolRegistry()

    class Bad(WriteFileTool):
        name = ""

    import pytest

    with pytest.raises(ValueError):
        registry.register(Bad())


def test_run_shell_with_quoted_args():
    """验证 run_shell 能正确执行带双引号参数的命令，不被 shell 二次解析。"""
    tool = ShellTool()
    if sys.platform == "win32":
        # Windows 上通过 PowerShell -EncodedCommand 执行，双引号参数不应被破坏
        args = ShellArgs(command='Get-Date -Format "yyyy-MM-dd"')
        result = tool.run(args)
        assert "退出码: 0" in result
        # 输出应包含当天日期（yyyy-MM-dd 格式）
        today = datetime.date.today().strftime("%Y-%m-%d")
        assert today in result
    else:
        args = ShellArgs(command='echo "hello world"')
        result = tool.run(args)
        assert "退出码: 0" in result
        assert "hello world" in result


def test_run_shell_chinese_output_decodes_utf8():
    """Windows 上 PowerShell 中文输出应正确显示，不出现乱码。"""
    if sys.platform != "win32":
        return
    tool = ShellTool()
    result = tool.run(ShellArgs(command="Write-Output 中文测试"))
    assert "退出码: 0" in result
    assert "中文测试" in result


def test_run_shell_error_strips_clixml():
    """Windows PowerShell 重定向错误流时不应返回 CLIXML 序列化文本。"""
    if sys.platform != "win32":
        return
    tool = ShellTool()
    result = tool.run(ShellArgs(command="Write-Error 出错了"))
    assert "#< CLIXML" not in result
    assert "出错了" in result


def test_run_shell_suppresses_progress_clixml():
    """Windows PowerShell 的进度记录不应以 CLIXML 噪音返回。"""
    if sys.platform != "win32":
        return
    tool = ShellTool()
    result = tool.run(ShellArgs(command="Write-Progress -Activity test; Write-Output done"))
    assert "#< CLIXML" not in result
    assert "done" in result


# ---------- 权限拦截（permission 注入） ----------


class _DenyAllApproval(ApprovalHandler):
    """拒绝一切的假审批器：ask 决策会按其结果被拦截。"""

    def approve(self, request: PermissionRequest, policy: PermissionPolicy) -> ConfirmAction:
        return ConfirmAction.DENIED


class _AllowAllApproval(ApprovalHandler):
    """批准一切的假审批器：ask 决策会按其结果放行。"""

    def approve(self, request: PermissionRequest, policy: PermissionPolicy) -> ConfirmAction:
        return ConfirmAction.APPROVED


def test_registry_permission_denies_dangerous_shell():
    """默认策略下危险命令直接拒绝，不进入审批也不执行。"""
    registry = build_default_registry(
        permission=PermissionEnforcer(build_default_policy(), _DenyAllApproval())
    )
    result = registry.execute("run_shell", {"command": "rm -rf /"})
    assert result.startswith("错误: 工具 'run_shell' 未获用户批准，已拒绝执行")


def test_registry_permission_denies_write_without_approval(tmp_path):
    """默认策略下 write_file 为 ask，审批拒绝时不落盘并返回拒绝说明。"""
    registry = build_default_registry(
        permission=PermissionEnforcer(build_default_policy(), _DenyAllApproval())
    )
    target = tmp_path / "blocked.txt"
    result = registry.execute("write_file", {"path": str(target), "content": "x"})
    assert result.startswith("错误: 工具 'write_file' 未获用户批准，已拒绝执行")
    # 被拒绝的工具调用不应产生副作用
    assert not target.exists()


def test_registry_permission_allows_write_after_approval(tmp_path):
    """ask 决策经审批批准后正常执行。"""
    registry = build_default_registry(
        permission=PermissionEnforcer(build_default_policy(), _AllowAllApproval())
    )
    target = tmp_path / "allowed.txt"
    registry.execute("write_file", {"path": str(target), "content": "x"})
    # write_file 放行后，read_file 按内置默认 allow 自动放行
    assert registry.execute("read_file", {"path": str(target)}) == "x"


def test_registry_permission_user_deny_rule_blocks_read(tmp_path):
    """用户 deny 规则可拒绝内置默认放行的只读工具。"""
    policy = build_default_policy(
        [PermissionRule(tool="read_file", pattern=None, decision=Decision.DENY)]
    )
    registry = build_default_registry(
        permission=PermissionEnforcer(policy, _AllowAllApproval())
    )
    result = registry.execute("read_file", {"path": str(tmp_path / "any.txt")})
    assert result.startswith("错误: 工具 'read_file' 未获用户批准，已拒绝执行")


def test_registry_direct_construction_with_permission(tmp_path):
    """ToolRegistry 直接构造时同样支持注入权限执行器。"""
    registry = ToolRegistry(
        permission=PermissionEnforcer(build_default_policy(), _DenyAllApproval())
    )
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    target = tmp_path / "direct.txt"
    result = registry.execute("write_file", {"path": str(target), "content": "x"})
    assert result.startswith("错误: 工具 'write_file' 未获用户批准，已拒绝执行")
    assert not target.exists()


def test_registry_permission_none_keeps_automatic_execution():
    """未注入权限执行器时保持旧行为：工具全自动执行。"""
    registry = build_default_registry()
    result = registry.execute("run_shell", {"command": "echo ok"})
    assert "退出码: 0" in result


def test_registry_denied_call_skips_tool_call_event(caplog):
    """被权限拒绝的调用不产生 tool_call 事件，只产生 permission_decision 审计。"""
    import logging

    caplog.set_level(logging.INFO, logger="sagent.tools.registry")
    caplog.set_level(logging.INFO, logger="sagent.permissions.enforcer")
    registry = build_default_registry(
        permission=PermissionEnforcer(build_default_policy(), _DenyAllApproval())
    )
    result = registry.execute("write_file", {"path": "blocked.txt", "content": "x"})
    assert result.startswith("错误: 工具 'write_file' 未获用户批准，已拒绝执行")
    tool_call_events = [r for r in caplog.records if getattr(r, "event", None) == "tool_call"]
    assert tool_call_events == []


def test_registry_allowed_call_logs_tool_call_after_permission(caplog):
    """放行的调用在权限决策之后记录 tool_call 事件。"""
    import logging

    caplog.set_level(logging.INFO, logger="sagent.tools.registry")
    caplog.set_level(logging.INFO, logger="sagent.permissions.enforcer")
    registry = build_default_registry(
        permission=PermissionEnforcer(build_default_policy(), _AllowAllApproval())
    )
    registry.execute("write_file", {"path": "allowed_log.txt", "content": "x"})
    events = [getattr(r, "event", None) for r in caplog.records]
    # tool_call 出现在 permission_decision 之后，且确有两条事件
    assert "permission_decision" in events
    assert "tool_call" in events
    assert events.index("permission_decision") < events.index("tool_call")
