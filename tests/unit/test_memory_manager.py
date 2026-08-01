"""记忆管理器单元测试。

覆盖 MemoryManager 的：
- build_memory_prefix：记忆为空时仅返回引导提示词；只 user / 只 memory / 两者均有
  内容时的前缀拼装与顺序。
- add / replace / remove：委托 store 写入、返回结果字符串、未找到目标文本返回提示、
  不超限时不触发反思。
- 超限触发反思整理：FakeLLMClient 回放返回整理后短文本，断言 store 内容被替换、
  LLM 被调用一次。
- 反思容错：LLM 抛异常时不阻断、保留写入前内容；LLM 输出为空时保留写入前内容。

使用 conftest 的 make_fake_llm 与 text_response 驱动，不调用真实模型。
"""

from __future__ import annotations

from typing import Any

from conftest import text_response

from sagent.memory.manager import MemoryManager
from sagent.memory.store import MemoryStore


class _BoomLLM:
    """chat 永远抛异常的假 LLM，用于验证反思整理失败容错。"""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        raise RuntimeError("boom")


# ========== build_memory_prefix ==========


def test_build_memory_prefix_empty_returns_guide_only(tmp_path, make_fake_llm):
    """记忆为空时仅返回记忆使用引导提示词，不含实际记忆小节。"""
    store = MemoryStore(tmp_path)
    manager = MemoryManager(store, make_fake_llm([]))
    prefix = manager.build_memory_prefix()
    # 引导提示词始终注入，即使记忆为空
    assert prefix  # 非空
    assert "add_memory" in prefix
    assert "[长期记忆]" in prefix
    # 记忆为空，不含实际记忆小节
    assert "## 用户档案" not in prefix
    assert "## 记忆" not in prefix


def test_build_memory_prefix_user_only(tmp_path, make_fake_llm):
    """只 user 有内容时前缀只含用户档案小节。"""
    store = MemoryStore(tmp_path)
    store.write_all("user", "用户喜欢深色模式")
    manager = MemoryManager(store, make_fake_llm([]))
    prefix = manager.build_memory_prefix()
    assert "[长期记忆]" in prefix
    assert "## 用户档案" in prefix
    assert "用户喜欢深色模式" in prefix
    # 不含记忆小节
    assert "## 记忆" not in prefix


def test_build_memory_prefix_memory_only(tmp_path, make_fake_llm):
    """只 memory 有内容时前缀只含记忆小节。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "项目使用 Python3.10")
    manager = MemoryManager(store, make_fake_llm([]))
    prefix = manager.build_memory_prefix()
    assert "[长期记忆]" in prefix
    assert "## 记忆" in prefix
    assert "项目使用 Python3.10" in prefix
    # 不含用户档案小节
    assert "## 用户档案" not in prefix


def test_build_memory_prefix_both_sections(tmp_path, make_fake_llm):
    """两者均有内容时前缀含两个小节，用户档案在记忆之前。"""
    store = MemoryStore(tmp_path)
    store.write_all("user", "用户偏好A")
    store.write_all("memory", "项目经验B")
    manager = MemoryManager(store, make_fake_llm([]))
    prefix = manager.build_memory_prefix()
    assert "[长期记忆]" in prefix
    assert "## 用户档案" in prefix
    assert "## 记忆" in prefix
    assert "用户偏好A" in prefix
    assert "项目经验B" in prefix
    # 用户档案小节在记忆小节之前
    assert prefix.index("## 用户档案") < prefix.index("## 记忆")


def test_build_memory_prefix_whitespace_treated_as_empty(tmp_path, make_fake_llm):
    """仅含空白的文件被视为空，前缀只含引导提示词不含记忆小节。"""
    store = MemoryStore(tmp_path)
    store.write_all("user", "   \n  \n")
    store.write_all("memory", "")
    manager = MemoryManager(store, make_fake_llm([]))
    prefix = manager.build_memory_prefix()
    # 引导提示词始终注入
    assert prefix
    assert "add_memory" in prefix
    # 空白内容视为空，不含记忆小节
    assert "## 用户档案" not in prefix
    assert "## 记忆" not in prefix


# ========== add ==========


def test_add_delegates_and_returns_result(tmp_path, make_fake_llm):
    """add 委托 store.append 并返回结果字符串；不超限时不触发反思。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    llm = make_fake_llm([])
    manager = MemoryManager(store, llm)
    result = manager.add("user", "新增偏好")
    assert "已添加" in result
    assert "user" in result
    assert store.read_all("user") == "新增偏好"
    # 未超限，LLM 未被调用
    assert llm.calls == []


