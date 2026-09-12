"""工具注册表。

负责注册工具、输出所有工具的 function schema，以及按名称执行工具。
对未知工具、参数错误、执行异常均做容错处理，返回错误信息字符串而不抛出崩溃。
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..observability import Span, get_logger
from ..permissions import PermissionEnforcer
from .base import Tool, ToolProvider

logger = get_logger(__name__)


class ToolRegistry:
    """工具注册表。"""

    def __init__(self, permission: PermissionEnforcer | None = None) -> None:
        """初始化注册表。

        参数:
            permission: 可选的权限执行器；非 None 时每次工具执行前统一授权
                （执行期单点拦截），缺省 None 保持全自动执行（向后兼容）。
        """
        self._tools: dict[str, Tool] = {}
        self._permission = permission

    def register(self, tool: Tool) -> None:
        """注册单个工具。名称重复时后者覆盖前者。"""
        if not tool.name:
            raise ValueError("工具必须具有非空的 name。")
        self._tools[tool.name] = tool

    def register_provider(self, provider: ToolProvider) -> None:
        """通过工具提供者批量注册工具（为 MCP / skill 预留的接入方式）。"""
        for tool in provider.provide_tools():
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        """按名称获取工具，不存在返回 None。"""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """返回所有已注册工具。"""
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 OpenAI function schema 列表。"""
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: str | dict[str, Any]) -> str:
        """按名称与参数执行工具。

        参数:
            name: 工具名称。
            arguments: 参数，可为 JSON 字符串（来自 LLM tool_calls）或 dict。

        返回:
            执行结果字符串；出现任何错误时返回以"错误:"开头的说明文本。
        """
        tool = self.get(name)
        if tool is None:
            logger.error(
                "未找到工具",
                extra={"event": "tool_not_found", "tool": name},
            )
            return f"错误: 未找到名为 '{name}' 的工具。"

        # 解析参数
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                logger.error(
                    "工具参数非法 JSON",
                    extra={"event": "tool_bad_args", "tool": name},
                )
                return f"错误: 工具 '{name}' 的参数不是合法 JSON: {exc}"
        else:
            parsed = arguments

        # 校验参数
        try:
            args_model = tool.validate_args(parsed)
        except Exception as exc:  # pydantic 校验错误等
            logger.exception(
                "工具参数校验失败",
                extra={"event": "tool_validate_error", "tool": name},
            )
            return f"错误: 工具 '{name}' 的参数校验失败: {exc}"

        # 权限闸门：参数校验通过后、真正执行前统一授权（单点拦截）；
        # 被拒时不执行工具，直接把拒绝说明作为结果返回，供引擎作为 tool 观察回写
        if self._permission is not None:
            outcome = self._permission.authorize(name, parsed)
            if not outcome.allowed:
                return outcome.message

        # 授权通过后才记录 tool_call 日志，保证"tool_call"事件只出现在
        # 实际进入执行的调用上（被拒绝的调用只产生 permission_decision 审计日志）
        logger.info(
            "执行工具",
            extra={"event": "tool_call", "tool": name, "tool_args": parsed},
        )

        # 执行（添加 Span 埋点）
        with Span("tool.execute") as span:
            span.set_attribute("tool.name", name)
            start = time.perf_counter()
            try:
                result = tool.run(args_model)
            except Exception as exc:  # 工具执行期异常
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status("error")
                logger.exception(
                    "工具执行失败",
                    extra={
                        "event": "tool_error",
                        "tool": name,
                        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                    },
                )
                return f"错误: 工具 '{name}' 执行失败: {exc}"
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            span.set_attribute("tool.latency_ms", latency_ms)
            span.set_attribute("tool.result_length", len(result))

        logger.info(
            "工具执行完成",
            extra={
                "event": "tool_result",
                "tool": name,
                "latency_ms": latency_ms,
                "result_length": len(result),
            },
        )
        return result
