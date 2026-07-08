"""配置加载单元测试。"""

from __future__ import annotations

import pytest

from sagent.config.loader import ENV_API_KEY, ENV_API_URL, ConfigError, load_config


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
agent:
  mode: plan
  max_iterations: 3
""",
    )
    config = load_config(str(path))
    assert config.llm.model == "gpt-4o-mini"
    assert config.llm.api_key == "sk-test"
    assert config.llm.temperature == 0.3
    assert config.agent.mode == "plan"
    assert config.agent.max_iterations == 3


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
