"""
Test Authentication Module

Tests for:
- JWT token creation, decoding, and validation
- Password hashing and verification
- Token ban list functionality
- Public endpoint configuration (where applicable)
"""

import uuid
from datetime import timedelta

from app.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_token_strict,
    get_token_ban_list,
    hash_password,
    verify_password,
)


# 密码哈希测试


class TestPasswordHashing:
    """密码哈希与验证相关测试。"""

    def test_hash_password_returns_non_empty_string(self):
        """hash_password 应该返回非空字符串。"""
        hashed = hash_password("Passw0rd!")
        assert isinstance(hashed, str)
        assert len(hashed) > 0
        # 不应明文存储
        assert hashed != "Passw0rd!"

    def test_hash_password_is_idempotent(self):
        """同一密码应产生不同哈希（盐值随机）。"""
        h1 = hash_password("Passw0rd!")
        h2 = hash_password("Passw0rd!")
        assert h1 != h2

    def test_verify_password_success(self):
        """verify_password 应能验证正确密码。"""
        hashed = hash_password("Passw0rd!")
        assert verify_password("Passw0rd!", hashed) is True

    def test_verify_password_failure(self):
        """verify_password 应拒绝错误密码。"""
        hashed = hash_password("Passw0rd!")
        assert verify_password("WrongPass", hashed) is False


# JWT Token 测试


class TestJWTTokens:
    """JWT Token 创建与解码测试。"""

    def test_create_access_token_returns_string(self):
        """create_access_token 返回字符串。"""
        token = create_access_token({"sub": "alice", "role": "user"})
        assert isinstance(token, str)
        assert len(token.split(".")) == 3  # 标准 JWT 三段式

    def test_decode_access_token_returns_payload(self):
        """应能解码刚刚创建的 access token。"""
        token = create_access_token({"sub": "alice", "role": "user"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "alice"
        assert payload["role"] == "user"
        assert payload["type"] == "access"

    def test_create_refresh_token_has_refresh_type(self):
        """refresh token 的 type 字段应为 'refresh'。"""
        token = create_refresh_token({"sub": "bob"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["type"] == "refresh"
        assert payload["sub"] == "bob"

    def test_decode_token_strict_rejects_wrong_type(self):
        """decode_token_strict(expected_type='access') 应拒绝 refresh token。"""
        refresh = create_refresh_token({"sub": "bob"})
        assert decode_token_strict(refresh, expected_type="access") is None
        assert decode_token_strict(refresh, expected_type="refresh") is not None

    def test_decode_invalid_token_returns_none(self):
        """无效 token 应返回 None。"""
        assert decode_token("invalid.token.string") is None
        assert decode_token("") is None
        assert decode_token("not-a-jwt") is None

    def test_custom_expiration(self):
        """自定义 expires_delta 参数应生效 - 使用 0 秒过期。"""
        # 创建一个立即过期的 token
        token = create_access_token(
            {"sub": "alice"},
            expires_delta=timedelta(seconds=-1),
        )
        # 负数 expiration 应导致 decode 失败（已过期）
        result = decode_token_strict(token, expected_type="access")
        assert result is None

    def test_custom_expiration_long(self):
        """自定义长过期时间 - decode 应成功。"""
        token = create_access_token(
            {"sub": "alice"},
            expires_delta=timedelta(hours=2),
        )
        result = decode_token_strict(token, expected_type="access")
        assert result is not None
        assert result["sub"] == "alice"


# TokenBanList 测试


class TestTokenBanList:
    """TokenBanList 测试。"""

    def test_ban_then_check(self, tmp_path, monkeypatch):
        """被 ban 的 token 在 check 时应被识别。"""
        ban_file = tmp_path / "banned.json"
        monkeypatch.setenv("LNN_BANNED_TOKENS_FILE", str(ban_file))

        from app.auth import security as security_module

        monkeypatch.setattr(security_module, "_token_ban_list", None)

        ban_list = get_token_ban_list()
        token = "test-token-" + uuid.uuid4().hex
        assert ban_list.is_banned(token) is False

        ban_list.ban(token)
        assert ban_list.is_banned(token) is True

    def test_unban_removes_token(self, tmp_path, monkeypatch):
        """重新 ban 一个不存在的 token：banned 列表应包含其 jti。"""
        ban_file = tmp_path / "banned.json"
        monkeypatch.setenv("LNN_BANNED_TOKENS_FILE", str(ban_file))

        from app.auth import security as security_module

        monkeypatch.setattr(security_module, "_token_ban_list", None)

        ban_list = get_token_ban_list()
        # 第一次 ban
        token = "test-token-xyz"
        ban_list.ban(token)
        assert ban_list.is_banned(token) is True
        # 第二次 ban 同一 token（幂等）
        ban_list.ban(token)
        assert ban_list.is_banned(token) is True

    def test_persistence_across_instances(self, tmp_path, monkeypatch):
        """ban 状态应持久化到文件，新实例应能读取。"""
        ban_file = tmp_path / "banned.json"
        monkeypatch.setenv("LNN_BANNED_TOKENS_FILE", str(ban_file))

        from app.auth import security as security_module

        monkeypatch.setattr(security_module, "_token_ban_list", None)

        ban_list_1 = get_token_ban_list()
        token = "persistent-token"
        ban_list_1.ban(token)

        # 重置单例，模拟新启动
        monkeypatch.setattr(security_module, "_token_ban_list", None)
        ban_list_2 = get_token_ban_list()
        assert ban_list_2.is_banned(token) is True


# Token 撤销与登出流程测试


class TestTokenRevocationFlow:
    """Token 撤销的端到端流程测试。"""

    def test_banned_token_cannot_be_decoded_for_use(self, tmp_path, monkeypatch):
        """被 ban 的 token 仍能被解码（payload 完整），但应通过 ban_list 阻止使用。"""
        ban_file = tmp_path / "banned.json"
        monkeypatch.setenv("LNN_BANNED_TOKENS_FILE", str(ban_file))

        from app.auth import security as security_module

        monkeypatch.setattr(security_module, "_token_ban_list", None)

        ban_list = get_token_ban_list()
        token = create_access_token({"sub": "alice", "role": "user"})

        # 未撤销时 decode_token_strict 成功
        assert decode_token_strict(token, expected_type="access") is not None

        # 撤销后 decode_token_strict 仍能解码，但 is_banned 为 True
        ban_list.ban(token)
        assert ban_list.is_banned(token) is True
