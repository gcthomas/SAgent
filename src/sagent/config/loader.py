"""配置加载器。

从 YAML 文件加载配置，使用 pydantic 校验，并支持通过环境变量覆盖敏感/部署相关项：
- LLM_API_KEY 覆盖 llm.api_key
- LLM_API_URL 覆盖 llm.base_url
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import AppConfig

# 默认配置文件名
DEFAULT_CONFIG_PATH = "config.yaml"

# 环境变量名
ENV_API_KEY = "LLM_API_KEY"
ENV_API_URL = "LLM_API_URL"


class ConfigError(Exception):
    """配置相关错误。"""


def load_config(config_path: str | None = None) -> AppConfig:
    """从 YAML 文件加载配置并返回 AppConfig。

    参数:
        config_path: 配置文件路径，为空时使用默认路径 config.yaml。

    返回:
        校验后的 AppConfig 对象。

    异常:
        ConfigError: 当文件不存在、YAML 解析失败或校验失败时抛出。
    """
    path = Path(config_path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise ConfigError(
            f"配置文件不存在: {path}. 请复制 config.example.yaml 为 {DEFAULT_CONFIG_PATH} 并填写配置。"
        )

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("配置文件内容格式错误，顶层应为映射（键值对）。")

    # 环境变量覆盖（仅当环境变量存在时生效）
    llm_section = raw.get("llm")
    if not isinstance(llm_section, dict):
        llm_section = {}
        raw["llm"] = llm_section

    env_api_key = os.environ.get(ENV_API_KEY)
    if env_api_key:
        llm_section["api_key"] = env_api_key

    env_api_url = os.environ.get(ENV_API_URL)
    if env_api_url:
        llm_section["base_url"] = env_api_url

    try:
        config = AppConfig(**raw)
    except ValidationError as exc:
        raise ConfigError(f"配置校验失败:\n{exc}") from exc

    # 必填项检查：api_key 必须来自配置文件或环境变量
    if not config.llm.api_key:
        raise ConfigError(
            f"缺少 LLM API Key。请在配置文件 llm.api_key 中填写，或设置环境变量 {ENV_API_KEY}。"
        )

    return config
