"""记忆管理器。

协调 MemoryStore 与 LLM 客户端，提供会话开始前的系统提示词前缀注入与
写入超限时的反思整理能力。

职责：
- build_memory_prefix：读取 store 中 user/memory 全文，拼装为记忆前缀文本，
  整个会话由调用方冻结复用（本方法不缓存，每次调用都重新读 store）。引擎在 run
  时将该前缀与自身默认系统提示词叠加，而非替换。
- add/replace/remove：委托 store 做对应写入，写入后若超限则触发反思整理。
- _reflect：读取全文，调用 LLM 做去重/合并/精简，整理后原子写回文件；
  LLM 调用失败或输出非法时记录日志并保留写入前内容，不抛异常、不阻断主流程。
"""

from __future__ import annotations

from typing import Any

from ..observability import get_logger
from .prompts import REFLECT_SYSTEM_PROMPT, build_injection_prefix
from .store import MemoryStore

logger = get_logger(__name__)


class MemoryManager:
    """记忆管理器，桥接存储与 LLM 反思整理。

    构造接收一个 MemoryStore 实例与一个 LLM 客户端。LLM 客户端只需具备
    chat(messages, tools=None) 方法并返回带 content 属性的响应对象，
    与 context.strategies.LLMSummaryCompression 的调用约定一致。
    """

    def __init__(self, store: MemoryStore, llm: Any) -> None:
        """初始化记忆管理器。

        参数:
            store: 记忆文件存储实例。
            llm: LLM 客户端，需有 chat(messages, tools=None) 方法，返回对象含 content 属性。
        """
        self._store = store
        self._llm = llm

    def build_memory_prefix(self) -> str:
        """构造记忆注入前缀。

        读取 store 中 user/memory 全文，拼装为记忆前缀文本。会话开始时调用一次，
        结果由调用方冻结复用；本方法本身不缓存，每次调用都重新读 store。
        引擎在 run 时将该前缀与自身默认系统提示词叠加，而非替换。

        返回:
            拼装好的记忆前缀文本；若两文件均为空则返回空字符串。
        """
        user_content = self._store.read_all("user")
        memory_content = self._store.read_all("memory")
        return build_injection_prefix(user_content, memory_content)

    def add(self, target: str, content: str) -> str:
        """追加内容到目标记忆文件。

        委托 store.append 完成写入；写入后若超限则触发反思整理。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            content: 要追加的文本内容。

        返回:
            返回给工具的结果字符串。
        """
        self._store.append(target, content)
        result = f"已添加到{target}记忆（{len(content)} 字符）"
        self._maybe_reflect(target)
        return result

    def replace(self, target: str, old: str, new: str) -> str:
        """在目标记忆文件中查找 old 替换为 new。

        委托 store.replace；未找到返回提示字符串（不报错）；替换后若超限触发反思整理。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            old: 待替换文本。
            new: 新文本。

        返回:
            返回给工具的结果字符串。
        """
        found = self._store.replace(target, old, new)
        if not found:
            return f"未在{target}记忆中找到待替换文本"
        result = f"已替换{target}记忆中的内容"
        self._maybe_reflect(target)
        return result

    def remove(self, target: str, content: str) -> str:
        """从目标记忆文件中移除指定文本。

        委托 store.remove；未找到返回提示字符串（不报错）；删除后若超限触发反思整理。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
            content: 待删除文本。

        返回:
            返回给工具的结果字符串。
        """
        found = self._store.remove(target, content)
        if not found:
            return f"未在{target}记忆中找到待删除文本"
        result = f"已从{target}记忆删除内容（{len(content)} 字符）"
        self._maybe_reflect(target)
        return result

    def _maybe_reflect(self, target: str) -> None:
        """写入后检查是否超限，超限则触发反思整理。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
        """
        if self._store.is_over_limit(target):
            self._reflect(target)

    def _reflect(self, target: str) -> None:
        """对目标记忆文件做反思整理。

        读取全文，构造反思整理提示词（system + user），调用 LLM 做去重/合并/精简，
        取响应内容作为整理后文本；校验非空后通过 store.write_all 原子写回。
        LLM 调用失败或输出非法时记录日志并保留写入前内容，不抛异常、不阻断主流程。

        参数:
            target: 目标文件标识，"user" 或 "memory"。
        """
        content = self._store.read_all(target)
        if not content:
            return
        max_chars = self._store.max_chars(target)
        system_prompt = REFLECT_SYSTEM_PROMPT.format(
            content=content, max_chars=max_chars
        )
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请输出整理后的记忆内容。"},
        ]
        logger.info(
            "发起记忆反思整理 LLM 请求",
            extra={
                "event": "memory_reflect_request",
                "target": target,
                "original_chars": len(content),
                "max_chars": max_chars,
            },
        )
        try:
            response = self._llm.chat(llm_messages, tools=None)
        except Exception:
            logger.exception(
                "记忆反思整理 LLM 调用失败",
                extra={"event": "memory_reflect_error", "target": target},
            )
            return
        refined = (response.content or "").strip()
        if not refined:
            logger.warning(
                "记忆反思整理输出为空，保留写入前内容",
                extra={"event": "memory_reflect_empty", "target": target},
            )
            return
        self._store.write_all(target, refined)
        logger.info(
            "记忆反思整理完成",
            extra={
                "event": "memory_reflect_done",
                "target": target,
                "refined_chars": len(refined),
            },
        )
