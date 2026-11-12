// 计划卡片：展示任务、总步数、重规划次数与各步骤状态
import type { PlanData } from './dataParts';
import { PLAN_STEP_LABELS } from './dataParts';

// 计划卡片属性
interface PlanCardProps {
  plan: PlanData;
}

export function PlanCard({ plan }: PlanCardProps) {
  return (
    <section className="plan-card">
      <header className="plan-card-head">
        <span className="plan-card-title">执行计划</span>
        <span className="plan-card-meta">
          共 {plan.total_steps} 步 · 重规划 {plan.replans} 次
        </span>
      </header>
      <p className="plan-card-task">{plan.task}</p>
      <ol className="plan-step-list">
        {plan.steps.map((step) => (
          <li key={step.index} className={`plan-step plan-step--${step.status}`}>
            <div className="plan-step-row">
              <span className="plan-step-title">
                第 {step.index} 步：{step.title}
              </span>
              <span className="plan-step-status">{PLAN_STEP_LABELS[step.status]}</span>
            </div>
            {step.status === 'completed' && step.summary && (
              <p className="plan-step-summary">{step.summary}</p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
