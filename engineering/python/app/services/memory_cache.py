"""
内存缓存客户端 - Redis 桌面版替代方案

当 Redis 不可用时（桌面模式），使用内存字典实现缓存功能。
支持 TTL、Hash、Set/Get 等基本操作。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目，支持 TTL"""

    value: Any
    expire_at: float | None = None

    def is_expired(self) -> bool:
        if self.expire_at is None:
            return False
        return time.time() > self.expire_at


class MemoryCache:
    """
    线程安全的内存缓存实现

    功能：
    - 支持 TTL 过期机制
    - 支持 Hash 结构（HSET/HGET/HGETALL）
    - 支持简单 KV（SET/GET/DELETE）
    - 后台清理过期条目
    """

    def __init__(self, cleanup_interval: int = 60):
        self._lock = threading.Lock()
        self._data: dict[str, CacheEntry] = {}
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: asyncio.Task | None = None
        self._started = False

    async def start(self):
        """启动后台清理任务"""
        if self._started:
            return
        self._started = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("MemoryCache started")

    async def stop(self):
        """停止后台清理任务"""
        self._started = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                # 主动取消清理任务时 CancelledError 是预期行为，无需处理
                pass
        self._data.clear()
        logger.info("MemoryCache stopped")

    async def _cleanup_loop(self):
        """定期清理过期条目"""
        while self._started:
            try:
                await asyncio.sleep(self._cleanup_interval)
                self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("MemoryCache cleanup error: %s", e)

    def _cleanup_expired(self):
        """清理所有过期条目"""
        with self._lock:
            expired_keys = [k for k, v in self._data.items() if v.is_expired()]
            for k in expired_keys:
                del self._data[k]
            if expired_keys:
                logger.debug("Cleaned up %d expired cache entries", len(expired_keys))

    # 简单 KV 操作

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        """设置键值对，ex 为过期时间（秒）"""
        expire_at = None
        if ex is not None:
            expire_at = time.time() + ex

        with self._lock:
            self._data[key] = CacheEntry(value=value, expire_at=expire_at)
        return True

    async def get(self, key: str) -> Any | None:
        """获取键值"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._data[key]
                return None
            return entry.value

    async def delete(self, key: str) -> bool:
        """删除键"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._data[key]
                return False
            return True

    # Hash 操作（用于任务进度存储）

    async def hset(self, key: str, mapping: dict[str, Any]) -> bool:
        """设置 Hash 字段"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None or not isinstance(entry.value, dict):
                entry = CacheEntry(value={})
                self._data[key] = entry

            # 合并新字段
            entry.value.update(mapping)
        return True

    async def hgetall(self, key: str) -> dict[str, Any]:
        """获取 Hash 所有字段"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return {}
            if entry.is_expired():
                del self._data[key]
                return {}
            if not isinstance(entry.value, dict):
                return {}
            return entry.value.copy()

    async def hget(self, key: str, field: str) -> Any | None:
        """获取 Hash 单个字段"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._data[key]
                return None
            if not isinstance(entry.value, dict):
                return None
            return entry.value.get(field)

    # 兼容性方法

    async def ping(self) -> bool:
        """兼容性方法：始终返回 True"""
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        """设置键的过期时间"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            entry.expire_at = time.time() + seconds
            return True

    async def info(self, section: str = "memory") -> dict[str, Any]:
        """兼容性方法：返回内存使用信息（供健康检查使用）"""
        with self._lock:
            entry_count = len(self._data)
        return {
            "used_memory_human": f"{entry_count * 256 / 1024:.1f}K",
            "connected_clients": 1,
            "total_keys": entry_count,
        }


# 全局单例

_memory_cache: MemoryCache | None = None
_holder_lock = threading.Lock()


def get_memory_cache() -> MemoryCache:
    """获取全局内存缓存单例"""
    global _memory_cache
    if _memory_cache is None:
        with _holder_lock:
            if _memory_cache is None:
                _memory_cache = MemoryCache()
    return _memory_cache


async def init_memory_cache() -> MemoryCache:
    """初始化并启动内存缓存"""
    cache = get_memory_cache()
    await cache.start()
    return cache


async def close_memory_cache():
    """关闭内存缓存"""
    global _memory_cache
    if _memory_cache is not None:
        await _memory_cache.stop()
        _memory_cache = None
