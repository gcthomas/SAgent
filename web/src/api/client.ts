import type {
  ApiErrorPayload,
  ApprovalAction,
  ApprovalDecisionResponse,
  RunCancelResponse,
  SessionListResponse,
  SessionMessagesResponse,
  SessionSearchResponse,
  SessionMeta,
  StreamFrame,
  TraceResponse,
} from './types';

// API 统一前缀，开发模式由 Vite proxy 转发到后端
const API_BASE = '/api';

// 携带后端错误码与详情的请求错误
export class ApiRequestError extends Error {
  readonly code: string;
  readonly detail: unknown;
  readonly status: number;

  constructor(message: string, code: string, detail: unknown, status: number) {
    super(message);
    this.name = 'ApiRequestError';
    this.code = code;
    this.detail = detail;
    this.status = status;
  }
}

// 将非 2xx 响应转换为带 code/message 的错误
async function toApiError(response: Response): Promise<ApiRequestError> {
  let payload: ApiErrorPayload | null = null;
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    payload = null;
  }
  const message =
    payload && typeof payload.message === 'string' && payload.message.length > 0
      ? payload.message
      : '请求失败';
  const code =
    payload && typeof payload.code === 'string' && payload.code.length > 0
      ? payload.code
      : 'error';
  return new ApiRequestError(message, code, payload?.detail ?? null, response.status);
}

// 统一请求封装：自动补充 JSON 请求头，204 返回 undefined
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// 查询会话列表
export async function listSessions(limit = 50, offset = 0): Promise<SessionListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<SessionListResponse>(`/sessions?${params.toString()}`);
}

// 新建会话，title 为 null 时由后端生成默认名称
export async function createSession(title: string | null): Promise<SessionMeta> {
  return request<SessionMeta>('/sessions', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

// 重命名会话
export async function renameSession(id: string, title: string): Promise<SessionMeta> {
  return request<SessionMeta>(`/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

// 删除会话
export async function deleteSession(id: string): Promise<void> {
  await request<void>(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// 搜索会话（标题与内容）
export async function searchSessions(q: string, limit = 20): Promise<SessionSearchResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request<SessionSearchResponse>(`/sessions/search?${params.toString()}`);
}

// 查询会话历史消息
export async function listMessages(id: string): Promise<SessionMessagesResponse> {
  return request<SessionMessagesResponse>(`/sessions/${encodeURIComponent(id)}/messages`);
}

// 查询会话完整轨迹
export async function getTrace(id: string): Promise<TraceResponse> {
  return request<TraceResponse>(`/sessions/${encodeURIComponent(id)}/trace`);
}

// 取消正在执行的一轮运行
export async function cancelRun(runId: string): Promise<RunCancelResponse> {
  return request<RunCancelResponse>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  });
}

// 提交审批决策：批准、拒绝或总是允许
export async function decideApproval(
  approvalId: string,
  action: ApprovalAction,
): Promise<ApprovalDecisionResponse> {
  return request<ApprovalDecisionResponse>(`/approvals/${encodeURIComponent(approvalId)}`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  });
}

// 流式对话的可选参数
export interface StreamOptions {
  signal?: AbortSignal;
  onFrame: (frame: StreamFrame) => void;
}

// 流式发送消息：逐行解析 SSE 的 event/data 字段，收到空行时分发一帧
export async function sendMessageStream(
  sessionId: string,
  message: string,
  options: StreamOptions,
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ message }),
      signal: options.signal,
    },
  );
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (!response.body) {
    throw new ApiRequestError('响应流不可用', 'stream_unavailable', null, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventName: string | null = null;
  let dataLines: string[] = [];

  // 分发表中已累积的一帧
  const dispatch = (): void => {
    if (dataLines.length > 0) {
      options.onFrame({ event: eventName, data: dataLines.join('\n') });
    }
    eventName = null;
    dataLines = [];
  };

  // 处理单行 SSE 文本
  const handleLine = (rawLine: string): void => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (line === '') {
      dispatch();
      return;
    }
    // 以冒号开头的行是注释，直接忽略
    if (line.startsWith(':')) {
      return;
    }
    const separator = line.indexOf(':');
    const field = separator === -1 ? line : line.slice(0, separator);
    const rawValue = separator === -1 ? '' : line.slice(separator + 1);
    const value = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue;
    if (field === 'event') {
      eventName = value;
    } else if (field === 'data') {
      dataLines.push(value);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let lineEnd = buffer.indexOf('\n');
      while (lineEnd >= 0) {
        handleLine(buffer.slice(0, lineEnd));
        buffer = buffer.slice(lineEnd + 1);
        lineEnd = buffer.indexOf('\n');
      }
    }
    buffer += decoder.decode();
    if (buffer.length > 0) {
      handleLine(buffer);
    }
    dispatch();
  } finally {
    reader.releaseLock();
  }
}
