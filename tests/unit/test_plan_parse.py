"""Plan 步骤解析单元测试。"""

from __future__ import annotations

from sagent.core.plan_engine import PlanEngine


def test_parse_plain_json():
    steps = PlanEngine._parse_steps('{"steps": ["a", "b", "c"]}')
    assert steps == ["a", "b", "c"]


def test_parse_json_in_code_fence():
    content = '```json\n{"steps": ["读取文件", "总结内容"]}\n```'
    steps = PlanEngine._parse_steps(content)
    assert steps == ["读取文件", "总结内容"]


def test_parse_bare_code_fence():
    content = '```\n{"steps": ["only"]}\n```'
    assert PlanEngine._parse_steps(content) == ["only"]


def test_parse_invalid_json_returns_empty():
    assert PlanEngine._parse_steps("这不是 JSON") == []


def test_parse_missing_steps_key_returns_empty():
    assert PlanEngine._parse_steps('{"foo": 1}') == []


def test_parse_filters_blank_steps():
    steps = PlanEngine._parse_steps('{"steps": ["a", "", "  ", "b"]}')
    assert steps == ["a", "b"]
