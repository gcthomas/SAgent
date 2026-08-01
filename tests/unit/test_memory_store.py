"""长期记忆 Markdown 文件存储单元测试。

覆盖 MemoryStore 的加载（文件不存在初始化空）、读取全文、追加、替换、删除、
字符上限检查、原子落盘（临时文件 + rename 无残留）、整文件覆写与跨实例持久化。
所有写操作使用 tmp_path 做文件隔离，不污染工作目录。
"""

from __future__ import annotations

import pytest

from sagent.memory.store import MemoryStore


# ========== 加载与初始化 ==========


def test_load_missing_file_init_empty(tmp_path):
    """文件不存在时 load 初始化为空字符串，read_all 返回空串，不报错。"""
    store = MemoryStore(tmp_path)
    assert store.read_all("user") == ""
    assert store.read_all("memory") == ""
    # 目录被自动创建
    assert tmp_path.exists()


def test_load_existing_file_reads_content(tmp_path):
    """文件存在时 load 读取全文到缓存。"""
    (tmp_path / "USER.md").write_text("已有用户档案", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("已有记忆", encoding="utf-8")
    store = MemoryStore(tmp_path)
    assert store.read_all("user") == "已有用户档案"
    assert store.read_all("memory") == "已有记忆"


# ========== 追加 ==========


def test_append_to_empty_no_leading_newline(tmp_path):
    """追加到空文件时直接作为正文，无前导换行。"""
    store = MemoryStore(tmp_path)
    store.append("user", "第一条")
    assert store.read_all("user") == "第一条"


def test_append_multiple_concatenates_with_newline(tmp_path):
    """多次追加以换行分隔，read_all 能读回完整内容。"""
    store = MemoryStore(tmp_path)
    store.append("user", "第一条")
    store.append("user", "第二条")
    store.append("user", "第三条")
    assert store.read_all("user") == "第一条\n第二条\n第三条"


def test_append_to_memory_target(tmp_path):
    """追加到 memory 目标文件正常落盘。"""
    store = MemoryStore(tmp_path)
    store.append("memory", "项目经验")
    assert store.read_all("memory") == "项目经验"
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == "项目经验"


# ========== 替换 ==========


def test_replace_found_returns_true(tmp_path):
    """找到待替换文本时返回 True，内容被替换。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "旧版本内容")
    assert store.replace("memory", "旧版本", "新版本") is True
    assert store.read_all("memory") == "新版本内容"


def test_replace_all_occurrences(tmp_path):
    """replace 替换所有匹配处。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "aXbXc")
    assert store.replace("memory", "X", "Y") is True
    assert store.read_all("memory") == "aYbYc"


def test_replace_not_found_returns_false_unchanged(tmp_path):
    """未找到待替换文本时返回 False 且不修改文件。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "原始内容")
    assert store.replace("memory", "不存在", "新") is False
    # 内容未被修改
    assert store.read_all("memory") == "原始内容"


# ========== 删除 ==========


def test_remove_found_returns_true(tmp_path):
    """找到待删除文本时返回 True，内容被移除。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "保留\n待删除\n保留2")
    assert store.remove("memory", "待删除") is True
    remaining = store.read_all("memory")
    assert "待删除" not in remaining
    assert "保留" in remaining


def test_remove_not_found_returns_false_unchanged(tmp_path):
    """未找到待删除文本时返回 False 且不修改文件。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "原始内容")
    assert store.remove("memory", "不存在") is False
    assert store.read_all("memory") == "原始内容"


def test_remove_tidies_blank_lines(tmp_path):
    """删除文本后清理多余空行。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "段A\n\n段B")
    assert store.remove("memory", "段A") is True
    # 删除段A后，多余空行被清理，仅剩段B
    assert store.read_all("memory") == "段B"


# ========== 字符上限检查 ==========


