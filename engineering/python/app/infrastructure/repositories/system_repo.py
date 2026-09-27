"""系统基础设施 Repository（健康检查等 V3.0 Repository 层）。"""

from __future__ import annotations
from typing import Any


class SystemRepo:
    def health_check_db(self) -> dict[str, Any]:
        from app.database.connection import get_sessionmaker

        sessionmaker = get_sessionmaker()
        return {"database": "ok" if sessionmaker is not None else "unavailable"}


def get_system_repo():
    return SystemRepo()
