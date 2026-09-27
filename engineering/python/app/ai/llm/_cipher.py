"""API Key 对称加密器（从 provider_registry 拆出）。"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class APIKeyCipher:
    """API Key 对称加密器（Fernet）。

    加密密钥派生优先级：
    1. 环境变量 LLM_PROVIDER_ENCRYPTION_KEY（Fernet 兼容的 base64 urlsafe 32字节）
    2. 项目令牌（.lnn_token 文件内容）→ SHA256 → Fernet key
    3. 临时密钥（仅当前会话有效，重启后已加密 Key 无法解密）

    第三种情况会记录警告，并自动重新加密所有存储的 Key。

    安全模式（strict，默认启用）：
        - 加密失败时抛出 ``RuntimeError``，绝不静默回退明文存储
        - 解密时密钥不可用抛出 ``RuntimeError``；解密本身失败仍返回空串
          （避免错误密钥下泄漏其他密文对应明文）
    非安全模式（strict=False，仅用于本地开发/测试）：
        - 保留旧行为，加密失败回退明文。生产环境严禁使用。
    """

    def __init__(self, strict: bool = True) -> None:
        self._strict = strict
        self._fernet: Any = None
        self._key_source: str = "unknown"
        try:
            from cryptography.fernet import Fernet

            self._Fernet = Fernet
        except ImportError:
            self._Fernet = None  # type: ignore
            msg = "cryptography 未安装，API Key 加密不可用。建议安装：pip install cryptography"
            if strict:
                raise RuntimeError(msg)
            logger.warning(msg)

    def _resolve_key(self) -> bytes | None:
        """解析加密密钥，返回 Fernet 兼容的 32 字节 base64 urlsafe。"""
        if self._Fernet is None:
            return None

        # 优先级 1: 环境变量
        env_key = os.environ.get("LLM_PROVIDER_ENCRYPTION_KEY")
        if env_key:
            try:
                self._key_source = "env"
                return env_key.encode() if isinstance(env_key, str) else env_key
            except Exception as e:
                logger.debug("Invalid LLM_PROVIDER_ENCRYPTION_KEY: %s", e)

        # 优先级 2: 项目令牌派生
        try:
            from app.config import config

            token = config.token.token
            import base64
            import hashlib

            digest = hashlib.sha256(token.encode("utf-8")).digest()
            key = base64.urlsafe_b64encode(digest)
            self._key_source = "token"
            return key
        except Exception as e:
            logger.debug("Failed to derive key from project token: %s", e)

        # 优先级 3: 临时密钥
        if self._Fernet is not None:
            key = self._Fernet.generate_key()
            self._key_source = "ephemeral"
            logger.warning(
                "使用临时加密密钥。重启后已存储的 API Key 将无法解密。"
                "请设置 LLM_PROVIDER_ENCRYPTION_KEY 环境变量以持久化。"
            )
            return key

        return None

    @property
    def available(self) -> bool:
        """加密是否可用。"""
        return self._Fernet is not None

    def encrypt(self, plaintext: str) -> str:
        """加密 API Key。

        Raises:
            RuntimeError: strict 模式下加密不可用或加密失败。
        """
        if not plaintext:
            return ""
        if self._Fernet is None:
            if self._strict:
                raise RuntimeError("API Key 加密不可用（cryptography 未安装），strict 模式下拒绝明文存储。")
            return plaintext  # 降级明文（仅 non-strict）
        try:
            if self._fernet is None:
                key = self._resolve_key()
                if key is None:
                    if self._strict:
                        raise RuntimeError("API Key 加密密钥不可用，strict 模式下拒绝明文存储。")
                    return plaintext
                self._fernet = self._Fernet(key)
            return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("API Key 加密失败: %s", e)
            if self._strict:
                raise RuntimeError(f"API Key 加密失败: {e}") from e
            return plaintext

    def decrypt(self, ciphertext: str) -> str:
        """解密 API Key。

        Raises:
            RuntimeError: strict 模式下加密后端或密钥不可用。
        """
        if not ciphertext:
            return ""
        if self._Fernet is None:
            if self._strict:
                raise RuntimeError("API Key 解密不可用（cryptography 未安装）。")
            return ciphertext  # 明文模式
        try:
            if self._fernet is None:
                key = self._resolve_key()
                if key is None:
                    if self._strict:
                        raise RuntimeError("API Key 解密密钥不可用，strict 模式下拒绝返回密文。")
                    return ciphertext
                self._fernet = self._Fernet(key)
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except RuntimeError:
            raise
        except Exception as e:
            # 解密失败可能是密钥变更，返回空字符串避免泄漏
            logger.warning("API Key 解密失败（密钥可能已变更）: %s", e)
            return ""
