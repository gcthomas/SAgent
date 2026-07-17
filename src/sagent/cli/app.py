"""CLI 应用。

提供命令行入口：加载配置、构建工具与引擎，进入交互循环。
支持通过 --config 指定配置文件，--mode 选择 react / plan 执行模式。
"""

from __future__ import annotations

import argparse
import sys

from ..config.loader import ConfigError, load_config
from ..context.context_manager import ContextManager
from ..core.plan_engine import PlanEngine
from ..core.prompts import REACT_SYSTEM_PROMPT
from ..core.react_engine import ReActEngine
from ..llm.client import LLMClient
from ..observability import get_logger, new_trace_id, setup_logging
from ..tools import build_default_registry

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

    engine = build_engine(
        mode, llm, registry, config.agent, on_event=_print_event,
        context_manager=context_manager,
    )

    tool_names = ", ".join(t.name for t in registry.list_tools())
    print("=" * 60)
    print("SAgent 已启动")
    print(f"模型: {config.llm.model}  |  模式: {mode}")
    print(f"可用工具: {tool_names}")
    print("输入你的问题开始对话；输入 exit / quit 退出。")
    print("=" * 60)

    # 交互循环
    while True:
        try:
            user_input = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if not user_input:
            continue
        if user_input.lower() in _EXIT_COMMANDS:
            print("再见。")
            logger.info("用户退出", extra={"event": "exit"})
            return 0

        # 为本次问答生成 trace_id，串联整条链路
        new_trace_id()
        logger.info(
            "收到用户输入",
            extra={"event": "user_input", "input": user_input, "mode": mode},
        )

        try:
            answer = engine.run(user_input)
        except Exception as exc:  # 捕获运行期异常，避免整个 CLI 崩溃
            print(f"执行出错: {exc}", file=sys.stderr)
            logger.exception("执行出错", extra={"event": "run_error"})
            continue

        logger.info(
            "生成最终回复",
            extra={"event": "final_answer", "answer_length": len(answer)},
        )
        print(f"\n助手 > {answer}")


def main() -> None:
    """脚本入口封装。"""
    sys.exit(run())


if __name__ == "__main__":
    main()
