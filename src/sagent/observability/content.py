"""内容采集策略工具。

控制 LLM 诊断内容的采集开关与长度截断。
内容采集有效值 = 观测内容采集开关 AND 日志内容开关，且始终经过脱敏器。
"""

from __future__ import annotations

from typing import Any

from ..config.models import ObservabilityConfig

# 模块级状态
_observability_config: ObservabilityConfig | None = None
_log_llm_content_flag: bool = False


def setup_content_capture(
    config: ObservabilityConfig,
    log_llm_content: bool,
) -> None:
    """初始化内容采集策略。

    参数:
        config: 可观测性配置
        log_llm_content: 日志内容开关（来自 LoggingConfig.log_llm_content）
    """
    global _observability_config, _log_llm_content_flag
    _observability_config = config
    _log_llm_content_flag = log_llm_content


def is_content_capture_enabled() -> bool:
    """内容采集有效值 = 观测内容采集开关 AND 日志内容开关。"""
    if _observability_config is None:
        return False
    return _observability_config.capture_content and _log_llm_content_flag


def get_content_max_length() -> int:
    """获取采集内容的最大字符长度。"""
    if _observability_config is None:
        return 500
    return _observability_config.content_max_length


def truncate_content(data: Any, max_length: int | None = None) -> Any:
    """递归截断字符串值到指定长度。

    参数:
        data: 要截断的数据（dict/list/str/其他）
        max_length: 最大长度，None 时使用配置值

    返回:
        截断后的数据
    """
    if max_length is None:
        max_length = get_content_max_length()
    if isinstance(data, str):
        if len(data) > max_length:
            return data[:max_length] + "...[truncated]"
        return data
    if isinstance(data, dict):
        return {k: truncate_content(v, max_length) for k, v in data.items()}
    if isinstance(data, list):
        return [truncate_content(v, max_length) for v in data]
    return data
