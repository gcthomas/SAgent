// 对话区的运行状态与用量展示：状态随本轮结束隐藏，用量常驻展示
import type { RunStatusData, UsageData } from './dataParts';
import { RUN_STATUS_LABELS } from './dataParts';

// 运行状态栏属性
interface RunStatusBarProps {
  runStatus: RunStatusData | null;
}

// 用量栏属性
interface UsageBarProps {
  usage: UsageData | null;
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

// 用量栏：常驻展示会话累计 token 用量，与运行状态分行
export function UsageBar({ usage }: UsageBarProps) {
  return (
    <div className="usage-bar">
      <span className="usage-summary">
        会话累计 Token：{usage ? usage.run_total.total_tokens : '未记录'}
      </span>
    </div>
  );
}
