"""CLI Skill 斜杠命令测试。

覆盖 /skills 列表与诊断展示、/skill 显式调用的参数校验、未知技能拒绝执行、
合法技能合成任务转交引擎，以及新分支不影响未知命令提示，全部离线运行。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import sagent.cli.app as cli_app
from sagent.cli.app import _dispatch_session_command, _dispatch_skill_command, _run_interactive
from sagent.cli.commands import ParsedCommand
from sagent.config.models import (
    AgentConfig,
    AppConfig,
    ContextConfig,
    LLMConfig,
    MCPConfig,
    MemoryConfig,
    ObservabilityConfig,
    PlanningConfig,
    SessionConfig,
    SkillConfig,
)
from sagent.skills import SkillLoader, SkillManager


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """构造隔离的 Skill 工作区，返回 (项目根, 用户主目录)，两个技能根均为空目录。"""
    project_root = tmp_path / "proj"
    user_home = tmp_path / "home"
    (project_root / ".sagent" / "skills").mkdir(parents=True)
    (user_home / ".sagent" / "skills").mkdir(parents=True)
    return project_root, user_home


def _write_skill(skills_root: Path, name: str, description: str, body: str = "按步骤执行任务。") -> None:
    """在指定技能根目录下写入一个合法的 SKILL.md。"""
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _build_manager(project_root: Path, user_home: Path) -> SkillManager:
    """在隔离工作区上构建已完成扫描的 Skill 管理器。"""
    manager = SkillManager(
        SkillConfig(enabled=True), project_root=project_root, user_home=user_home
    )
    manager.discover()
    return manager


# ---------- /skills 列表命令 ----------


def test_dispatch_skills_without_manager_prints_disabled(capsys):
    """skill_manager 为 None（skills.enabled=false）时提示未启用。"""
    result = _dispatch_skill_command(ParsedCommand("skills", []), None, None)

    assert result is None
    assert capsys.readouterr().out.strip() == "Skill 功能未启用"


def test_dispatch_skills_empty_prints_no_skills(capsys, tmp_path):
    """无可用技能时输出未发现可用技能，且不输出不可用项。"""
    project_root, user_home = _make_workspace(tmp_path)
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(ParsedCommand("skills", []), manager, SkillLoader(manager))

    assert result is None
    assert capsys.readouterr().out.strip() == "未发现可用技能"


def test_dispatch_skills_lists_qualified_name_and_description(capsys, tmp_path):
    """有技能时按限定名与描述逐行列出，覆盖 project 与 user 两个来源。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(project_root / ".sagent" / "skills", "code-review", "审查代码差异的正确性。")
    _write_skill(user_home / ".sagent" / "skills", "pdf-report", "生成 PDF 报告的流程与模板。")
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(ParsedCommand("skills", []), manager, SkillLoader(manager))

    assert result is None
    output = capsys.readouterr().out
    assert "可用技能:" in output
    assert "* project:code-review  审查代码差异的正确性。" in output
    assert "* user:pdf-report  生成 PDF 报告的流程与模板。" in output
    assert "不可用项" not in output


def test_dispatch_skills_lists_issues(capsys, tmp_path):
    """存在诊断时输出不可用项段落（目录路径与原因）。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(project_root / ".sagent" / "skills", "code-review", "审查代码差异。")
    # 缺少 SKILL.md 的子目录应产生诊断
    (project_root / ".sagent" / "skills" / "broken").mkdir()
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(ParsedCommand("skills", []), manager, SkillLoader(manager))

    assert result is None
    output = capsys.readouterr().out
    assert "不可用项:" in output
    assert "缺少 SKILL.md" in output
    assert str(project_root / ".sagent" / "skills" / "broken") in output


# ---------- /skill 显式调用命令 ----------


def test_dispatch_skill_without_manager_prints_disabled(capsys):
    """skill_manager 为 None 时提示未启用，不解析参数。"""
    result = _dispatch_skill_command(ParsedCommand("skill", ["code-review"]), None, None)

    assert result is None
    assert capsys.readouterr().out.strip() == "Skill 功能未启用"


def test_dispatch_skill_missing_args_prints_usage(capsys, tmp_path):
    """缺少技能名参数时输出用法提示。"""
    project_root, user_home = _make_workspace(tmp_path)
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(ParsedCommand("skill", []), manager, SkillLoader(manager))

    assert result is None
    assert capsys.readouterr().out.strip() == "用法: /skill <名称或来源限定名> [补充说明]"


def test_dispatch_skill_unknown_ref_rejects_without_task(capsys, tmp_path):
    """未知技能透出异常消息（错误前缀），返回 None 且不产出合成任务。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(project_root / ".sagent" / "skills", "code-review", "审查代码差异。")
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(ParsedCommand("skill", ["no-such", "任务"]), manager, SkillLoader(manager))

    assert result is None
    assert capsys.readouterr().out.strip() == "错误: 未知的 Skill: no-such"


