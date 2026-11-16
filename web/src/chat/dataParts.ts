// 对话流中的自定义 data 部件类型与展示辅助函数
import type { UIMessage } from 'ai';
import type { ApprovalDecisionStatus } from '../api/types';

// 运行状态
export type RunState =
  | 'thinking'
  | 'tool_calling'
  | 'executing'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled';

// data-run-status 部件数据
export interface RunStatusData {
  run_id: string;
  session_id: string;
  status: RunState;
  message: string | null;
  iteration: number | null;
  plan_step_index: number | null;
  plan_step_total: number | null;
  plan_step_title: string | null;
}

// 计划步骤状态
export type PlanStepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'blocked'
  | 'skipped';

// 计划中的单个步骤
export interface PlanStepData {
  index: number;
  title: string;
  description: string;
  status: PlanStepStatus;
  summary: string;
}

// data-plan 部件数据
export interface PlanData {
  task: string;
  status: string;
  current_step: number | null;
  total_steps: number;
  replans: number;
  steps: PlanStepData[];
}

// 单次请求的 token 与耗时统计
export interface UsageTotals {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  // prompt 中命中缓存的 token 数，未上报缓存用量时为 0
  cached_tokens: number;
  latency_ms: number;
}

// data-usage 部件数据
export interface UsageData {
  run_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  latency_ms: number | null;
  run_total: UsageTotals;
}

// 审批状态
export type ApprovalStatus = 'pending' | ApprovalDecisionStatus;

// data-approval 部件数据
export interface ApprovalData {
  approval_id: string;
  tool_name: string;
  arguments: unknown;
  reason: string | null;
  status: ApprovalStatus;
  // time.monotonic 截止秒；无限等待或已结束时为 null
  expires_at: number | null;
}

// data-turn-usage 部件数据：本轮耗时与 token 用量
export interface TurnUsageData {
  run_id: string;
  message_id: string;
  duration_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  // prompt 中命中缓存的 token 数
  cached_tokens: number;
}

// 后端 data 部件名称到数据类型的映射
// 使用类型别名以便满足 UIDataTypes 的隐式索引签名约束
export type SAgentDataParts = {
  'run-status': RunStatusData;
  plan: PlanData;
  usage: UsageData;
  approval: ApprovalData;
  'turn-usage': TurnUsageData;
};

// 对话使用的 UI 消息类型
export type SAgentUIMessage = UIMessage<never, SAgentDataParts>;

// 运行状态中文文案
export const RUN_STATUS_LABELS: Record<RunState, string> = {
  thinking: '思考中',
  tool_calling: '调用工具',
  executing: '执行中',
  waiting_approval: '等待审批',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

// 计划步骤状态中文文案
export const PLAN_STEP_LABELS: Record<PlanStepStatus, string> = {
  pending: '等待中',
  running: '进行中',
  completed: '已完成',
  failed: '失败',
  blocked: '受阻',
  skipped: '已跳过',
};

// 审批状态中文文案
export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: '等待审批',
  approved: '已批准',
  denied: '已拒绝',
};

// 工具输出展示的最大字符数
export const MAX_OUTPUT_LENGTH = 2000;

// 将任意值转换为可读 JSON 文本
export function formatJson(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// 按最大字符数截断文本
export function truncateText(text: string, maxLength: number = MAX_OUTPUT_LENGTH): {
  text: string;
  truncated: boolean;
} {
  if (text.length <= maxLength) {
    return { text, truncated: false };
  }
  return { text: text.slice(0, maxLength), truncated: true };
}
