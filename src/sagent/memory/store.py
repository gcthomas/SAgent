"""长期记忆 Markdown 文件存储。

基于两个本地 Markdown 文件（USER.md 与 MEMORY.md）的非结构化长期记忆持久化。
提供加载、读取全文、追加、替换、删除与字符上限检查能力，所有写操作采用
临时文件 + os.replace 原子落盘，避免中途异常导致文件损坏。

该模块不依赖 LLM，也不依赖 config 模块，构造时传入文件目录与字符上限，
避免循环依赖。反思整理等 LLM 相关逻辑由上层记忆管理器负责，不在本模块实现。
文件名固定为 USER.md 与 MEMORY.md，不可配置。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..observability.logging_setup import get_logger

logger = get_logger(__name__)

# 固定文件名常量
_USER_FILE = "USER.md"
_MEMORY_FILE = "MEMORY.md"

# 目标文件标识常量
_TARGET_USER = "user"
_TARGET_MEMORY = "memory"


class MemoryStore:
    """Markdown 文件记忆存储。

    维护两个本地 Markdown 文件的内容缓存，支持加载、读取、追加、替换、
    删除与字符上限检查。所有写操作先更新内存缓存，再原子落盘。
    """

    def __init__(
        self,
        directory: Path | str,
        user_max_chars: int = 2000,
        memory_max_chars: int = 4000,
    ) -> None:
        """初始化记忆存储并加载文件内容。

        参数:
            directory: 记忆文件所在目录。
            user_max_chars: 用户文件字符上限。
            memory_max_chars: 记忆文件字符上限。
        """
        self._directory = Path(directory)
        self._user_max_chars = user_max_chars
        self._memory_max_chars = memory_max_chars
        # 内容缓存：文件名 -> 全文
        self._contents: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """加载两个文件全文到内存缓存。

        文件不存在则初始化为空字符串，不报错。
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        self._contents[_USER_FILE] = self._read_file(_USER_FILE)
        self._contents[_MEMORY_FILE] = self._read_file(_MEMORY_FILE)

    def read_all(self, target: str) -> str:
        """返回目标文件全文。

        参数:
            target: 目标文件标识，"user" 或 "memory"。

        返回:
            对应文件的全文内容。
        """
        file_name = self._target_to_file(target)
        return self._contents.get(file_name, "")

    def append(self, target: str, content: str) -> None:
        """追加内容到目标文件并落盘。

        以换行分隔追加到现有内容末尾；若文件为空则直接作为正文。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            content: 要追加的文本内容。
        """
        file_name = self._target_to_file(target)
        current = self._contents.get(file_name, "")
        new_content = current + "\n" + content if current else content
        self._contents[file_name] = new_content
        self._persist(file_name, new_content)
        logger.info(
            "记忆追加写入",
            extra={
                "event": "memory_append",
                "target": target,
                "chars": len(content),
            },
        )

    def replace(self, target: str, old: str, new: str) -> bool:
        """在目标文件全文中查找 old 替换为 new。

        替换所有匹配处。未找到则返回 False，不修改文件。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            old: 待替换文本。
            new: 新文本。

        返回:
            是否找到并替换成功。
        """
        file_name = self._target_to_file(target)
        current = self._contents.get(file_name, "")
        if old not in current:
            return False
        new_content = current.replace(old, new)
        self._contents[file_name] = new_content
        self._persist(file_name, new_content)
        logger.info(
            "记忆替换",
            extra={
                "event": "memory_replace",
                "target": target,
                "old_chars": len(old),
                "new_chars": len(new),
            },
        )
        return True

    def remove(self, target: str, content: str) -> bool:
        """在目标文件全文中查找并移除指定文本。

        移除所有匹配处，并清理因删除留下的多余空行。未找到则返回 False，不修改文件。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            content: 待删除文本。

        返回:
            是否找到并删除成功。
        """
        file_name = self._target_to_file(target)
        current = self._contents.get(file_name, "")
        if content not in current:
            return False
        new_content = self._tidy(current.replace(content, ""))
        self._contents[file_name] = new_content
        self._persist(file_name, new_content)
        logger.info(
            "记忆删除",
            extra={
                "event": "memory_remove",
                "target": target,
                "chars": len(content),
            },
        )
        return True

    def is_over_limit(self, target: str) -> bool:
        """返回目标文件内容长度是否超过上限。

        参数:
            target: 目标文件标识，"user" 或 "memory"。

        返回:
            超过上限返回 True，否则 False。
        """
        file_name = self._target_to_file(target)
        current = self._contents.get(file_name, "")
        limit = self._user_max_chars if target == _TARGET_USER else self._memory_max_chars
        return len(current) > limit

    def max_chars(self, target: str) -> int:
        """返回目标文件的字符上限。

        参数:
            target: 目标文件标识，"user" 或 "memory"。

        返回:
            对应文件的字符上限。
        """
        if target == _TARGET_USER:
            return self._user_max_chars
        if target == _TARGET_MEMORY:
            return self._memory_max_chars
        raise ValueError(f"未知的目标文件标识: {target}")

    def write_all(self, target: str, content: str) -> None:
        """整文件覆写目标记忆文件内容并原子落盘。

        用于反思整理后将整理后的全文写回文件。直接替换内存缓存与磁盘文件，
        不做追加或查找。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            content: 要覆写的全文内容。
        """
        file_name = self._target_to_file(target)
        self._contents[file_name] = content
        self._persist(file_name, content)
        logger.info(
            "记忆整文件覆写",
            extra={
                "event": "memory_write_all",
                "target": target,
                "chars": len(content),
            },
        )

    def _target_to_file(self, target: str) -> str:
        """将目标标识转换为文件名。

        参数:
            target: 目标文件标识，"user" 或 "memory"。

        返回:
            对应的文件名。
        """
        if target == _TARGET_USER:
            return _USER_FILE
        if target == _TARGET_MEMORY:
            return _MEMORY_FILE
        raise ValueError(f"未知的目标文件标识: {target}")

    def _read_file(self, file_name: str) -> str:
        """读取单个记忆文件全文，文件不存在返回空字符串。

        参数:
            file_name: 文件名。

        返回:
            文件全文内容。
        """
        path = self._directory / file_name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _persist(self, file_name: str, content: str) -> None:
        """将内容原子写入目标文件。

        先写同目录下的临时文件（追加 .tmp 后缀），再通过 os.replace 原子替换为目标文件。

        参数:
            file_name: 文件名。
            content: 要写入的文本内容。
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / file_name
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)

    @staticmethod
    def _tidy(text: str) -> str:
        """清理因删除文本留下的多余连续空行。

        折叠三个及以上连续空行为两个，并去掉首尾空白行，保持 Markdown 整洁。

        参数:
            text: 原始文本。

        返回:
            清理后的文本。
        """
        cleaned: list[str] = []
        blank_run = 0
        for line in text.splitlines():
            if line.strip() == "":
                blank_run += 1
                if blank_run <= 1:
                    cleaned.append("")
            else:
                blank_run = 0
                cleaned.append(line)
        return "\n".join(cleaned).strip()
