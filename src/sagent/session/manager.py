"""会话管理器。

协调 SessionStore 与 ContextManager，负责会话的创建、切换、删除，
以及上下文消息的增量保存与工作上下文还原。管理器持有当前会话 id 与
已持久化游标 persisted_seq，通过 export_new_messages 导出新增消息
并委托 SessionStore 增量写入，切换会话时通过 build_working_context
还原最近压缩摘要及其后消息。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ..context.context_manager import ContextManager
from ..observability import get_logger
from .models import CompactionEvent, SessionMeta, SessionMessage
from .store import SessionStore

logger = get_logger(__name__)


class SessionManager:
    """会话管理器，协调 SessionStore 与 ContextManager。"""

    def __init__(
        self,
        store: SessionStore,
        context_manager: ContextManager,
        mode: str = "react",
    ) -> None:
        """初始化会话管理器。

        参数:
            store: 会话持久化存储
            context_manager: 上下文管理器
            mode: 执行模式（react/plan）
        """
        self._store = store
        self._cm = context_manager
        self._mode = mode
        # 当前会话 id
        self._current_session_id: str | None = None
        # 当前会话已持久化的最大 seq
        self._persisted_seq: int = 0
        # 注册压缩摘要事件回调
        self._cm.set_compaction_callback(self._on_compaction)

    # --- 会话操作 ---

    def new_session(self, title: str = "") -> SessionMeta:
        """创建新会话并切换为当前会话。

        生成短 uuid 作为会话 id，创建元数据并写入存储，
        重置上下文管理器，返回会话元数据。

        参数:
            title: 会话标题，为空时使用默认标题"新会话"

        返回:
            新创建的会话元数据
        """
        session_id = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat()
        meta = SessionMeta(
            id=session_id,
            title=title or "新会话",
            created_at=now,
            updated_at=now,
            mode=self._mode,
            message_count=0,
            persisted_seq=0,
        )
        self._store.create_session(meta)
        self._current_session_id = meta.id
        self._persisted_seq = 0
        self._cm.reset()
        logger.info(
            "创建新会话",
            extra={"event": "session_new", "session_id": meta.id, "mode": self._mode},
        )
        return meta

    def switch_session(self, session_id: str) -> SessionMeta | None:
        """切换到指定会话，还原工作上下文。

        从存储加载会话元数据与工作上下文（最近压缩摘要 + 其后消息），
        装载到上下文管理器，并更新持久化游标。

        参数:
            session_id: 目标会话 id

        返回:
            会话元数据；会话不存在返回 None
        """
        session = self._store.get_session(session_id)
        if session is None:
            logger.warning("切换会话失败：会话不存在", extra={"event": "session_switch_not_found", "session_id": session_id})
            return None
        # 构建工作上下文（最近摘要 + 其后消息）
        messages = self._store.build_working_context(session_id)
        # 转换为 dict 格式后装载到上下文管理器
        dict_msgs = [self._session_message_to_dict(m) for m in messages]
        self._cm.load_messages(dict_msgs)
        self._current_session_id = session_id
        self._persisted_seq = session.persisted_seq
        logger.info(
            "切换会话",
            extra={
                "event": "session_switch",
                "session_id": session_id,
                "persisted_seq": self._persisted_seq,
                "loaded_count": len(dict_msgs),
            },
        )
        return session

    def rename_session(self, new_title: str) -> bool:
        """重命名当前会话。

        参数:
            new_title: 新标题

        返回:
            是否成功；无当前会话返回 False
        """
        if self._current_session_id is None:
            return False
        result = self._store.rename_session(
            self._current_session_id, new_title, datetime.now().isoformat()
        )
        return result

    def delete_session(self, session_id: str) -> bool:
        """删除指定会话。

        拒绝删除当前会话以避免持久化游标失效。

        参数:
            session_id: 待删除会话 id

        返回:
            成功返回 True；删除当前会话返回 False
        """
        if session_id == self._current_session_id:
            return False
        self._store.delete_session(session_id)
        return True

    def list_sessions(self) -> list[SessionMeta]:
        """列出全部会话（按 updated_at 降序）。

        返回:
            会话元数据列表
        """
        return self._store.list_sessions()

    def get_current_session(self) -> SessionMeta | None:
        """获取当前会话元数据。

        返回:
            当前会话元数据；无当前会话返回 None
        """
        if self._current_session_id is None:
            return None
        return self._store.get_session(self._current_session_id)

    # --- 保存与记录 ---

    def save_current(self) -> int:
        """增量保存当前会话的新增消息。

        从上下文管理器导出 seq 大于已持久化游标的新消息，
        转换为 SessionMessage 后委托存储追加写入，并推进游标。

        返回:
            新增消息条数；无当前会话或无新增返回 0
        """
        if self._current_session_id is None:
            return 0
        new_dicts = self._cm.export_new_messages(self._persisted_seq)
        if not new_dicts:
            return 0
        session_messages = [self._dict_to_session_message(d) for d in new_dicts]
        count = self._store.append_messages(
            self._current_session_id, session_messages, datetime.now().isoformat()
        )
        # 推进持久化游标为新增消息中的最大 seq（若有新增）
        if count > 0:
            self._persisted_seq = max(m.seq for m in session_messages)
        logger.info(
            "增量保存会话消息",
            extra={
                "event": "session_save",
                "session_id": self._current_session_id,
                "saved_count": count,
                "persisted_seq": self._persisted_seq,
            },
        )
        return count

    def _on_compaction(
        self, summary: str, covered_from_seq: int, covered_to_seq: int
    ) -> None:
        """压缩摘要事件回调，由 ContextManager 在第三层摘要成功后调用。

        构造压缩事件并写入存储。用 try/except 包裹避免回调异常影响压缩流程。

        参数:
            summary: 摘要文本
            covered_from_seq: 被摘要覆盖的起始 seq
            covered_to_seq: 被摘要覆盖的结束 seq
        """
        if self._current_session_id is None:
            return
        try:
            event = CompactionEvent(
                type="compaction",
                summary=summary,
                covered_from_seq=covered_from_seq,
                covered_to_seq=covered_to_seq,
                created_at=datetime.now().isoformat(),
            )
            self._store.append_compaction_event(self._current_session_id, event)
            logger.info(
                "记录压缩事件",
                extra={
                    "event": "compaction_recorded",
                    "session_id": self._current_session_id,
                    "covered_from_seq": covered_from_seq,
                    "covered_to_seq": covered_to_seq,
                },
            )
        except Exception:
            logger.exception(
                "记录压缩事件失败",
                extra={
                    "event": "compaction_record_error",
                    "session_id": self._current_session_id,
                },
            )

    def record_compaction(
        self, summary: str, covered_from_seq: int, covered_to_seq: int
    ) -> None:
        """公开方法，供外部主动调用记录压缩事件。

        参数:
            summary: 摘要文本
            covered_from_seq: 被摘要覆盖的起始 seq
            covered_to_seq: 被摘要覆盖的结束 seq
        """
        self._on_compaction(summary, covered_from_seq, covered_to_seq)

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """搜索会话消息内容。

        参数:
            keyword: 搜索关键词
            limit: 最多返回条数

        返回:
            匹配结果列表
        """
        return self._store.search(keyword, limit)

    # --- 启动会话确保 ---

    def ensure_current_session(self, session_id: str | None = None) -> SessionMeta:
        """确保存在可用的当前会话。

        若指定 session_id 且会话存在，切换到该会话；否则创建新会话。

        参数:
            session_id: 可选的会话 id

        返回:
            当前会话元数据
        """
        if session_id:
            meta = self.switch_session(session_id)
            if meta is not None:
                return meta
        return self.new_session()

    # --- 私有辅助 ---

    def _session_message_to_dict(self, m: SessionMessage) -> dict[str, Any]:
        """将 SessionMessage 转换为 dict 格式。

        保留 seq 字段，按需还原 tool_calls 与 tool_call_id。

        参数:
            m: SessionMessage 实例

        返回:
            OpenAI 消息格式的 dict
        """
        d: dict[str, Any] = {"role": m.role, "content": m.content, "seq": m.seq}
        if m.tool_calls is not None:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            d["tool_call_id"] = m.tool_call_id
        return d

    def _dict_to_session_message(self, d: dict[str, Any]) -> SessionMessage:
        """将 dict 转换为 SessionMessage。

        参数:
            d: OpenAI 消息格式的 dict

        返回:
            SessionMessage 实例
        """
        return SessionMessage(
            role=d.get("role", "user"),
            content=d.get("content", "") or "",
            tool_calls=d.get("tool_calls"),
            tool_call_id=d.get("tool_call_id"),
            seq=d.get("seq", 0),
        )
