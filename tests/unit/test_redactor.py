"""统一递归脱敏器单元测试。

覆盖敏感键名匹配、值正则模式（Bearer/JWT/PEM/邮箱/手机号/银行卡/
URL 凭证/URL 查询参数）、嵌套结构、非敏感值透传与组合场景。
"""

from __future__ import annotations

from sagent.observability.redactor import redact

_REDACTED = "***REDACTED***"


# ---------- 敏感键名 ----------


def test_redact_password_key():
    """password 键的值被脱敏。"""
    result = redact({"password": "secret123"})
    assert result["password"] == _REDACTED
    assert "secret123" not in str(result)


def test_redact_api_key_key():
    """api_key 键的值被脱敏。"""
    result = redact({"api_key": "sk-abc123"})
    assert result["api_key"] == _REDACTED
    assert "sk-abc123" not in str(result)


def test_redact_secret_key():
    """secret 键的值被脱敏。"""
    result = redact({"secret": "my_secret"})
    assert result["secret"] == _REDACTED


def test_redact_authorization_key():
    """authorization 键的值被脱敏。"""
    result = redact({"authorization": "Bearer xyz"})
    assert result["authorization"] == _REDACTED


def test_redact_credential_key():
    """credential 键的值被脱敏。"""
    result = redact({"credential": "user:pass"})
    assert result["credential"] == _REDACTED


def test_redact_private_key_key():
    """private_key 键的值被脱敏。"""
    result = redact({"private_key": "keydata"})
    assert result["private_key"] == _REDACTED


def test_redact_access_key_key():
    """access_key 键的值被脱敏。"""
    result = redact({"access_key": "AKIA1234"})
    assert result["access_key"] == _REDACTED


def test_redact_refresh_token_key():
    """refresh_token 键的值被脱敏。"""
    result = redact({"refresh_token": "rt_abc"})
    assert result["refresh_token"] == _REDACTED


def test_redact_auth_token_key():
    """auth_token 键的值被脱敏。"""
    result = redact({"auth_token": "at_xyz"})
    assert result["auth_token"] == _REDACTED


def test_redact_bearer_key():
    """bearer 键的值被脱敏。"""
    result = redact({"bearer": "token123"})
    assert result["bearer"] == _REDACTED


def test_redact_passwd_key():
    """passwd 键的值被脱敏。"""
    result = redact({"passwd": "p"})
    assert result["passwd"] == _REDACTED


def test_redact_pwd_key():
    """pwd 键的值被脱敏。"""
    result = redact({"pwd": "p"})
    assert result["pwd"] == _REDACTED


def test_redact_apikey_key():
    """apikey 键的值被脱敏。"""
    result = redact({"apikey": "k"})
    assert result["apikey"] == _REDACTED


def test_redact_api_key_with_hyphen_key():
    """api-key 键的值被脱敏。"""
    result = redact({"api-key": "k"})
    assert result["api-key"] == _REDACTED


def test_redact_privatekey_key():
    """privatekey 键的值被脱敏。"""
    result = redact({"privatekey": "k"})
    assert result["privatekey"] == _REDACTED


def test_redact_accesskey_key():
    """accesskey 键的值被脱敏。"""
    result = redact({"accesskey": "k"})
    assert result["accesskey"] == _REDACTED


# ---------- 键名子串匹配（大小写不敏感） ----------


def test_redact_key_substring_user_password():
    """user_password 匹配 password 子串。"""
    result = redact({"user_password": "secret"})
    assert result["user_password"] == _REDACTED


def test_redact_key_substring_x_api_key():
    """X_API_KEY 匹配 api_key 子串（大小写不敏感）。"""
    result = redact({"X_API_KEY": "sk-xxx"})
    assert result["X_API_KEY"] == _REDACTED


def test_redact_key_substring_client_secret():
    """client_secret 匹配 secret 子串。"""
    result = redact({"client_secret": "s"})
    assert result["client_secret"] == _REDACTED


# ---------- 不误杀 tokens ----------


