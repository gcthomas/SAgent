"""CLI 参数解析与引擎选择的针对性测试。

只覆盖 app.py 中含逻辑的部分（参数解析、模式覆盖、引擎选择），
不测试打印横幅与交互式输入循环等纯 IO 代码。
"""

from __future__ import annotations

from sagent.cli.app import _build_parser, build_engine, resolve_mode
from sagent.config.models import AgentConfig
from sagent.core.plan_engine import PlanEngine
from sagent.core.react_engine import ReActEngine
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
