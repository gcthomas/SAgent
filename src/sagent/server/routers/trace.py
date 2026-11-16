"""会话轨迹查询路由。

按真实用户消息切分交互轮次（跳过计划步骤指令），聚合助手文本、工具调用结果与压缩事件，
并补充持久化的轮次用量统计与系统提示词来源。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from ...core.plan_engine import PLAN_STEP_TASK_PREFIX
from ...core.prompts import REACT_SYSTEM_PROMPT
from ...memory import MemoryManager
from ...observability import get_logger
from ...session.models import SessionMessage, TurnRecord
from ...session.store import SessionStore
from ...skills import SkillManager, build_skill_catalog_prefix
from ..schemas import (
    TraceCompaction,
    TraceResponse,
    TraceToolCall,
    TraceTurn,
    TraceUserMessage,
    UsageInfo,
)
from .sessions import query_rows, require_store, to_session_meta

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["trace"])

# 消息原始列查询：SessionStore.load_messages 未暴露 created_at
_TIMESTAMP_SQL = """
SELECT seq, created_at
FROM messages
WHERE session_id = ?
ORDER BY seq ASC
"""

# 压缩事件查询：按覆盖区间结束序号升序排列
_COMPACTION_SQL = """
SELECT type, summary, covered_from_seq, covered_to_seq, created_at
FROM session_events
WHERE session_id = ?
ORDER BY covered_to_seq ASC, id ASC
"""


def _arguments_text(arguments: Any) -> str:
    """将工具参数统一为字符串；非字符串结构按 JSON 序列化。

    参数:
        arguments: 持久化的工具参数。

    返回:
        可直接展示的参数字符串。
    """
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return ""
    return json.dumps(arguments, ensure_ascii=False)


def _extract_tool_call(call: dict[str, Any]) -> tuple[str | None, str, str]:
    """提取工具调用的 id、名称与参数字符串。

    历史中的 tool_calls 采用 OpenAI 嵌套结构（function.name / function.arguments），
    扁平结构（name / arguments）按同一契约输出。

    参数:
        call: 单条工具调用字典。

    返回:
        (工具调用 id, 工具名称, 参数字符串)。
    """
    function = call.get("function")
    if isinstance(function, dict):
        return (
            call.get("id"),
            str(function.get("name") or ""),
            _arguments_text(function.get("arguments")),
        )
    return (
        call.get("id"),
        str(call.get("name") or ""),
        _arguments_text(call.get("arguments")),
    )


def _build_tool_calls(
    raw_calls: list[dict[str, Any]], results: dict[str, str]
) -> list[TraceToolCall]:
    """构建轮次内的工具调用明细，并按 tool_call_id 关联执行结果。

    参数:
        raw_calls: assistant 消息中持久化的工具调用列表。
        results: tool_call_id 到工具结果文本的映射。

    返回:
        工具调用明细列表，无法关联的调用结果保持 None。
    """
    entries: list[TraceToolCall] = []
    for call in raw_calls:
        call_id, name, arguments = _extract_tool_call(call)
        result = results.get(call_id) if call_id is not None else None
        entries.append(
            TraceToolCall(
                tool_call_id=call_id,
                name=name,
                arguments=arguments,
                result=result,
            )
        )
    return entries


def _split_turns(messages: list[SessionMessage]) -> list[dict[str, Any]]:
    """按真实用户消息切分轮次并归集助手文本、工具调用与结果。

    计划步骤指令以 user 消息形式持久化，属于当前轮次的内部执行上下文，
    不作为新的交互轮次起点。

    参数:
        messages: 按 seq 升序排列的完整历史消息。

    返回:
        轮次中间结构列表，每项包含 user、assistant_texts、tool_calls、
        tool_results 与 compactions。
    """
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for message in messages:
        if message.role == "user":
            if current is not None and (message.content or "").startswith(PLAN_STEP_TASK_PREFIX):
                continue
            current = {
                "user": message,
                "assistant_texts": [],
                "tool_calls": [],
                "tool_results": {},
                "compactions": [],
            }
            turns.append(current)
            continue
        if current is None:
            # 第一条 user 之前的消息（如 system 提示词）不属于任何轮次
            continue
        if message.role == "assistant":
            if message.content:
                current["assistant_texts"].append(message.content)
            if message.tool_calls:
                current["tool_calls"].extend(message.tool_calls)
        elif message.role == "tool" and message.tool_call_id is not None:
            current["tool_results"][message.tool_call_id] = message.content or ""
    return turns


def _turn_index_for_seq(turns: list[dict[str, Any]], seq: int) -> int:
    """定位压缩事件覆盖区间所属的轮次下标。

    参数:
        turns: 轮次中间结构列表。
        seq: 压缩事件覆盖的结束序号。

    返回:
        轮次下标；无法归属时返回最后一轮。
    """
    for index, turn in enumerate(turns):
        start = turn["user"].seq
        if index + 1 < len(turns):
            end: int | None = turns[index + 1]["user"].seq - 1
        else:
            end = None
        if seq >= start and (end is None or seq <= end):
            return index
    return len(turns) - 1


def _build_default_system_prompt(
    memory: MemoryManager | None, skill_manager: SkillManager | None
) -> str:
    """按引擎的叠加顺序构建默认系统提示词。

    复用既有前缀构建函数：Skill 目录前缀在前，记忆前缀叠加到最前，
    REACT_SYSTEM_PROMPT 位于最后，与 ReAct / Plan 引擎保持一致。

    参数:
        memory: 长期记忆管理器，未启用时为 None。
        skill_manager: Skill 管理器，未启用时为 None。

    返回:
        完整的默认系统提示词。
    """
    prompt = REACT_SYSTEM_PROMPT
    skill_prefix = build_skill_catalog_prefix(skill_manager)
    if skill_prefix:
        prompt = skill_prefix + "\n\n" + prompt
    if memory is not None:
        memory_prefix = memory.build_memory_prefix()
        if memory_prefix:
            prompt = memory_prefix + "\n\n" + prompt
    return prompt


def _usage_from_record(record: TurnRecord) -> UsageInfo:
    """将轮次记录的 token 用量转换为响应模型。

    参数:
        record: 轮次统计记录。

    返回:
        该轮次的 token 用量。
    """
    return UsageInfo(
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
        cached_tokens=record.cached_tokens,
    )


def _build_turns(
    store: SessionStore,
    session_id: str,
    turns: list[dict[str, Any]],
    timestamps: dict[int, str],
    records: list[TurnRecord],
) -> list[TraceTurn]:
    """构建轨迹轮次响应列表。

    参数:
        store: 会话持久化存储。
        session_id: 会话唯一标识。
        turns: 轮次中间结构列表。
        timestamps: 消息 seq 到创建时间的映射。
        records: 轮次统计记录列表。

    返回:
        轨迹轮次列表。
    """
    by_index = {record.turn_index: record for record in records}
    events = query_rows(store, _COMPACTION_SQL, (session_id,))
    for event in events:
        if not turns:
            break
        target = _turn_index_for_seq(turns, event["covered_to_seq"])
        turns[target]["compactions"].append(TraceCompaction(**event))

    result: list[TraceTurn] = []
    for index, turn in enumerate(turns, start=1):
        record = by_index.get(index)
        user: SessionMessage = turn["user"]
        result.append(
            TraceTurn(
                turn_index=index,
                started_at=record.started_at if record is not None else None,
                finished_at=record.finished_at if record is not None else None,
                duration_ms=record.duration_ms if record is not None else None,
                mode=record.mode if record is not None else None,
                outcome=record.outcome if record is not None else None,
                usage=_usage_from_record(record) if record is not None else None,
                user=TraceUserMessage(
                    seq=user.seq,
                    content=user.content or "",
                    created_at=timestamps[user.seq],
                ),
                assistant_text="\n\n".join(turn["assistant_texts"]),
                tool_calls=_build_tool_calls(turn["tool_calls"], turn["tool_results"]),
                compactions=turn["compactions"],
            )
        )
    return result


@router.get("/{session_id}/trace", response_model=TraceResponse)
def get_trace(session_id: str, request: Request) -> TraceResponse:
    """返回会话轨迹，包含系统提示词、轮次明细与会话累计用量。

    参数:
        session_id: 会话唯一标识。
        request: 当前请求。

    返回:
        会话轨迹响应。
    """
    store = require_store(request)
    meta = store.get_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    messages = store.load_messages(session_id)
    timestamps = {
        row["seq"]: row["created_at"]
        for row in query_rows(store, _TIMESTAMP_SQL, (session_id,))
    }

    stored_prompt: str | None = None
    for message in messages:
        if message.role == "system":
            stored_prompt = message.content
            break
    system_prompt: str
    system_prompt_source: Literal["stored", "default"]
    if stored_prompt is not None:
        system_prompt = stored_prompt
        system_prompt_source = "stored"
    else:
        system_prompt = _build_default_system_prompt(
            request.app.state.memory, request.app.state.skill_manager
        )
        system_prompt_source = "default"

    turns = _split_turns(messages)
    records = store.list_turns(session_id)
    trace_turns = _build_turns(store, session_id, turns, timestamps, records)

    if records:
        usage = UsageInfo(
            prompt_tokens=sum(record.prompt_tokens for record in records),
            completion_tokens=sum(record.completion_tokens for record in records),
            total_tokens=sum(record.total_tokens for record in records),
            cached_tokens=sum(record.cached_tokens for record in records),
        )
    else:
        usage = None

    return TraceResponse(
        session=to_session_meta(meta),
        system_prompt=system_prompt,
        system_prompt_source=system_prompt_source,
        total_turns=len(trace_turns),
        usage=usage,
        turns=trace_turns,
    )
