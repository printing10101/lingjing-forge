"""ProviderRegistry 注册表实现（从 provider_registry 拆出）。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.ai.llm._cipher import APIKeyCipher
from app.ai.llm._db import _get_db_path
from app.ai.llm._factory import create_provider, _default_provider_templates
from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderCapability,
    ProviderConfig,
    ProviderType,
)
from app.config.limits import DEFAULT_SQLITE_LOCK_TIMEOUT_SEC

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """LLM Provider 注册表。

    职责：
    - 持久化 Provider 配置到 SQLite
    - 加密存储 API Key
    - 创建并缓存 Provider 实例
    - 管理激活 Provider（互斥）
    - 提供查询/增删改接口
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _get_db_path()
        self._cipher = APIKeyCipher()
        self._lock = threading.RLock()
        self._instances: dict[str, LLMProvider] = {}  # provider_id -> 实例缓存
        self._initialized = False

    # 数据库初始化

    def _get_conn(self) -> sqlite3.Connection:
        """获取 SQLite 连接（每次新建，使用完关闭）。"""
        conn = sqlite3.connect(str(self._db_path), timeout=DEFAULT_SQLITE_LOCK_TIMEOUT_SEC)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        """初始化数据库表和种子数据。"""
        with self._lock:
            if self._initialized:
                return

            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS llm_providers (
                        provider_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        provider_type TEXT NOT NULL,
                        base_url TEXT NOT NULL DEFAULT '',
                        api_key_encrypted TEXT NOT NULL DEFAULT '',
                        default_model TEXT NOT NULL DEFAULT '',
                        timeout INTEGER NOT NULL DEFAULT 60,
                        max_retries INTEGER NOT NULL DEFAULT 3,
                        retry_delay REAL NOT NULL DEFAULT 1.0,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        is_active INTEGER NOT NULL DEFAULT 0,
                        priority INTEGER NOT NULL DEFAULT 0,
                        capabilities TEXT NOT NULL DEFAULT '["chat"]',
                        extra TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_llm_providers_active
                    ON llm_providers(is_active) WHERE is_active = 1
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_llm_providers_enabled
                    ON llm_providers(enabled) WHERE enabled = 1
                """)
                conn.commit()

                # 种子数据：首次初始化时插入默认模板
                count = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
                if count == 0:
                    self._seed_defaults(conn)

            finally:
                conn.close()

            self._initialized = True
            logger.info("ProviderRegistry 初始化完成，数据库: %s", self._db_path)

    def _seed_defaults(self, conn: sqlite3.Connection) -> None:
        """插入默认 Provider 模板。"""
        templates = _default_provider_templates()
        for tpl in templates:
            self._insert_config(conn, tpl)
        logger.info("已插入 %d 个默认 Provider 模板", len(templates))

    def _insert_config(self, conn: sqlite3.Connection, config: ProviderConfig) -> None:
        """插入一条 Provider 配置。"""
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_providers
            (provider_id, name, provider_type, base_url, api_key_encrypted,
             default_model, timeout, max_retries, retry_delay, enabled,
             is_active, priority, capabilities, extra, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
            (
                config.provider_id,
                config.name,
                config.provider_type.value,
                config.base_url,
                self._cipher.encrypt(config.api_key),
                config.default_model,
                config.timeout,
                config.max_retries,
                config.retry_delay,
                int(config.enabled),
                int(config.is_active),
                config.priority,
                json.dumps([c.value for c in config.capabilities]),
                json.dumps(config.extra, ensure_ascii=False),
            ),
        )

    # 行 -> ProviderConfig 转换

    def _row_to_config(self, row: sqlite3.Row) -> ProviderConfig:
        """数据库行转换为 ProviderConfig 对象。"""
        try:
            provider_type = ProviderType(row["provider_type"])
        except ValueError:
            provider_type = ProviderType.OPENAI_COMPATIBLE

        caps_raw = row["capabilities"] or '["chat"]'
        try:
            caps_list = json.loads(caps_raw)
            capabilities = [ProviderCapability(c) for c in caps_list]
        except (json.JSONDecodeError, ValueError):
            capabilities = [ProviderCapability.CHAT]

        extra_raw = row["extra"] or "{}"
        try:
            extra = json.loads(extra_raw)
        except json.JSONDecodeError:
            extra = {}

        return ProviderConfig(
            provider_id=row["provider_id"],
            name=row["name"],
            provider_type=provider_type,
            base_url=row["base_url"],
            api_key=self._cipher.decrypt(row["api_key_encrypted"] or ""),
            default_model=row["default_model"],
            timeout=row["timeout"],
            max_retries=row["max_retries"],
            retry_delay=row["retry_delay"],
            enabled=bool(row["enabled"]),
            is_active=bool(row["is_active"]),
            priority=row["priority"],
            capabilities=capabilities,
            extra=extra,
        )

    # 查询接口

    def list_providers(self, include_disabled: bool = True) -> list[ProviderConfig]:
        """列出所有 Provider 配置。"""
        self._init_db()
        conn = self._get_conn()
        try:
            if include_disabled:
                rows = conn.execute("SELECT * FROM llm_providers ORDER BY priority DESC, name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM llm_providers WHERE enabled = 1 ORDER BY priority DESC, name"
                ).fetchall()
            return [self._row_to_config(r) for r in rows]
        finally:
            conn.close()

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """获取指定 Provider 配置。"""
        self._init_db()
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM llm_providers WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            return self._row_to_config(row) if row else None
        finally:
            conn.close()

    def get_active_provider_config(self) -> ProviderConfig | None:
        """获取当前激活的 Provider 配置。"""
        self._init_db()
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM llm_providers WHERE is_active = 1 LIMIT 1").fetchone()
            return self._row_to_config(row) if row else None
        finally:
            conn.close()

    # 增删改

    def upsert_provider(self, config: ProviderConfig) -> None:
        """新增或更新 Provider 配置。

        如果 provider_id 已存在则更新，否则插入。
        """
        self._init_db()
        with self._lock:
            conn = self._get_conn()
            try:
                self._insert_config(conn, config)
                conn.commit()
                # 失效实例缓存
                self._instances.pop(config.provider_id, None)
            finally:
                conn.close()
        logger.info("Provider 配置已保存: %s", config.provider_id)

    def delete_provider(self, provider_id: str) -> bool:
        """删除 Provider 配置。

        Returns:
            True 如果删除成功
        """
        self._init_db()
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM llm_providers WHERE provider_id = ?",
                    (provider_id,),
                )
                conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    self._instances.pop(provider_id, None)
                    logger.info("Provider 已删除: %s", provider_id)
                return deleted
            finally:
                conn.close()

    def set_active(self, provider_id: str) -> bool:
        """设置激活的 Provider（互斥）。

        Returns:
            True 如果设置成功
        """
        self._init_db()
        with self._lock:
            conn = self._get_conn()
            try:
                # 检查目标是否存在且启用
                row = conn.execute(
                    "SELECT enabled FROM llm_providers WHERE provider_id = ?",
                    (provider_id,),
                ).fetchone()
                if row is None:
                    return False
                if not bool(row["enabled"]):
                    logger.warning("无法激活未启用的 Provider: %s", provider_id)
                    return False

                # 互斥：先清除所有 is_active
                conn.execute("UPDATE llm_providers SET is_active = 0")
                # 设置目标
                conn.execute(
                    "UPDATE llm_providers SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE provider_id = ?",
                    (provider_id,),
                )
                conn.commit()
                # 失效所有实例缓存（激活变更影响 get_active_provider）
                self._instances.clear()
                logger.info("激活 Provider: %s", provider_id)
                return True
            finally:
                conn.close()

    def set_enabled(self, provider_id: str, enabled: bool) -> bool:
        """启用/禁用 Provider。

        禁用激活中的 Provider 会同时取消其激活状态。
        """
        self._init_db()
        with self._lock:
            conn = self._get_conn()
            try:
                if enabled:
                    conn.execute(
                        "UPDATE llm_providers SET enabled = 1, updated_at = CURRENT_TIMESTAMP WHERE provider_id = ?",
                        (provider_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE llm_providers SET enabled = 0, is_active = 0, "
                        "updated_at = CURRENT_TIMESTAMP WHERE provider_id = ?",
                        (provider_id,),
                    )
                conn.commit()
                self._instances.pop(provider_id, None)
                return conn.total_changes > 0
            finally:
                conn.close()

    # 实例获取

    def get_provider_instance(self, provider_id: str) -> LLMProvider | None:
        """获取 Provider 实例（带缓存）。

        配置变更后缓存自动失效。
        """
        self._init_db()
        with self._lock:
            if provider_id in self._instances:
                return self._instances[provider_id]

            config = self.get_provider(provider_id)
            if config is None or not config.enabled:
                return None

            try:
                instance = create_provider(config)
                self._instances[provider_id] = instance
                return instance
            except Exception as e:
                logger.error(
                    "创建 Provider 实例失败 (%s): %s",
                    provider_id,
                    e,
                    exc_info=True,
                )
                return None

    def get_active_provider(self) -> LLMProvider | None:
        """获取当前激活的 Provider 实例。

        如果没有激活的 Provider，返回 None。
        """
        config = self.get_active_provider_config()
        if config is None:
            return None
        return self.get_provider_instance(config.provider_id)

    def clear_instance_cache(self) -> None:
        """清除所有实例缓存。"""
        with self._lock:
            self._instances.clear()

    # 批量导入（用于自动探测结果）

    def import_from_detection(self, configs: list[ProviderConfig]) -> int:
        """从自动探测结果导入 Provider 配置。

        仅导入 provider_id 不存在的配置，已存在的保留用户配置。
        同时将第一个导入的本地 Provider 自动激活（如果当前无激活）。

        Returns:
            实际导入的数量
        """
        if not configs:
            return 0

        self._init_db()
        imported = 0
        first_local_id: str | None = None

        with self._lock:
            conn = self._get_conn()
            try:
                for cfg in configs:
                    # 检查是否已存在
                    existing = conn.execute(
                        "SELECT provider_id FROM llm_providers WHERE provider_id = ?",
                        (cfg.provider_id,),
                    ).fetchone()
                    if existing:
                        continue

                    self._insert_config(conn, cfg)
                    imported += 1
                    if first_local_id is None and cfg.provider_type.is_local:
                        first_local_id = cfg.provider_id

                conn.commit()

                # 自动激活
                if first_local_id is not None:
                    active = conn.execute(
                        "SELECT provider_id FROM llm_providers WHERE is_active = 1 LIMIT 1"
                    ).fetchone()
                    if active is None:
                        conn.execute(
                            "UPDATE llm_providers SET is_active = 1, "
                            "enabled = 1, "
                            "updated_at = CURRENT_TIMESTAMP "
                            "WHERE provider_id = ?",
                            (first_local_id,),
                        )
                        conn.commit()
                        logger.info(
                            "自动激活探测到的 Provider: %s",
                            first_local_id,
                        )
            finally:
                conn.close()

        if imported > 0:
            self._instances.clear()
            logger.info("从探测结果导入 %d 个 Provider", imported)
        return imported

    # 调试/状态

    def get_status_summary(self) -> dict[str, Any]:
        """返回注册表状态摘要。"""
        self._init_db()
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
            enabled = conn.execute("SELECT COUNT(*) FROM llm_providers WHERE enabled = 1").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM llm_providers WHERE is_active = 1").fetchone()[0]
            local_count = conn.execute(
                "SELECT COUNT(*) FROM llm_providers WHERE provider_type IN "
                "('ollama','lmstudio','llamacpp','llama','vllm','tgi','koboldcpp')"
            ).fetchone()[0]
            cloud_count = total - local_count

            active_row = conn.execute(
                "SELECT provider_id, name, provider_type FROM llm_providers WHERE is_active = 1 LIMIT 1"
            ).fetchone()

            return {
                "total": total,
                "enabled": enabled,
                "active_count": active,
                "local_count": local_count,
                "cloud_count": cloud_count,
                "active_provider_id": active_row["provider_id"] if active_row else None,
                "active_provider_name": active_row["name"] if active_row else None,
                "encryption_available": self._cipher.available,
                "db_path": str(self._db_path),
            }
        finally:
            conn.close()


# 全局单例

_registry: ProviderRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ProviderRegistry:
    """获取全局 ProviderRegistry 实例（线程安全懒加载）。"""
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is not None:
            return _registry
        _registry = ProviderRegistry()
        _registry._init_db()
        return _registry


def init_registry(db_path: Path | None = None) -> ProviderRegistry:
    """显式初始化注册表（可选，用于测试或自定义路径）。"""
    global _registry
    with _registry_lock:
        _registry = ProviderRegistry(db_path=db_path)
        _registry._init_db()
        return _registry


def reset_registry() -> None:
    """重置全局注册表（主要供测试使用）。"""
    global _registry
    with _registry_lock:
        _registry = None