def test_dispatch_skill_ambiguous_ref_shows_candidates(capsys, tmp_path):
    """同名短名歧义时透出异常消息中的候选列表与限定名指引。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(project_root / ".sagent" / "skills", "code-review", "项目级脚本。")
    _write_skill(user_home / ".sagent" / "skills", "code-review", "用户级脚本。")
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(ParsedCommand("skill", ["code-review", "任务"]), manager, SkillLoader(manager))

    assert result is None
    output = capsys.readouterr().out.strip()
    assert output.startswith("错误: Skill 名称歧义")
    assert "user:code-review" in output
    assert "请使用来源限定名" in output


def test_dispatch_skill_valid_ref_builds_synthetic_task(capsys, tmp_path):
    """合法技能打印使用提示，并返回包含渲染正文与补充说明的合成任务。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(
        project_root / ".sagent" / "skills",
        "code-review",
        "审查代码差异。",
        body="技能正文第一行。",
    )
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(
        ParsedCommand("skill", ["code-review", "重点看", "登录模块"]),
        manager,
        SkillLoader(manager),
    )

    assert result is not None
    assert '请严格按照技能 "project:code-review" 的说明执行任务。' in result
    assert "--- 技能正文 ---" in result
    assert "技能正文第一行。" in result
    assert "用户补充说明:\n重点看 登录模块" in result
    assert "使用技能: project:code-review" in capsys.readouterr().out


