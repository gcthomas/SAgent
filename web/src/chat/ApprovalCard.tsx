// 审批卡片：展示待审批工具与参数摘要，并提交批准、拒绝或总是允许
import { useState } from 'react';
import classNames from 'classnames';
import type { ApprovalAction } from '../api/types';
import { ApiRequestError } from '../api/client';
import type { ApprovalData } from './dataParts';
import { APPROVAL_STATUS_LABELS, formatJson, truncateText } from './dataParts';

// 参数摘要的最大字符数
const ARGUMENTS_SUMMARY_LENGTH = 120;

// 审批卡片属性
interface ApprovalCardProps {
  approval: ApprovalData;
  onDecide: (approvalId: string, action: ApprovalAction) => Promise<void>;
}

// 把审批请求失败转换为中文提示
function toApprovalError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 409) {
      return '审批已过期';
    }
    if (error.status === 404) {
      return '审批不存在';
    }
    return error.message || '审批提交失败';
  }
  return '审批提交失败';
}

export function ApprovalCard({ approval, onDecide }: ApprovalCardProps) {
  const [submitting, setSubmitting] = useState<ApprovalAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = approval.status === 'pending';
  const argsSummary = truncateText(formatJson(approval.arguments), ARGUMENTS_SUMMARY_LENGTH).text;

  const handleDecide = async (action: ApprovalAction): Promise<void> => {
    setSubmitting(action);
    setError(null);
    try {
      await onDecide(approval.approval_id, action);
    } catch (err) {
      setError(toApprovalError(err));
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <section className={classNames('approval-card', !pending && 'approval-card--decided')}>
      <header className="approval-card-head">
        <span className="approval-card-title">工具审批</span>
        <span className="approval-card-status">{APPROVAL_STATUS_LABELS[approval.status]}</span>
      </header>
      <div className="approval-card-row">
        <span className="approval-card-label">工具</span>
        <span className="approval-card-tool">{approval.tool_name}</span>
      </div>
      <div className="approval-card-row">
        <span className="approval-card-label">参数摘要</span>
        <span className="approval-card-summary">{argsSummary}</span>
      </div>
      <details className="approval-card-args">
        <summary>完整参数</summary>
        <pre>{formatJson(approval.arguments)}</pre>
      </details>
      {approval.reason && <p className="approval-card-reason">原因：{approval.reason}</p>}
      {error && <p className="chat-error">{error}</p>}
      {pending ? (
        <div className="approval-card-actions">
          <button
            type="button"
            className="primary-button"
            disabled={submitting !== null}
            onClick={() => void handleDecide('approve')}
          >
            批准
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={submitting !== null}
            onClick={() => void handleDecide('deny')}
          >
            拒绝
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={submitting !== null}
            onClick={() => void handleDecide('always')}
          >
            总是允许
          </button>
        </div>
      ) : (
        <p className="approval-card-done">该审批已处理，仅可查看。</p>
      )}
    </section>
  );
}
