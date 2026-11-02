"""Skill 前缀注入引擎测试（使用 FakeLLMClient 离线回放）。

验证 ReAct 引擎把 Skill 目录前缀正确叠加到系统提示词、与记忆前缀
共存时 memory 保持最前、skill_prefix 为 None 时行为向后兼容，
Plan 分解阶段感知技能目录前缀，以及 load_skill 工具结果经
ToolRegistry 进入上下文路径。不依赖真实 LLM。
"""

from __future__ import annotations

from conftest import text_response

from sagent.config.models import SkillConfig
from sagent.core.plan_engine import PlanEngine
from sagent.core.prompts import PLAN_DECOMPOSE_PROMPT, REACT_SYSTEM_PROMPT
from sagent.core.react_engine import ReActEngine
from sagent.skills.loader import SkillLoader
from sagent.skills.manager import SkillManager
from sagent.tools import build_default_registry
from sagent.tools.skill_tool import LoadSkillTool

# ReAct 系统提示词中的可辨识片段，用于断言默认提示词仍保留在末尾
_REACT_PROMPT_FRAGMENT = "推理-行动-观察"

_SKILL_PREFIX = "[可用技能]\n\n## 技能\n- project:demo: 演示技能用途"
_MEMORY_PREFIX = "[长期记忆]\n## 记忆\n- 用户偏好简洁回答"


def test_react_injects_skill_prefix_into_system_prompt(make_fake_llm, agent_config):
    """skill_prefix 叠加到系统提示词，且默认 ReAct 提示词片段保留。"""
    llm = make_fake_llm([text_response("ok")])
    engine = ReActEngine(llm, build_default_registry(), agent_config)

    result = engine.run("任务", skill_prefix=_SKILL_PREFIX)

    assert result == "ok"
    first_message = llm.calls[0]["messages"][0]
    assert first_message["role"] == "system"
    # 首条系统消息同时包含 Skill 前缀与 ReAct 默认系统提示词片段
    assert _SKILL_PREFIX in first_message["content"]
    assert _REACT_PROMPT_FRAGMENT in first_message["content"]


def test_react_memory_prefix_precedes_skill_prefix(make_fake_llm, agent_config):
    """memory_prefix 保持最前，位于 skill_prefix 与系统提示词之前。"""
    llm = make_fake_llm([text_response("ok")])
    engine = ReActEngine(llm, build_default_registry(), agent_config)

    engine.run("任务", memory_prefix=_MEMORY_PREFIX, skill_prefix=_SKILL_PREFIX)

    content = llm.calls[0]["messages"][0]["content"]
    assert _MEMORY_PREFIX in content
    assert _SKILL_PREFIX in content
    assert _REACT_PROMPT_FRAGMENT in content
    # 叠加顺序：memory 前缀最前，skill 前缀次之，系统提示词最后
    assert (
        content.index(_MEMORY_PREFIX)
        < content.index(_SKILL_PREFIX)
        < content.index(_REACT_PROMPT_FRAGMENT)
    )


def test_react_skill_prefix_none_matches_default_call(make_fake_llm, agent_config):
    """skill_prefix 为 None 时首条消息与未传参数完全一致（向后兼容）。"""
    llm_none = make_fake_llm([text_response("ok")])
    ReActEngine(llm_none, build_default_registry(), agent_config).run(
        "普通任务", skill_prefix=None
    )

    llm_default = make_fake_llm([text_response("ok")])
    ReActEngine(llm_default, build_default_registry(), agent_config).run("普通任务")

    expected = llm_default.calls[0]["messages"][0]["content"]
    actual = llm_none.calls[0]["messages"][0]["content"]
    assert actual == expected
    assert actual == REACT_SYSTEM_PROMPT


def test_plan_decompose_injects_skill_prefix(make_fake_llm, agent_config):
    """Plan 分解阶段把技能目录前缀叠加到拆解 system 消息。"""
    llm = make_fake_llm(
        [
            # 1) 拆解
            text_response('{"steps": [{"title": "标题一", "description": "步骤一"}]}'),
            # 2) 步骤一（ReAct 直接答）
            text_response("步骤完成"),
            # 3) 汇总
            text_response("整体汇总结果"),
        ]
    )
    engine = PlanEngine(llm, build_default_registry(), agent_config)

    result = engine.run("一个复杂任务", skill_prefix=_SKILL_PREFIX)

    assert result == "整体汇总结果"
    assert llm.remaining == 0
    system_content = llm.calls[0]["messages"][0]["content"]
    assert system_content != PLAN_DECOMPOSE_PROMPT
    assert PLAN_DECOMPOSE_PROMPT in system_content
    assert _SKILL_PREFIX in system_content
    # 顺序：拆解提示词在前，技能目录前缀在后
    assert system_content.index(PLAN_DECOMPOSE_PROMPT) < system_content.index(_SKILL_PREFIX)


def test_plan_decompose_without_skill_prefix_keeps_default_prompt(make_fake_llm, agent_config):
    """skill_prefix 为空时拆解 system 消息保持原有 PLAN_DECOMPOSE_PROMPT。"""
    llm = make_fake_llm(
        [
            text_response('{"steps": [{"title": "标题一", "description": "步骤一"}]}'),
            text_response("步骤完成"),
            text_response("整体汇总结果"),
        ]
    )
    engine = PlanEngine(llm, build_default_registry(), agent_config)

    engine.run("一个复杂任务")

    assert llm.calls[0]["messages"][0]["content"] == PLAN_DECOMPOSE_PROMPT


def test_load_skill_tool_result_enters_context_paths(tmp_path):
    """load_skill 经 ToolRegistry 执行并返回技能正文，可重复加载。

    说明：load_skill 与其他工具一致，经 ToolRegistry.execute 执行，其返回值
    在 ReAct 循环中作为 role=tool 的观察消息追加（无上下文管理器时进入
    messages，有上下文管理器时经 cm.add_message 进入历史）。因此 Skill 正文
    天然参与 ContextManager 的 token 统计、压缩与会话持久化路径，无需额外
    代码。工具幂等可重复执行：上下文压缩裁剪掉历史中的正文后，模型可再次
    调用 load_skill 重新加载，即"压缩后重新加载 Skill"的保障机制。
    """
    project_skills = tmp_path / "project" / ".sagent" / "skills"
    skill_dir = project_skills / "demo"
    skill_dir.mkdir(parents=True)
    body = "这是技能正文内容。"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: 演示技能用途\n---\n\n{body}\n",
        encoding="utf-8",
    )
    manager = SkillManager(
        SkillConfig(),
        project_root=tmp_path / "project",
        user_home=tmp_path / "home",
    )
    registry = build_default_registry()
    registry.register(LoadSkillTool(SkillLoader(manager)))

    # 短名引用：返回渲染文本，含限定名与正文
    observation = registry.execute("load_skill", {"name": "demo"})
    assert "project:demo" in observation
    assert body in observation

    # 来源限定名引用再次执行：正文可重复加载，供压缩后重新获取
    again = registry.execute("load_skill", {"name": "project:demo"})
    assert again == observation
