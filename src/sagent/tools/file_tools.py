"""内置工具：文件读写。

提供读取与写入本地文件的能力。写入时可选择覆盖或追加，并自动创建父目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .base import Tool

# 单次读取返回的最大字符数，避免超长内容撑爆上下文
_MAX_READ_CHARS = 20000


class ReadFileArgs(BaseModel):
    """读取文件参数。"""

    path: str = Field(..., description="要读取的文件路径")


class ReadFileTool(Tool):
    """读取本地文本文件内容。"""

    name = "read_file"
    description = "读取指定路径的本地文本文件内容（UTF-8 编码）。"
    args_schema = ReadFileArgs

    def run(self, args: ReadFileArgs) -> str:
        path = Path(args.path)
        if not path.exists():
            return f"错误: 文件不存在: {path}"
        if not path.is_file():
            return f"错误: 路径不是文件: {path}"
        text = path.read_text(encoding="utf-8")
        if len(text) > _MAX_READ_CHARS:
            return text[:_MAX_READ_CHARS] + f"\n...（内容过长，已截断，共 {len(text)} 字符）"
        return text


class WriteFileArgs(BaseModel):
    """写入文件参数。"""

    path: str = Field(..., description="要写入的文件路径")
    content: str = Field(..., description="要写入的文本内容")
    mode: Literal["overwrite", "append"] = Field(
        default="overwrite", description="写入模式：overwrite 覆盖，append 追加"
    )


class WriteFileTool(Tool):
    """写入本地文本文件。"""

    name = "write_file"
    description = "将文本内容写入指定路径的文件（UTF-8 编码），支持覆盖或追加，自动创建父目录。"
    args_schema = WriteFileArgs

    def run(self, args: WriteFileArgs) -> str:
        path = Path(args.path)
        # 自动创建父目录
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if args.mode == "append" else "w"
        with path.open(file_mode, encoding="utf-8") as f:
            f.write(args.content)
        return f"已{'追加' if args.mode == 'append' else '写入'}文件: {path}（{len(args.content)} 字符）"
