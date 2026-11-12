// 轨迹视图：展示会话累计用量、系统初始提示词与逐轮交互明细
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiRequestError, getTrace } from '../api/client';
import type { TraceResponse, TraceToolCall, TraceTurn } from '../api/types';

// 轨迹视图属性
interface TraceViewProps {
  sessionId: string | null;
}

// 复制状态
type CopyState = 'idle' | 'success' | 'failure';

// 将异常转换为中文错误文案
function toErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message || '请求失败';
  }
  if (error instanceof Error) {
    return error.message || '请求失败';
  }
  return '请求失败';
}

// 格式化耗时：不足 1 秒显示毫秒，否则显示秒
function formatDuration(durationMs: number | null): string {
  if (durationMs === null) {
    return '未记录';
  }
  if (durationMs >= 1000) {
    return `${(durationMs / 1000).toFixed(1)} s`;
  }
  return `${durationMs} ms`;
}

// 汇总所有轮次耗时，忽略未记录（null）的轮次；全部未记录时返回 null
function sumDurations(turns: TraceTurn[]): number | null {
  let total = 0;
  let recorded = false;
  for (const turn of turns) {
    if (turn.duration_ms !== null) {
      total += turn.duration_ms;
      recorded = true;
    }
  }
  return recorded ? total : null;
}

// 格式化总耗时：超过 1 分钟显示分钟，其余沿用单轮耗时格式
function formatTotalDuration(durationMs: number | null): string {
  if (durationMs === null) {
    return '未记录';
  }
  if (durationMs >= 60000) {
    return `${(durationMs / 60000).toFixed(1)} 分钟`;
  }
  return formatDuration(durationMs);
}

// 生成轮次标题中的用户输入摘要
function summarizeUserText(text: string): string {
  const singleLine = text.replace(/\s+/g, ' ').trim();
  if (!singleLine) {
    return '（空输入）';
  }
  return singleLine.length > 40 ? `${singleLine.slice(0, 40)}…` : singleLine;
}

// 格式化 token 用量，未记录时返回中文提示
function formatTokens(usage: TraceResponse['usage']): string {
  return usage === null ? '未记录' : String(usage.total_tokens);
}

// 尝试格式化工具参数字符串，解析失败时原样展示
function formatArguments(argumentsText: string): string {
  if (!argumentsText) {
    return '（无参数）';
  }
  try {
    return JSON.stringify(JSON.parse(argumentsText), null, 2);
  } catch {
    return argumentsText;
  }
}

// 单条工具调用：折叠展示名称与调用 id，展开后查看参数与结果
function TraceToolItem({ call }: { call: TraceToolCall }) {
  return (
    <details className="trace-tool">
      <summary className="trace-tool-summary">
        <span className="trace-tool-name">{call.name || '未命名工具'}</span>
        {call.tool_call_id !== null && <span className="trace-tool-id">{call.tool_call_id}</span>}
      </summary>
      <div className="trace-tool-body">
        <p className="trace-label">参数</p>
        <pre className="trace-pre">{formatArguments(call.arguments)}</pre>
        <p className="trace-label">结果</p>
        <pre className="trace-pre">{call.result ?? '未关联结果'}</pre>
      </div>
    </details>
  );
}

