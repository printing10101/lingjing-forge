"""事件方法组：SSE 订阅/广播。"""

from __future__ import annotations

import asyncio
import json
from typing import Any


import logging

logger = logging.getLogger(__name__)


class _TaskEventsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _subscribers: Any

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        if job_id in self._subscribers:
            self._subscribers[job_id].append(q)
        return q

    def unsubscribe(self, job_id: str, queue: asyncio.Queue):
        if job_id in self._subscribers:
            try:
                self._subscribers[job_id].remove(queue)
            except ValueError as remove_err:
                # 重复 unsubscribe 时 queue 已不在列表中是预期行为
                logger.debug(
                    "Queue already removed from subscribers for job %s: %s",
                    job_id,
                    remove_err,
                    exc_info=True,
                )

    async def _broadcast_event(self, job_id: str, event_type: str, data: dict[str, Any]):
        event = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        if job_id in self._subscribers:
            dead_queues = []
            for q in self._subscribers[job_id]:
                try:
                    # [A-H19] 使用 put_nowait 避免队列满时阻塞广播者，
                    # 进而阻塞 task_lock 持有者导致死锁。
                    # 队列满时丢弃事件并记录警告，消费者侧通过心跳检测存活。
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "Subscriber queue full for job %s, event dropped",
                        job_id,
                    )
                except (RuntimeError, OSError):
                    dead_queues.append(q)
            for q in dead_queues:
                self._subscribers[job_id].remove(q)