def test_add_not_over_limit_no_reflection(tmp_path, make_fake_llm):
    """内容未超上限时不触发反思，FakeLLMClient 空队列不报错。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    llm = make_fake_llm([])
    manager = MemoryManager(store, llm)
    manager.add("memory", "短内容")
    assert llm.remaining == 0
    assert llm.calls == []


def test_add_over_limit_triggers_reflection(tmp_path, make_fake_llm):
    """超限时触发反思整理，store 内容被替换为整理后短文本，LLM 调用一次。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    llm = make_fake_llm([text_response("精简后的记忆")])
    manager = MemoryManager(store, llm)
    big = "x" * 100  # 远超 20 上限
    result = manager.add("user", big)
    # add 返回结果字符串（反思在内部完成）
    assert "已添加" in result
    # LLM 被调用一次
    assert len(llm.calls) == 1
    # 反思整理后内容被替换为短文本
    assert store.read_all("user") == "精简后的记忆"
    # 整理后内容未超限
    assert store.is_over_limit("user") is False


# ========== replace ==========


def test_replace_found_returns_success(tmp_path, make_fake_llm):
    """replace 找到目标返回成功提示，内容被替换。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    llm = make_fake_llm([])
    manager = MemoryManager(store, llm)
    store.write_all("user", "旧偏好")
    result = manager.replace("user", "旧偏好", "新偏好")
    assert "已替换" in result
    assert store.read_all("user") == "新偏好"
    # 未超限不触发反思
    assert llm.calls == []


def test_replace_not_found_returns_hint(tmp_path, make_fake_llm):
    """replace 未找到目标返回提示，不报错、不修改文件。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    llm = make_fake_llm([])
    manager = MemoryManager(store, llm)
    store.write_all("user", "原始内容")
    result = manager.replace("user", "不存在", "新")
    assert "未在" in result
    assert "找到" in result
    assert store.read_all("user") == "原始内容"
    # 未触发反思
    assert llm.calls == []


def test_replace_over_limit_triggers_reflection(tmp_path, make_fake_llm):
    """替换后超限触发反思整理，内容被替换为整理后文本。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    store.write_all("memory", "旧内容")
    llm = make_fake_llm([text_response("整理后")])
    manager = MemoryManager(store, llm)
    manager.replace("memory", "旧内容", "y" * 100)
    assert len(llm.calls) == 1
    assert store.read_all("memory") == "整理后"


# ========== remove ==========


def test_remove_found_returns_success(tmp_path, make_fake_llm):
    """remove 找到目标返回成功提示，内容被移除。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    llm = make_fake_llm([])
    manager = MemoryManager(store, llm)
    store.write_all("memory", "待删除\n保留内容")
    result = manager.remove("memory", "待删除")
    assert "已从" in result
    assert "删除" in result
    remaining = store.read_all("memory")
    assert "待删除" not in remaining
    assert "保留内容" in remaining
    # 未超限不触发反思
    assert llm.calls == []


def test_remove_not_found_returns_hint(tmp_path, make_fake_llm):
    """remove 未找到目标返回提示，不报错、不修改文件。"""
    store = MemoryStore(tmp_path, user_max_chars=1000, memory_max_chars=1000)
    llm = make_fake_llm([])
    manager = MemoryManager(store, llm)
    store.write_all("memory", "原始内容")
    result = manager.remove("memory", "不存在")
    assert "未在" in result
    assert "找到" in result
    assert store.read_all("memory") == "原始内容"
    assert llm.calls == []


# ========== 反思整理容错 ==========


def test_reflect_llm_exception_no_throw_keeps_content(tmp_path):
    """LLM 抛异常时反思整理不抛异常，保留写入前内容。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    manager = MemoryManager(store, _BoomLLM())
    big = "x" * 100  # 超限触发反思，但 LLM 抛异常
    # add 不应抛异常
    result = manager.add("user", big)
    assert "已添加" in result
    # 反思失败，保留写入前的大内容
    assert store.read_all("user") == big


def test_reflect_empty_output_keeps_content(tmp_path, make_fake_llm):
    """LLM 输出为空时保留写入前内容。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    llm = make_fake_llm([text_response("")])
    manager = MemoryManager(store, llm)
    big = "x" * 100
    result = manager.add("user", big)
    assert "已添加" in result
    # LLM 被调用一次但返回空
    assert len(llm.calls) == 1
    # 内容保留为写入前的大内容
    assert store.read_all("user") == big


def test_reflect_whitespace_output_keeps_content(tmp_path, make_fake_llm):
    """LLM 输出仅空白时视为空，保留写入前内容。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    llm = make_fake_llm([text_response("   \n  ")])
    manager = MemoryManager(store, llm)
    big = "x" * 100
    manager.add("user", big)
    assert len(llm.calls) == 1
    assert store.read_all("user") == big