// 单个交互轮次：默认折叠，展开后按顺序展示输入、回答、工具调用与压缩事件
function TraceTurnBlock({ turn, defaultOpen }: { turn: TraceTurn; defaultOpen: boolean }) {
  return (
    <details className="trace-turn" open={defaultOpen}>
      <summary className="trace-turn-summary">
        <span className="trace-turn-head">
          <span className="trace-turn-index">第 {turn.turn_index} 轮</span>
          <span className="trace-turn-ask">{summarizeUserText(turn.user.content)}</span>
          <span className="trace-turn-stat">耗时：{formatDuration(turn.duration_ms)}</span>
          <span className="trace-turn-stat">Token：{formatTokens(turn.usage)}</span>
        </span>
      </summary>
      <div className="trace-turn-body">
        <div className="trace-block">
          <p className="trace-label">用户输入</p>
          <div className="trace-text">{turn.user.content || '（空输入）'}</div>
        </div>
        <div className="trace-block">
          <p className="trace-label">助手回答</p>
          <div className="trace-text">{turn.assistant_text || '本轮没有助手文本'}</div>
        </div>
        {turn.tool_calls.length > 0 && (
          <div className="trace-block">
            <p className="trace-label">工具调用（{turn.tool_calls.length}）</p>
            <div className="trace-tool-list">
              {turn.tool_calls.map((call, index) => (
                <TraceToolItem
                  key={`${call.tool_call_id ?? call.name}-${index}`}
                  call={call}
                />
              ))}
            </div>
          </div>
        )}
        {turn.compactions.length > 0 && (
          <div className="trace-compaction-list">
            {turn.compactions.map((compaction, index) => (
              <div
                className="trace-compaction"
                key={`${compaction.covered_to_seq}-${index}`}
              >
                <div className="trace-compaction-head">
                  <span className="trace-compaction-label">上下文已压缩</span>
                  <span className="trace-compaction-range">
                    覆盖消息 seq {compaction.covered_from_seq} ~ {compaction.covered_to_seq}
                  </span>
                </div>
                <div className="trace-compaction-summary">
                  {compaction.summary || '未记录摘要'}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="trace-turn-meta">
          <span>模式：{turn.mode ?? '未记录'}</span>
          <span>结果：{turn.outcome ?? '未记录'}</span>
          <span>开始：{turn.started_at ?? '未记录'}</span>
          <span>结束：{turn.finished_at ?? '未记录'}</span>
        </div>
      </div>
    </details>
  );
}

export function TraceView({ sessionId }: TraceViewProps) {
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const copyTimer = useRef<number | null>(null);

  // 卸载时清理复制提示的定时器
  useEffect(() => {
    return () => {
      if (copyTimer.current !== null) {
        window.clearTimeout(copyTimer.current);
      }
    };
  }, []);

  // 会话切换或重新加载时重新拉取轨迹，用 cancelled 标记避免竞态与卸载后写入状态
  useEffect(() => {
    if (sessionId === null) {
      setTrace(null);
      setLoading(false);
      setError(null);
      setNotFound(false);
      return;
    }
    let cancelled = false;
    setTrace(null);
    setLoading(true);
    setError(null);
    setNotFound(false);
    void (async () => {
      try {
        const data = await getTrace(sessionId);
        if (!cancelled) {
          setTrace(data);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiRequestError && err.status === 404) {
            setNotFound(true);
          } else {
            setError(toErrorMessage(err));
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, reloadToken]);

  // 重新加载轨迹
  const handleReload = useCallback(() => {
    setReloadToken((prev) => prev + 1);
  }, []);

  // 复制系统提示词全文并给出中文反馈
  const handleCopy = useCallback(async () => {
    const text = trace?.system_prompt ?? '';
    try {
      await navigator.clipboard.writeText(text);
      setCopyState('success');
    } catch {
      setCopyState('failure');
    }
    if (copyTimer.current !== null) {
      window.clearTimeout(copyTimer.current);
    }
    copyTimer.current = window.setTimeout(() => setCopyState('idle'), 2000);
  }, [trace]);

  if (sessionId === null) {
    return (
      <div className="placeholder">
        <p className="placeholder-title">请选择或新建会话后查看轨迹</p>
        <p className="placeholder-hint">从左侧选择会话，即可查看系统提示词与逐轮交互明细。</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="trace-view">
        <p className="trace-hint">正在加载轨迹…</p>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="trace-view">
        <div className="trace-state">
          <p className="trace-state-text">会话不存在，可能已被删除。</p>
          <button type="button" className="secondary-button" onClick={handleReload}>
            重新加载
          </button>
        </div>
      </div>
    );
  }

  if (error !== null) {
    return (
      <div className="trace-view">
        <div className="trace-state">
          <p className="trace-error">{error}</p>
          <button type="button" className="secondary-button" onClick={handleReload}>
            重新加载
          </button>
        </div>
      </div>
    );
  }

  if (trace === null) {
    return (
      <div className="trace-view">
        <p className="trace-hint">暂无轨迹数据</p>
      </div>
    );
  }

  return (
    <div className="trace-view">
      <div className="trace-overview">
        <span className="trace-overview-item">
          交互轮数：<span className="trace-overview-value">{trace.total_turns}</span>
        </span>
        <span className="trace-overview-item">
          累计 Token：<span className="trace-overview-value">{formatTokens(trace.usage)}</span>
        </span>
        <span className="trace-overview-item">
          总耗时：
          <span className="trace-overview-value">{formatTotalDuration(sumDurations(trace.turns))}</span>
        </span>
        <span className="trace-overview-item">
          系统提示词：
          <span className="trace-overview-value">
            {trace.system_prompt_source === 'stored' ? '来自历史记录' : '引擎默认提示词'}
          </span>
        </span>
      </div>

      <section className="trace-section">
        <div className="trace-section-head">
          <h2 className="trace-section-title">系统初始提示词</h2>
          <div className="trace-section-actions">
            <span className="trace-copy-status">
              {copyState === 'success' ? '已复制' : copyState === 'failure' ? '复制失败' : ''}
            </span>
            <button
              type="button"
              className="secondary-button trace-copy-button"
              onClick={handleCopy}
              disabled={trace.system_prompt.length === 0}
            >
              复制
            </button>
          </div>
        </div>
        <details className="trace-prompt">
          <summary className="trace-prompt-summary">展开查看完整提示词</summary>
          <pre className="trace-pre">{trace.system_prompt || '未记录'}</pre>
        </details>
      </section>

      <section className="trace-turns">
        <h2 className="trace-section-title">交互轮次</h2>
        {trace.turns.length === 0 ? (
          <p className="trace-hint">该会话还没有交互记录</p>
        ) : (
          trace.turns.map((turn) => (
            <TraceTurnBlock
              key={`${trace.session.id}-${turn.turn_index}`}
              turn={turn}
              defaultOpen={turn.turn_index === 1}
            />
          ))
        )}
      </section>
    </div>
  );
}
