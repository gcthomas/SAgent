// 对话视图：基于 AI SDK 的 useChat 与 assistant-ui 运行时渲染消息流
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { APICallError, DefaultChatTransport } from 'ai';
import type { DataUIPart } from 'ai';
import { useChat } from '@ai-sdk/react';
import { AssistantRuntimeProvider, ThreadPrimitive } from '@assistant-ui/react';
import { useAISDKRuntime } from '@assistant-ui/react-ai-sdk';
import type { ApprovalAction } from '../api/types';
import { cancelRun, decideApproval, listMessages } from '../api/client';
import { ApiRequestError } from '../api/client';
import type {
  ApprovalData,
  PlanData,
  RunStatusData,
  SAgentDataParts,
  SAgentUIMessage,
  TurnUsageData,
  UsageData,
} from './dataParts';
import { buildHistoryMessages } from './history';
import { ApprovalCard } from './ApprovalCard';
import { Composer } from './Composer';
import { PlanCard } from './PlanCard';
import { StatusBar } from './StatusBar';
import { AssistantMessage, UserMessage } from './ThreadMessages';

// 对话视图属性
interface ChatViewProps {
  sessionId: string | null;
}

// 读取一条 UI 消息中的纯文本内容
function readMessageText(message: SAgentUIMessage | undefined): string {
  if (!message) {
    return '';
  }
  let text = '';
  for (const part of message.parts) {
    if (part.type === 'text') {
      text += part.text;
    }
  }
  return text;
}

// 取最后一条用户消息的文本，作为本轮请求的唯一消息
function readLastUserText(messages: SAgentUIMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      return readMessageText(messages[index]);
    }
  }
  return '';
}

// 读取后端错误响应体中的 message 字段
function readErrorBody(body: string | undefined): string | null {
  if (!body) {
    return null;
  }
  try {
    const parsed = JSON.parse(body) as { message?: unknown };
    if (parsed && typeof parsed.message === 'string' && parsed.message.length > 0) {
      return parsed.message;
    }
  } catch {
    return null;
  }
  return null;
}

// 流式连接中断的统一提示文案
const INTERRUPTED_MESSAGE = '连接已中断，本轮回答可能不完整';

// 把对话流错误转换为中文提示：带 HTTP 状态的错误沿用后端文案，其余视为连接中断
function toChatError(error: Error): string {
  if (APICallError.isInstance(error)) {
    if (error.statusCode === 409) {
      return '该会话正在执行中';
    }
    if (typeof error.statusCode === 'number') {
      return readErrorBody(error.responseBody) ?? (error.message || '请求失败');
    }
    return INTERRUPTED_MESSAGE;
  }
  return INTERRUPTED_MESSAGE;
}

// 把普通请求失败转换为中文提示
function toRequestError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message || '请求失败';
  }
  if (error instanceof Error) {
    return error.message || '请求失败';
  }
  return '请求失败';
}

// 按审批 id 新增或更新审批卡片
function upsertApproval(prev: ApprovalData[], next: ApprovalData): ApprovalData[] {
  const index = prev.findIndex((item) => item.approval_id === next.approval_id);
  if (index === -1) {
    return [...prev, next];
  }
  const merged = prev.slice();
  merged[index] = next;
  return merged;
}

