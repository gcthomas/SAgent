"""MCP 工具过滤逻辑。

通过 allow / deny 两个列表实现白名单/黑名单过滤，不依赖 MCP SDK，可独立单元测试。
"""

from __future__ import annotations


class ToolFilter:
    """MCP 工具过滤器。

    过滤语义：
    - allow 与 deny 均为空 -> 不过滤，所有工具通过
    - allow 非空 -> 只有 allow 列表中的工具通过（白名单）
    - deny 非空 -> deny 列表中的工具被拦截，其余通过（黑名单）
    - allow 与 deny 均非空 -> 先白名单过滤，再从结果中移除 deny 中的工具（交集后再排除）

    参数:
        allow: 白名单工具名列表，为空时不做白名单过滤
        deny: 黑名单工具名列表，为空时不做黑名单过滤
    """

    def __init__(self, allow: list[str] | None = None, deny: list[str] | None = None) -> None:
        self._allow = set(allow) if allow else set()
        self._deny = set(deny) if deny else set()

    def should_include(self, tool_name: str) -> bool:
        """判断单个工具是否应被包含。

        参数:
            tool_name: 工具名称

        返回:
            True 表示该工具通过过滤应被包含，False 表示被过滤掉
        """
        # allow 与 deny 均为空：不过滤
        if not self._allow and not self._deny:
            return True
        # allow 非空：白名单模式，只有 allow 中的通过
        if self._allow:
            if tool_name not in self._allow:
                return False
            # allow 非空且 deny 也非空：白名单通过后再检查 deny
            if self._deny and tool_name in self._deny:
                return False
            return True
        # 仅 deny 非空：黑名单模式
        if self._deny and tool_name in self._deny:
            return False
        return True

    def filter_tools(self, tool_names: list[str]) -> list[str]:
        """批量过滤工具名列表。

        参数:
            tool_names: 待过滤的工具名列表

        返回:
            通过过滤的工具名列表（保持原顺序）
        """
        return [name for name in tool_names if self.should_include(name)]
