"""记忆安全扫描器。

对写入长期记忆的内容执行安全扫描，提供四类检测：
- 不可见字符净化：移除 C0/C1 控制字符、零宽字符、双向控制符等不可见字符。
- 凭证检测：识别 PEM 私钥、通用凭证赋值、OpenAI/AWS/GitHub/Slack/Google 密钥与 Bearer 令牌。
- Shell 威胁检测：识别敏感文件路径引用、危险删除、网络管道执行与 eval 命令。
- Prompt 注入检测：识别中英文指令覆盖、角色重置等提示词注入模式。

scan 主流程先净化不可见字符，再依次执行三个拒绝类检测，任一命中即拦截写入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..observability import get_logger

logger = get_logger(__name__)

# 不可见字符预编译正则：C0 控制字符（保留 \t \n \r）、DEL 与 C1 控制字符、
# 零宽字符、双向控制符
_INVISIBLE_RE = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f'
    r'\u200b\u200c\u200d\u2060\ufeff'
    r'\u202a-\u202e\u2066-\u2069]'
)

# 凭证检测模式列表：(预编译正则, 命中描述)
_CREDENTIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
        '检测到 PEM 私钥',
    ),
    (
        re.compile(
            r'(?:api_key|apikey|secret|password|passwd|token|access_key|client_secret)\s*[:=]\s*["\']?[A-Za-z0-9_\-]{16,}["\']?'
        ),
        '检测到凭证赋值',
    ),
    (
        re.compile(r'sk-[A-Za-z0-9]{20,}'),
        '检测到 OpenAI API Key',
    ),
    (
        re.compile(r'AKIA[0-9A-Z]{16}'),
        '检测到 AWS Access Key',
    ),
    (
        re.compile(r'gh[ps]_[A-Za-z0-9]{36,}'),
        '检测到 GitHub Token',
    ),
    (
        re.compile(r'xox[baprs]-[A-Za-z0-9\-]{10,}'),
        '检测到 Slack Token',
    ),
    (
        re.compile(r'AIza[0-9A-Za-z_\-]{35}'),
        '检测到 Google API Key',
    ),
    (
        re.compile(r'Bearer\s+[A-Za-z0-9_\-\.]{20,}'),
        '检测到 Bearer Token',
    ),
]

# Shell 威胁检测模式列表（忽略大小写）
_SHELL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'authorized_keys', re.IGNORECASE), '检测到 authorized_keys 引用'),
    (re.compile(r'/etc/passwd', re.IGNORECASE), '检测到 /etc/passwd 引用'),
    (re.compile(r'/etc/shadow', re.IGNORECASE), '检测到 /etc/shadow 引用'),
    (re.compile(r'/etc/sudoers', re.IGNORECASE), '检测到 /etc/sudoers 引用'),
    (re.compile(r'crontab', re.IGNORECASE), '检测到 crontab 引用'),
    (re.compile(r'/dev/tcp/', re.IGNORECASE), '检测到 /dev/tcp/ 网络管道'),
    (re.compile(r'/dev/udp/', re.IGNORECASE), '检测到 /dev/udp/ 网络管道'),
    (re.compile(r'rm\s+-rf\s+/', re.IGNORECASE), '检测到 rm -rf / 危险删除'),
    (re.compile(r'curl.*\|\s*(?:sh|bash)', re.IGNORECASE), '检测到 curl 管道执行'),
    (re.compile(r'wget.*\|\s*(?:sh|bash)', re.IGNORECASE), '检测到 wget 管道执行'),
    (
        re.compile(r'base64\s+-d.*\|\s*(?:sh|bash)', re.IGNORECASE),
        '检测到 base64 解码管道执行',
    ),
    (re.compile(r'eval\s', re.IGNORECASE), '检测到 eval 命令'),
]

# Prompt 注入检测模式列表（忽略大小写）
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 英文模式
    (
        re.compile(r'ignore\s+(?:all\s+|previous\s+)?(?:instructions?|prompts?)', re.IGNORECASE),
        '检测到忽略指令注入',
    ),
    (
        re.compile(r'disregard\s+(?:the\s+|previous\s+)?instructions?', re.IGNORECASE),
        '检测到无视指令注入',
    ),
    (
        re.compile(r'forget\s+(?:all\s+|previous\s+)?instructions?', re.IGNORECASE),
        '检测到遗忘指令注入',
    ),
    (
        re.compile(r'override\s+(?:previous\s+)?(?:instructions?|prompt)', re.IGNORECASE),
        '检测到覆盖指令注入',
    ),
    (
        re.compile(r'you\s+are\s+now\s+a', re.IGNORECASE),
        '检测到角色重置注入',
    ),
    (
        re.compile(r'new\s+instructions?\s*:', re.IGNORECASE),
        '检测到新指令注入',
    ),
    (
        re.compile(r'system\s*:', re.IGNORECASE),
        '检测到系统提示注入',
    ),
    (
        re.compile(r'act\s+as\s+if', re.IGNORECASE),
        '检测到角色扮演注入',
    ),
    # 中文模式
    (
        re.compile(r'忽略(?:以上|之前|前面|所有)(?:的)?(?:指令|提示|规则|内容)'),
        '检测到中文忽略指令注入',
    ),
    (
        re.compile(r'你现在是'),
        '检测到中文角色重置注入',
    ),
    (
        re.compile(r'新(?:的)?指令'),
        '检测到中文新指令注入',
    ),
    (
        re.compile(r'系统提示'),
        '检测到中文系统提示注入',
    ),
    (
        re.compile(r'覆盖之前的'),
        '检测到中文覆盖指令注入',
    ),
    (
        re.compile(r'假装你是'),
        '检测到中文假装角色注入',
    ),
    (
        re.compile(r'扮演'),
        '检测到中文扮演注入',
    ),
]


@dataclass
class ScanResult:
    """安全扫描结果。"""

    sanitized: str  # 净化后内容
    blocked: bool  # 是否被拦截
    reasons: list[str] = field(default_factory=list)  # 命中原因列表
    categories: list[str] = field(default_factory=list)  # 命中类别列表


class MemorySecurityScanner:
    """记忆安全扫描器。

    四类规则全部启用、不可关闭。内部不存任何可变状态，方法都是纯函数式的。
    scan 方法先净化不可见字符，再依次执行凭证、Shell、Prompt 注入三个拒绝类检测，
    任一命中即标记拦截。
    """

    def __init__(self) -> None:
        """初始化扫描器，无参数，四类规则全部启用。"""
        # 无可变状态，所有规则以模块级预编译正则实现
        return

    def _sanitize_invisible(self, content: str) -> tuple[str, int]:
        """移除不可见控制字符。

        清除 C0 控制字符（保留 \t \n \r）、DEL 与 C1 控制字符、零宽字符与双向控制符。

        参数:
            content: 待净化的文本。

        返回:
            元组 (净化后文本, 移除字符数)。
        """
        sanitized, removed = _INVISIBLE_RE.subn('', content)
        return sanitized, removed

    def _detect_credentials(self, content: str) -> list[str]:
        """检测凭证泄露模式。

        参数:
            content: 待检测文本（已净化不可见字符）。

        返回:
            命中原因列表，空列表表示未命中。
        """
        reasons: list[str] = []
        for pattern, description in _CREDENTIAL_PATTERNS:
            if pattern.search(content):
                reasons.append(description)
        return reasons

    def _detect_shell_threats(self, content: str) -> list[str]:
        """检测 Shell 威胁模式（忽略大小写）。

        参数:
            content: 待检测文本（已净化不可见字符）。

        返回:
            命中原因列表，空列表表示未命中。
        """
        reasons: list[str] = []
        for pattern, description in _SHELL_PATTERNS:
            if pattern.search(content):
                reasons.append(description)
        return reasons

    def _detect_prompt_injection(self, content: str) -> list[str]:
        """检测 Prompt 注入模式（忽略大小写，含中英文）。

        参数:
            content: 待检测文本（已净化不可见字符）。

        返回:
            命中原因列表，空列表表示未命中。
        """
        reasons: list[str] = []
        for pattern, description in _INJECTION_PATTERNS:
            if pattern.search(content):
                reasons.append(description)
        return reasons

    def scan(self, content: str) -> ScanResult:
        """对内容执行安全扫描。

        先净化不可见字符，再在净化后内容上依次执行凭证、Shell、Prompt 注入检测，
        收集所有命中原因与类别，任一拒绝类命中则标记拦截。

        参数:
            content: 待扫描的原始内容。

        返回:
            ScanResult，包含净化后内容、是否拦截、命中原因与类别。
        """
        sanitized, _ = self._sanitize_invisible(content)
        reasons: list[str] = []
        categories: list[str] = []

        cred_reasons = self._detect_credentials(sanitized)
        if cred_reasons:
            reasons.extend(cred_reasons)
            categories.append('credential_leak')

        shell_reasons = self._detect_shell_threats(sanitized)
        if shell_reasons:
            reasons.extend(shell_reasons)
            categories.append('shell_threat')

        injection_reasons = self._detect_prompt_injection(sanitized)
        if injection_reasons:
            reasons.extend(injection_reasons)
            categories.append('prompt_injection')

        blocked = bool(reasons)
        return ScanResult(
            sanitized=sanitized,
            blocked=blocked,
            reasons=reasons,
            categories=categories,
        )
