"""CLI 应用。

提供命令行入口：加载配置、构建工具与引擎，进入交互循环。
支持通过 --config 指定配置文件，--mode 选择 react / plan 执行模式。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config.loader import ConfigError, load_config
from ..context.context_manager import ContextManager
from ..core.plan_engine import PlanEngine
from ..core.react_engine import ReActEngine
from ..llm.client import LLMClient
from ..memory import MemoryManager, MemoryStore
from ..observability import get_logger, new_trace_id, setup_logging
from ..session.manager import SessionManager
from ..session.store import SessionStore
from ..tools import AddMemoryTool, RemoveMemoryTool, ReplaceMemoryTool, build_default_registry
from ..tools.mcp import MCPSessionManager, build_mcp_providers
from .commands import ParsedCommand, parse_command

# 退出命令
_EXIT_COMMANDS = {"exit", "quit", ":q"}

logger = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="sagent",
        description="SAgent: 一个简单通用的 CLI Agent（支持 ReAct 与 Plan 模式）。",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认 config.yaml）",
    )
    parser.add_argument(
        "--mode",
        choices=["react", "plan"],
        default=None,
        help="执行模式：react 或 plan（默认取配置文件中的 agent.mode）",
    )
    return parser


def _print_event(text: str) -> None:
    """打印中间过程事件。"""
    print(text, flush=True)


def resolve_mode(args_mode: str | None, config_mode: str) -> str:
    """确定最终执行模式：命令行 --mode 优先，否则回退到配置中的默认模式。"""
    return args_mode or config_mode


def build_engine(
    mode: str,
    llm: LLMClient,
    registry,
    agent_config,
    on_event=None,
    context_manager=None,
) -> PlanEngine | ReActEngine:
    """根据模式构建对应的执行引擎。"""
    if mode == "plan":
        return PlanEngine(llm, registry, agent_config, on_event=on_event, context_manager=context_manager)
    return ReActEngine(llm, registry, agent_config, on_event=on_event, context_manager=context_manager)


def _dispatch_session_command(parsed: ParsedCommand, session_manager) -> None:
    """分发会话管理斜杠命令，负责 IO 编排，不返回值。

    参数:
        parsed: 解析后的斜杠命令
        session_manager: 会话管理器，为 None 时提示会话管理未启用
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
        sessions = session_manager.list_sessions()
        cur = session_manager.get_current_session()
        cur_id = cur.id if cur is not None else None
        if not sessions:
            print("暂无会话")
        else:
            for s in sessions:
                mark = "*" if s.id == cur_id else " "
                print(
                    f"{mark} {s.id} | {s.title} | 更新: {s.updated_at} | 消息: {s.message_count}"
                )
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
        print("  /sessions        列出全部会话")
        print("  /switch <id>     切换会话")
        print("  /rename <标题>   重命名当前会话")
        print("  /delete <id>     删除会话(不能删除当前会话)")
        print("  /search <关键词> 搜索历史消息")
        print("  /session         显示当前会话信息")
        print("  /help            显示此帮助")
    else:
        print("未知命令，输入 /help 查看可用命令")


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
    logger.info(
        "SAgent 启动",
        extra={"event": "startup", "model": config.llm.model},
    )

    # 命令行 --mode 覆盖配置中的默认模式
    mode = resolve_mode(args.mode, config.agent.mode)

    # 构建依赖
    llm = LLMClient(config.llm)
    registry = build_default_registry()
    context_manager = ContextManager(config.context, llm, config.llm.model)

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
        session_manager = SessionManager(session_store, context_manager, mode=mode)
        session_manager.ensure_current_session()

    engine = build_engine(
        mode, llm, registry, config.agent, on_event=_print_event,
        context_manager=context_manager,
    )

    # 会话开始前构建冻结的记忆前缀：memory 启用时读取记忆文件构造前缀，
    # 整个会话复用同一份前缀以命中 prefix cache；未启用时为 None，保持默认行为。
    # 引擎在 run 时将该前缀与自身默认系统提示词叠加，而非替换。
    memory_prefix: str | None = None
    if memory_manager is not None:
        memory_prefix = memory_manager.build_memory_prefix()

    tool_names = ", ".join(t.name for t in registry.list_tools())
    print("=" * 60)
    print("SAgent 已启动")
    print(f"模型: {config.llm.model}  |  模式: {mode}")
    print(f"可用工具: {tool_names}")
    if session_manager is not None:
        cur = session_manager.get_current_session()
        if cur is not None:
            print(f"当前会话: {cur.id} | {cur.title}")
        print("会话命令: /new /sessions /switch /rename /delete /search /session /help")
    if memory_manager is not None:
        print(f"记忆: 已启用 | {config.memory.dir}/ @ {Path.cwd()}")
    else:
        print("记忆: 未启用")
    if mcp_session_manager is not None:
        mcp_servers = [s.name for s in config.mcp.servers if s.enabled]
        mcp_tool_count = sum(1 for t in registry.list_tools() if t.name.startswith("mcp_"))
        print(f"MCP: 已启用 | 服务器: {', '.join(mcp_servers) or '无'} | 工具: {mcp_tool_count} 个")
    print("输入你的问题开始对话；输入 exit / quit 退出。")
    print("=" * 60)

    # 交互循环
    while True:
        try:
            user_input = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            if session_manager is not None and config.session.auto_save:
                try:
                    session_manager.save_current()
                except Exception:
                    logger.exception("退出前会话保存失败", extra={"event": "session_save_error"})
            if mcp_session_manager is not None:
                mcp_session_manager.shutdown()
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
            print("再见。")
            logger.info("用户退出", extra={"event": "exit"})
            return 0

        # 检查是否为斜杠命令
        parsed = parse_command(user_input)
        if parsed is not None:
            _dispatch_session_command(parsed, session_manager)
            continue

        # 为本次问答生成 trace_id，串联整条链路
        new_trace_id()
        logger.info(
            "收到用户输入",
            extra={"event": "user_input", "input": user_input, "mode": mode},
        )

        try:
            answer = engine.run(user_input, memory_prefix=memory_prefix)
        except Exception as exc:  # 捕获运行期异常，避免整个 CLI 崩溃
            print(f"执行出错: {exc}", file=sys.stderr)
            logger.exception("执行出错", extra={"event": "run_error"})
            # 异常时也尝试保存已有消息
            if session_manager is not None and config.session.auto_save:
                try:
                    session_manager.save_current()
                except Exception:
                    logger.exception("会话自动保存失败", extra={"event": "session_save_error"})
            continue

        logger.info(
            "生成最终回复",
            extra={"event": "final_answer", "answer_length": len(answer)},
        )
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
