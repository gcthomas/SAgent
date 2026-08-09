"""内置工具与注册表单元测试。"""

from __future__ import annotations

import datetime
import sys

from sagent.tools import build_default_registry
from sagent.tools.file_tools import ReadFileTool, WriteFileTool
from sagent.tools.registry import ToolRegistry
from sagent.tools.shell_tool import ShellArgs, ShellTool


def test_to_openai_schema_shape():
    tool = ReadFileTool()
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert "parameters" in schema["function"]
    # pydantic 生成的 JSON schema 应包含 path 属性
    assert "path" in schema["function"]["parameters"]["properties"]


def test_write_then_read_roundtrip(tmp_path):
    registry = build_default_registry()
    target = tmp_path / "sub" / "note.txt"

    write_result = registry.execute(
        "write_file", {"path": str(target), "content": "你好，世界"}
    )
    assert "写入文件" in write_result
    # 自动创建父目录并写入成功
    assert target.exists()

    read_result = registry.execute("read_file", {"path": str(target)})
    assert read_result == "你好，世界"


def test_append_mode(tmp_path):
    registry = build_default_registry()
    target = tmp_path / "log.txt"
    registry.execute("write_file", {"path": str(target), "content": "a"})
    registry.execute(
        "write_file", {"path": str(target), "content": "b", "mode": "append"}
    )
    assert registry.execute("read_file", {"path": str(target)}) == "ab"


def test_read_missing_file_returns_error(tmp_path):
    registry = build_default_registry()
    result = registry.execute("read_file", {"path": str(tmp_path / "none.txt")})
    assert result.startswith("错误")


def test_unknown_tool_returns_error():
    registry = ToolRegistry()
    result = registry.execute("no_such_tool", {})
    assert result.startswith("错误")
    assert "no_such_tool" in result


def test_invalid_json_arguments_returns_error(tmp_path):
    registry = build_default_registry()
    # 非法 JSON 字符串
    result = registry.execute("read_file", "{not json}")
    assert result.startswith("错误")


def test_arg_validation_error_returns_error():
    registry = build_default_registry()
    # 缺少必填的 path 参数
    result = registry.execute("read_file", {})
    assert result.startswith("错误")


def test_register_empty_name_raises():
    registry = ToolRegistry()

    class Bad(WriteFileTool):
        name = ""

    import pytest

    with pytest.raises(ValueError):
        registry.register(Bad())


def test_run_shell_with_quoted_args():
    """验证 run_shell 能正确执行带双引号参数的命令，不被 shell 二次解析。"""
    tool = ShellTool()
    if sys.platform == "win32":
        # Windows 上通过 PowerShell -EncodedCommand 执行，双引号参数不应被破坏
        args = ShellArgs(command='Get-Date -Format "yyyy-MM-dd"')
        result = tool.run(args)
        assert "退出码: 0" in result
        # 输出应包含当天日期（yyyy-MM-dd 格式）
        today = datetime.date.today().strftime("%Y-%m-%d")
        assert today in result
    else:
        args = ShellArgs(command='echo "hello world"')
        result = tool.run(args)
        assert "退出码: 0" in result
        assert "hello world" in result
