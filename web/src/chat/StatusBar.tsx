// 对话顶部的运行状态、计划进度与用量信息
import type { RunStatusData, TurnUsageData, UsageData } from './dataParts';
import { RUN_STATUS_LABELS } from './dataParts';

// 状态栏属性
interface StatusBarProps {
  runStatus: RunStatusData | null;
  usage: UsageData | null;
  // 本轮耗时与 token 用量；展示在顶部作为最近一轮的汇总
  turnUsage: TurnUsageData | null;
}

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

export function StatusBar({ runStatus, usage, turnUsage }: StatusBarProps) {
  const planStep = readPlanProgress(runStatus);
  const percent =
    planStep === null ? 0 : Math.min(100, Math.round((planStep.index / planStep.total) * 100));

  return (
    <div className="status-bar">
      {runStatus && (
        <span className={`status-pill status-pill--${runStatus.status}`}>
          {RUN_STATUS_LABELS[runStatus.status]}
        </span>
      )}
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
      <span className="usage-summary">
        本轮耗时：
        {turnUsage && turnUsage.duration_ms !== null
          ? `${Math.round(turnUsage.duration_ms)} ms`
          : usage && usage.latency_ms !== null
            ? `${Math.round(usage.latency_ms)} ms`
            : '未记录'}
        <span className="usage-divider">·</span>
        会话累计 Token：{usage ? usage.run_total.total_tokens : '未记录'}
      </span>
    </div>
  );
}
