"""内置工具：Shell / 命令执行。

在本地执行系统命令并返回标准输出与标准错误。包含超时处理，避免长时间阻塞。
注意：该工具会执行任意命令，存在安全风险，请在可信环境中使用。
"""

from __future__ import annotations

import base64
import subprocess
import sys

from pydantic import BaseModel, Field

from .base import Tool

# 输出返回的最大字符数
_MAX_OUTPUT_CHARS = 10000


class ShellArgs(BaseModel):
    """命令执行参数。"""

    command: str = Field(..., description="要执行的系统命令（通过系统 shell 执行）")
    timeout: int = Field(default=30, description="命令超时时间（秒）")


class ShellTool(Tool):
    """执行本地系统命令。"""

    name = "run_shell"
    description = "在本地系统 shell 中执行命令，返回标准输出与标准错误。请谨慎使用。"
    args_schema = ShellArgs

    def run(self, args: ShellArgs) -> str:
        try:
            if sys.platform == "win32":
                # Windows 上通过 PowerShell 的 -EncodedCommand 执行命令
                # 将命令以 UTF-16LE 编码后 Base64 编码传入，避免双引号与转义符被 shell 二次解析
                encoded = base64.b64encode(args.command.encode("utf-16-le")).decode("ascii")
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-EncodedCommand", encoded],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=args.timeout,
                )
            else:
                completed = subprocess.run(
                    args.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=args.timeout,
                )
        except subprocess.TimeoutExpired:
            return f"错误: 命令执行超时（超过 {args.timeout} 秒）: {args.command}"

        parts: list[str] = [f"退出码: {completed.returncode}"]
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stdout:
            parts.append(f"标准输出:\n{stdout}")
        if stderr:
            parts.append(f"标准错误:\n{stderr}")
        if not stdout and not stderr:
            parts.append("（无输出）")

        result = "\n".join(parts)
        if len(result) > _MAX_OUTPUT_CHARS:
            result = result[:_MAX_OUTPUT_CHARS] + "\n...（输出过长，已截断）"
        return result
