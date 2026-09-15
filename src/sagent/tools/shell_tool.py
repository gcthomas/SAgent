"""内置工具：Shell / 命令执行。

在本地执行系统命令并返回标准输出与标准错误。包含超时处理，避免长时间阻塞。
注意：该工具会执行任意命令，存在安全风险，请在可信环境中使用。
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field

from .base import Tool

# 输出返回的最大字符数
_MAX_OUTPUT_CHARS = 10000

# PowerShell 前置命令：抑制进度流噪音，并强制重定向输出使用 UTF-8，避免中文乱码
_PS_PREAMBLE = "$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"

# CLIXML 文本前缀，Windows PowerShell 5.1 在重定向输出时会把非成功流序列化为 CLIXML
_CLIXML_PREFIX = "#< CLIXML"


def _unescape_clixml(text: str) -> str:
    """还原 CLIXML 中的 _xHHHH_ 转义为对应字符。"""
    return re.sub(
        r"_x([0-9A-Fa-f]{4})_",
        lambda match: chr(int(match.group(1), 16)),
        text,
    )


def _decode_clixml(stderr: str) -> str:
    """将 Windows PowerShell 5.1 序列化的 CLIXML 错误流还原为纯文本。

    非 CLIXML 输入或解析失败时原样返回，避免影响普通标准错误输出。
    """
    if not stderr.startswith(_CLIXML_PREFIX):
        return stderr
    try:
        root = ET.fromstring(stderr[len(_CLIXML_PREFIX):])
    except ET.ParseError:
        return stderr
    messages = [
        _unescape_clixml(node.text)
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "S" and node.text
    ]
    decoded = "".join(messages).strip()
    # 前置命令会出现在错误定位信息中，去掉首处以保持错误信息干净
    return decoded.replace(_PS_PREAMBLE, "", 1)


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
                # 先前置抑制进度流并设置 UTF-8 输出编码，避免进度噪音与中文乱码；再将命令
                # 以 UTF-16LE 编码后 Base64 编码传入，避免双引号与转义符被 shell 二次解析
                full_command = _PS_PREAMBLE + args.command
                encoded = base64.b64encode(full_command.encode("utf-16-le")).decode("ascii")
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
        stderr = _decode_clixml((completed.stderr or "").strip())
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