def test_dispatch_skill_without_instruction_rejects(capsys, tmp_path):
    """缺少任务说明时提示提供说明，返回 None 且不产出合成任务。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(project_root / ".sagent" / "skills", "code-review", "审查代码差异。")
    manager = _build_manager(project_root, user_home)

    result = _dispatch_skill_command(
        ParsedCommand("skill", ["code-review"]), manager, SkillLoader(manager)
    )

    assert result is None
    output = capsys.readouterr().out
    assert "请提供任务说明，例如： /skill <名称> <任务>" in output
    assert "请严格按照技能" not in output


def test_dispatch_skill_body_over_budget_rejects_without_task(capsys, tmp_path):
    """正文超预算时加载器返回错误提示，拒绝执行且不产出合成任务。"""
    project_root, user_home = _make_workspace(tmp_path)
    _write_skill(
        project_root / ".sagent" / "skills",
        "code-review",
        "审查代码差异。",
        body="超长正文" * 100,
    )
    manager = _build_manager(project_root, user_home)
    loader = SkillLoader(manager, max_body_tokens=1)

    result = _dispatch_skill_command(
        ParsedCommand("skill", ["code-review", "任务"]), manager, loader
    )

    assert result is None
    assert capsys.readouterr().out.strip().startswith("错误:")


# ---------- /help 帮助文本 ----------


class _NoopSessionManager:
    """仅满足 help 分发所需的空会话管理器。"""

    def get_current_session(self):
        return None


def test_dispatch_help_lists_skill_commands(capsys):
    """/help 在 skills 启用时追加 skill 命令说明。"""
    _dispatch_session_command(ParsedCommand("help", []), _NoopSessionManager(), skills_enabled=True)

    output = capsys.readouterr().out
    assert "  /skills           列出可用技能与不可用诊断" in output
    assert "  /skill <名称> [说明]  显式调用技能执行任务" in output


def test_dispatch_help_hides_skill_commands_when_disabled(capsys):
    """skills 未启用时 /help 不展示 skill 命令说明。"""
    _dispatch_session_command(ParsedCommand("help", []), _NoopSessionManager(), skills_enabled=False)

    output = capsys.readouterr().out
    assert "/skills" not in output
    assert "/skill" not in output


# ---------- 交互循环端到端 ----------


class _RecordingEngine:
    """记录 run 收到的任务文本并返回固定答案的假引擎。"""

    def __init__(self, on_event):
        self.on_event = on_event
        # 模拟引擎的本轮实际执行路径，供 app 记录 sagent.mode 使用
        self.last_mode = "react"
        self.tasks: list[str] = []

    def run(self, task: str, memory_prefix=None, skill_prefix=None) -> str:
        self.tasks.append(task)
        return "技能任务执行完成"


class _ScriptedReader:
    """按顺序提供预设输入的假输入读取器。"""

    def __init__(self, inputs: list[str]) -> None:
        self.inputs = iter(inputs)

    def read(self, _prompt: str) -> str:
        return next(self.inputs)


def _interactive_config(
    skills_enabled: bool = True, extra_dirs: list[str] | None = None
) -> AppConfig:
    """构造不触碰外部资源且可控制 Skill 开关的交互配置。"""
    return AppConfig(
        llm=LLMConfig(model="test-model", stream=False),
        agent=AgentConfig(max_iterations=1),
        planning=PlanningConfig(strategy="never"),
        context=ContextConfig(enable_summary=False),
        session=SessionConfig(enabled=False),
        memory=MemoryConfig(enabled=False),
        mcp=MCPConfig(enabled=False),
        observability=ObservabilityConfig(enabled=False),
        skills=SkillConfig(enabled=skills_enabled, extra_dirs=extra_dirs or []),
    )


def _patch_app(monkeypatch, created: list[_RecordingEngine]) -> None:
    """替换 _run_interactive 的外部依赖，收集创建的假引擎。"""

    def fake_build_engine(*args, on_event, **kwargs):
        engine = _RecordingEngine(on_event)
        created.append(engine)
        return engine

    monkeypatch.setattr(cli_app, "LLMClient", lambda config: SimpleNamespace(config=config))
    monkeypatch.setattr(cli_app, "build_engine", fake_build_engine)
    monkeypatch.setattr(cli_app, "flush_metrics", lambda: None)
    monkeypatch.setattr(cli_app, "close_observability", lambda: None)


def test_run_interactive_skill_command_delegates_to_engine(monkeypatch, capsys, tmp_path):
    """/skill 合法命中时引擎收到包含技能正文与补充说明的合成任务。"""
    skill_root = tmp_path / "skills"
    _write_skill(skill_root, "code-review", "审查代码差异。", body="技能正文第一行。")
    created: list[_RecordingEngine] = []
    _patch_app(monkeypatch, created)

    reader = _ScriptedReader(["/skill code-review 补充说明文字", "exit"])
    cli_app._run_interactive(_interactive_config(extra_dirs=[str(skill_root)]), reader)

    output = capsys.readouterr().out
    engine = created[0]
    assert len(engine.tasks) == 1
    task = engine.tasks[0]
    assert '请严格按照技能 "extra:code-review" 的说明执行任务。' in task
    assert "技能正文第一行。" in task
    assert "用户补充说明:\n补充说明文字" in task
    assert "使用技能: extra:code-review" in output
    assert "助手 > 技能任务执行完成" in output


def test_run_interactive_unknown_skill_never_calls_engine(monkeypatch, capsys, tmp_path):
    """/skill 引用未知技能时透出异常消息，引擎零调用。"""
    skill_root = tmp_path / "skills"
    _write_skill(skill_root, "code-review", "审查代码差异。")
    created: list[_RecordingEngine] = []
    _patch_app(monkeypatch, created)

    reader = _ScriptedReader(["/skill no-such 任务", "exit"])
    cli_app._run_interactive(_interactive_config(extra_dirs=[str(skill_root)]), reader)

    output = capsys.readouterr().out
    assert "错误: 未知的 Skill: no-such" in output
    assert created[0].tasks == []


def test_run_interactive_skill_without_instruction_never_calls_engine(monkeypatch, capsys, tmp_path):
    """/skill 缺少任务说明时仅提示用法，引擎零调用。"""
    skill_root = tmp_path / "skills"
    _write_skill(skill_root, "code-review", "审查代码差异。")
    created: list[_RecordingEngine] = []
    _patch_app(monkeypatch, created)

    reader = _ScriptedReader(["/skill code-review", "exit"])
    cli_app._run_interactive(_interactive_config(extra_dirs=[str(skill_root)]), reader)

    output = capsys.readouterr().out
    assert "请提供任务说明，例如： /skill <名称> <任务>" in output
    assert "使用技能:" not in output
    assert created[0].tasks == []


def test_dispatch_unknown_command_keeps_prompt(capsys):
    """未知斜杠命令仍走会话命令分发的未知命令提示。"""
    _dispatch_session_command(ParsedCommand("unknown", []), _NoopSessionManager())

    assert capsys.readouterr().out.strip() == "未知命令，输入 /help 查看可用命令"


def test_run_interactive_unknown_command_keeps_prompt(monkeypatch, capsys):
    """未知斜杠命令不触发引擎执行；session 未启用时提示会话管理未启用（既有行为）。"""
    created: list[_RecordingEngine] = []
    _patch_app(monkeypatch, created)

    reader = _ScriptedReader(["/unknown", "exit"])
    cli_app._run_interactive(_interactive_config(skills_enabled=False), reader)

    output = capsys.readouterr().out
    assert "会话管理未启用" in output
    assert "未找到技能" not in output
    assert created[0].tasks == []
