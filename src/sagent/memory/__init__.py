"""长期记忆系统包。

提供基于本地 Markdown 文件的长期记忆持久化能力，包含文件存储、记忆管理器
（注入构建/反思整理）与提示词定义。
"""

from __future__ import annotations

from .manager import MemoryManager
from .store import MemoryStore

__all__ = ["MemoryStore", "MemoryManager"]
