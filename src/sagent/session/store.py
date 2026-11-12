"""会话持久化存储。

基于 SQLite 实现会话元数据、消息历史与压缩事件的持久化，
支持 FTS5 全文索引（不可用时降级为 LIKE 查询）。采用双层表结构：
messages 表为 append-only 完整历史，session_events 记录压缩事件，
工作上下文由最近摘要 + 其后原始消息拼接而成。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from ..observability import get_logger
from .models import CompactionEvent, SessionMeta, SessionMessage, SessionSearchHit, TurnRecord

logger = get_logger(__name__)

# turns 表的查询列顺序，供 append_turn / list_turns 复用
_TURN_COLUMNS = (
    "session_id, turn_index, started_at, finished_at, duration_ms, "
    "prompt_tokens, completion_tokens, total_tokens, outcome, mode"
)


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，使关键词按字面量参与匹配（配合 ESCAPE '\\' 使用）。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_fts_query(keyword: str) -> str:
    """将关键词转换为 FTS5 短语查询串。

    整体加双引号并转义内部双引号，避免用户输入被解释为 FTS5 查询语法。
    """
    return '"' + keyword.replace('"', '""') + '"'


class SessionStore:
    """会话持久化存储，基于 SQLite + FTS5。"""

    def __init__(self, db_path: str, enable_fts: bool = True) -> None:
        """初始化存储，建立连接并创建表结构。

        参数:
            db_path: SQLite 数据库文件路径
            enable_fts: 是否启用 FTS5 全文索引
        """
        # check_same_thread=False 允许 Web 服务跨线程复用同一连接，
        # 所有语句执行与事务统一由 _lock 串行化
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 可重入锁：允许嵌套调用（如 build_working_context 内部调用 load_messages）
        self._lock = threading.RLock()
        self._enable_fts = enable_fts
        self._init_pragmas()
        self._fts_available = self._check_fts5_available()
        self._init_schema()
        if enable_fts and self._fts_available:
            self._init_fts()

    def _init_pragmas(self) -> None:
        """初始化 SQLite PRAGMA 配置：开启 WAL 模式与外键约束。"""
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")

    def _check_fts5_available(self) -> bool:
        """检测当前 SQLite 是否支持 FTS5 全文索引。

        通过尝试创建一个临时 FTS5 虚表来检测可用性，
        失败时记录 WARNING 日志并返回 False。

        返回:
            支持返回 True，不支持返回 False
        """
        try:
            with self._lock:
                self._conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
                self._conn.execute("DROP TABLE _fts5_probe")
            return True
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 不可用，将降级为 LIKE 查询: %s", exc)
            return False

    def _init_schema(self) -> None:
        """创建数据库表结构（sessions / messages / session_events / turns）与唯一索引。

        所有建表语句使用 IF NOT EXISTS，可重复执行，旧库打开时自动补齐缺失表。
        """
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    persisted_seq INTEGER NOT NULL DEFAULT 0,
                    plan_state TEXT
                )
                """
            )
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
            if "plan_state" not in columns:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN plan_state TEXT")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    covered_from_seq INTEGER NOT NULL,
                    covered_to_seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            # 轮次统计表：只承载耗时与用量等派生数据，不参与消息事实源
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            # 唯一索引避免同一会话重复写入相同轮次
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq
                ON messages(session_id, seq)
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_session_turn_index
                ON turns(session_id, turn_index)
                """
            )
            self._conn.commit()

    def _init_fts(self) -> None:
        """创建 FTS5 全文索引虚表与同步触发器。

        使用外部内容表（content='messages'）而非无内容表（content=''），
        使 snippet() 能从 messages 表读取原文生成高亮摘要。
        触发器在 messages 表发生 INSERT/DELETE/UPDATE 时自动同步
        messages_fts 索引，保证索引与原始数据一致。
        """
        # 迁移：检测旧的无内容（contentless）FTS5 表并删除以便重建
        with self._lock:
            migrated = self._migrate_contentless_fts()

            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(content, content='messages', content_rowid='id', detail='full')
                """
            )
            # 插入时同步索引
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_fts_ai
                AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END
                """
            )
            # 删除时从索引移除
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_fts_ad
                AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
                END
                """
            )
            # 更新时先删后插（先移除旧索引项，再插入新索引项）
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_fts_au
                AFTER UPDATE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
                    INSERT INTO messages_fts(rowid, content)
                    VALUES (new.id, new.content);
                END
                """
            )
            # 迁移后或索引为空但 messages 表有数据时，从 messages 表重建索引
            if migrated:
                self._conn.execute(
                    "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
                )
            else:
                fts_empty = self._conn.execute(
                    "SELECT 1 FROM messages_fts LIMIT 1"
                ).fetchone() is None
                if fts_empty:
                    msg_exists = self._conn.execute(
                        "SELECT 1 FROM messages LIMIT 1"
                    ).fetchone() is not None
                    if msg_exists:
                        self._conn.execute(
                            "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
                        )
            self._conn.commit()

    def _migrate_contentless_fts(self) -> bool:
        """检测旧的无内容（contentless）FTS5 表，若存在则删除以便重建为外部内容表。

        无内容表（content=''）的 snippet() 返回 NULL，无法生成搜索摘要。
        返回是否执行了迁移。
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        if row and "content=''" in (row["sql"] or ""):
            with self._lock:
                self._conn.execute("DROP TRIGGER IF EXISTS messages_fts_ai")
                self._conn.execute("DROP TRIGGER IF EXISTS messages_fts_ad")
                self._conn.execute("DROP TRIGGER IF EXISTS messages_fts_au")
                self._conn.execute("DROP TABLE IF EXISTS messages_fts")
            return True
        return False

    # --- 会话 CRUD ---

    def create_session(self, session: SessionMeta) -> None:
        """插入一条会话元数据。

        参数:
            session: 会话元数据
        """
        # plan_state 为 dict | None，写入前需序列化为 JSON 字符串（与 save_plan_state 保持一致）
        serialized_plan_state = (
            json.dumps(session.plan_state, ensure_ascii=False) if session.plan_state is not None else None
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions
                    (id, title, created_at, updated_at, mode, message_count, persisted_seq, plan_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.created_at,
                    session.updated_at,
                    session.mode,
                    session.message_count,
                    session.persisted_seq,
                    serialized_plan_state,
                ),
            )

    def save_plan_state(self, session_id: str, plan_state: dict | None) -> None:
        """保存会话的活动计划快照。"""
        serialized = json.dumps(plan_state, ensure_ascii=False) if plan_state is not None else None
        with self._lock, self._conn:
            self._conn.execute("UPDATE sessions SET plan_state = ? WHERE id = ?", (serialized, session_id))

    def list_sessions(self, limit: int | None = None, offset: int = 0) -> list[SessionMeta]:
        """按更新时间与 id 降序返回会话，可指定数量与偏移。

        参数:
            limit: 最多返回条数，为 None 时返回全部会话
            offset: 指定 limit 时跳过的会话条数

        返回:
            会话元数据列表，最近更新的排在最前
        """
        query = """
            SELECT id, title, created_at, updated_at, mode, message_count, persisted_seq, plan_state
            FROM sessions
            ORDER BY updated_at DESC, id DESC
        """
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params: tuple[Any, ...] = (limit, offset)
        else:
            params = ()
        with self._lock:
            cur = self._conn.execute(query, params)
            rows = cur.fetchall()
            cur.close()
        return [
            SessionMeta(
                id=row["id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                mode=row["mode"],
                message_count=row["message_count"],
                persisted_seq=row["persisted_seq"],
                plan_state=json.loads(row["plan_state"]) if row["plan_state"] else None,
            )
            for row in rows
        ]

    def get_session(self, session_id: str) -> SessionMeta | None:
        """按 id 查询单个会话。

        参数:
            session_id: 会话唯一标识

        返回:
            会话元数据，不存在返回 None
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, title, created_at, updated_at, mode, message_count, persisted_seq, plan_state
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            )
            row = cur.fetchone()
            cur.close()
        if row is None:
            return None
        return SessionMeta(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            mode=row["mode"],
            message_count=row["message_count"],
            persisted_seq=row["persisted_seq"],
            plan_state=json.loads(row["plan_state"]) if row["plan_state"] else None,
        )

    def rename_session(self, session_id: str, new_title: str, updated_at: str) -> bool:
        """更新会话标题与更新时间。

        参数:
            session_id: 会话唯一标识
            new_title: 新标题
            updated_at: 更新时间（ISO 格式字符串）

        返回:
            是否成功（affected rows > 0）
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                UPDATE sessions SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_title, updated_at, session_id),
            )
            return cur.rowcount > 0

    def delete_session(self, session_id: str) -> None:
        """删除会话，CASCADE 自动删除关联的消息与压缩事件。

        参数:
            session_id: 会话唯一标识
        """
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )

    # --- 消息追加与加载 ---

    def append_messages(
        self, session_id: str, messages: list[SessionMessage], now: str
    ) -> int:
        """增量追加会话消息。

        仅写入 seq 超过已持久化游标（persisted_seq）的新增消息，
        使用 INSERT OR IGNORE 配合唯一索引确保重复写入安全。
        新增消息按 seq 升序排序后插入，并推进 persisted_seq 游标。

        参数:
            session_id: 会话唯一标识
            messages: 待追加的消息列表
            now: 当前时间（ISO 格式字符串），用于更新 updated_at

        返回:
            新增消息条数（无新增则返回 0）
        """
        if not messages:
            return 0
        with self._lock:
            # 读取当前已持久化的游标
            cur = self._conn.execute(
                "SELECT persisted_seq FROM sessions WHERE id = ?", (session_id,)
            )
            row = cur.fetchone()
            cur.close()
            if row is None:
                return 0
            persisted_seq = row["persisted_seq"]
            # 过滤出 seq > persisted_seq 的新增消息，按 seq 升序排序
            new_messages = sorted(
                [m for m in messages if m.seq > persisted_seq],
                key=lambda m: m.seq,
            )
            if not new_messages:
                return 0
            max_seq = new_messages[-1].seq
            count = len(new_messages)
            with self._conn:
                for m in new_messages:
                    # content 为 None 时存空字符串
                    content = m.content if m.content is not None else ""
                    # tool_calls 序列化为 JSON 文本，为 None 时存 NULL
                    tool_calls_json = (
                        json.dumps(m.tool_calls, ensure_ascii=False)
                        if m.tool_calls is not None
                        else None
                    )
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO messages
                            (session_id, seq, role, content, tool_calls, tool_call_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            m.seq,
                            m.role,
                            content,
                            tool_calls_json,
                            m.tool_call_id,
                            now,
                        ),
                    )
                # 推进 persisted_seq，更新 updated_at 与 message_count
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET persisted_seq = ?, updated_at = ?, message_count = message_count + ?
                    WHERE id = ?
                    """,
                    (max_seq, now, count, session_id),
                )
            return count

    def load_messages(self, session_id: str) -> list[SessionMessage]:
        """按 seq 升序加载会话全部消息。

        解析 tool_calls 的 JSON 文本（若非空），还原为 SessionMessage。

        参数:
            session_id: 会话唯一标识

        返回:
            SessionMessage 列表，按 seq 升序排列
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT role, content, tool_calls, tool_call_id, seq
                FROM messages
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
            cur.close()
        result: list[SessionMessage] = []
        for row in rows:
            tool_calls_text = row["tool_calls"]
            tool_calls = json.loads(tool_calls_text) if tool_calls_text else None
            result.append(
                SessionMessage(
                    role=row["role"],
                    content=row["content"],
                    tool_calls=tool_calls,
                    tool_call_id=row["tool_call_id"],
                    seq=row["seq"],
                )
            )
        return result

    # --- 压缩事件与工作上下文 ---

    def append_compaction_event(
        self, session_id: str, event: CompactionEvent
    ) -> None:
        """追加一条压缩事件，不修改 messages 表。

        参数:
            session_id: 会话唯一标识
            event: 压缩事件
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO session_events
                    (session_id, type, summary, covered_from_seq, covered_to_seq, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event.type,
                    event.summary,
                    event.covered_from_seq,
                    event.covered_to_seq,
                    event.created_at,
                ),
            )

    def build_working_context(self, session_id: str) -> list[SessionMessage]:
        """构建工作上下文：最近压缩事件摘要 + 其后原始消息。

        若存在压缩事件，返回「摘要消息（role=system,
        content=[历史摘要] {summary}, seq=covered_to_seq+1，
        工具字段为 None）+ 其后 seq > covered_to_seq 的原始消息」。
        若无压缩事件，返回全部消息（等价于 load_messages）。

        参数:
            session_id: 会话唯一标识

        返回:
            工作上下文消息列表
        """
        with self._lock:
            # 读取最近一条压缩事件（按 created_at、id 降序取首条）
            cur = self._conn.execute(
                """
                SELECT type, summary, covered_from_seq, covered_to_seq, created_at
                FROM session_events
                WHERE session_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (session_id,),
            )
            row = cur.fetchone()
            cur.close()
            if row is None:
                # 无压缩事件，返回全部消息
                return self.load_messages(session_id)
            covered_to_seq = row["covered_to_seq"]
            summary = row["summary"]
            # 摘要消息的 seq 取 covered_to_seq+1（虚拟位置），保持 seq 连续性
            summary_seq = covered_to_seq + 1
            summary_msg = SessionMessage(
                role="system",
                content=f"[历史摘要] {summary}",
                tool_calls=None,
                tool_call_id=None,
                seq=summary_seq,
            )
            # 加载 covered_to_seq 之后的原始消息
            cur = self._conn.execute(
                """
                SELECT role, content, tool_calls, tool_call_id, seq
                FROM messages
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                """,
                (session_id, covered_to_seq),
            )
            rows = cur.fetchall()
            cur.close()
        rest: list[SessionMessage] = []
        for r in rows:
            tool_calls_text = r["tool_calls"]
            tool_calls = json.loads(tool_calls_text) if tool_calls_text else None
            rest.append(
                SessionMessage(
                    role=r["role"],
                    content=r["content"],
                    tool_calls=tool_calls,
                    tool_call_id=r["tool_call_id"],
                    seq=r["seq"],
                )
            )
        return [summary_msg] + rest

    # --- 搜索 ---

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """搜索会话消息内容。

        FTS5 可用时使用全文检索（MATCH），否则降级为 LIKE 查询。
        keyword 参数：FTS5 用原样传入（支持 FTS5 查询语法），
        LIKE 用 %keyword% 模糊匹配。

        参数:
            keyword: 搜索关键词
            limit: 最多返回条数

        返回:
            list[dict]，每个 dict 含 session_id、seq、role、content、snippet
        """
        results: list[dict[str, Any]] = []
        with self._lock:
            if self._fts_available:
                cur = self._conn.execute(
                    """
                    SELECT m.session_id, m.seq, m.role, m.content,
                           snippet(messages_fts, 0, '<>', '</>', '...', 10) AS snippet
                    FROM messages_fts
                    JOIN messages m ON m.id = messages_fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (keyword, limit),
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT session_id, seq, role, content, content AS snippet
                    FROM messages
                    WHERE content LIKE ?
                    ORDER BY seq DESC
                    LIMIT ?
                    """,
                    (f"%{keyword}%", limit),
                )
            rows = cur.fetchall()
            cur.close()
        for row in rows:
            results.append(
                {
                    "session_id": row["session_id"],
                    "seq": row["seq"],
                    "role": row["role"],
                    "content": row["content"],
                    "snippet": row["snippet"],
                }
            )
        return results

    def search_sessions(self, keyword: str, limit: int = 20) -> list[SessionSearchHit]:
        """按关键词联合搜索会话标题与消息内容。

        标题命中使用 LIKE 字面量匹配；消息内容命中在 FTS5 可用时走全文索引，
        否则降级为 LIKE。命中结果按会话聚合去重，按 updated_at 降序返回。

        参数:
            keyword: 搜索关键词
            limit: 最多返回条数

        返回:
            SessionSearchHit 列表，含会话元数据、命中来源与摘要
        """
        if not keyword or limit <= 0:
            return []
        like_pattern = f"%{_escape_like(keyword)}%"
        # 会话 id -> 命中摘要
        snippets: dict[str, str] = {}
        title_hits: set[str] = set()
        content_hits: set[str] = set()
        rows_by_session: dict[str, sqlite3.Row] = {}

        with self._lock:
            # 标题命中（LIKE 字面量匹配，转义 % 与 _）
            cur = self._conn.execute(
                """
                SELECT id, title, created_at, updated_at, mode, message_count, persisted_seq, plan_state
                FROM sessions
                WHERE title LIKE ? ESCAPE '\\'
                """,
                (like_pattern,),
            )
            for row in cur.fetchall():
                title_hits.add(row["id"])
                rows_by_session[row["id"]] = row
                snippets[row["id"]] = row["title"]
            cur.close()

            # 消息内容命中：FTS5 可用时用全文索引，否则降级为 LIKE
            if self._enable_fts and self._fts_available:
                cur = self._conn.execute(
                    """
                    SELECT m.session_id AS session_id, m.content AS content,
                           snippet(messages_fts, 0, '', '', '...', 12) AS snippet
                    FROM messages_fts
                    JOIN messages m ON m.id = messages_fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    """,
                    (_build_fts_query(keyword),),
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT session_id, content, NULL AS snippet
                    FROM messages
                    WHERE content LIKE ? ESCAPE '\\'
                    ORDER BY seq ASC
                    """,
                    (like_pattern,),
                )
            for row in cur.fetchall():
                session_id = row["session_id"]
                content_hits.add(session_id)
                # 同一会话只保留首个命中片段；无片段时截取消息内容前 120 字符
                snippet = row["snippet"] or (row["content"] or "")[:120]
                snippets.setdefault(session_id, snippet)
            cur.close()

            # 补充仅由内容命中的会话元数据
            missing = [sid for sid in content_hits if sid not in rows_by_session]
            if missing:
                placeholders = ", ".join("?" for _ in missing)
                cur = self._conn.execute(
                    f"""
                    SELECT id, title, created_at, updated_at, mode, message_count, persisted_seq, plan_state
                    FROM sessions
                    WHERE id IN ({placeholders})
                    """,
                    tuple(missing),
                )
                for row in cur.fetchall():
                    rows_by_session[row["id"]] = row
                cur.close()

        hits: list[SessionSearchHit] = []
        for session_id in title_hits | content_hits:
            row = rows_by_session.get(session_id)
            if row is None:
                continue
            if session_id in title_hits and session_id in content_hits:
                match_source = "both"
            elif session_id in title_hits:
                match_source = "title"
            else:
                match_source = "content"
            hits.append(
                SessionSearchHit(
                    session=SessionMeta(
                        id=row["id"],
                        title=row["title"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        mode=row["mode"],
                        message_count=row["message_count"],
                        persisted_seq=row["persisted_seq"],
                        plan_state=json.loads(row["plan_state"]) if row["plan_state"] else None,
                    ),
                    match_source=match_source,
                    snippet=snippets.get(session_id),
                )
            )
        # 按 updated_at 降序，同一时间按 id 降序保证稳定
        hits.sort(key=lambda hit: (hit.session.updated_at, hit.session.id), reverse=True)
        return hits[:limit]

    # --- 轮次统计 ---

    def append_turn(self, turn: TurnRecord) -> None:
        """写入一条轮次统计记录。

        参数:
            turn: 轮次统计记录
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO turns
                    (session_id, turn_index, started_at, finished_at, duration_ms,
                     prompt_tokens, completion_tokens, total_tokens, outcome, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.session_id,
                    turn.turn_index,
                    turn.started_at,
                    turn.finished_at,
                    turn.duration_ms,
                    turn.prompt_tokens,
                    turn.completion_tokens,
                    turn.total_tokens,
                    turn.outcome,
                    turn.mode,
                ),
            )

    def list_turns(self, session_id: str) -> list[TurnRecord]:
        """按轮次序号升序返回会话的全部轮次统计。

        参数:
            session_id: 会话唯一标识

        返回:
            TurnRecord 列表，按 turn_index 升序排列
        """
        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT {_TURN_COLUMNS}
                FROM turns
                WHERE session_id = ?
                ORDER BY turn_index ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
            cur.close()
        return [
            TurnRecord(
                session_id=row["session_id"],
                turn_index=row["turn_index"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                duration_ms=row["duration_ms"],
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["total_tokens"],
                outcome=row["outcome"],
                mode=row["mode"],
            )
            for row in rows
        ]

    def count_turns(self, session_id: str) -> int:
        """返回会话已落盘的轮次统计数量。

        参数:
            session_id: 会话唯一标识

        返回:
            轮次记录数
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM turns WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            cur.close()
        return int(row["n"]) if row is not None else 0

    def count_sessions(self) -> int:
        """返回全部会话数量，供分页接口计算 total。

        返回:
            会话总数
        """
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM sessions")
            row = cur.fetchone()
            cur.close()
        return int(row["n"]) if row is not None else 0

    def sum_turn_tokens(self, session_id: str) -> tuple[int, int, int] | None:
        """返回会话全部轮次的 token 用量合计。

        参数:
            session_id: 会话唯一标识

        返回:
            (prompt_tokens, completion_tokens, total_tokens) 三元组；
            会话没有任何轮次记录时返回 None
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT COUNT(*) AS n,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM turns
                WHERE session_id = ?
                """,
                (session_id,),
            )
            row = cur.fetchone()
            cur.close()
        if row is None or int(row["n"]) == 0:
            return None
        return (
            int(row["prompt_tokens"]),
            int(row["completion_tokens"]),
            int(row["total_tokens"]),
        )

    def query_rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        """在内部连接上执行只读查询并返回行字典列表。

        供服务层读取未封装为实体方法的明细字段（如消息的 created_at、
        压缩事件），与写入路径共用同一把锁保证线程安全。

        参数:
            sql: 只读 SQL 语句。
            params: 查询参数。

        返回:
            以列名为键的行字典列表。
        """
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
        return [dict(row) for row in rows]
