"""
Ring Log Buffer - Fixed-size in-memory ring buffer with async disk persistence.

Design inspired by gstack's real-time logging architecture:
- Three independent ring buffers: request, ai_inference, system_event
- Thread-safe append operations that never block HTTP request processing
- Async background flush to .gstack/*.log files every second
- Query with pagination, time-range filtering, and type selection
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time as _time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

LogBufferType = Literal["request", "ai_inference", "system_event"]
BUFFER_TYPES: tuple[LogBufferType, ...] = ("request", "ai_inference", "system_event")

DEFAULT_CAPACITY = 50_000
FLUSH_INTERVAL = 1.0
GSTACK_DIR = ".gstack"


@dataclass
class LogEntry:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    type: LogBufferType = "system_event"
    level: str = "INFO"
    source: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class RingBuffer:
    """Thread-safe fixed-capacity ring buffer backed by collections.deque."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._capacity = capacity
        self._buffer: deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._total_appended = 0
        self._total_dropped = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def total_appended(self) -> int:
        return self._total_appended

    @property
    def total_dropped(self) -> int:
        return self._total_dropped

    def append(self, entry: LogEntry) -> None:
        with self._lock:
            was_full = len(self._buffer) >= self._capacity
            self._buffer.append(entry)
            self._total_appended += 1
            if was_full:
                self._total_dropped += 1

    def snapshot(self) -> list[LogEntry]:
        with self._lock:
            return list(self._buffer)

    def get_last_n(self, n: int) -> list[LogEntry]:
        """获取最近 N 条记录，避免全量 snapshot 后再切片"""
        with self._lock:
            buffer_len = len(self._buffer)
            if n >= buffer_len:
                return list(self._buffer)
            # 使用 itertools.islice 高效获取最后 N 条
            from itertools import islice

            start = buffer_len - n
            return list(islice(self._buffer, start, buffer_len))

    def query(
        self,
        since: str | None = None,
        until: str | None = None,
        level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[LogEntry], int]:
        with self._lock:
            entries = list(self._buffer)
        filtered = entries
        if since:
            filtered = [e for e in filtered if e.timestamp >= since]
        if until:
            filtered = [e for e in filtered if e.timestamp <= until]
        if level:
            filtered = [e for e in filtered if e.level.upper() == level.upper()]
        total = len(filtered)
        page = filtered[offset : offset + limit]
        return page, total


class RingLogBuffer:
    """
    Central manager for three ring log buffers with async disk flush.

    Buffers:
      - request: HTTP request/response lifecycle logs
      - ai_inference: LNN model inference tracing logs
      - system_event: system lifecycle, errors, startup/shutdown events
    """

    def __init__(
        self,
        base_dir: str | None = None,
        capacity: int = DEFAULT_CAPACITY,
        flush_interval: float = FLUSH_INTERVAL,
    ):
        if base_dir is None:
            base_dir = str(Path.home() / ".lingjing" / GSTACK_DIR)
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._flush_interval = flush_interval

        self._buffers: dict[LogBufferType, RingBuffer] = {t: RingBuffer(capacity=capacity) for t in BUFFER_TYPES}

        self._flush_task: asyncio.Task | None = None
        self._running = False
        self._start_time = _time.time()

    @property
    def uptime_seconds(self) -> float:
        return _time.time() - self._start_time

    def append(
        self,
        buffer_type: LogBufferType,
        level: str = "INFO",
        source: str = "",
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        if buffer_type not in self._buffers:
            logger.warning("Unknown buffer type: %s, falling back to system_event", buffer_type)
            buffer_type = "system_event"
        entry = LogEntry(
            type=buffer_type,
            level=level.upper(),
            source=source,
            message=message,
            data=data or {},
        )
        self._buffers[buffer_type].append(entry)

    def query(
        self,
        buffer_type: LogBufferType,
        since: str | None = None,
        until: str | None = None,
        level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if buffer_type not in self._buffers:
            return {"entries": [], "total": 0, "offset": offset, "limit": limit}
        entries, total = self._buffers[buffer_type].query(
            since=since, until=until, level=level, limit=limit, offset=offset
        )
        return {
            "entries": [asdict(e) for e in entries],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def stats(self) -> dict[str, Any]:
        return {
            "uptime_seconds": self.uptime_seconds,
            "buffers": {
                t: {
                    "size": self._buffers[t].size,
                    "capacity": self._buffers[t].capacity,
                    "total_appended": self._buffers[t].total_appended,
                    "total_dropped": self._buffers[t].total_dropped,
                }
                for t in BUFFER_TYPES
            },
            "flush_interval": self._flush_interval,
        }

    async def _flush_loop(self) -> None:
        flushed_counts: dict[LogBufferType, int] = {t: 0 for t in BUFFER_TYPES}
        while self._running:
            await asyncio.sleep(self._flush_interval)
            try:
                for buffer_type in BUFFER_TYPES:
                    buf = self._buffers[buffer_type]
                    current_total = buf.total_appended
                    already_flushed = flushed_counts[buffer_type]
                    new_count = current_total - already_flushed
                    if new_count <= 0:
                        continue
                    # 使用 get_last_n 直接获取最新条目，避免全量 snapshot
                    new_entries = buf.get_last_n(new_count)
                    if not new_entries:
                        continue
                    log_path = self._base_dir / f"{buffer_type}.log"
                    try:
                        with open(log_path, "a", encoding="utf-8") as f:
                            for entry in new_entries:
                                f.write(entry.to_json() + "\n")
                    except (OSError, ValueError, TypeError, KeyError) as e:
                        logger.error("Failed to flush %s buffer: %s", buffer_type, e)
                    flushed_counts[buffer_type] = current_total
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as e:
                logger.exception("Error during ring buffer flush cycle: %s", e)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(
            "RingLogBuffer started: capacity=%d, flush_interval=%.1fs, dir=%s",
            DEFAULT_CAPACITY,
            self._flush_interval,
            self._base_dir,
        )

    async def stop(self) -> None:
        self._running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                # 异步任务被取消是正常关闭路径，无需记录异常
                pass
        for buffer_type in BUFFER_TYPES:
            buf = self._buffers[buffer_type]
            entries = buf.snapshot()
            if entries:
                log_path = self._base_dir / f"{buffer_type}.log"
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        for entry in entries:
                            f.write(entry.to_json() + "\n")
                except OSError as e:
                    logger.error("Failed final flush for %s: %s", buffer_type, e)
        logger.info("RingLogBuffer stopped")


class _RingLogBufferHolder:
    """Thread-safe lazy holder for the :class:`RingLogBuffer` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: RingLogBuffer | None = None

    def get(
        self,
        base_dir: str | None = None,
        capacity: int = DEFAULT_CAPACITY,
        flush_interval: float = FLUSH_INTERVAL,
    ) -> RingLogBuffer:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = RingLogBuffer(
                base_dir=base_dir,
                capacity=capacity,
                flush_interval=flush_interval,
            )
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _RingLogBufferHolder()


def get_ring_log_buffer(
    base_dir: str | None = None,
    capacity: int = DEFAULT_CAPACITY,
    flush_interval: float = FLUSH_INTERVAL,
) -> RingLogBuffer:
    """获取共享的 :class:`RingLogBuffer` 单例；首次访问时懒初始化。

    Returns:
        :class:`RingLogBuffer` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_ring_log_buffer)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get(
        base_dir=base_dir,
        capacity=capacity,
        flush_interval=flush_interval,
    )