def test_is_over_limit_over(tmp_path):
    """内容长度超过上限时返回 True。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    store.write_all("user", "x" * 21)
    assert store.is_over_limit("user") is True


def test_is_over_limit_not_over(tmp_path):
    """内容长度等于上限（未超过）时返回 False。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    store.write_all("user", "x" * 20)
    assert store.is_over_limit("user") is False


def test_is_over_limit_empty(tmp_path):
    """空内容不超过上限。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=20)
    assert store.is_over_limit("user") is False
    assert store.is_over_limit("memory") is False


def test_is_over_limit_memory_uses_memory_limit(tmp_path):
    """memory 目标使用 memory_max_chars 上限。"""
    store = MemoryStore(tmp_path, user_max_chars=20, memory_max_chars=100)
    # 30 字符超过 user 上限但未超过 memory 上限
    content = "x" * 30
    store.write_all("memory", content)
    assert store.is_over_limit("memory") is False
    # 改到 user 则超限
    store.write_all("user", content)
    assert store.is_over_limit("user") is True


def test_max_chars_returns_limits(tmp_path):
    """max_chars 返回对应文件的字符上限。"""
    store = MemoryStore(tmp_path, user_max_chars=15, memory_max_chars=25)
    assert store.max_chars("user") == 15
    assert store.max_chars("memory") == 25


# ========== 原子落盘与整文件覆写 ==========


def test_atomic_write_no_tmp_residual(tmp_path):
    """写入后文件存在且无残留 .tmp 临时文件。"""
    store = MemoryStore(tmp_path)
    store.append("user", "内容")
    store.append("memory", "记忆")
    assert (tmp_path / "USER.md").exists()
    assert (tmp_path / "MEMORY.md").exists()
    # 不应有残留临时文件
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_all_overwrites_entire_file(tmp_path):
    """write_all 整文件覆写，不追加。"""
    store = MemoryStore(tmp_path)
    store.append("user", "旧内容")
    store.write_all("user", "全新内容")
    assert store.read_all("user") == "全新内容"
    assert (tmp_path / "USER.md").read_text(encoding="utf-8") == "全新内容"


def test_write_all_then_no_tmp_residual(tmp_path):
    """write_all 同样采用原子落盘，无残留临时文件。"""
    store = MemoryStore(tmp_path)
    store.write_all("memory", "整文件覆写内容")
    assert list(tmp_path.glob("*.tmp")) == []


# ========== 持久化 ==========


def test_persistence_across_instances(tmp_path):
    """重新构造 MemoryStore 能读取到已落盘内容。"""
    store = MemoryStore(tmp_path, user_max_chars=100, memory_max_chars=100)
    store.append("user", "持久化的用户偏好")
    store.append("memory", "持久化的项目经验")
    # 重新构造，应从磁盘读取
    store2 = MemoryStore(tmp_path, user_max_chars=100, memory_max_chars=100)
    assert store2.read_all("user") == "持久化的用户偏好"
    assert store2.read_all("memory") == "持久化的项目经验"


def test_persistence_after_replace_and_remove(tmp_path):
    """替换与删除后落盘内容可被新实例读取。"""
    store = MemoryStore(tmp_path, user_max_chars=100, memory_max_chars=100)
    # 选用完全不互为子串的字符串，避免 remove 移除所有匹配处时误伤
    store.write_all("memory", "苹果\n香蕉")
    store.replace("memory", "苹果", "葡萄")
    store.remove("memory", "香蕉")
    store2 = MemoryStore(tmp_path, user_max_chars=100, memory_max_chars=100)
    assert store2.read_all("memory") == "葡萄"


# ========== 非法目标 ==========


def test_unknown_target_raises_value_error(tmp_path):
    """未知目标标识在读取时抛出 ValueError。"""
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.read_all("unknown")


def test_unknown_target_append_raises(tmp_path):
    """未知目标标识在追加时抛出 ValueError。"""
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.append("unknown", "x")


def test_unknown_target_max_chars_raises(tmp_path):
    """未知目标标识在 max_chars 时抛出 ValueError。"""
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.max_chars("unknown")
