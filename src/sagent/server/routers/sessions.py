"""会话管理路由。

提供会话列表、创建、重命名、删除、搜索与历史消息读取接口。会话删除统一
经由运行时注册表编排（先取消进行中的运行再删库），不直接调用
SessionStore.delete_session。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from ...observability import get_logger
from ...session.models import SessionMeta
from ...session.store import SessionStore
from ..runtime import RuntimeRegistry, SessionNotFoundError
from ..schemas import (
    ContextInfo,
    MessageItem,
    MessageListResponse,
    SessionCreateRequest,
    SessionListResponse,
    SessionMetaResponse,
    SessionRenameRequest,
    SessionSearchItem,
    SessionSearchResponse,
    UsageInfo,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# 创建会话时的默认标题
_DEFAULT_TITLE = "新会话"


def query_rows(
    store: SessionStore, sql: str, params: tuple[Any, ...]
) -> list[dict[str, Any]]:
    """在会话存储连接上执行只读查询。

    转发到 ``SessionStore.query_rows`` 公开方法，保持与写入路径一致的
    线程安全约束，避免直接触碰存储内部属性。

    参数:
        store: 会话持久化存储。
        sql: 只读查询语句。
        params: 查询参数。

    返回:
        以列名为键的行字典列表。
    """
    return store.query_rows(sql, params)


def require_store(request: Request) -> SessionStore:
    """获取会话存储；会话功能未启用时返回 503。

    参数:
        request: 当前请求。

    返回:
        已启用的会话存储实例。
    """
    store = request.app.state.store
    if store is None:
        raise HTTPException(status_code=503, detail="会话功能未启用")
    return store


def to_session_meta(meta: SessionMeta) -> SessionMetaResponse:
    """将会话元数据转换为 API 响应模型。

    参数:
        meta: 持久化的会话元数据。

    返回:
        字段与前端契约一致的会话元数据。
    """
    return SessionMetaResponse(
        id=meta.id,
        title=meta.title,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        mode=meta.mode,
        message_count=meta.message_count,
    )


def _raise_not_found(session_id: str) -> None:
    """抛出会话不存在的 404。

    参数:
        session_id: 会话唯一标识。
    """
    raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")


@router.get("", response_model=SessionListResponse)
def list_sessions(
    request: Request,
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
) -> SessionListResponse:
    """按更新时间倒序返回会话列表。

    参数:
        request: 当前请求。
        limit: 单页最大条数。
        offset: 分页偏移量。

    返回:
        当前页会话列表与总数。
    """
    store = require_store(request)
    metas = store.list_sessions(limit=limit, offset=offset)
    total = store.count_sessions()
    return SessionListResponse(
        sessions=[to_session_meta(meta) for meta in metas],
        total=total,
    )


@router.post("", response_model=SessionMetaResponse, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreateRequest, request: Request) -> SessionMetaResponse:
    """创建会话。

    参数:
        payload: 创建会话请求，标题可为空。
        request: 当前请求。

    返回:
        新建会话的元数据。
    """
    store = require_store(request)
    title = (payload.title or "").strip() or _DEFAULT_TITLE
    now = datetime.now().isoformat()
    meta = SessionMeta(
        id=uuid.uuid4().hex[:8],
        title=title,
        created_at=now,
        updated_at=now,
        mode="react",
        message_count=0,
        persisted_seq=0,
    )
    store.create_session(meta)
    logger.info(
        "Web 创建会话",
        extra={"event": "web_session_created", "session_id": meta.id},
    )
    return to_session_meta(meta)


@router.get("/search", response_model=SessionSearchResponse)
def search_sessions(
    request: Request,
    q: str = "",
    limit: int = Query(20, ge=1),
) -> SessionSearchResponse:
    """按关键词搜索会话标题与消息内容。

    参数:
        request: 当前请求。
        q: 搜索关键词，为空时返回空列表。
        limit: 最大命中条数。

    返回:
        命中的会话列表。
    """
    store = require_store(request)
    keyword = q.strip()
    if not keyword:
        return SessionSearchResponse(sessions=[])
    hits = store.search_sessions(keyword, limit)
    return SessionSearchResponse(
        sessions=[
            SessionSearchItem(
                **to_session_meta(hit.session).model_dump(),
                match_source=hit.match_source,
                snippet=hit.snippet,
            )
            for hit in hits
        ]
    )


@router.patch("/{session_id}", response_model=SessionMetaResponse)
def rename_session(
    session_id: str, payload: SessionRenameRequest, request: Request
) -> SessionMetaResponse:
    """重命名会话。

    参数:
        session_id: 会话唯一标识。
        payload: 重命名请求，标题不能为空。
        request: 当前请求。

    返回:
        更新后的会话元数据。
    """
    store = require_store(request)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    if not store.rename_session(session_id, title, datetime.now().isoformat()):
        _raise_not_found(session_id)
    meta = store.get_session(session_id)
    if meta is None:
        _raise_not_found(session_id)
    return to_session_meta(meta)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, request: Request) -> Response:
    """删除会话。

    先由运行时注册表取消进行中的运行并释放会话级资源，再删除持久化记录。

    参数:
        session_id: 会话唯一标识。
        request: 当前请求。

    返回:
        无响应体的 204。
    """
    runtimes: RuntimeRegistry | None = request.app.state.runtimes
    if runtimes is None:
        raise HTTPException(status_code=503, detail="会话功能未启用")
    try:
        runtimes.delete_session(session_id)
    except SessionNotFoundError:
        _raise_not_found(session_id)
    logger.info(
        "Web 删除会话",
        extra={"event": "web_session_deleted", "session_id": session_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{session_id}/messages", response_model=MessageListResponse)
def list_messages(session_id: str, request: Request) -> MessageListResponse:
    """返回会话全部历史消息。

    参数:
        session_id: 会话唯一标识。
        request: 当前请求。

    返回:
        按 seq 升序排列的消息列表，以及会话累计 token 用量。
    """
    store = require_store(request)
    if store.get_session(session_id) is None:
        _raise_not_found(session_id)
    rows = query_rows(
        store,
        """
        SELECT id, seq, role, content, tool_calls, tool_call_id, created_at
        FROM messages
        WHERE session_id = ?
        ORDER BY seq ASC
        """,
        (session_id,),
    )
    messages = [
        MessageItem(
            id=row["id"],
            seq=row["seq"],
            role=row["role"],
            content=row["content"] or "",
            tool_calls=json.loads(row["tool_calls"]) if row["tool_calls"] else None,
            tool_call_id=row["tool_call_id"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    # 会话累计用量来自 turns 表，供前端在历史会话中展示用量栏
    totals = store.sum_turn_tokens(session_id)
    usage = (
        UsageInfo(
            prompt_tokens=totals[0],
            completion_tokens=totals[1],
            total_tokens=totals[2],
            cached_tokens=totals[3],
        )
        if totals is not None
        else None
    )
    # 上下文占用只在服务端已存在该会话运行时时可读，避免为只读接口创建运行时
    runtimes: RuntimeRegistry | None = request.app.state.runtimes
    runtime = runtimes.get(session_id) if runtimes is not None else None
    context = (
        ContextInfo(
            used_tokens=runtime.context_manager.token_count,
            max_tokens=runtime.context_manager.max_context_tokens,
        )
        if runtime is not None
        else None
    )
    return MessageListResponse(messages=messages, usage=usage, context=context)
