"""CLI 应用。

提供命令行入口：加载配置、构建工具与引擎，进入交互循环。
支持通过 --config 指定配置文件，计划策略由配置文件中的 planning.strategy 控制。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config.loader import ConfigError, load_config
from ..config.models import AppConfig, PermissionConfig, PlanningConfig
from ..context.context_manager import ContextManager
from ..context.token_counter import count_text_tokens
from ..core.agent_engine import AgentEngine
from ..llm.client import LLMClient
from ..memory import MemoryManager, MemoryStore
from ..observability import Span, close_observability, flush_metrics, get_logger, setup_content_capture, setup_logging, setup_observability
from ..permissions import (
    Decision,
    InteractiveApprovalHandler,
    PermissionEnforcer,
    PermissionRule,
    build_default_policy,
    parse_rule,
)
from ..session.manager import SessionManager
from ..session.store import SessionStore
from ..skills import SkillLoader, SkillManager, SkillNotFoundError, build_skill_catalog_prefix
from ..terminal_input import TerminalInput
from ..tools import AddMemoryTool, RemoveMemoryTool, ReplaceMemoryTool, build_default_registry
from ..tools.mcp import MCPSessionManager, build_mcp_providers
from .commands import ParsedCommand, parse_command
from .event_presenter import EventPresenter, StdoutAdapter

# 退出命令
_EXIT_COMMANDS = {"exit", "quit", ":q"}

logger = get_logger(__name__)


class _ArgumentParser(argparse.ArgumentParser):
    """为已移除的模式参数提供明确的迁移提示。"""

    def error(self, message: str) -> None:
        if "--mode" in message:
            message = f"{message}；--mode 已移除，请改用配置文件中的 planning.strategy"
        super().error(message)


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = _ArgumentParser(
        prog="sagent",
        description="SAgent: 一个简单通用的 CLI Agent。",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认 config.yaml）",
    )
    return parser


def build_engine(
    llm: LLMClient,
    registry,
    agent_config,
    planning_config: PlanningConfig | None = None,
    on_event=None,
    context_manager=None,
) -> AgentEngine:
    """构建统一入口执行引擎。"""
    return AgentEngine(
        llm,
        registry,
        agent_config,
        planning_config=planning_config,
        on_event=on_event,
        context_manager=context_manager,
    )


def _build_permission(
    permission_config: PermissionConfig, reader: TerminalInput | None = None,
) -> PermissionEnforcer | None:
    """根据权限配置构建权限执行器。

    组装用户规则（allow / deny / ask 列表），并配置交互审批器的超时与非交互行为。
    跨列表固定优先级 deny > allow > ask：YAML 三字段无"出现顺序"语义，
    同一请求同时命中多个列表时拒绝压过放行、放行压过确认；实现上按
    ask -> allow -> deny 顺序追加规则，借助 last-match-wins 使 deny 生效。

    参数:
        permission_config: 权限控制配置。

    返回:
        PermissionEnforcer 实例；未启用权限控制时返回 None（保持全自动执行）。
    """
    if not permission_config.enabled:
        return None
    rules: list[PermissionRule] = []
    # 组装顺序 ask -> allow -> deny：策略按 last-match-wins 决策，
    # 后追加的 deny 规则压过 allow、allow 压过 ask，即跨列表固定优先级
    for text in permission_config.ask:
        rules.append(parse_rule(text, Decision.ASK))
    for text in permission_config.allow:
        rules.append(parse_rule(text, Decision.ALLOW))
    for text in permission_config.deny:
        rules.append(parse_rule(text, Decision.DENY))
    policy = build_default_policy(rules)
    approval = InteractiveApprovalHandler(reader=reader)
    # 审批超时与非交互动作统一经权限执行器注入（构造时透传给交互审批器，
    # 避免执行器缺省的 non_interactive="deny" 覆盖用户配置）
    return PermissionEnforcer(
        policy,
        approval,
        timeout=permission_config.ask_timeout,
        non_interactive=permission_config.non_interactive,
    )


def _dispatch_session_command(
    parsed: ParsedCommand, session_manager, skills_enabled: bool = False
) -> None:
    """分发会话管理斜杠命令，负责 IO 编排，不返回值。

    参数:
        parsed: 解析后的斜杠命令
        session_manager: 会话管理器，为 None 时提示会话管理未启用
        skills_enabled: Skill 功能是否启用（skills.enabled），控制 /help
            中 Skill 命令说明的展示
    """
    if session_manager is None:
        print("会话管理未启用")
        return
    name = parsed.name
    if name == "new":
        title = parsed.args[0] if parsed.args else ""
        meta = session_manager.new_session(title)
        print(f"已创建新会话: {meta.id} | {meta.title}")
    elif name == "sessions":
        if len(parsed.args) > 2:
            print("用法: /sessions [每页数量] [页码]（默认每页 20 条）")
            return
        try:
            limit = int(parsed.args[0]) if parsed.args else 20
            page = int(parsed.args[1]) if len(parsed.args) == 2 else 1
        except ValueError:
            print("用法: /sessions [每页数量] [页码]（默认每页 20 条）")
            return
        if limit <= 0 or page <= 0:
            print("用法: /sessions [每页数量] [页码]（默认每页 20 条）")
            return
        sessions = session_manager.list_sessions(limit=limit + 1, offset=(page - 1) * limit)
        cur = session_manager.get_current_session()
        cur_id = cur.id if cur is not None else None
        if not sessions:
            print("暂无会话")
        else:
            for s in sessions[:limit]:
                mark = "*" if s.id == cur_id else " "
                print(
                    f"{mark} {s.id} | {s.title} | 更新: {s.updated_at} | 消息: {s.message_count}"
                )
            if len(sessions) > limit:
                print(f"还有更多会话，可使用 /sessions {limit} {page + 1} 查看下一页")
        if page > 1:
            print(f"上一页: /sessions {limit} {page - 1}")
    elif name == "switch":
        if not parsed.args:
            print("用法: /switch <会话id>")
            return
        result = session_manager.switch_session(parsed.args[0])
        if result is None:
            print("会话不存在")
        else:
            print(f"已切换到会话: {result.id} | {result.title}")
    elif name == "rename":
        if not parsed.args:
            print("用法: /rename <新标题>")
            return
        ok = session_manager.rename_session(" ".join(parsed.args))
        if ok:
            print("已重命名当前会话")
        else:
            print("重命名失败:无当前会话")
    elif name == "delete":
        if not parsed.args:
            print("用法: /delete <会话id>")
            return
        ok = session_manager.delete_session(parsed.args[0])
        if ok:
            print("已删除")
        else:
            print("不能删除当前会话或会话不存在")
    elif name == "search":
        if not parsed.args:
            print("用法: /search <关键词>")
            return
        results = session_manager.search(" ".join(parsed.args))
        if not results:
            print("无匹配")
        else:
            for r in results:
                print(f"[{r['session_id']}] {r['role']}: {r['snippet']}")
    elif name == "session":
        cur = session_manager.get_current_session()
        if cur is None:
            print("无当前会话")
        else:
            print(f"当前会话: {cur.id}")
            print(f"标题: {cur.title}")
            print(f"模式: {cur.mode}")
            print(f"消息数: {cur.message_count}")
            print(f"已持久化序号: {cur.persisted_seq}")
    elif name == "help":
        print("会话命令:")
        print("  /new [标题]      创建新会话")
        print("  /sessions [每页数量] [页码]  分页列出会话（默认每页 20 条）")
        print("  /switch <id>     切换会话")
        print("  /rename <标题>   重命名当前会话")
        print("  /delete <id>     删除会话(不能删除当前会话)")
        print("  /search <关键词> 搜索历史消息")
        print("  /session         显示当前会话信息")
        print("  /help            显示此帮助")
        # Skill 命令说明随 skills.enabled 门控：未启用时不展示
        if skills_enabled:
            print("  /skills           列出可用技能与不可用诊断")
            print("  /skill <名称> [说明]  显式调用技能执行任务")
    else:
        print("未知命令，输入 /help 查看可用命令")


def _dispatch_skill_command(parsed: ParsedCommand, skill_manager, skill_loader) -> str | None:
    """分发 Skill 斜杠命令（/skills 列表、/skill 显式调用）。

    返回值约定（改动最小方案）：
    - 返回 None 表示本轮命令已处理完毕（列表展示、未启用、用法与未找到等分支），
      交互循环直接进入下一轮；
    - /skill 合法命中时不在此处执行引擎，而是返回合成任务文本，由交互循环把
      user_input 替换为该文本后复用现有"非斜杠输入"执行路径（Span、异常处理、
      会话保存与 presenter 全部复用），避免复制整段执行循环体。

    参数:
        parsed: 解析后的斜杠命令
        skill_manager: Skill 管理器，为 None 时提示 Skill 功能未启用
        skill_loader: Skill 正文加载器，与 skill_manager 同时构建或同时为 None

    返回:
        合成任务文本（需转交引擎执行）或 None（本轮已处理完毕）
    """
    if skill_manager is None or skill_loader is None:
        print("Skill 功能未启用")
        return None
    if parsed.name == "skills":
        skills = skill_manager.skills
        if not skills:
            print("未发现可用技能")
        else:
            print("可用技能:")
            for skill in skills:
                print(f"* {skill.qualified_name}  {skill.description}")
        issues = skill_manager.issues
        if issues:
            print("不可用项:")
            for issue in issues:
                print(f"- {issue.directory}: {issue.reason}")
        return None
    # 剩余分支为 /skill <名称或来源限定名> [补充说明...]
    if not parsed.args:
        print("用法: /skill <名称或来源限定名> [补充说明]")
        return None
    ref = parsed.args[0]
    # 缺少任务说明时拒绝执行，避免用占位文本把"无"交给 LLM 处理
    if len(parsed.args) <= 1:
        print("请提供任务说明，例如： /skill <名称> <任务>")
        return None
    try:
        skill = skill_manager.resolve(ref)
    except SkillNotFoundError as exc:
        # 名称校验失败不进入 LLM 执行；透出异常消息，含同名歧义时的候选列表
        print(f"错误: {exc}")
        return None
    rendered = skill_loader.load(skill.qualified_name)
    # 加载器错误（如正文超预算）以 "错误:" 开头返回，此时提示后不进入 LLM 执行
    if rendered.startswith("错误:"):
        print(rendered)
        return None
    instruction = " ".join(parsed.args[1:])
    print(f"使用技能: {skill.qualified_name}")
    return (
        f'请严格按照技能 "{skill.qualified_name}" 的说明执行任务。\n\n'
        f"{rendered}\n\n"
        f"用户补充说明:\n{instruction}"
    )


def run() -> int:
    """CLI 主入口，返回进程退出码。"""
    parser = _build_parser()
    args = parser.parse_args()

    # 加载配置
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    # 初始化日志系统
    setup_logging(config.logging)
    setup_observability(config.observability)
    setup_content_capture(config.observability, config.logging.log_llm_content)
    logger.info(
        "SAgent 启动",
        extra={"event": "startup", "model": config.llm.model},
    )

    with TerminalInput() as reader:
        return _run_interactive(config, reader)


def _run_interactive(config: AppConfig, reader: TerminalInput) -> int:
    """使用入口持有的共享输入构建依赖并运行交互循环。"""
    # 构建依赖（权限执行器仅当 permissions.enabled 时注入，未启用时保持全自动执行）
    llm = LLMClient(config.llm)
    summary_llm = (
        LLMClient(config.context.summary_llm)
        if config.context.enable_summary and config.context.summary_llm is not None
        else None
    )
    permission = _build_permission(config.permissions, reader=reader)
    registry = build_default_registry(permission=permission)
    context_manager = ContextManager(
        config.context,
        summary_llm=summary_llm,
        summary_model=config.context.summary_llm.model
        if config.context.summary_llm is not None
        else "",
    )

    # 构建长期记忆管理器（仅当 memory 启用时）；并向工具表追加三个记忆工具
    memory_manager = None
    if config.memory.enabled:
        memory_store = MemoryStore(
            directory=Path.cwd() / config.memory.dir,
            user_max_chars=config.memory.user_max_chars,
            memory_max_chars=config.memory.memory_max_chars,
        )
        memory_manager = MemoryManager(memory_store, llm)
        registry.register(AddMemoryTool(memory_manager))
        registry.register(ReplaceMemoryTool(memory_manager))
        registry.register(RemoveMemoryTool(memory_manager))

    # 构建 Skill 管理器（仅当 skills 启用时）；启动时执行一次发现扫描
    skill_manager: SkillManager | None = None
    # 正文加载器与技能管理器同时构建，供 load_skill 工具与 /skill 命令复用
    skill_loader: SkillLoader | None = None
    if config.skills.enabled:
        from ..tools.skill_tool import LoadSkillTool, ReadSkillResourceTool

        skill_manager = SkillManager(config.skills)
        skill_manager.discover()
        # 与记忆工具一致：skill 工具按需显式注册，不进入 build_default_registry
        # 渲染后全文的输出预算与上下文侧单条工具结果截断预算保持一致；
        # 资源文件读取由 skills.max_resource_tokens 独立控制；
        # token 计数函数与截断侧完全相同（同 model/method），杜绝口径偏差
        skill_loader = SkillLoader(
            skill_manager,
            max_body_tokens=config.skills.max_body_tokens,
            max_output_tokens=config.context.max_tool_output_tokens,
            max_resource_tokens=config.skills.max_resource_tokens,
            token_counter=count_text_tokens,
            token_counter_kwargs={
                "model": config.context.summary_llm.model if config.context.summary_llm is not None else "",
                "method": config.context.token_counter_method,
            },
        )
        registry.register(LoadSkillTool(skill_loader))
        registry.register(ReadSkillResourceTool(skill_loader))

    # 构建 MCP 工具提供者（仅当 mcp 启用时）；启动会话管理器并注册 MCP 工具
    mcp_session_manager: MCPSessionManager | None = None
    if config.mcp.enabled:
        mcp_session_manager = MCPSessionManager()
        mcp_session_manager.start()
        for provider in build_mcp_providers(config.mcp, mcp_session_manager):
            registry.register_provider(provider)

    # 构建会话管理器（必须在 build_engine 之前，
    # 因为 SessionManager 构造时会向 context_manager 注册压缩回调）
    session_store = None
    session_manager = None
    if config.session.enabled:
        session_store = SessionStore(config.session.db_path, config.session.enable_fts)
        session_manager = SessionManager(session_store, context_manager)
        # 不在启动时自动创建会话，延迟到首次用户输入时按需创建，
        # 避免用户直接退出或仅浏览历史时产生垃圾空会话

    presenter = EventPresenter(StdoutAdapter(sys.stdout))
    engine = build_engine(
        llm,
        registry,
        config.agent,
        planning_config=config.planning,
        on_event=presenter.handle,
        context_manager=context_manager,
    )
    engine._session_manager = session_manager

    # 会话开始前构建冻结的记忆前缀：memory 启用时读取记忆文件构造前缀，
    # 整个会话复用同一份前缀以命中 prefix cache；未启用时为 None，保持默认行为。
    # 引擎在 run 时将该前缀与自身默认系统提示词叠加，而非替换。
    memory_prefix: str | None = None
    if memory_manager is not None:
        memory_prefix = memory_manager.build_memory_prefix()

    # 会话开始前构建冻结的 Skill 目录前缀：仅含元数据目录与 load_skill 调用约束，
    # 整个会话复用同一份前缀以命中 prefix cache；未启用或无可用技能时为 None。
    skill_prefix: str | None = None
    if skill_manager is not None:
        skill_prefix = build_skill_catalog_prefix(skill_manager)

    tool_names = ", ".join(t.name for t in registry.list_tools())
    print("=" * 60)
    print("SAgent 已启动")
    print(
        f"模型: {config.llm.model}  |  计划策略: {config.planning.strategy}  |  "
        f"流式输出: {'开启' if config.llm.stream else '关闭'}"
    )
    print(f"可用工具: {tool_names}")
    if session_manager is not None:
        cur = session_manager.get_current_session()
        if cur is not None:
            print(f"当前会话: {cur.id} | {cur.title}")
        else:
            print("会话: 尚未创建（首次对话时自动创建）")
        print("会话命令: /new /sessions /switch /rename /delete /search /session /help")
    if memory_manager is not None:
        print(f"记忆: 已启用 | {config.memory.dir}/ @ {Path.cwd()}")
    else:
        print("记忆: 未启用")
    if mcp_session_manager is not None:
        mcp_servers = [s.name for s in config.mcp.servers if s.enabled]
        mcp_tool_count = sum(1 for t in registry.list_tools() if t.name.startswith("mcp_"))
        print(f"MCP: 已启用 | 服务器: {', '.join(mcp_servers) or '无'} | 工具: {mcp_tool_count} 个")
    if skill_manager is not None:
        skills = skill_manager.skills
        issue_count = len(skill_manager.issues)
        summary = f"可用技能: {len(skills)} 个" if skills else "未发现可用技能"
        if issue_count:
            summary += f" | 异常: {issue_count} 条（/skills 查看）"
        print(f"Skill: 已启用 | {summary}")
    else:
        print("Skill: 未启用")
    if permission is not None:
        print(
            f"权限控制: 已启用 | 审批超时: {config.permissions.ask_timeout:g} 秒 | "
            "只读自动放行，写盘/执行类工具需确认（y/n/a），危险命令直接拒绝"
        )
    else:
        print("权限控制: 未启用（工具全自动执行）")
    print("输入你的问题开始对话；输入 exit / quit 退出。")
    print("=" * 60)

    # 交互循环
    while True:
        try:
            user_input = reader.read("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            if session_manager is not None and config.session.auto_save:
                try:
                    session_manager.save_current()
                except Exception:
                    logger.exception("退出前会话保存失败", extra={"event": "session_save_error"})
            if mcp_session_manager is not None:
                mcp_session_manager.shutdown()
            flush_metrics()
            close_observability()
            print("\n再见。")
            return 0

        if not user_input:
            continue
        if user_input.lower() in _EXIT_COMMANDS:
            if session_manager is not None and config.session.auto_save:
                try:
                    session_manager.save_current()
                except Exception:
                    logger.exception("退出前会话保存失败", extra={"event": "session_save_error"})
            if mcp_session_manager is not None:
                mcp_session_manager.shutdown()
            flush_metrics()
            close_observability()
            print("再见。")
            logger.info("用户退出", extra={"event": "exit"})
            return 0

        # 检查是否为斜杠命令
        parsed = parse_command(user_input)
        if parsed is not None:
            # /skills、/skill 走 Skill 命令分发；/skill 合法命中时返回合成任务文本，
            # 替换 user_input 后继续走下方普通执行路径（复用 Span、异常处理与会话保存），
            # 返回 None 表示命令已处理完，直接进入下一轮
            if parsed.name in ("skills", "skill"):
                skill_task = _dispatch_skill_command(parsed, skill_manager, skill_loader)
                if skill_task is None:
                    continue
                user_input = skill_task
            else:
                _dispatch_session_command(parsed, session_manager, skills_enabled=config.skills.enabled)
                continue

        # 延迟创建会话：首次真正对话时按需创建，避免启动即产生空会话。
        # 若用户已通过 /new 或 /switch 显式创建/切换会话，则 get_current_session() 返回非 None，不会重复创建。
        if session_manager is not None and session_manager.get_current_session() is None:
            session_manager.ensure_current_session()

        # 获取当前 session_id（仅在会话上下文可用时）
        session_id = None
        if session_manager is not None:
            cur = session_manager.get_current_session()
            if cur is not None:
                session_id = cur.id

        with Span("agent.run", session_id=session_id) as span:
            span.set_attribute("sagent.planning_strategy", config.planning.strategy)
            logger.info(
                "收到用户输入",
                extra={"event": "user_input", "planning_strategy": config.planning.strategy},
            )

            # 每轮开始前重置 presenter 的流式跟踪，并终结上一轮可能残留的半行输出
            presenter.begin_turn()
            try:
                answer = engine.run(
                    user_input,
                    memory_prefix=memory_prefix,
                    skill_prefix=skill_prefix,
                )
            except KeyboardInterrupt:
                # 用户在执行期间中断：终结流式半行，记录状态后继续交互循环
                span.set_attribute("sagent.outcome", "interrupted")
                span.set_attribute("sagent.mode", engine.last_mode)
                span.set_status("error")
                presenter.end_streaming()
                print("[已中断当前执行]")
                logger.info("用户中断执行", extra={"event": "run_interrupted"})
                if session_manager is not None and config.session.auto_save:
                    try:
                        session_manager.save_current()
                    except Exception:
                        logger.exception("会话自动保存失败", extra={"event": "session_save_error"})
                continue
            except Exception as exc:  # 捕获运行期异常，避免整个 CLI 崩溃
                span.set_attribute("sagent.outcome", "failed")
                span.set_attribute("sagent.mode", engine.last_mode)
                span.set_status("error")
                # 异常可能发生在流式输出中途：先终结半行，避免下一轮输出粘连
                presenter.end_streaming()
                print(f"执行出错: {exc}", file=sys.stderr)
                logger.exception("执行出错", extra={"event": "run_error"})
                # 异常时也尝试保存已有消息
                if session_manager is not None and config.session.auto_save:
                    try:
                        session_manager.save_current()
                    except Exception:
                        logger.exception("会话自动保存失败", extra={"event": "session_save_error"})
                continue

            outcome = "completed"
            if not answer:
                outcome = "empty_answer"
            span.set_attribute("sagent.outcome", outcome)
            span.set_attribute("sagent.mode", engine.last_mode)
            span.set_attribute("sagent.answer_length", len(answer))
            logger.info(
                "生成最终回复",
                extra={"event": "final_answer", "answer_length": len(answer)},
            )
            # 非流式模式始终打印；流式模式下仅当本轮没有任何增量流出时
            # 才打印兜底答案（如空内容提示、最大迭代收尾），避免重复输出
            if not config.llm.stream or not presenter.turn_streamed:
                print(f"\n助手 > {answer}")

        # 每轮问答后增量保存会话消息
        if session_manager is not None and config.session.auto_save:
            try:
                session_manager.save_current()
            except Exception:
                logger.exception("会话自动保存失败", extra={"event": "session_save_error"})


def main() -> None:
    """脚本入口封装。"""
    sys.exit(run())


if __name__ == "__main__":
    main()
