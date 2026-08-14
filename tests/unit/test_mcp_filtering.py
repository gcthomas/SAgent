"""ToolFilter 过滤逻辑单元测试。

测试 allow 白名单、deny 黑名单、两者均空无过滤、两者均非空先白名单再排除 各种场景。
"""

from __future__ import annotations

from sagent.tools.mcp.filtering import ToolFilter


class TestToolFilterNoFilter:
    """allow 与 deny 均为空时，不过滤。"""

    def test_default_no_filter(self):
        f = ToolFilter()
        assert f.should_include("any_tool") is True

    def test_empty_lists_no_filter(self):
        f = ToolFilter(allow=[], deny=[])
        assert f.should_include("any_tool") is True

    def test_filter_tools_passes_all(self):
        f = ToolFilter()
        result = f.filter_tools(["a", "b", "c"])
        assert result == ["a", "b", "c"]


class TestToolFilterAllowWhitelist:
    """allow 非空时，只有 allow 列表中的工具通过。"""

    def test_only_allowlisted_passes(self):
        f = ToolFilter(allow=["tool_a", "tool_b"])
        assert f.should_include("tool_a") is True
        assert f.should_include("tool_b") is True
        assert f.should_include("tool_c") is False

    def test_filter_tools_whitelist(self):
        f = ToolFilter(allow=["a", "b"])
        result = f.filter_tools(["a", "b", "c", "d"])
        assert result == ["a", "b"]

    def test_order_preserved(self):
        f = ToolFilter(allow=["c", "a", "b"])
        result = f.filter_tools(["a", "b", "c", "d"])
        assert result == ["a", "b", "c"]


class TestToolFilterDenyBlacklist:
    """deny 非空时，deny 列表中的工具被拦截。"""

    def test_denylisted_blocked(self):
        f = ToolFilter(deny=["dangerous_tool"])
        assert f.should_include("dangerous_tool") is False
        assert f.should_include("safe_tool") is True

    def test_filter_tools_blacklist(self):
        f = ToolFilter(deny=["b"])
        result = f.filter_tools(["a", "b", "c"])
        assert result == ["a", "c"]


class TestToolFilterAllowAndDeny:
    """allow 与 deny 均非空时，先白名单过滤再从结果中排除 deny。"""

    def test_combined_whitelist_then_exclude(self):
        f = ToolFilter(allow=["a", "b", "c"], deny=["b"])
        assert f.should_include("a") is True
        assert f.should_include("b") is False  # 在 allow 中但在 deny 中
        assert f.should_include("c") is True
        assert f.should_include("d") is False  # 不在 allow 中

    def test_filter_tools_combined(self):
        f = ToolFilter(allow=["a", "b", "c"], deny=["b"])
        result = f.filter_tools(["a", "b", "c", "d"])
        assert result == ["a", "c"]
