// 工具调用卡片：展示工具名、输入参数与截断后的输出
import { useState } from 'react';
import classNames from 'classnames';
import type {
  MessagePartStatus,
  ToolCallMessagePart,
  ToolCallMessagePartStatus,
} from '@assistant-ui/react';
import { MAX_OUTPUT_LENGTH, formatJson, truncateText } from './dataParts';

// 工具卡片接收的部件属性
type ToolCardProps = Pick<
  ToolCallMessagePart,
  'toolName' | 'args' | 'argsText' | 'result' | 'isError'
> & {
  status: MessagePartStatus | ToolCallMessagePartStatus;
};

export function ToolCard({ toolName, args, argsText, result, isError, status }: ToolCardProps) {
  const [expanded, setExpanded] = useState(false);
  const running = status.type === 'running';
  const output = result === undefined ? '' : typeof result === 'string' ? result : formatJson(result);
  const { text: preview, truncated } = truncateText(output, MAX_OUTPUT_LENGTH);
  const shown = expanded ? output : preview;
  const inputText = args === undefined ? argsText : formatJson(args);

  return (
    <div className={classNames('tool-card', isError && 'tool-card--error')}>
      <div className="tool-card-head">
        <span className="tool-card-name">{toolName}</span>
        <span className="tool-card-status">
          {running ? '执行中' : isError ? '执行失败' : '执行完成'}
        </span>
      </div>
      <details className="tool-card-input">
        <summary>输入参数</summary>
        <pre>{inputText}</pre>
      </details>
      <div className="tool-card-output">
        <pre>{running ? '等待工具输出...' : shown || '无输出'}</pre>
        {truncated && (
          <button
            type="button"
            className="text-button"
            onClick={() => setExpanded((prev) => !prev)}
          >
            {expanded ? '收起' : `展开全部（共 ${output.length} 字符）`}
          </button>
        )}
      </div>
    </div>
  );
}
