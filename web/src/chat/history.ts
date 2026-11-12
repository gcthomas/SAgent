// 把后端会话历史转换为 AI SDK 的 UI 消息列表
import type { SessionMessage } from '../api/types';
import type { SAgentUIMessage } from './dataParts';

// assistant.tool_calls 中单个工具调用的关键信息
interface ToolCallInfo {
  name: string;
  input: unknown;
}

// 解析 assistant 消息中的 tool_calls，兼容对象与 JSON 字符串两种参数形式
function parseToolCalls(raw: unknown): { id: string; info: ToolCallInfo }[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const calls: { id: string; info: ToolCallInfo }[] = [];
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const id = typeof record.id === 'string' ? record.id : null;
    const fn = record.function;
    if (!id || typeof fn !== 'object' || fn === null) {
      continue;
    }
    const fnRecord = fn as Record<string, unknown>;
    const name = typeof fnRecord.name === 'string' ? fnRecord.name : '未知工具';
    const rawArgs = fnRecord.arguments;
    let input: unknown = rawArgs;
    if (typeof rawArgs === 'string') {
      try {
        input = JSON.parse(rawArgs);
      } catch {
        input = rawArgs;
      }
    }
    calls.push({ id, info: { name, input } });
  }
  return calls;
}

// 收集所有工具调用，按 tool_call_id 建索引，供工具结果消息复用名称与参数
function collectToolCalls(messages: SessionMessage[]): Map<string, ToolCallInfo> {
  const calls = new Map<string, ToolCallInfo>();
  for (const message of messages) {
    if (message.role !== 'assistant') {
      continue;
    }
    for (const call of parseToolCalls(message.tool_calls)) {
      calls.set(call.id, call.info);
    }
  }
  return calls;
}

// 把历史消息转换为 UI 消息：用户/助手文本直出，工具结果合成为动态工具卡片
export function buildHistoryMessages(messages: SessionMessage[]): SAgentUIMessage[] {
  const toolCalls = collectToolCalls(messages);
  const result: SAgentUIMessage[] = [];
  for (const message of messages) {
    if (message.role === 'user') {
      result.push({
        id: `hist-${message.id}`,
        role: 'user',
        parts: [{ type: 'text', text: message.content }],
      });
      continue;
    }
    if (message.role === 'assistant') {
      if (message.content.length > 0) {
        result.push({
          id: `hist-${message.id}`,
          role: 'assistant',
          parts: [{ type: 'text', text: message.content }],
        });
      }
      continue;
    }
    if (message.role === 'tool') {
      const call = message.tool_call_id ? toolCalls.get(message.tool_call_id) : undefined;
      result.push({
        id: `hist-tool-${message.id}`,
        role: 'assistant',
        parts: [
          {
            type: 'dynamic-tool',
            toolName: call ? call.name : '未知工具',
            toolCallId: message.tool_call_id ?? `hist-tool-${message.id}`,
            state: 'output-available',
            input: call ? call.input : {},
            output: message.content,
          },
        ],
      });
    }
  }
  return result;
}
