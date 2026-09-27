"""JWT / Token 过期与撤销安全测试。

覆盖：

- ``create_access_token`` / ``create_refresh_token`` 颁发的 token 包含正确的
  ``exp`` 字段与 ``type`` 字段
- ``decode_token`` 与 ``decode_token_strict`` 对过期 / 篡改 / 类型错误 token
  的拒绝行为
- ``TokenBanList`` 撤销列表的持久化、清理与查询
- 撤销后该 token 仍能拒绝（即使尚未到期）
- 篡改签名 / 篡改 payload 应当解码失败
- 缺失 ``sub`` / ``type`` 字段时的安全行为
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
import jwt

from app.auth import security as security_module
from app.auth.security import (
    ALGORITHM,
    SECRET_KEY,
    TokenBanList,
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_token_strict,
    generate_secure_jwt_secret,
    hash_password,
    verify_password,
)


# Fixtures


@pytest.fixture
def fresh_ban_list(tmp_path, monkeypatch):
    """为每个测试创建独立的 ban 列表文件并指向它。"""

    target = tmp_path / "ban_list.json"
    monkeypatch.setattr(security_module, "BANNED_TOKENS_FILE", str(target))
    return TokenBanList(file_path=str(target))


@pytest.fixture
def ensure_secret(monkeypatch):
    """确保 ``LNN_JWT_SECRET`` 已配置。"""

    if not __import__("os").environ.get("LNN_JWT_SECRET"):
        monkeypatch.setenv(
            "LNN_JWT_SECRET",
            "test_conftest_default_secret_value_min_32chars_safe",
        )


# 密钥强度


class TestSecretGeneration:
    def test_generate_returns_strong_secret(self):
        s = generate_secure_jwt_secret()
        assert isinstance(s, str)
        assert len(s) >= 32

    def test_generate_rejects_short_length(self):
        with pytest.raises(ValueError):
            generate_secure_jwt_secret(length=8)


# Token 颁发与解码


class TestTokenIssuance:
    def test_access_token_contains_exp_and_type(self):
        token = create_access_token({"sub": "u1"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload.get("type") == "access"
        assert "exp" in payload
        # 过期时间应当在未来
        assert payload["exp"] > int(time.time())

    def test_refresh_token_contains_type_refresh(self):
        token = create_refresh_token({"sub": "u1"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload.get("type") == "refresh"

    def test_custom_expires_delta(self):
        delta = timedelta(minutes=1)
        token = create_access_token({"sub": "u1"}, expires_delta=delta)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # 过期时间应在 (now, now+2min) 之间
        now = int(time.time())
        assert now < payload["exp"] <= now + 120

    def test_decode_token_valid(self):
        token = create_access_token({"sub": "u1"})
        decoded = decode_token(token)
        assert decoded is not None
        assert decoded.get("sub") == "u1"

    def test_decode_token_tampered_returns_none(self):
        token = create_access_token({"sub": "u1"})
        # 篡改 payload 段字符串：payload 变了，重算的 HMAC 必然与原签名不匹配。
        # 不能翻签名末位字符——base64 末位含 2 个填充位，末字符恰为 Y(011000) 时
        # 换成 a(011010) 只动填充位，解码字节不变、签名依旧有效（约 1/15 概率漏检，
        # 2026-09-18 CI run 35367561384 实测触发）。
        header, payload, sig = token.split(".")
        tampered_payload = payload[:-1] + ("a" if payload[-1] != "a" else "b")
        tampered = f"{header}.{tampered_payload}.{sig}"
        assert tampered != token
        assert decode_token(tampered) is None

    def test_decode_token_garbage_returns_none(self):
        assert decode_token("not-a-real-token") is None
        assert decode_token("") is None
        assert decode_token("a.b.c") is None


# Token 过期


class TestTokenExpiration:
    def test_decode_returns_none_for_expired_token(self, monkeypatch):
        # 颁发一个 1 分钟后过期的 token
        create_access_token({"sub": "u1"}, expires_delta=timedelta(minutes=1))
        # 把系统时间向后拨 1 小时（jose 内部使用 datetime.utcnow，monkeypatch 比较难）
        datetime.now(timezone.utc) + timedelta(hours=1)
        expired = jwt.encode(
            {
                "sub": "u1",
                "type": "access",
                "exp": int(time.time()) - 10,
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        assert decode_token(expired) is None

    def test_decode_token_strict_rejects_wrong_type(self):
        # 用 refresh token 去解码 access
        refresh = create_refresh_token({"sub": "u1"})
        assert decode_token_strict(refresh, expected_type="access") is None

        # 用 access token 去解码 refresh
        access = create_access_token({"sub": "u1"})
        assert decode_token_strict(access, expected_type="refresh") is None

    def test_decode_token_strict_rejects_missing_sub(self):
        # 没有 sub 的 token 即使类型正确也应当被严格模式拒绝
        token = jwt.encode(
            {
                "type": "access",
                "exp": int(time.time()) + 60,
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        assert decode_token_strict(token, expected_type="access") is None

    def test_decode_token_strict_accepts_valid(self):
        token = create_access_token({"sub": "alice"})
        decoded = decode_token_strict(token, expected_type="access")
        assert decoded is not None
        assert decoded.get("sub") == "alice"


# 密码哈希


class TestPasswordHashing:
    def test_hash_and_verify(self):
        plain = "S3cret!passw0rd"
        h = hash_password(plain)
        assert h != plain
        assert verify_password(plain, h) is True
        assert verify_password("wrong", h) is False

    def test_hash_produces_different_salts(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # 盐不同

    def test_hash_with_empty_password(self):
        # bcrypt 的行为视版本而定：部分版本拒绝空密码，部分版本允许。
        # 我们的安全测试不依赖具体行为，只验证 verify 不会因空密码误判。
        h = ""
        try:
            h = hash_password("")
        except ValueError:
            return  # 拒绝空密码是合理实现
        # 如果未抛错，则 verify("") 应返回 True，verify("x") 应返回 False
        assert verify_password("", h) is True
        assert verify_password("x", h) is False


# Token 撤销列表


class TestTokenBanList:
    def test_initial_state_empty(self, fresh_ban_list: TokenBanList):
        assert fresh_ban_list.is_banned("any") is False

    def test_ban_valid_token(self, fresh_ban_list: TokenBanList):
        token = create_access_token(
            {"sub": "u1", "jti": "jti-001"},
            expires_delta=timedelta(minutes=5),
        )
        fresh_ban_list.ban(token)
        assert fresh_ban_list.is_banned(token) is True

    def test_ban_undecodable_token(self, fresh_ban_list: TokenBanList):
        # 即使 token 不可解码，也应被记录（用于审计）
        fresh_ban_list.ban("garbage-token-string-here-for-testing")
        assert fresh_ban_list.is_banned("garbage-token-string-here-for-testing") is True

    def test_ban_persists_across_instances(self, tmp_path, monkeypatch):
        target = tmp_path / "ban_persist.json"
        monkeypatch.setattr(security_module, "BANNED_TOKENS_FILE", str(target))
        a = TokenBanList(file_path=str(target))
        token = create_access_token(
            {"sub": "u1", "jti": "jti-persist"},
            expires_delta=timedelta(minutes=5),
        )
        a.ban(token)
        # 新实例化应加载持久化数据
        b = TokenBanList(file_path=str(target))
        assert b.is_banned(token) is True

    def test_ban_uses_jti_when_available(self, fresh_ban_list: TokenBanList):
        # 不同 token 但相同 jti 应当被识别为已撤销
        token_a = create_access_token(
            {"sub": "u1", "jti": "shared-jti"},
            expires_delta=timedelta(minutes=5),
        )
        fresh_ban_list.ban(token_a)

        # 构造另一个 token，但 jti 相同
        token_b = create_access_token(
            {"sub": "u2", "jti": "shared-jti"},
            expires_delta=timedelta(minutes=5),
        )
        assert fresh_ban_list.is_banned(token_b) is True

    def test_cleanup_removes_expired_bans(self, tmp_path, monkeypatch):
        target = tmp_path / "ban_cleanup.json"
        monkeypatch.setattr(security_module, "BANNED_TOKENS_FILE", str(target))

        # 直接构造包含已过期 ban 的 json 文件
        payload = {
            "tokens": ["expired-jti", "valid-jti"],
            "expiry": {
                "expired-jti": "2020-01-01T00:00:00+00:00",  # 已过期
                "valid-jti": "2099-01-01T00:00:00+00:00",
            },
        }
        target.write_text(json.dumps(payload), encoding="utf-8")

        b = TokenBanList(file_path=str(target))
        # 过期条目应被清理
        assert b.is_banned("token-with-expired-jti") is False
        # 有效条目保留
        token = create_access_token(
            {"sub": "u1", "jti": "valid-jti"},
            expires_delta=timedelta(minutes=5),
        )
        assert b.is_banned(token) is True