export function ChatView({ sessionId }: ChatViewProps) {
  const sessionIdRef = useRef<string | null>(sessionId);
  const [runStatus, setRunStatus] = useState<RunStatusData | null>(null);
  const [plan, setPlan] = useState<PlanData | null>(null);
  const [usage, setUsage] = useState<UsageData | null>(null);
  // 本轮耗时与 token 用量，在助手消息下方展示
  const [turnUsage, setTurnUsage] = useState<TurnUsageData | null>(null);
  const [approvals, setApprovals] = useState<ApprovalData[]>([]);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // 请求准备钩子：URL 随会话 id 变化，请求体只携带一条用户消息
  const transport = useMemo(
    () =>
      new DefaultChatTransport<SAgentUIMessage>({
        prepareSendMessagesRequest: ({ messages }) => ({
          api: `/api/sessions/${encodeURIComponent(sessionIdRef.current ?? '')}/messages`,
          body: { message: readLastUserText(messages) },
        }),
      }),
    [],
  );

  // 收集流中的自定义 data 部件，避免 data 部件在消息列表中重复出现
  const handleData = useCallback((part: DataUIPart<SAgentDataParts>) => {
    switch (part.type) {
      case 'data-run-status':
        setRunStatus(part.data);
        break;
      case 'data-plan':
        setPlan(part.data);
        break;
      case 'data-usage':
        setUsage(part.data);
        break;
      case 'data-turn-usage':
        setTurnUsage(part.data);
        break;
      case 'data-approval':
        setApprovals((prev) => upsertApproval(prev, part.data));
        break;
      default:
        break;
    }
  }, []);

  const chat = useChat<SAgentUIMessage>({ transport, onData: handleData });
  const runtime = useAISDKRuntime(chat);
  const running = chat.status === 'submitted' || chat.status === 'streaming';

  // 停止按钮读取最新的 stop 方法，避免会话切换副作用重复执行
  const stopRef = useRef(chat.stop);
  useEffect(() => {
    stopRef.current = chat.stop;
  });

  // 新一轮请求开始时清空上一轮的运行状态，终态时复位停止中标记
  useEffect(() => {
    if (chat.status === 'submitted') {
      setRunStatus(null);
      setPlan(null);
      setUsage(null);
      setTurnUsage(null);
      setApprovals([]);
      setStopping(false);
      setStopError(null);
    } else if (chat.status === 'ready' || chat.status === 'error') {
      setStopping(false);
    }
  }, [chat.status]);

  // 拉取指定会话的历史消息；isCancelled 用于避免会话切换或重复加载时写入过期结果
  const loadHistory = useCallback(
    async (targetId: string, isCancelled: () => boolean): Promise<void> => {
      setHistoryLoading(true);
      try {
        const data = await listMessages(targetId);
        if (!isCancelled()) {
          chat.setMessages(buildHistoryMessages(data.messages));
        }
      } catch (error) {
        if (!isCancelled()) {
          setHistoryError(toRequestError(error));
          chat.setMessages([]);
        }
      } finally {
        if (!isCancelled()) {
          setHistoryLoading(false);
        }
      }
    },
    [chat.setMessages],
  );

  // 切换会话时中断当前流并加载历史消息
  useEffect(() => {
    void stopRef.current();
    setRunStatus(null);
    setPlan(null);
    setUsage(null);
    setTurnUsage(null);
    setApprovals([]);
    setStopping(false);
    setStopError(null);
    setHistoryError(null);
    if (sessionId === null) {
      setHistoryLoading(false);
      chat.setMessages([]);
      return;
    }
    let cancelled = false;
    void loadHistory(sessionId, () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [sessionId, loadHistory, chat.setMessages]);

  // 重新加载会话历史：清空对话错误后重新拉取消息，不重发上一条用户消息
  const handleReloadHistory = useCallback(() => {
    if (sessionId === null || historyLoading) {
      return;
    }
    setHistoryError(null);
    chat.clearError();
    void loadHistory(sessionId, () => sessionIdRef.current !== sessionId);
  }, [sessionId, historyLoading, loadHistory, chat.clearError]);

  // 停止当前运行：优先通过后端取消接口终止，等待流自然关闭
  const handleStop = useCallback(() => {
    if (stopping || !running) {
      return;
    }
    setStopError(null);
    const runId = runStatus?.run_id ?? null;
    if (runId === null) {
      void stopRef.current();
      return;
    }
    setStopping(true);
    void (async () => {
      try {
        await cancelRun(runId);
      } catch (error) {
        setStopError(toRequestError(error));
        setStopping(false);
      }
    })();
  }, [running, runStatus, stopping]);

  // 提交审批决策并更新卡片状态
  const handleDecide = useCallback(
    async (approvalId: string, action: ApprovalAction): Promise<void> => {
      const result = await decideApproval(approvalId, action);
      setApprovals((prev) =>
        prev.map((item) =>
          item.approval_id === approvalId ? { ...item, status: result.status } : item,
        ),
      );
    },
    [],
  );

  if (sessionId === null) {
    return (
      <div className="placeholder">
        <p className="placeholder-title">选择或新建会话</p>
        <p className="placeholder-hint">从左侧选择会话，或新建会话后开始对话。</p>
      </div>
    );
  }

  return (
    <div className="chat-view">
      <StatusBar runStatus={runStatus} usage={usage} turnUsage={turnUsage} />
      {historyLoading && <p className="chat-hint">正在加载消息…</p>}
      {historyError && <p className="chat-error">{historyError}</p>}
      <AssistantRuntimeProvider runtime={runtime}>
        <ThreadPrimitive.Root className="thread-root">
          <ThreadPrimitive.Viewport className="thread-viewport">
            <ThreadPrimitive.Empty>
              <p className="chat-empty">输入消息，开始与 Agent 对话。</p>
            </ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages>
              {({ message }) =>
                message.role === 'user' ? (
                  <UserMessage />
                ) : (
                  <>
                    <AssistantMessage />
                    {turnUsage !== null && turnUsage.message_id === message.id && (
                      <p className="message-usage">
                        本轮耗时：
                        {turnUsage.duration_ms !== null
                          ? `${Math.round(turnUsage.duration_ms)} ms`
                          : '未记录'}
                        <span className="usage-divider">·</span>
                        Token：{turnUsage.total_tokens}
                      </p>
                    )}
                  </>
                )
              }
            </ThreadPrimitive.Messages>
          </ThreadPrimitive.Viewport>
        </ThreadPrimitive.Root>
        {(plan !== null || approvals.length > 0 || stopError !== null || chat.error) && (
          <div className="chat-panels">
            {plan !== null && <PlanCard plan={plan} />}
            {approvals.map((approval) => (
              <ApprovalCard
                key={approval.approval_id}
                approval={approval}
                onDecide={handleDecide}
              />
            ))}
            {stopError !== null && <p className="chat-error">{stopError}</p>}
            {chat.error && (
              <div className="chat-error">
                <p className="chat-error-text">{toChatError(chat.error)}</p>
                <button
                  type="button"
                  className="secondary-button chat-error-action"
                  onClick={handleReloadHistory}
                  disabled={historyLoading}
                >
                  {historyLoading ? '加载中…' : '重新加载历史'}
                </button>
              </div>
            )}
          </div>
        )}
        <Composer running={running} stopping={stopping} onStop={handleStop} />
      </AssistantRuntimeProvider>
    </div>
  );
}
