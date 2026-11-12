// 后端 Web API 的 TypeScript 类型定义

// 会话元数据
export interface SessionMeta {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  mode: string;
  message_count: number;
}

// 会话列表响应
export interface SessionListResponse {
  sessions: SessionMeta[];
  total: number;
}

// 搜索命中来源
export type SessionMatchSource = 'title' | 'content' | 'both';

// 会话搜索结果条目
export interface SessionSearchHit extends SessionMeta {
  match_source: SessionMatchSource;
  snippet: string | null;
}

// 会话搜索响应
export interface SessionSearchResponse {
  sessions: SessionSearchHit[];
}

// 消息角色
export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';

// 单条会话消息
export interface SessionMessage {
  id: number;
  seq: number;
  role: MessageRole;
  content: string;
  tool_calls: unknown | null;
  tool_call_id: string | null;
  created_at: string;
}

// 会话消息列表响应
export interface SessionMessagesResponse {
  messages: SessionMessage[];
  // 会话累计 token 用量（全部轮次合计），无轮次记录时为 null
  usage: TraceUsage | null;
}

// 轨迹中的 token 用量
export interface TraceUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

// 轨迹轮次中的用户输入
export interface TraceUserMessage {
  seq: number;
  content: string;
  created_at: string;
}

// 轨迹中的工具调用明细
export interface TraceToolCall {
  tool_call_id: string | null;
  name: string;
  arguments: string;
  result: string | null;
}

// 轨迹中的上下文压缩事件
export interface TraceCompaction {
  type: string;
  summary: string;
  covered_from_seq: number;
  covered_to_seq: number;
  created_at: string;
}

// 轨迹中的单轮记录
export interface TraceTurn {
  turn_index: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  mode: 'react' | 'plan' | null;
  outcome: string | null;
  usage: TraceUsage | null;
  user: TraceUserMessage;
  assistant_text: string;
  tool_calls: TraceToolCall[];
  compactions: TraceCompaction[];
}

// 系统提示词来源：历史记录中的提示词或引擎默认提示词
export type TracePromptSource = 'stored' | 'default';

// 会话轨迹响应
export interface TraceResponse {
  session: SessionMeta;
  system_prompt: string;
  system_prompt_source: TracePromptSource;
  total_turns: number;
  usage: TraceUsage | null;
  turns: TraceTurn[];
}

// 运行取消响应
export interface RunCancelResponse {
  status: string;
}

// 审批动作
export type ApprovalAction = 'approve' | 'deny' | 'always';

// 审批决策后的状态
export type ApprovalDecisionStatus = 'approved' | 'denied';

// 审批决策响应
export interface ApprovalDecisionResponse {
  approval_id: string;
  status: ApprovalDecisionStatus;
}

// 后端统一错误响应
export interface ApiErrorPayload {
  code: string;
  message: string;
  detail: unknown;
}

// SSE 帧：event 为事件名（缺省为 null），data 为原始数据文本
export interface StreamFrame {
  event: string | null;
  data: string;
}