def test_redact_does_not_redact_tokens_key():
    """input_tokens 键不应被脱敏（对指标字段无影响）。"""
    result = redact({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["total_tokens"] == 150


# ---------- 值正则模式 ----------


def test_redact_bearer_token_value():
    """字符串中的 Bearer token 被脱敏。"""
    result = redact("Authorization: Bearer abc123")
    assert "abc123" not in result
    assert _REDACTED in result


def test_redact_jwt_value():
    """JWT token 被脱敏。"""
    jwt = "eyJxxx.eyJyyy.zzz"
    result = redact(f"token={jwt}")
    assert jwt not in result
    assert _REDACTED in result


def test_redact_pem_private_key_value():
    """PEM 私钥块被脱敏。"""
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEAxyz123\n"
        "-----END RSA PRIVATE KEY-----"
    )
    result = redact(pem)
    assert "MIIEpAIBAAKCAQEAxyz123" not in result
    assert _REDACTED in result


def test_redact_email_value():
    """邮箱地址被脱敏。"""
    result = redact("联系我 user@example.com 谢谢")
    assert "user@example.com" not in result
    assert _REDACTED in result


def test_redact_phone_value():
    """中国手机号被脱敏。"""
    result = redact("电话 13812345678 备用")
    assert "13812345678" not in result
    assert _REDACTED in result


def test_redact_bank_card_value():
    """16-19 位银行卡号被脱敏。"""
    card = "6222021234567890123"
    result = redact(f"卡号 {card}")
    assert card not in result
    assert _REDACTED in result


def test_redact_url_credentials():
    """URL 中的 user:password@ 凭证被脱敏。"""
    result = redact("https://user:pass@host.com/path")
    assert "pass" not in result
    assert _REDACTED in result


def test_redact_url_query_sensitive_params():
    """URL 查询参数中的敏感字段被脱敏。"""
    result = redact("https://api.example.com?api_key=secret123&page=1")
    assert "secret123" not in result
    assert _REDACTED in result


# ---------- 嵌套结构 ----------


def test_redact_nested_dict():
    """嵌套 dict 中的敏感值被脱敏。"""
    result = redact({"config": {"password": "secret", "model": "gpt-4"}})
    assert result["config"]["password"] == _REDACTED
    assert result["config"]["model"] == "gpt-4"


def test_redact_nested_list():
    """嵌套 list 中的敏感值被脱敏。"""
    result = redact([{"api_key": "sk-xxx"}, {"name": "ok"}])
    assert result[0]["api_key"] == _REDACTED
    assert result[1]["name"] == "ok"


def test_redact_deeply_nested():
    """深层嵌套结构中的敏感值被脱敏。"""
    result = redact({
        "level1": {
            "level2": [
                {"secret": "deep_secret"}
            ]
        }
    })
    assert result["level1"]["level2"][0]["secret"] == _REDACTED


# ---------- 非敏感值透传 ----------


def test_redact_non_sensitive_dict():
    """非敏感键值的 dict 原样返回。"""
    data = {"model": "gpt-4", "temperature": 0.7}
    result = redact(data)
    assert result == data


def test_redact_non_string_values():
    """非字符串值（int/float/bool）原样返回。"""
    data = {"count": 42, "ratio": 0.5, "flag": True}
    result = redact(data)
    assert result == data


def test_redact_none_value():
    """None 值原样返回。"""
    result = redact({"key": None})
    assert result["key"] is None


def test_redact_plain_string_no_sensitive():
    """不含敏感内容的字符串原样返回。"""
    text = "这是一段普通文本，没有敏感信息。"
    assert redact(text) == text


# ---------- 组合场景 ----------


def test_redact_combined_sensitive_and_non_sensitive():
    """混合敏感与非敏感键的 dict。"""
    data = {
        "model": "gpt-4",
        "api_key": "sk-abc123",
        "temperature": 0.7,
        "password": "hunter2",
    }
    result = redact(data)
    assert result["model"] == "gpt-4"
    assert result["temperature"] == 0.7
    assert result["api_key"] == _REDACTED
    assert result["password"] == _REDACTED


def test_redact_string_multiple_patterns():
    """单个字符串中包含多个敏感模式，全部被脱敏。"""
    text = "Bearer token1 and user@host.com phone 13812345678"
    result = redact(text)
    assert "token1" not in result
    assert "user@host.com" not in result
    assert "13812345678" not in result


def test_redact_assert_no_original_sensitive_in_output():
    """所有敏感值在输出中均不存在。"""
    sensitive_values = [
        "super_secret_password",
        "sk-abc123key",
        "Bearer xyz789",
        "user@example.com",
        "13812345678",
    ]
    data = {
        "password": sensitive_values[0],
        "api_key": sensitive_values[1],
        "auth_header": sensitive_values[2],
        "contact": sensitive_values[3],
        "phone": sensitive_values[4],
    }
    result = redact(data)
    output_str = str(result)
    for val in sensitive_values:
        assert val not in output_str, f"敏感值未被脱敏: {val}"


# ---------- 边界情况 ----------


def test_redact_empty_dict():
    """空 dict 返回空 dict。"""
    assert redact({}) == {}


def test_redact_empty_list():
    """空 list 返回空 list。"""
    assert redact([]) == []


def test_redact_empty_string():
    """空字符串原样返回。"""
    assert redact("") == ""


def test_redact_int_passthrough():
    """整数原样返回。"""
    assert redact(42) == 42


def test_redact_none_passthrough():
    """None 原样返回。"""
    assert redact(None) is None
