"""权限策略内核。

定义工具调用权限的三态决策（allow / ask / deny）与纯逻辑决策器 PermissionPolicy：
- allow：自动放行
- ask：需用户批准后执行
- deny：直接拒绝

决策匹配按"用户配置规则优先（同一请求命中多条规则时最后一条生效，last-match-wins）、
内置默认规则兜底"，当 run_shell 的命令文本命中跨平台危险命令清单
（Linux shell 与 Windows PowerShell）时直接拒绝。
本模块为纯逻辑组件，无 I/O、无配置依赖，可独立单元测试。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    """权限决策三态：放行 / 需确认 / 拒绝。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionRule:
    """权限规则：工具名 + 可选参数字符串模式 + 决策。

    pattern 为 None 时匹配该工具的任意参数；非 None 时按 fnmatch 通配
    （* ? [seq]）与参数文本做小写化匹配。
    """

    tool: str
    pattern: str | None = None
    decision: Decision = Decision.ASK


@dataclass
class PermissionRequest:
    """权限请求：待授权的工具名与参数字典。"""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)


def args_to_text(args: dict[str, Any]) -> str:
    """将参数字典按值的顺序拼接为用于模式匹配的文本。

    参数:
        args: 工具参数字典。

    返回:
        各参数值以单个空格连接后的字符串（如 {"command": "git push -f"} -> "git push -f"）。
    """
    return ' '.join(str(value) for value in args.values())


# 权限匹配只关注的工具主参数字段：其余参数（如 run_shell 的 timeout、
# write_file 的 content）不参与模式匹配与 always-allow 白名单键，避免干扰匹配。
_TOOL_PRIMARY_ARGS: dict[str, tuple[str, ...]] = {
    'run_shell': ('command',),
    'write_file': ('path',),
}


def canonical_permission_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """提取工具参数中参与权限匹配的主参数，过滤无关字段。

    run_shell 只保留 command（排除 timeout 等），write_file 只保留 path
    （排除 content 正文），避免超时秒数或文件内容混入匹配文本与
    always-allow 白名单键；未登记的工具原样返回。

    参数:
        tool: 工具名称。
        args: 原始工具参数字典。

    返回:
        只含主参数的字典（原样拷贝，不修改入参）。
    """
    primary = _TOOL_PRIMARY_ARGS.get(tool)
    if primary is None:
        return dict(args)
    return {key: args[key] for key in primary if key in args}


