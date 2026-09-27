"""Provider 延迟缓存（从 router 拆出）。"""

from __future__ import annotations

import threading


class LatencyCache:
    """Provider 延迟缓存。

    记录最近 N 次调用的平均延迟，用于延迟感知路由。
    延迟数据来自 Provider 实例的 latency_ms 属性。
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._max = max_entries
        self._latencies: dict[str, list[float]] = {}
        # H11 修复：record/get_avg/clear 并发访问 _latencies 需加锁，
        # 原 dict 键值重建与 list.pop(0) 在并发下会丢失更新或抛 KeyError。
        self._lock = threading.Lock()

    def record(self, provider_id: str, latency_ms: float) -> None:
        """记录一次延迟。"""
        with self._lock:
            if provider_id not in self._latencies:
                self._latencies[provider_id] = []
            bucket = self._latencies[provider_id]
            bucket.append(latency_ms)
            if len(bucket) > self._max:
                bucket.pop(0)

    def get_avg(self, provider_id: str) -> float | None:
        """获取平均延迟。"""
        with self._lock:
            bucket = self._latencies.get(provider_id)
            if not bucket:
                return None
            return sum(bucket) / len(bucket)

    def clear(self) -> None:
        with self._lock:
            self._latencies.clear()


# ProviderRouter
