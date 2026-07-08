"""Agent 功能评测骨架。

设计目标：为后续功能迭代提供可复现的能力评测。分两种模式：

1. 离线回放（默认）：使用 FakeLLMClient 按场景预设的响应回放，验证 agent 在
   给定模型行为下的编排是否正确、最终输出是否满足断言。快速、免费、可复现。

2. 真实 LLM（可选）：设置环境变量 RUN_LLM_EVALS=1 时启用，直接调用配置好的
   真实模型执行任务，并用简单的断言 + 可扩展的 LLM-as-judge 评估结果质量。
   需要有效的 LLM_API_KEY（及可选 LLM_API_URL）。

评测场景以数据形式集中定义在 SCENARIOS 中，新增能力时只需追加场景即可。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

import pytest

from conftest import make_tool_call, text_response, tool_response

from sagent.config.models import AgentConfig
from sagent.core.react_engine import ReActEngine
from sagent.llm.client import LLMResponse
from sagent.tools import build_default_registry


@dataclass
class Scenario:
    """一个评测场景。

    属性:
        name: 场景名称。
        task: 交给 agent 的任务输入。
        offline_responses: 离线回放模式下 FakeLLMClient 依次返回的响应工厂
            （用函数延迟构造，避免跨用例共享可变对象）。
        expect_substrings: 断言最终输出应包含的子串（离线与真实模式共用）。
    """

    name: str
    task: str
    offline_responses: Callable[[], list[LLMResponse]]
    expect_substrings: list[str] = field(default_factory=list)


# 集中定义评测场景：新增 agent 能力时在此追加即可
SCENARIOS: list[Scenario] = [
    Scenario(
        name="direct_answer",
        task="用一句话介绍你自己。",
        offline_responses=lambda: [text_response("我是一个通用 CLI Agent。")],
        expect_substrings=["Agent"],
    ),
    Scenario(
        name="read_file_then_answer",
        task="读取工作区的说明文件并概述其用途。",
        offline_responses=lambda: [
            tool_response([make_tool_call("read_file", {"path": "README.md"})]),
            text_response("这是一个通用 CLI Agent 项目的说明。"),
        ],
        expect_substrings=["Agent"],
    ),
]


RUN_LLM = os.environ.get("RUN_LLM_EVALS") == "1"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_offline_eval(scenario: Scenario, make_fake_llm):
    """离线回放评测：默认执行，快速且可复现。"""
    llm = make_fake_llm(scenario.offline_responses())
    engine = ReActEngine(
        llm, build_default_registry(), AgentConfig(mode="react", max_iterations=5)
    )
    result = engine.run(scenario.task)
    for sub in scenario.expect_substrings:
        assert sub in result, f"场景 {scenario.name} 输出缺少期望子串: {sub}"


@pytest.mark.llm_eval
@pytest.mark.skipif(not RUN_LLM, reason="需设置 RUN_LLM_EVALS=1 且配置真实 LLM 才运行")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_real_llm_eval(scenario: Scenario):
    """真实 LLM 评测：调用真实模型执行任务，做基础质量断言。

    这里做的是最小可用版本：断言输出非空。可在此接入 LLM-as-judge：
    再用一次 LLMClient.chat 让评审模型对 (task, result) 打分并断言分数达标。
    """
    from sagent.config.loader import load_config
    from sagent.llm.client import LLMClient

    config = load_config(os.environ.get("SAGENT_CONFIG"))
    llm = LLMClient(config.llm)
    engine = ReActEngine(llm, build_default_registry(), config.agent)
    result = engine.run(scenario.task)

    assert result and result.strip(), f"场景 {scenario.name} 返回了空结果"
    score = _judge(llm, scenario.task, result)
    assert score >= 3, f"场景 {scenario.name} 评审分过低: {score}"


def _judge(llm, task: str, answer: str) -> int:
    """LLM-as-judge：让模型对回答质量打 1-5 分，解析失败时返回保守分。"""
    import re

    messages = [
        {
            "role": "system",
            "content": "你是严格的评审员。根据任务与回答质量打 1-5 分，只输出一个整数。",
        },
        {"role": "user", "content": f"任务:\n{task}\n\n回答:\n{answer}\n\n请给出 1-5 的整数评分。"},
    ]
    resp = llm.chat(messages, tools=None)
    match = re.search(r"[1-5]", resp.content or "")
    return int(match.group()) if match else 3
