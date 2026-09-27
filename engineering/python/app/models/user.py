from __future__ import annotations

import logging
import os
import json
import threading
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


USER_STORE_FILE = os.environ.get("LNN_USER_STORE_FILE", ".lnn_users.json")


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=8, max_length=128)
    invite_code: str | None = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    username: str
    role: str = "user"
    created_at: str = ""
    last_login: str | None = None
    is_guest: bool = False


class UserRecord:
    def __init__(
        self,
        username: str,
        password_hash: str,
        role: str = "user",
        created_at: str | None = None,
        last_login: str | None = None,
        is_active: bool = True,
        must_change_password: bool = False,
    ):
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.last_login = last_login
        self.is_active = is_active
        self.must_change_password = must_change_password

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """序列化用户记录。

        Args:
            include_sensitive: 是否包含敏感字段（如 password_hash）。
                默认 False，确保密码哈希不会通过 to_dict() 泄露到 API 响应或日志。
                仅在内部持久化（_save）等必须场景显式传 True。

        Returns:
            不含 password_hash 的用户字典（include_sensitive=False 时）。
        """
        data = {
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at,
            "last_login": self.last_login,
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
        }
        if include_sensitive:
            data["password_hash"] = self.password_hash
        return data

    def get_password_hash(self) -> str:
        """获取密码哈希（仅供内部认证逻辑使用，不对外暴露）。"""
        return self.password_hash

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserRecord":
        return cls(
            username=data["username"],
            password_hash=data["password_hash"],
            role=data.get("role", "user"),
            created_at=data.get("created_at"),
            last_login=data.get("last_login"),
            is_active=data.get("is_active", True),
            must_change_password=data.get("must_change_password", False),
        )


class UserStore:
    def __init__(self, file_path: str | None = None):
        self._file_path = Path(file_path or USER_STORE_FILE)
        self._users: dict[str, UserRecord] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if self._file_path.exists():
            try:
                data = json.loads(self._file_path.read_text())
                self._users = {u["username"]: UserRecord.from_dict(u) for u in data.get("users", [])}
            except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
                # 用户数据文件损坏时重置为空，记录错误以便排查
                logger.error("Failed to load user data from %s: %s", self._file_path, e, exc_info=True)
                self._users = {}

    def _save(self):
        # include_sensitive=True: 持久化必须保存 password_hash，否则重启后无法认证
        data = {"users": [u.to_dict(include_sensitive=True) for u in self._users.values()]}
        self._file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        # 安全要求：限制用户存储文件权限为 600（仅属主可读写），防止其他用户读取密码哈希
        try:
            os.chmod(self._file_path, 0o600)
        except (OSError, PermissionError) as e:
            # Windows 或某些文件系统不支持 chmod，记录告警但不阻断流程
            logger.warning("Failed to chmod %s to 0o600: %s", self._file_path, e)

    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "user",
        must_change_password: bool = False,
    ) -> UserRecord:
        with self._lock:
            if username in self._users:
                raise ValueError(f"User '{username}' already exists")
            record = UserRecord(
                username=username,
                password_hash=password_hash,
                role=role,
                must_change_password=must_change_password,
            )
            self._users[username] = record
            self._save()
            return record

    def get_user(self, username: str) -> UserRecord | None:
        with self._lock:
            return self._users.get(username)

    def update_last_login(self, username: str):
        with self._lock:
            if username in self._users:
                self._users[username].last_login = datetime.now(timezone.utc).isoformat()
                self._save()

    def set_active(self, username: str, active: bool):
        with self._lock:
            if username in self._users:
                self._users[username].is_active = active
                self._save()

    def set_role(self, username: str, role: str):
        with self._lock:
            if username in self._users:
                self._users[username].role = role
                self._save()

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            return list(self._users.values())


class _UserStoreHolder:
    """Thread-safe lazy holder for the :class:`UserStore` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: UserStore | None = None

    def get(self) -> UserStore:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = UserStore()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _UserStoreHolder()


def get_user_store() -> UserStore:
    """获取共享的 :class:`UserStore` 单例；首次访问时懒初始化。

    Returns:
        :class:`UserStore` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_user_store)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()
