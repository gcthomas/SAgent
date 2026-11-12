"""Web API 请求与响应模型。

定义会话管理、历史消息与轨迹查询接口的请求/响应结构。字段名与前端契约
保持一致，不使用别名，避免前后端字段对不上。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionMetaResponse(BaseModel):
    """会话元数据响应。"""

    id: str = Field(..., description="会话唯一标识")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间（ISO 格式字符串）")
    updated_at: str = Field(..., description="更新时间（ISO 格式字符串）")
    mode: str = Field(..., description="执行模式（react/plan）")
    message_count: int = Field(..., description="消息条数")


class SessionListResponse(BaseModel):
    """会话列表响应。"""

    sessions: list[SessionMetaResponse] = Field(..., description="当前页会话列表")
    total: int = Field(..., description="会话总数（不受分页影响）")


class SessionCreateRequest(BaseModel):
    """创建会话请求。"""

    title: str | None = Field(default=None, description="会话标题，为空时使用默认标题")


class SessionRenameRequest(BaseModel):
    """重命名会话请求。"""

    title: str = Field(..., description="新会话标题")


class ChatMessageRequest(BaseModel):
    """发送对话消息请求。"""

    message: str = Field(..., description="用户消息文本，去除首尾空白后不得为空")
    message_id: str | None = Field(default=None, description="可选的助手消息 id，缺省使用运行 id")


class RunCancelResponse(BaseModel):
    """取消运行响应。"""

    status: Literal["cancelling"] = Field(..., description="取消受理状态，固定为 cancelling")


class ApprovalDecisionRequest(BaseModel):
    """审批决策请求。"""

    action: str = Field(
        default="",
        description="决策动作：approve / deny / always，缺省视为非法动作",
    )


class ApprovalDecisionResponse(BaseModel):
    """审批决策响应。"""

    approval_id: str = Field(..., description="审批 id")
    status: Literal["approved", "denied"] = Field(..., description="决策结果")


class SessionSearchItem(SessionMetaResponse):
    """搜索命中的会话条目。"""

    match_source: Literal["title", "content", "both"] = Field(..., description="命中来源")
    snippet: str | None = Field(default=None, description="命中片段，无片段时为 None")


class SessionSearchResponse(BaseModel):
    """会话搜索响应。"""

    sessions: list[SessionSearchItem] = Field(..., description="命中的会话列表")


class MessageItem(BaseModel):
    """会话历史消息条目。"""

    id: int = Field(..., description="消息主键")
    seq: int = Field(..., description="会话内单调递增序号")
    role: Literal["system", "user", "assistant", "tool"] = Field(..., description="消息角色")
    content: str = Field(..., description="消息文本内容")
    tool_calls: Any | None = Field(default=None, description="工具调用列表")
    tool_call_id: str | None = Field(default=None, description="工具调用 id（tool 消息携带）")
    created_at: str = Field(..., description="写入时间（ISO 格式字符串）")


class MessageListResponse(BaseModel):
    """会话历史消息响应。"""

    messages: list[MessageItem] = Field(..., description="按 seq 升序排列的消息列表")


class UsageInfo(BaseModel):
    """token 用量。"""

    prompt_tokens: int = Field(..., description="prompt token 用量")
    completion_tokens: int = Field(..., description="completion token 用量")
    total_tokens: int = Field(..., description="总 token 用量")


class TraceUserMessage(BaseModel):
    """轨迹轮次中的用户输入。"""

    seq: int = Field(..., description="用户消息序号")
    content: str = Field(..., description="用户输入文本")
    created_at: str = Field(..., description="用户消息写入时间（ISO 格式字符串）")


class TraceToolCall(BaseModel):
    """轨迹轮次中的工具调用及其结果。"""

    tool_call_id: str | None = Field(..., description="工具调用 id")
    name: str = Field(..., description="工具名称")
    arguments: str = Field(..., description="工具参数字符串")
    result: str | None = Field(default=None, description="工具执行结果，未关联时为 None")


class TraceCompaction(BaseModel):
    """轨迹中的上下文压缩事件。"""

    type: str = Field(..., description="事件类型")
    summary: str = Field(..., description="压缩摘要")
    covered_from_seq: int = Field(..., description="被覆盖的起始 seq")
    covered_to_seq: int = Field(..., description="被覆盖的结束 seq")
    created_at: str = Field(..., description="事件创建时间（ISO 格式字符串）")


class TraceTurn(BaseModel):
    """轨迹中的单个交互轮次。"""

    turn_index: int = Field(..., description="轮次序号，从 1 开始")
    started_at: str | None = Field(default=None, description="本轮开始时间，未记录时为 None")
    finished_at: str | None = Field(default=None, description="本轮结束时间，未记录时为 None")
    duration_ms: int | None = Field(default=None, description="本轮耗时（毫秒），未记录时为 None")
    mode: Literal["react", "plan"] | None = Field(default=None, description="本轮执行模式")
    outcome: str | None = Field(default=None, description="本轮结果状态")
    usage: UsageInfo | None = Field(default=None, description="本轮 token 用量，未记录时为 None")
    user: TraceUserMessage = Field(..., description="本轮用户输入")
    assistant_text: str = Field(..., description="本轮全部助手文本拼接结果")
    tool_calls: list[TraceToolCall] = Field(..., description="本轮工具调用明细")
    compactions: list[TraceCompaction] = Field(..., description="属于本轮的压缩事件")


class TraceResponse(BaseModel):
    """会话轨迹响应。"""

    session: SessionMetaResponse = Field(..., description="会话元数据")
    system_prompt: str = Field(..., description="系统初始提示词全文")
    system_prompt_source: Literal["stored", "default"] = Field(..., description="系统提示词来源")
    total_turns: int = Field(..., description="交互轮数")
    usage: UsageInfo | None = Field(default=None, description="会话累计 token 用量，无轮次记录时为 None")
    turns: list[TraceTurn] = Field(..., description="按时间顺序排列的轮次列表")
