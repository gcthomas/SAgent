// 对话消息渲染：用户气泡、助手 Markdown 文本与工具结果卡片
import { MessagePrimitive } from '@assistant-ui/react';
import { MarkdownText } from './Markdown';
import { ToolCard } from './ToolCard';

// 用户消息的纯文本部件，保留原始换行，不按 Markdown 解析
function TextPart({ text }: { text: string }) {
  return <span className="message-text">{text}</span>;
}

// 助手文本部件，按 Markdown 渲染
function MarkdownPart({ text }: { text: string }) {
  return <MarkdownText text={text} />;
}

// 助手消息内的部件配置：文本 + 工具卡片
const assistantPartComponents = {
  Text: MarkdownPart,
  tools: { Fallback: ToolCard },
};

// 用户消息
export function UserMessage() {
  return (
    <MessagePrimitive.Root className="message message--user">
      <div className="message-bubble">
        <MessagePrimitive.Parts components={{ Text: TextPart }} />
      </div>
    </MessagePrimitive.Root>
  );
}

// 助手消息
export function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message message--assistant">
      <div className="message-bubble">
        <MessagePrimitive.Parts components={assistantPartComponents} />
      </div>
    </MessagePrimitive.Root>
  );
}
