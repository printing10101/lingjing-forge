"""规则分组 CRUD mixin（从 rule_db 拆出）。"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any
from collections.abc import Callable

from app.database._models import RuleGroup

logger = logging.getLogger(__name__)


class _GroupCrudMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_conn: Callable[..., Any]
    _now: Callable[..., Any]

    def _row_to_group(self, row: sqlite3.Row) -> RuleGroup:
        return RuleGroup(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_group(self, group: RuleGroup) -> RuleGroup:
        now = self._now()
        if group.created_at is None:
            group.created_at = now
        group.updated_at = now

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO rule_groups (name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (group.name, group.description, group.created_at, group.updated_at),
        )
        conn.commit()
        group.id = cursor.lastrowid
        logger.info("创建规则分组: %s (id=%s)", group.name, group.id)
        return group

    def update_group(self, group_id: int, group: RuleGroup) -> RuleGroup | None:
        now = self._now()
        group.updated_at = now

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE rule_groups SET name=?, description=?, updated_at=? WHERE id=?",
            (group.name, group.description, group.updated_at, group_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        group.id = group_id
        return group

    def delete_group(self, group_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rule_groups WHERE id=?", (group_id,))
        conn.commit()
        return cursor.rowcount > 0

    def get_group(self, group_id: int) -> RuleGroup | None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rule_groups WHERE id=?", (group_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_group(row)

    def list_groups(self) -> list[RuleGroup]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rule_groups ORDER BY created_at DESC")
        return [self._row_to_group(row) for row in cursor.fetchall()]

    def get_group_rule_count(self, group_id: int) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rules WHERE group_id=?", (group_id,))
        return cursor.fetchone()[0]

    def _find_group_by_name(self, name: str) -> RuleGroup | None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rule_groups WHERE name=?", (name,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_group(row)
