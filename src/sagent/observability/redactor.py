"""统一递归脱敏器。

在所有 JSON 日志、Span 属性、Span 事件和本地导出前使用同一脱敏器，
递归处理敏感字段名和值。脱敏默认启用且不可被内容采集开关绕过。
"""

from __future__ import annotations

import re
from typing import Any

# 统一掩码
_REDACTED = "***REDACTED***"

# 敏感键名（小写匹配，包含即命中）
# 注意：不匹配 "tokens"（复数），避免误杀 input_tokens/output_tokens 等指标字段
_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password", "passwd", "pwd",
    "api_key", "apikey", "api-key",
    "secret",
    "authorization",
    "credential",
    "private_key", "privatekey",
    "access_key", "accesskey",
    "refresh_token", "auth_token",
    "bearer",
)

# 敏感值正则模式
# Bearer token: Bearer xxx
_BEARER_RE = re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+', re.IGNORECASE)
# JWT: eyJxxx.eyJxxx.xxx
_JWT_RE = re.compile(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
# PEM 私钥
_PEM_RE = re.compile(
    r'-----BEGIN\s+(?:RSA\s+)?(?:PRIVATE|PUBLIC)\s+KEY-----[\s\S]*?-----END\s+(?:RSA\s+)?(?:PRIVATE|PUBLIC)\s+KEY-----'
)
# 邮箱
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
# 中国手机号
_PHONE_RE = re.compile(r'1[3-9]\d{9}')
# 银行卡号（16-19 位连续数字，前后非数字边界）
_BANK_CARD_RE = re.compile(r'(?<!\d)\d{16,19}(?!\d)')
# URL 凭证：scheme://user:password@host
_URL_CRED_RE = re.compile(r'://([^:/\s]+):([^@/\s]+)@')
# URL 查询参数中的敏感字段
_URL_QUERY_SENSITIVE_RE = re.compile(
    r'([?&](?:api_key|apikey|token|secret|password|access_key|refresh_token|authorization)=[^&]*)',
    re.IGNORECASE
)

# 所有值正则模式列表
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _BEARER_RE, _JWT_RE, _PEM_RE, _EMAIL_RE, _PHONE_RE, _BANK_CARD_RE, _URL_CRED_RE, _URL_QUERY_SENSITIVE_RE,
)


def _is_sensitive_key(key: str) -> bool:
    """判断键名是否敏感（小写包含匹配）。"""
    key_lower = key.lower()
    return any(sub in key_lower for sub in _SENSITIVE_KEY_SUBSTRINGS)


def _redact_string(value: str) -> str:
    """对字符串值应用所有值正则模式进行脱敏。"""
    result = value
    for pattern in _VALUE_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


def redact(data: Any) -> Any:
    """递归脱敏 dict、list、str 中的敏感内容。

    对 dict 的敏感键，整体替换值为掩码。
    对所有字符串值，应用正则模式脱敏内嵌的凭证、PII 等。
    """
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            if _is_sensitive_key(str(key)):
                result[key] = _REDACTED
            else:
                result[key] = redact(value)
        return result
    if isinstance(data, list):
        return [redact(item) for item in data]
    if isinstance(data, str):
        return _redact_string(data)
    return data
