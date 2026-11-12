"""Web 服务启动入口。

提供 `python -m sagent.server` 启动方式，加载配置后按 server 段监听地址与端口
启动 FastAPI 应用。
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from ..config.loader import ConfigError, load_config
from ..observability import setup_content_capture, setup_logging, setup_observability
from .app import create_app


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="sagent.server",
        description="SAgent Web 服务。",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）",
    )
    return parser


def main() -> int:
    """启动 Web 服务，返回进程退出码。"""
    args = _build_parser().parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    # 初始化日志与可观测性，与 CLI 启动流程保持一致
    setup_logging(config.logging)
    setup_observability(config.observability)
    setup_content_capture(config.observability, config.logging.log_llm_content)

    uvicorn.run(create_app(config), host=config.server.host, port=config.server.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