def parse_rule(text: str, decision: Decision) -> PermissionRule:
    """解析配置中的规则字符串为 PermissionRule。

    格式为 "工具名" 或 "工具名:参数模式"（以第一个冒号分隔，兼容模式中
    含盘符路径如 "write_file:C:\\tmp\\*"）。strip 后为空时抛 ValueError。

    参数:
        text: 规则字符串，如 "run_shell"、"run_shell:git push*"。
        decision: 该规则的决策。

    返回:
        解析后的 PermissionRule。
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError('权限规则不能为空')
    tool, sep, pattern = stripped.partition(':')
    tool = tool.strip()
    if not tool:
        raise ValueError('权限规则缺少工具名')
    if not sep:
        return PermissionRule(tool=tool, pattern=None, decision=decision)
    pattern = pattern.strip()
    return PermissionRule(tool=tool, pattern=pattern or None, decision=decision)


# 跨平台危险命令清单：(预编译正则, 命中描述)，全部忽略大小写，命中即 deny。
# 覆盖通用 / 跨平台、Linux / 类 Unix 与 Windows / PowerShell 三类危险命令。
DANGEROUS_SHELL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 通用 / 跨平台
    (re.compile(r'git\s+reset\s+--hard', re.IGNORECASE), '检测到 git reset --hard 强制重置'),
    (re.compile(r'git\s+push\s+[^;|&]*(-f\b|--force\b)', re.IGNORECASE), '检测到 git push 强制推送'),
    (re.compile(r'\bDROP\s+TABLE\b', re.IGNORECASE), '检测到 DROP TABLE 破坏性 SQL'),
    (re.compile(r'\bDELETE\s+FROM\b', re.IGNORECASE), '检测到 DELETE FROM 破坏性 SQL'),
    (re.compile(r'\bTRUNCATE\s+TABLE\b', re.IGNORECASE), '检测到 TRUNCATE TABLE 破坏性 SQL'),
    (re.compile(r'chmod\s+[^;|&]*-R\s+777\s+/(?=\s|$)', re.IGNORECASE), '检测到全盘 chmod 777'),
    (re.compile(r'chown\s+[^;|&]*-R\b', re.IGNORECASE), '检测到递归 chown'),
    (re.compile(r'\bmv\s+/(?=\s|$)', re.IGNORECASE), '检测到移动系统根目录'),
    (re.compile(r'cp\s+[^;|&]*-r[^;|&]*/dev/null', re.IGNORECASE), '检测到复制到 /dev/null'),
    # Linux / 类 Unix
    (re.compile(r'rm\s+[^;|&]*-[^;|&]*[rf][^;|&]*\s+/(?=\s|$)', re.IGNORECASE), '检测到 rm 递归强制删除根路径'),
    (re.compile(r'rm\s+[^;|&]*-[rf]+\s+~(?=\s|$)', re.IGNORECASE), '检测到 rm 删除用户主目录'),
    (re.compile(r'rm\s+[^;|&]*-[rf]+\s+\.(?=\s|$)', re.IGNORECASE), '检测到 rm 删除当前目录'),
    (re.compile(r'\bmkfs(\.\w+)?\b', re.IGNORECASE), '检测到 mkfs 整盘格式化'),
    (re.compile(r'\bformat\s+(/dev/|[a-z]:)', re.IGNORECASE), '检测到 format 磁盘格式化'),
    (re.compile(r'\bdd\s+[^;|&]*if=/dev/(zero|urandom)', re.IGNORECASE), '检测到 dd 覆写磁盘'),
    (re.compile(r'\bshred\b', re.IGNORECASE), '检测到 shred 文件擦除'),
    (re.compile(r':\(\)\s*\{.*\|.*\}', re.IGNORECASE), '检测到 fork 炸弹'),
    (re.compile(r'curl[^;|&]*\|\s*(sh|bash)\b', re.IGNORECASE), '检测到 curl 管道执行'),
    (re.compile(r'wget[^;|&]*\|\s*(sh|bash)\b', re.IGNORECASE), '检测到 wget 管道执行'),
    (
        re.compile(r'base64\s+-d[^;|&]*\|\s*(sh|bash)\b', re.IGNORECASE),
        '检测到 base64 解码管道执行',
    ),
    (re.compile(r'\beval\s', re.IGNORECASE), '检测到 eval 动态执行'),
    (re.compile(r'\bkill\s+-9\s+-1\b', re.IGNORECASE), '检测到 kill 全部进程'),
    (re.compile(r'\bpkill\s+[^;|&]*-9\b', re.IGNORECASE), '检测到 pkill 强制杀进程'),
    (re.compile(r'\buseradd\b', re.IGNORECASE), '检测到 useradd 创建账户'),
    (re.compile(r'\busermod\s+[^;|&]*-u\s+0\b', re.IGNORECASE), '检测到 usermod 提权到 root'),
    (re.compile(r'\bpasswd\b', re.IGNORECASE), '检测到 passwd 修改密码'),
    (re.compile(r'/etc/passwd', re.IGNORECASE), '检测到 /etc/passwd 引用'),
    (re.compile(r'/etc/shadow', re.IGNORECASE), '检测到 /etc/shadow 引用'),
    (re.compile(r'/etc/sudoers', re.IGNORECASE), '检测到 /etc/sudoers 引用'),
    (re.compile(r'authorized_keys', re.IGNORECASE), '检测到 authorized_keys 引用'),
    (re.compile(r'\bcrontab\b', re.IGNORECASE), '检测到 crontab 计划任务操作'),
    (re.compile(r'/dev/tcp/', re.IGNORECASE), '检测到 /dev/tcp/ 网络管道'),
    (re.compile(r'/dev/udp/', re.IGNORECASE), '检测到 /dev/udp/ 网络管道'),
    (re.compile(r'sudo\s+rm\b', re.IGNORECASE), '检测到 sudo rm 提权删除'),
    (re.compile(r'sudo\s+shutdown\b', re.IGNORECASE), '检测到 sudo 提权关机'),
    # Windows / PowerShell
    (re.compile(r'\bdel\s+[^;|&]*/s\b', re.IGNORECASE), '检测到 del 递归删除'),
    (re.compile(r'\brd\s+[^;|&]*/s\b', re.IGNORECASE), '检测到 rd 递归删除'),
    (re.compile(r'\brmdir\s+[^;|&]*/s\b', re.IGNORECASE), '检测到 rmdir 递归删除'),
    (
        re.compile(r'Remove-Item\s+[^;|&]*(-Recurse[^;|&]*-Force|-Force[^;|&]*-Recurse)', re.IGNORECASE),
        '检测到 Remove-Item 递归强制删除',
    ),
    (re.compile(r'\bFormat-Volume\b', re.IGNORECASE), '检测到 Format-Volume 格式化卷'),
    (re.compile(r'\bClear-Disk\b', re.IGNORECASE), '检测到 Clear-Disk 清空磁盘'),
    (re.compile(r'\bInitialize-Disk\b', re.IGNORECASE), '检测到 Initialize-Disk 初始化磁盘'),
    (re.compile(r'\bdiskpart\b', re.IGNORECASE), '检测到 diskpart 磁盘分区操作'),
    (re.compile(r'\bcipher\s+[^;|&]*/w\b', re.IGNORECASE), '检测到 cipher 擦除卷空闲空间'),
    (re.compile(r'\bStop-Computer\b', re.IGNORECASE), '检测到 Stop-Computer 关机'),
    (re.compile(r'\bRestart-Computer\b', re.IGNORECASE), '检测到 Restart-Computer 重启'),
    (re.compile(r'\bshutdown\s+[^;|&]*/(s|r)\b', re.IGNORECASE), '检测到 shutdown 关机或重启'),
    (re.compile(r'\biex\b', re.IGNORECASE), '检测到 iex 动态执行'),
    (re.compile(r'\bInvoke-Expression\b', re.IGNORECASE), '检测到 Invoke-Expression 动态执行'),
    (
        re.compile(r'Invoke-WebRequest[^;|&]*-OutFile[^;|&]*Start-Process', re.IGNORECASE),
        '检测到下载并启动进程',
    ),
    (
        re.compile(r'powershell(\.exe)?\s+[^;|&]*(?<!\S)(-enc|-e|-EncodedCommand)\b', re.IGNORECASE),
        '检测到 powershell 编码命令执行',
    ),
    (
        re.compile(r'powershell(\.exe)?\s+[^;|&]*(?<!\S)-c\b[^;|&]*DownloadString', re.IGNORECASE),
        '检测到 powershell 下载执行',
    ),
    (
        re.compile(r'cmd(\.exe)?\s+/c\s+[^;|&]*(DownloadString|iex\b|Invoke-Expression)', re.IGNORECASE),
        '检测到 cmd 下载并执行',
    ),
    (re.compile(r'\bmshta\b', re.IGNORECASE), '检测到 mshta 执行远程脚本'),
    (re.compile(r'\bregsvr32\b', re.IGNORECASE), '检测到 regsvr32 注册执行'),
    (re.compile(r'\bCertUtil\s+[^;|&]*-urlcache', re.IGNORECASE), '检测到 CertUtil 下载'),
    (re.compile(r'\bbitsadmin\b', re.IGNORECASE), '检测到 bitsadmin 传输执行'),
    (re.compile(r'\breg\s+add\s+[^;|&]*/f\b', re.IGNORECASE), '检测到 reg add 强制写注册表'),
    (re.compile(r'\breg\s+delete\b', re.IGNORECASE), '检测到 reg delete 删注册表'),
    (re.compile(r'\bsc\s+(stop|delete)\b', re.IGNORECASE), '检测到 sc 服务停止或删除'),
    (re.compile(r'net\s+user\s+[^;|&]*/add\b', re.IGNORECASE), '检测到 net user 创建账户'),
    (
        re.compile(r'net\s+localgroup\s+administrators\s+[^;|&]*/add\b', re.IGNORECASE),
        '检测到 net localgroup 提权加组',
    ),
    (
        re.compile(r'Add-MpPreference\s+[^;|&]*-DisableRealtimeMonitoring', re.IGNORECASE),
        '检测到关闭 Defender 实时防护',
    ),
    (
        re.compile(r'Set-MpPreference\s+[^;|&]*-DisableRealtimeMonitoring', re.IGNORECASE),
        '检测到关闭 Defender 实时防护',
    ),
    (re.compile(r'\btaskkill\s+[^;|&]*/f\b', re.IGNORECASE), '检测到 taskkill 强制杀进程'),
    (re.compile(r'\bStop-Process\s+[^;|&]*-Force\b', re.IGNORECASE), '检测到 Stop-Process 强制杀进程'),
    (re.compile(r'\bntdsutil\b', re.IGNORECASE), '检测到 ntdsutil 目录服务操作'),
    (re.compile(r'\bvssadmin\s+delete\s+shadows', re.IGNORECASE), '检测到删除卷影副本'),
    (re.compile(r'\bwmic\s+process\s+call\s+create\b', re.IGNORECASE), '检测到 wmic 创建进程'),
]


class PermissionPolicy:
    """权限策略决策器（纯逻辑，无 I/O）。

    先按 last-match-wins 匹配用户规则，未命中任何用户规则时回退内置默认：
    - run_shell 命令文本命中 DANGEROUS_SHELL_PATTERNS -> deny
    - read_file / add_memory / replace_memory / remove_memory -> allow
    - 工具名以 mcp_ 开头 -> allow
    - 其余（含 write_file、run_shell、未知工具）-> ask
    """

    def __init__(self, user_rules: list[PermissionRule] | None = None) -> None:
        """初始化策略。

        参数:
            user_rules: 用户配置规则列表（可为空），先于内置默认匹配。
        """
        self._user_rules: list[PermissionRule] = list(user_rules) if user_rules else []

    def decide(self, request: PermissionRequest) -> Decision:
        """对权限请求输出三态决策。

        参数:
            request: 权限请求（工具名 + 参数）。

        返回:
            Decision.ALLOW / Decision.ASK / Decision.DENY。
        """
        text = args_to_text(request.args)

        # 第一步：匹配用户规则，同一请求命中多条规则时最后一条生效（last-match-wins），
        # 用户规则可覆盖内置默认（包括用 allow 覆盖内置 deny）
        matched: Decision | None = None
        for rule in self._user_rules:
            if rule.tool != request.tool:
                continue
            if rule.pattern is not None and not fnmatch.fnmatch(text.lower(), rule.pattern.lower()):
                continue
            matched = rule.decision
        if matched is not None:
            return matched

        # 第二步：内置默认规则兜底，先检查 run_shell 命令文本是否命中危险命令清单
        if request.tool == 'run_shell':
            for pattern, _description in DANGEROUS_SHELL_PATTERNS:
                if pattern.search(text):
                    return Decision.DENY
        if request.tool in ('read_file', 'add_memory', 'replace_memory', 'remove_memory'):
            return Decision.ALLOW
        if request.tool.startswith('mcp_'):
            return Decision.ALLOW
        return Decision.ASK


def build_default_policy(user_rules: list[PermissionRule] | None = None) -> PermissionPolicy:
    """构建默认权限策略（内置默认规则 + 可选用户规则覆盖）。

    参数:
        user_rules: 用户配置规则列表（可为 None 或空）。

    返回:
        PermissionPolicy 实例。
    """
    return PermissionPolicy(user_rules=user_rules)
