// 底部输入区：Enter 发送、Shift+Enter 换行，运行中显示停止按钮
import { ComposerPrimitive } from '@assistant-ui/react';

// 输入区属性
interface ComposerProps {
  running: boolean;
  stopping: boolean;
  onStop: () => void;
}

export function Composer({ running, stopping, onStop }: ComposerProps) {
  return (
    <ComposerPrimitive.Root className="composer">
      <ComposerPrimitive.Input
        className="composer-input"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        submitMode="enter"
        minRows={1}
        maxRows={6}
      />
      {running ? (
        <button
          type="button"
          className="composer-stop"
          disabled={stopping}
          onClick={onStop}
        >
          {stopping ? '停止中' : '停止'}
        </button>
      ) : (
        <ComposerPrimitive.Send className="primary-button composer-send">
          发送
        </ComposerPrimitive.Send>
      )}
    </ComposerPrimitive.Root>
  );
}
