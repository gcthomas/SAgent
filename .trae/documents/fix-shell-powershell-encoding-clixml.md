# 修复 run_shell 在 Windows 上的中文乱码与 CLIXML 噪音

## 概述

`run_shell` 在 Windows 上通过 `powershell -NoProfile -EncodedCommand` 执行命令，存在两个问题：

1. 未显式设置 PowerShell 的输出编码，在系统代码页为 GBK（CP936）的机器上，中文输出会被按 UTF-8 解码而出现乱码。
2. Windows PowerShell 5.1 在输出被重定向时，会把非成功流序列化成 `#< CLIXML ...` 文本写到标准错误。用户日志中的噪音实际是模块首次加载的进度记录（`<Obj S="progress">`，文本为“正在准备首次使用模块。”），并非真实错误；此外真实的错误流也会被序列化成 CLIXML，两者都应从返回结果中去掉。

用户此前通过在 `MEMORY.md` 中给 LLM 加提示的方式要求其自行设置编码，但这种方式依赖模型行为，不稳定。本次改为在 `shell_tool.py` 代码层面直接修复。

## 现状分析

相关文件：

- [shell_tool.py](file:///d:/myworkspaces/SAgent/src/sagent/tools/shell_tool.py#L35-L75)：`ShellTool.run` 在 Windows 分支将命令用 UTF-16LE 编码后 Base64 编码，再传给 `powershell -EncodedCommand`；捕获输出后直接拼接 `标准输出` / `标准错误`。
- [test_tools.py](file:///d:/myworkspaces/SAgent/tests/unit/test_tools.py#L98-L113)：现有 `test_run_shell_with_quoted_args` 只覆盖 ASCII 输出，未覆盖中文与错误流。

已通过本机实测确认：

- 未设置编码时，PowerShell 重定向的 stdout 在本机为 UTF-8（但用户机器代码页为 GBK 时会是 GBK，导致 Python 按 UTF-8 解码乱码）。
- 错误流（如 `Write-Error`）在重定向时稳定输出 `#< CLIXML ...` 到标准错误，且 `2>&1` / `*>&1` 无法将其合并为纯文本。
- 进度流（如模块首次加载的 `正在准备首次使用模块。`）在重定向时也会输出为 `<Obj S="progress">` 的 CLIXML；前置 `$ProgressPreference='SilentlyContinue';` 可从源头完全抑制该进度记录，使 stderr 为空。
- 前置 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;` 可让重定向输出（含 CLIXML）统一为 UTF-8。
- CLIXML 是带默认命名空间的 XML，真实错误/警告文本位于 `<S>` 元素（如 `<S S="Error">`）内，特殊字符被编码为 `_xHHHH_`（如 `_x000A_` 表示换行）；进度记录位于 `<Obj S="progress">` 内，无 `<S>` 元素。

## 改动方案

### 1. 修改 [shell_tool.py](file:///d:/myworkspaces/SAgent/src/sagent/tools/shell_tool.py)

#### 1.1 新增导入

在现有 `import base64` / `import subprocess` / `import sys` 之后新增：

```python
import re
import xml.etree.ElementTree as ET
```

#### 1.2 新增常量

在 `_MAX_OUTPUT_CHARS` 之后新增：

```python
# PowerShell 前置命令：抑制进度流噪音，并强制重定向输出使用 UTF-8，避免中文乱码
_PS_PREAMBLE = "$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"

# CLIXML 文本前缀，Windows PowerShell 5.1 在重定向输出时会把非成功流序列化为 CLIXML
_CLIXML_PREFIX = "#< CLIXML"
```

#### 1.3 新增 CLIXML 解码辅助函数

在 `ShellArgs` 类定义之前（或 `_MAX_OUTPUT_CHARS` 常量之后）新增两个模块级函数：

```python
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
```

#### 1.4 修改 Windows 分支

将 [当前 Windows 分支](file:///d:/myworkspaces/SAgent/src/sagent/tools/shell_tool.py#L37-L48) 中构造命令与编码部分改为在用户命令前拼接 `_PS_PREAMBLE`：

```python
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
```

#### 1.5 修改标准错误后处理

将 [当前 stderr 计算处](file:///d:/myworkspaces/SAgent/src/sagent/tools/shell_tool.py#L63-L64) 改为先解码 CLIXML：

```python
stdout = (completed.stdout or "").strip()
stderr = _decode_clixml((completed.stderr or "").strip())
```

### 2. 补充测试 [test_tools.py](file:///d:/myworkspaces/SAgent/tests/unit/test_tools.py)

在 `test_run_shell_with_quoted_args` 之后新增三个用例（仅 Windows 生效）：

```python
def test_run_shell_chinese_output_decodes_utf8():
    """Windows 上 PowerShell 中文输出应正确显示，不出现乱码。"""
    if sys.platform != "win32":
        return
    tool = ShellTool()
    result = tool.run(ShellArgs(command="Write-Output 中文测试"))
    assert "退出码: 0" in result
    assert "中文测试" in result


def test_run_shell_error_strips_clixml():
    """Windows PowerShell 重定向错误流时不应返回 CLIXML 序列化文本。"""
    if sys.platform != "win32":
        return
    tool = ShellTool()
    result = tool.run(ShellArgs(command="Write-Error 出错了"))
    assert "#< CLIXML" not in result
    assert "出错了" in result


def test_run_shell_suppresses_progress_clixml():
    """Windows PowerShell 的进度记录不应以 CLIXML 噪音返回。"""
    if sys.platform != "win32":
        return
    tool = ShellTool()
    result = tool.run(ShellArgs(command="Write-Progress -Activity test; Write-Output done"))
    assert "#< CLIXML" not in result
    assert "done" in result
```

## 假设与决策

- 仅修改 Windows 分支；非 Windows 的 `shell=True` 分支保持原样。
- 保持使用 `powershell`（Windows PowerShell 5.1），不切换到 `pwsh`，避免引入新依赖与行为变化。
- `MEMORY.md` 中关于编码的提示属于运行时记忆文件，不在本次代码修改范围内；修复后可不再依赖该提示（用户可自行决定是否手动清理）。
- 不加 fallback：假定目标环境存在可写控制台（`[Console]::OutputEncoding` 可设置），与现有 `powershell` 调用前提一致。
- 进度流噪音通过前置 `$ProgressPreference='SilentlyContinue'` 从源头抑制；真实错误/警告流仍会被序列化为 CLIXML，由 `_decode_clixml` 提取 `<S>` 元素文本还原为纯文本。

## 验证步骤

1. 运行受影响范围测试：

```powershell
python -m pytest tests/unit/test_tools.py -v
```

2. 运行全量测试：

```powershell
python -m pytest
```

3. 手工验证（可选，Windows 下）：

```powershell
python -c "from sagent.tools.shell_tool import ShellTool, ShellArgs; print(ShellTool().run(ShellArgs(command='Write-Output 中文测试; Write-Error 出错了')))"
python -c "from sagent.tools.shell_tool import ShellTool, ShellArgs; print(ShellTool().run(ShellArgs(command='Write-Progress -Activity test; Write-Output done')))"
```

预期：输出包含正确的中文，且不再出现 `#< CLIXML` 与“正在准备首次使用模块。”等进度噪音。
