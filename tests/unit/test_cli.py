"""CLI 参数解析与引擎选择的针对性测试。

只覆盖 app.py 中含逻辑的部分（参数解析、模式覆盖、引擎选择），
不测试打印横幅与交互式输入循环等纯 IO 代码。
"""

from __future__ import annotations

import sys

from sagent.cli.app import _build_parser, _build_permission, build_engine, resolve_mode
from sagent.cli.commands import parse_command
from sagent.config.models import AgentConfig, PermissionConfig
from sagent.core.plan_engine import PlanEngine
from sagent.core.react_engine import ReActEngine
from sagent.permissions import Decision, PermissionEnforcer
from sagent.tools import build_default_registry


def test_parser_defaults():
    args = _build_parser().parse_args([])
    assert args.config is None
    assert args.mode is None


def test_parser_parses_mode_and_config():
    args = _build_parser().parse_args(["--mode", "plan", "--config", "my.yaml"])
    assert args.mode == "plan"
    assert args.config == "my.yaml"


def test_parser_rejects_invalid_mode():
    import pytest

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--mode", "invalid"])


def test_resolve_mode_prefers_cli_arg():
    # 命令行 --mode 优先于配置默认值
    assert resolve_mode("plan", "react") == "plan"


def test_resolve_mode_falls_back_to_config():
    # 未指定命令行 --mode 时回退到配置默认值
    assert resolve_mode(None, "plan") == "plan"


def test_build_engine_react():
    engine = build_engine(
        "react", llm=None, registry=build_default_registry(), agent_config=AgentConfig()
    )
    assert isinstance(engine, ReActEngine)


def test_build_engine_plan():
    engine = build_engine(
        "plan", llm=None, registry=build_default_registry(), agent_config=AgentConfig()
    )
    assert isinstance(engine, PlanEngine)


def test_parse_command_simple_name():
    """解析仅含命令名的简单斜杠命令。"""
    result = parse_command("/new")
    assert result is not None
    assert result.name == "new"
    assert result.args == []


def test_parse_command_with_single_arg():
    """解析带单个参数的斜杠命令。"""
    result = parse_command("/switch abc123")
    assert result is not None
    assert result.name == "switch"
    assert result.args == ["abc123"]


def test_parse_command_with_multiple_args():
    """解析带多个参数的斜杠命令，参数按空格分割。"""
    result = parse_command("/rename my new title")
    assert result is not None
    assert result.name == "rename"
    assert result.args == ["my", "new", "title"]


def test_parse_command_search_keyword():
    """解析 search 命令的多个关键词参数。"""
    result = parse_command("/search hello world")
    assert result is not None
    assert result.name == "search"
    assert result.args == ["hello", "world"]


def test_parse_command_help():
    """解析 help 命令（无参数）。"""
    result = parse_command("/help")
    assert result is not None
    assert result.name == "help"
    assert result.args == []


def test_parse_command_session():
    """解析 session 命令（无参数）。"""
    result = parse_command("/session")
    assert result is not None
    assert result.name == "session"
    assert result.args == []


def test_parse_command_unknown_command():
    """parse_command 不校验命令是否有效，仅解析结构。"""
    result = parse_command("/unknown")
    assert result is not None
    assert result.name == "unknown"
    assert result.args == []


def test_parse_command_slash_only():
    """仅输入 / 视为未知命令，name 为空字符串。"""
    result = parse_command("/")
    assert result is not None
    assert result.name == ""
    assert result.args == []


def test_parse_command_non_slash_input_returns_none():
    """非斜杠开头的普通输入返回 None。"""
    assert parse_command("hello world") is None


def test_parse_command_empty_string_returns_none():
    """空字符串返回 None。"""
    assert parse_command("") is None


def test_parse_command_no_leading_slash_returns_none():
    """前导空格导致不以 / 开头，返回 None。"""
    assert parse_command(" new") is None


# ---------- 权限组装（_build_permission） ----------


class _NonTtyStdin:
    """非终端 stdin，使交互审批器在测试中确定性走 fail-safe 路径。"""

    def isatty(self) -> bool:
        return False


def test_build_permission_disabled_returns_none():
    """permissions.enabled=False 时不构建权限执行器（保持全自动执行）。"""
    assert _build_permission(PermissionConfig(enabled=False)) is None


def test_build_permission_enabled_returns_enforcer():
    """缺省启用时返回权限执行器。"""
    enforcer = _build_permission(PermissionConfig())
    assert isinstance(enforcer, PermissionEnforcer)


def test_build_permission_user_allow_rule_allows_write_file():
    """用户 allow 规则覆盖内置默认的 ask。"""
    enforcer = _build_permission(PermissionConfig(allow=["write_file"]))
    outcome = enforcer.authorize("write_file", {"path": "a.txt", "content": "x"})
    assert outcome.allowed is True
    assert outcome.decision is Decision.ALLOW


def test_build_permission_user_deny_rule_denies_read_file():
    """用户 deny 规则覆盖内置默认的 allow，返回拒绝说明。"""
    enforcer = _build_permission(PermissionConfig(deny=["read_file"]))
    outcome = enforcer.authorize("read_file", {"path": "a.txt"})
    assert outcome.allowed is False
    assert outcome.message == "错误: 工具 'read_file' 未获用户批准，已拒绝执行"
    assert outcome.decision is Decision.DENY


def test_build_permission_ask_rule_non_tty_denies(monkeypatch):
    """用户 ask 规则覆盖内置默认 allow；非交互终端按 fail-safe 拒绝。"""
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    enforcer = _build_permission(PermissionConfig(ask=["read_file"]))
    outcome = enforcer.authorize("read_file", {"path": "a.txt"})
    assert outcome.decision is Decision.ASK
    assert outcome.allowed is False


def test_build_permission_non_interactive_allow_passthrough(monkeypatch):
    """non_interactive=allow 配置须透传到审批器：非交互终端放行而非拒绝。"""
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    enforcer = _build_permission(PermissionConfig(ask=["read_file"], non_interactive="allow"))
    outcome = enforcer.authorize("read_file", {"path": "a.txt"})
    assert outcome.decision is Decision.ASK
    assert outcome.allowed is True


def test_build_permission_deny_overrides_allow_across_lists():
    """跨列表优先级 deny > allow：同请求同时命中两列表时拒绝生效。"""
    enforcer = _build_permission(
        PermissionConfig(allow=["write_file"], deny=["write_file"])
    )
    outcome = enforcer.authorize("write_file", {"path": "a.txt", "content": "x"})
    assert outcome.allowed is False
    assert outcome.decision is Decision.DENY


def test_build_permission_allow_overrides_ask_across_lists():
    """跨列表优先级 allow > ask：同请求同时命中两列表时放行生效。"""
    enforcer = _build_permission(
        PermissionConfig(ask=["read_file"], allow=["read_file"])
    )
    outcome = enforcer.authorize("read_file", {"path": "a.txt"})
    assert outcome.allowed is True
    assert outcome.decision is Decision.ALLOW


def test_build_permission_cross_list_priority_by_pattern(monkeypatch):
    """带参数模式的跨列表优先级：deny(模式命中) 压过 allow(整工具)。"""
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    enforcer = _build_permission(
        PermissionConfig(
            allow=["run_shell"],
            deny=["run_shell:rm -rf *"],
        )
    )
    outcome = enforcer.authorize("run_shell", {"command": "rm -rf /"})
    assert outcome.allowed is False
    assert outcome.decision is Decision.DENY
