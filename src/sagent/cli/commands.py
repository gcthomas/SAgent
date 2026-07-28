"""CLI 斜杠命令解析器。

提供与命令族无关的纯函数 parse_command，把 /name arg... 解析为命令名+参数结构，
非斜杠输入返回 None。未来新增任意 slash command 均复用此解析器。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedCommand:
    """解析后的斜杠命令结构。"""

    # 命令名（不含 /），如 "new"、"switch"
    name: str
    # 参数列表（按空格分割）
    args: list[str]


def parse_command(raw: str) -> ParsedCommand | None:
    """将用户输入解析为斜杠命令。非 / 开头返回 None。

    参数:
        raw: 用户原始输入（已 strip）

    返回:
        ParsedCommand 或 None（非斜杠输入）
    """
    if not raw.startswith("/"):
        return None
    # 去掉前导 /
    body = raw[1:].strip()
    if not body:
        # 仅输入 / 视为未知命令（name 为空）
        return ParsedCommand(name="", args=[])
    parts = body.split()
    return ParsedCommand(name=parts[0], args=parts[1:])
