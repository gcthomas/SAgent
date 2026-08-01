"""长期记忆工具单元测试。

覆盖 AddMemoryTool / ReplaceMemoryTool / RemoveMemoryTool：
- to_openai_schema：type 为 function、function.name 正确、parameters 含 target
  与对应业务字段、target 字段含 enum ["user","memory"]。
- run 委托 manager：用真实 MemoryManager（store 用 tmp_path，llm 用 FakeLLMClient
  空队列因不触发反思）实例化工具，调用 run 验证委托生效（断言返回字符串与
  store/文件内容变化）。
"""

from __future__ import annotations

import pytest

from sagent.memory.manager import MemoryManager
from sagent.memory.store import MemoryStore
from sagent.tools.memory_tool import (
    AddMemoryArgs,
    AddMemoryTool,
    RemoveMemoryArgs,
    RemoveMemoryTool,
    ReplaceMemoryArgs,
    ReplaceMemoryTool,
)


@pytest.fixture
def store_and_manager(tmp_path, make_fake_llm):
    """构建真实 MemoryManager（空队列 LLM，因不超限不会触发反思）。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    manager = MemoryManager(store, make_fake_llm([]))
    return store, manager


# ========== Schema：AddMemoryTool ==========


def test_add_memory_schema_shape(store_and_manager):
    """add_memory schema 基本结构正确。"""
    _, manager = store_and_manager
    schema = AddMemoryTool(manager).to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "add_memory"
    assert "description" in schema["function"]
    params = schema["function"]["parameters"]
    assert "target" in params["properties"]
    assert "content" in params["properties"]
    # 必填字段
    assert "target" in params["required"]
    assert "content" in params["required"]


def test_add_memory_schema_target_enum(store_and_manager):
    """add_memory 的 target 字段含 enum ["user","memory"]。"""
    _, manager = store_and_manager
    schema = AddMemoryTool(manager).to_openai_schema()
    target_prop = schema["function"]["parameters"]["properties"]["target"]
    assert target_prop.get("enum") == ["user", "memory"]


# ========== Schema：ReplaceMemoryTool ==========


def test_replace_memory_schema_shape(store_and_manager):
    """replace_memory schema 基本结构正确。"""
    _, manager = store_and_manager
    schema = ReplaceMemoryTool(manager).to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "replace_memory"
    assert "description" in schema["function"]
    params = schema["function"]["parameters"]
    assert "target" in params["properties"]
    assert "old" in params["properties"]
    assert "new" in params["properties"]
    assert "target" in params["required"]
    assert "old" in params["required"]
    assert "new" in params["required"]


def test_replace_memory_schema_target_enum(store_and_manager):
    """replace_memory 的 target 字段含 enum ["user","memory"]。"""
    _, manager = store_and_manager
    schema = ReplaceMemoryTool(manager).to_openai_schema()
    target_prop = schema["function"]["parameters"]["properties"]["target"]
    assert target_prop.get("enum") == ["user", "memory"]


# ========== Schema：RemoveMemoryTool ==========


def test_remove_memory_schema_shape(store_and_manager):
    """remove_memory schema 基本结构正确。"""
    _, manager = store_and_manager
    schema = RemoveMemoryTool(manager).to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "remove_memory"
    assert "description" in schema["function"]
    params = schema["function"]["parameters"]
    assert "target" in params["properties"]
    assert "content" in params["properties"]
    assert "target" in params["required"]
    assert "content" in params["required"]


def test_remove_memory_schema_target_enum(store_and_manager):
    """remove_memory 的 target 字段含 enum ["user","memory"]。"""
    _, manager = store_and_manager
    schema = RemoveMemoryTool(manager).to_openai_schema()
    target_prop = schema["function"]["parameters"]["properties"]["target"]
    assert target_prop.get("enum") == ["user", "memory"]


# ========== run 委托 manager ==========


def test_add_memory_run_delegates(store_and_manager):
    """AddMemoryTool.run 委托 manager.add，内容被追加并落盘。"""
    store, manager = store_and_manager
    tool = AddMemoryTool(manager)
    args = AddMemoryArgs(target="user", content="用户偏好深色模式")
    result = tool.run(args)
    assert "已添加" in result
    assert store.read_all("user") == "用户偏好深色模式"
    # 落盘验证
    assert (store._directory / "USER.md").read_text(encoding="utf-8") == "用户偏好深色模式"


def test_add_memory_run_to_memory_target(store_and_manager):
    """AddMemoryTool.run 可写入 memory 目标。"""
    store, manager = store_and_manager
    tool = AddMemoryTool(manager)
    args = AddMemoryArgs(target="memory", content="项目经验条目")
    result = tool.run(args)
    assert "已添加" in result
    assert store.read_all("memory") == "项目经验条目"


def test_replace_memory_run_delegates(store_and_manager):
    """ReplaceMemoryTool.run 委托 manager.replace，内容被替换。"""
    store, manager = store_and_manager
    store.append("memory", "旧经验")
    tool = ReplaceMemoryTool(manager)
    args = ReplaceMemoryArgs(target="memory", old="旧经验", new="新经验")
    result = tool.run(args)
    assert "已替换" in result
    assert store.read_all("memory") == "新经验"


def test_replace_memory_run_not_found_hint(store_and_manager):
    """ReplaceMemoryTool.run 未找到目标返回提示，不报错。"""
    store, manager = store_and_manager
    store.append("memory", "原始内容")
    tool = ReplaceMemoryTool(manager)
    args = ReplaceMemoryArgs(target="memory", old="不存在", new="新")
    result = tool.run(args)
    assert "未在" in result
    assert "找到" in result
    assert store.read_all("memory") == "原始内容"


def test_remove_memory_run_delegates(store_and_manager):
    """RemoveMemoryTool.run 委托 manager.remove，内容被移除。"""
    store, manager = store_and_manager
    store.append("user", "待删除")
    tool = RemoveMemoryTool(manager)
    args = RemoveMemoryArgs(target="user", content="待删除")
    result = tool.run(args)
    assert "已从" in result
    assert "删除" in result
    assert store.read_all("user") == ""


def test_remove_memory_run_not_found_hint(store_and_manager):
    """RemoveMemoryTool.run 未找到目标返回提示，不报错。"""
    store, manager = store_and_manager
    store.append("user", "保留内容")
    tool = RemoveMemoryTool(manager)
    args = RemoveMemoryArgs(target="user", content="不存在")
    result = tool.run(args)
    assert "未在" in result
    assert "找到" in result
    assert store.read_all("user") == "保留内容"


def test_tools_inherit_from_base_tool():
    """三个工具均继承自 Tool 基类。"""
    from sagent.tools.base import Tool

    assert issubclass(AddMemoryTool, Tool)
    assert issubclass(ReplaceMemoryTool, Tool)
    assert issubclass(RemoveMemoryTool, Tool)
