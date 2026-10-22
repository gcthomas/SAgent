"""Plan 步骤解析单元测试。"""

from __future__ import annotations

import logging

from sagent.core.plan_engine import PlanEngine


def test_parse_plain_json():
    steps = PlanEngine._parse_steps(
        '{"steps": [{"title": "a", "description": "a1"}, '
        '{"title": "b", "description": "b1"}, '
        '{"title": "c", "description": "c1"}]}'
    )
    assert steps == [("a", "a1"), ("b", "b1"), ("c", "c1")]


def test_parse_json_in_code_fence():
    content = (
        '```json\n{"steps": [{"title": "读取文件", "description": "读取目标文件内容"}, '
        '{"title": "总结内容", "description": "总结文件要点"}]}\n```'
    )
    steps = PlanEngine._parse_steps(content)
    assert steps == [("读取文件", "读取目标文件内容"), ("总结内容", "总结文件要点")]


def test_parse_bare_code_fence():
    content = '```\n{"steps": [{"title": "only", "description": "only step"}]}\n```'
    assert PlanEngine._parse_steps(content) == [("only", "only step")]


def test_parse_invalid_json_returns_empty():
    assert PlanEngine._parse_steps("这不是 JSON") == []


def test_parse_missing_steps_key_returns_empty():
    assert PlanEngine._parse_steps('{"foo": 1}') == []


def test_parse_filters_incomplete_items():
    content = (
        '{"steps": ['
        '{"title": "a", "description": "a1"}, '
        '{"title": "", "description": "无标题"}, '
        '{"title": "无描述", "description": "  "}, '
        '{"title": "b"}'
        ']}'
    )
    assert PlanEngine._parse_steps(content) == [("a", "a1")]


def test_parse_drops_plain_string_items():
    content = '{"steps": ["a", {"title": "b", "description": "b1"}]}'
    assert PlanEngine._parse_steps(content) == [("b", "b1")]


def test_parse_invalid_json_logs_warning(caplog):
    """解析失败时记录带原因和内容片段的告警日志。"""
    with caplog.at_level(logging.WARNING, logger="sagent.core.plan_engine"):
        assert PlanEngine._parse_steps("这不是 JSON") == []

    record = next(r for r in caplog.records if getattr(r, "event", None) == "plan_parse_failed")
    assert record.reason == "invalid_json"
    assert "这不是 JSON" in record.content_preview


def test_parse_missing_steps_logs_warning(caplog):
    """缺少 steps 列表时记录数据类型，便于定位契约偏差。"""
    with caplog.at_level(logging.WARNING, logger="sagent.core.plan_engine"):
        assert PlanEngine._parse_steps('{"foo": 1}') == []

    record = next(r for r in caplog.records if getattr(r, "event", None) == "plan_parse_failed")
    assert record.reason == "steps_not_list"
    assert record.data_type == "dict"


def test_parse_dropped_items_logs_warning(caplog):
    """丢弃无效条目时记录条目级原因。"""
    content = '{"steps": ["a", {"title": "", "description": "d"}]}'
    with caplog.at_level(logging.WARNING, logger="sagent.core.plan_engine"):
        assert PlanEngine._parse_steps(content) == []

    record = next(r for r in caplog.records if getattr(r, "event", None) == "plan_parse_dropped")
    assert record.kept_count == 0
    assert record.dropped_count == 2
    assert [item["reason"] for item in record.dropped] == ["not_object", "missing_fields"]
