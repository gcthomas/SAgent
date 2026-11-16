"""ContextManager 用量快照单元测试。

覆盖 record_llm_usage 的累计行为、usage_snapshot 的副本语义与差值计算，
以及未启用 API 校准时仍然累计用量。
"""

from __future__ import annotations

from sagent.config.models import ContextConfig
from sagent.context.context_manager import ContextManager


def _make_cm(use_api_calibration: bool = True) -> ContextManager:
    """构造不启用摘要的最小 ContextManager。"""
    config = ContextConfig(
        max_context_tokens=128000,
        token_counter_method="heuristic",
        enable_summary=False,
        use_api_calibration=use_api_calibration,
    )
    return ContextManager(config)


def test_usage_snapshot_accumulates_and_returns_copy():
    """连续两次 record_llm_usage 后快照返回累计值，且快照为副本。"""
    cm = _make_cm()
    cm.record_llm_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )
    first = cm.usage_snapshot()
    assert first == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
    }

    cm.record_llm_usage(
        {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    )
    second = cm.usage_snapshot()
    assert second == {
        "prompt_tokens": 17,
        "completion_tokens": 8,
        "total_tokens": 25,
        "cached_tokens": 0,
    }
    # 先前的快照不随后续累计变化
    assert first == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
    }

    # 修改返回值不影响内部状态
    second["total_tokens"] = 9999
    assert cm.usage_snapshot()["total_tokens"] == 25


def test_usage_snapshot_diff_for_single_turn():
    """两次快照的差值即单轮用量。"""
    cm = _make_cm()
    before = cm.usage_snapshot()
    cm.record_llm_usage(
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    )
    cm.record_llm_usage(
        {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180}
    )
    after = cm.usage_snapshot()
    assert after["total_tokens"] - before["total_tokens"] == 300
    assert after["prompt_tokens"] - before["prompt_tokens"] == 250
    assert after["completion_tokens"] - before["completion_tokens"] == 50


def test_usage_snapshot_accumulates_without_calibration():
    """未启用 API 校准时仍累计用量，但不写入校准基准。"""
    cm = _make_cm(use_api_calibration=False)
    cm.record_llm_usage(
        {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
    )
    assert cm.usage_snapshot() == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
        "cached_tokens": 0,
    }
    assert cm._calibrated_tokens is None


def test_usage_snapshot_handles_missing_fields_and_none():
    """缺失字段按 0 累计，usage 为 None 时不改变累计值。"""
    cm = _make_cm()
    assert cm.usage_snapshot() == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    }
    cm.record_llm_usage({"prompt_tokens": 9})
    assert cm.usage_snapshot() == {
        "prompt_tokens": 9,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    }
    cm.record_llm_usage(None)
    assert cm.usage_snapshot()["prompt_tokens"] == 9
