// 对话区的运行状态与用量展示：状态随本轮结束隐藏，用量常驻展示
import type { TraceUsage } from '../api/types';
import type { RunStatusData } from './dataParts';
import { RUN_STATUS_LABELS } from './dataParts';

// 运行状态栏属性
interface RunStatusBarProps {
  runStatus: RunStatusData | null;
}

// 用量栏属性
interface UsageBarProps {
  // 会话累计 token 用量；null 表示历史与本轮都没有可用记录
  usage: TraceUsage | null;
}

// 千分位格式化 token 数量，便于直观看清量级
function formatTokens(value: number): string {
  return value.toLocaleString('en-US');
}

// 计算缓存命中率百分比；输入 token 为 0 时无法计算，返回 null
function calcCacheHitRate(usage: TraceUsage): number | null {
  if (usage.prompt_tokens <= 0) {
    return null;
  }
  const cached = Math.min(usage.cached_tokens, usage.prompt_tokens);
  return (cached / usage.prompt_tokens) * 100;
}

// 本轮已结束的终态：状态条不再展示
const TERMINAL_STATUSES = new Set<string>(['completed', 'failed', 'cancelled']);

// 提取计划进度信息，缺少步骤索引时返回 null
function readPlanProgress(
  runStatus: RunStatusData | null,
): { index: number; total: number; title: string | null } | null {
  if (runStatus === null) {
    return null;
  }
  const { plan_step_index: index, plan_step_total: total } = runStatus;
  if (index === null || total === null || total <= 0) {
    return null;
  }
  return { index, total, title: runStatus.plan_step_title };
}

// 运行状态条：本轮运行结束后隐藏，位于消息区下方
export function RunStatusBar({ runStatus }: RunStatusBarProps) {
  if (runStatus === null || TERMINAL_STATUSES.has(runStatus.status)) {
    return null;
  }
  const planStep = readPlanProgress(runStatus);
  const percent =
    planStep === null ? 0 : Math.min(100, Math.round((planStep.index / planStep.total) * 100));

  return (
    <div className="status-bar">
      <span className={`status-pill status-pill--${runStatus.status}`}>
        {RUN_STATUS_LABELS[runStatus.status]}
      </span>
      {planStep && (
        <div className="plan-progress">
          <span className="plan-progress-label">
            第 {planStep.index}/{planStep.total} 步
            {planStep.title ? `：${planStep.title}` : ''}
          </span>
          <div className="plan-progress-track">
            <div className="plan-progress-fill" style={{ width: `${percent}%` }} />
          </div>
        </div>
      )}
    </div>
  );
}

// 用量栏：常驻展示会话累计总量与缓存命中率，点击摘要展开用量明细
export function UsageBar({ usage }: UsageBarProps) {
  if (usage === null) {
    return (
      <div className="usage-bar">
        <span className="usage-summary">会话累计 Token：未记录</span>
      </div>
    );
  }
  // 命中缓存是 prompt 的子集，未命中部分据此扣减
  const cached = Math.min(usage.cached_tokens, usage.prompt_tokens);
  const missed = usage.prompt_tokens - cached;
  const hitRate = calcCacheHitRate(usage);
  return (
    <div className="usage-bar">
      <details className="usage-details">
        <summary className="usage-summary" title="点击查看用量明细">
          <span>会话累计 Token：{formatTokens(usage.total_tokens)}</span>
          <span className="usage-divider">·</span>
          <span>缓存命中率：{hitRate === null ? '--' : `${hitRate.toFixed(1)}%`}</span>
          <span className="usage-toggle">明细</span>
        </summary>
        <div className="usage-popover">
          <p className="usage-popover-title">会话累计用量</p>
          <div className="usage-row">
            <span>输入 Token</span>
            <span>{formatTokens(usage.prompt_tokens)}</span>
          </div>
          <div className="usage-row">
            <span>其中命中缓存</span>
            <span>{formatTokens(cached)}</span>
          </div>
          <div className="usage-row">
            <span>未命中缓存</span>
            <span>{formatTokens(missed)}</span>
          </div>
          <div className="usage-row">
            <span>输出 Token</span>
            <span>{formatTokens(usage.completion_tokens)}</span>
          </div>
          <div className="usage-row">
            <span>总 Token</span>
            <span>{formatTokens(usage.total_tokens)}</span>
          </div>
        </div>
      </details>
    </div>
  );
}
