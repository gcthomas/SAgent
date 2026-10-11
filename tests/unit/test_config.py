"""配置加载单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagent.config.loader import (
    ENV_API_KEY,
    ENV_API_URL,
    ENV_SUMMARY_API_KEY,
    ENV_SUMMARY_API_URL,
    ConfigError,
    load_config,
)
from sagent.config.models import PermissionConfig, PlanningConfig


def _write_config(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_ok(tmp_path):
    path = _write_config(
        tmp_path,
        """
llm:
  model: gpt-4o-mini
  api_key: sk-test
  temperature: 0.3
context:
  summary_llm:
    model: gpt-4o-mini
    api_key: sk-summary
agent:
  max_iterations: 3
planning:
  strategy: always
  max_steps: 6
  max_replans: 4
""",
    )
    config = load_config(str(path))
    assert config.llm.model == "gpt-4o-mini"
    assert config.llm.api_key == "sk-test"
    assert config.llm.temperature == 0.3
    assert config.llm.stream is True
    assert config.context.summary_llm is not None
    assert config.context.summary_llm.api_key == "sk-summary"
    assert config.agent.max_iterations == 3
    assert config.planning.strategy == "always"
    assert config.planning.max_steps == 6
    assert config.planning.max_replans == 4


def test_planning_config_defaults_and_validation():
    config = PlanningConfig()
    assert config.strategy == "adaptive"
    assert config.max_steps == 10
    assert config.max_replans == 2
    with pytest.raises(ValidationError):
        PlanningConfig(strategy="invalid")
    with pytest.raises(ValidationError):
        PlanningConfig(max_steps=0)
    with pytest.raises(ValidationError):
        PlanningConfig(max_replans=-1)


def test_llm_stream_can_be_disabled(tmp_path):
    path = _write_config(
        tmp_path,
        """
llm:
  model: gpt-4o-mini
  api_key: sk-test
  stream: false
context:
  enable_summary: false
""",
    )
    config = load_config(str(path))
    assert config.llm.stream is False


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "not_exist.yaml"))


def test_missing_api_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    path = _write_config(
        tmp_path,
        """
llm:
  model: gpt-4o-mini
""",
    )
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_env_overrides_api_key_and_url(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-from-env")
    monkeypatch.setenv(ENV_API_URL, "https://compat.example.com/v1")
    path = _write_config(
        tmp_path,
        """
llm:
  model: deepseek-chat
  api_key: sk-in-file
context:
  summary_llm:
    model: summary-model
    api_key: summary-in-file
""",
    )
    config = load_config(str(path))
    # 环境变量优先覆盖文件值
    assert config.llm.api_key == "sk-from-env"
    assert config.llm.base_url == "https://compat.example.com/v1"


def test_invalid_top_level_raises(tmp_path):
    path = _write_config(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_summary_llm_env_overrides_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-main")
    monkeypatch.setenv(ENV_SUMMARY_API_KEY, "sk-summary-env")
    monkeypatch.setenv(ENV_SUMMARY_API_URL, "https://summary.example.com/v1")
    path = _write_config(
        tmp_path,
        """
llm:
  model: main-model
context:
  summary_llm:
    model: summary-model
    api_key: summary-in-file
    base_url: https://in-file.example.com/v1
""",
    )
    config = load_config(str(path))
    assert config.context.summary_llm is not None
    assert config.context.summary_llm.api_key == "sk-summary-env"
    assert config.context.summary_llm.base_url == "https://summary.example.com/v1"


def test_summary_config_required_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-main")
    path = _write_config(tmp_path, "llm:\n  model: main-model\n")
    with pytest.raises(ConfigError, match="context.summary_llm"):
        load_config(str(path))


def test_summary_api_key_required_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-main")
    path = _write_config(
        tmp_path,
        """
llm:
  model: main-model
context:
  summary_llm:
    model: summary-model
""",
    )
    with pytest.raises(ConfigError, match="Context 摘要 LLM API Key"):
        load_config(str(path))


def test_summary_config_optional_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-main")
    path = _write_config(
        tmp_path,
        """
llm:
  model: main-model
context:
  enable_summary: false
""",
    )
    config = load_config(str(path))
    assert config.context.summary_llm is None


# ---------- PermissionConfig（权限控制配置） ----------


def test_permission_config_defaults():
    config = PermissionConfig()
    # 缺省启用权限控制，审批超时 600 秒，非交互默认拒绝
    assert config.enabled is True
    assert config.ask_timeout == 600.0
    assert config.non_interactive == "deny"
    assert config.allow == []
    assert config.deny == []
    assert config.ask == []


def test_permission_config_rejects_blank_rule():
    with pytest.raises(ValidationError):
        PermissionConfig(allow=["   "])


def test_permission_config_rejects_rule_missing_tool():
    with pytest.raises(ValidationError):
        PermissionConfig(deny=[":pattern"])


def test_permission_config_rejects_invalid_non_interactive():
    with pytest.raises(ValidationError):
        PermissionConfig(non_interactive="maybe")


def test_permission_config_rejects_negative_ask_timeout():
    # ask_timeout 必须为非负数
    with pytest.raises(ValidationError):
        PermissionConfig(ask_timeout=-1)


def test_permission_config_accepts_zero_ask_timeout():
    # 0 为合法边界值（立即超时，fail-safe 拒绝）
    config = PermissionConfig(ask_timeout=0)
    assert config.ask_timeout == 0
