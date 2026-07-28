"""会话数据模型定义。

使用 pydantic 定义会话管理所需的持久化数据结构，包含会话元数据、
会话消息与压缩事件。会话消息对应 OpenAI 消息结构并附加单调递增序号，
用于支持 SQLite 持久化与增量保存。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionMeta(BaseModel):
    """会话元数据。

    记录会话的基本属性，用于会话列表展示与持久化游标追踪。
    """

    # 会话唯一标识（短 uuid）
    id: str = Field(..., description="会话唯一标识（短 uuid）")
    # 会话标题
    title: str = Field(..., description="会话标题")
    # 创建时间（ISO 格式字符串）
    created_at: str = Field(..., description="创建时间（ISO 格式字符串）")
    # 更新时间（ISO 格式字符串）
    updated_at: str = Field(..., description="更新时间（ISO 格式字符串）")
    # 执行模式（react/plan）
    mode: str = Field(..., description="执行模式（react/plan）")
    # 消息条数
    message_count: int = Field(default=0, description="消息条数")
    # 已持久化的最大 seq 游标
    persisted_seq: int = Field(default=0, description="已持久化的最大 seq 游标")


class SessionMessage(BaseModel):
    """会话消息。

    对应 OpenAI 消息结构，并附加会话内单调递增序号 seq，
    用于增量持久化与压缩事件边界对齐。
    """

    # 消息角色（user/assistant/tool/system）
    role: str = Field(..., description="消息角色（user/assistant/tool/system）")
    # 消息文本内容
    content: str = Field(default="", description="消息文本内容")
    # 工具调用（assistant 消息可能携带）
    tool_calls: list[dict] | None = Field(default=None, description="工具调用（assistant 消息可能携带）")
    # 工具调用 id（tool 消息携带）
    tool_call_id: str | None = Field(default=None, description="工具调用 id（tool 消息携带）")
    # 会话内单调递增序号
    seq: int = Field(..., description="会话内单调递增序号")


class CompactionEvent(BaseModel):
    """压缩事件。

    记录上下文压缩时生成的摘要及被摘要覆盖的消息序号区间，
    用于在恢复历史时将摘要与剩余消息正确拼接。
    """

    # 事件类型（如 "compaction"）
    type: str = Field(..., description="事件类型（如 'compaction'）")
    # 摘要文本
    summary: str = Field(..., description="摘要文本")
    # 被摘要覆盖的起始 seq
    covered_from_seq: int = Field(..., description="被摘要覆盖的起始 seq")
    # 被摘要覆盖的结束 seq
    covered_to_seq: int = Field(..., description="被摘要覆盖的结束 seq")
    # 事件创建时间（ISO 格式字符串）
    created_at: str = Field(..., description="事件创建时间（ISO 格式字符串）")
