// 助手文本的 Markdown 渲染：启用 GFM 语法与代码块高亮
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';
import 'highlight.js/styles/github.css';

// 渲染 Markdown 文本；流式增量中未闭合的代码块由 react-markdown 按普通文本降级，不会中断渲染
export function MarkdownText({ text }: { text: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
