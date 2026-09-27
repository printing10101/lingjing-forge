"""通知 Repository（V3.0 Repository 层）。"""

from __future__ import annotations
from typing import Any


class NotificationRepo:
    async def health_check(self) -> dict[str, Any]:
        from app.database.connection import get_sessionmaker

        sessionmaker = get_sessionmaker()
        return {"database": "ok" if sessionmaker is not None else "unavailable"}

    async def list_notifications(self, limit: int = 20, offset: int = 0):
        return {"items": [], "total": 0}


def get_notification_repo():
    return NotificationRepo()
