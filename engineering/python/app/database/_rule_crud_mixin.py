"""工艺规则 CRUD mixin（从 rule_db 拆出）。"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any
from collections.abc import Callable

from app.config.limits import DEFAULT_QUERY_LIMIT
from app.database._models import ProcessRule, RuleCondition, RuleResult

logger = logging.getLogger(__name__)


class _RuleCrudMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_conn: Callable[..., Any]
    _now: Callable[..., Any]

    def _row_to_rule(self, row: sqlite3.Row) -> ProcessRule:
        conditions = json.loads(row["conditions_json"])
        result = json.loads(row["result_json"])
        return ProcessRule(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            group_id=row["group_id"],
            conditions=[RuleCondition(**c) for c in conditions],
            logic_operator=row["logic_operator"],
            result=RuleResult(**result) if result else None,
            status=row["status"],
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_rule(self, rule: ProcessRule) -> ProcessRule:
        now = self._now()
        if rule.created_at is None:
            rule.created_at = now
        rule.updated_at = now

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO rules (
                name, description, group_id, conditions_json, logic_operator,
                result_json, status, priority, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.name,
                rule.description,
                rule.group_id,
                json.dumps([c.to_dict() for c in rule.conditions], ensure_ascii=False),
                rule.logic_operator,
                json.dumps(rule.result.to_dict() if rule.result else None, ensure_ascii=False),
                rule.status,
                rule.priority,
                rule.created_at,
                rule.updated_at,
            ),
        )
        conn.commit()
        rule.id = cursor.lastrowid
        logger.info("创建规则: %s (id=%s)", rule.name, rule.id)
        return rule

    def update_rule(self, rule_id: int, rule: ProcessRule) -> ProcessRule | None:
        now = self._now()
        rule.updated_at = now

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE rules SET
                name=?, description=?, group_id=?, conditions_json=?,
                logic_operator=?, result_json=?, status=?, priority=?, updated_at=?
            WHERE id=?
            """,
            (
                rule.name,
                rule.description,
                rule.group_id,
                json.dumps([c.to_dict() for c in rule.conditions], ensure_ascii=False),
                rule.logic_operator,
                json.dumps(rule.result.to_dict() if rule.result else None, ensure_ascii=False),
                rule.status,
                rule.priority,
                rule.updated_at,
                rule_id,
            ),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        rule.id = rule_id
        logger.info("更新规则: %s (id=%s)", rule.name, rule_id)
        return rule

    def delete_rule(self, rule_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info("删除规则: id=%s", rule_id)
            return True
        return False

    def get_rule(self, rule_id: int) -> ProcessRule | None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rules WHERE id=?", (rule_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_rule(row)

    def list_rules(
        self,
        group_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
        sort_by: str = "updated_at",
        sort_order: str = "DESC",
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProcessRule]:
        query = "SELECT * FROM rules WHERE 1=1"
        params: list = []

        if group_id is not None:
            query += " AND group_id=?"
            params.append(group_id)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        if keyword:
            query += " AND (name LIKE ? OR description LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        valid_sort = {"name", "created_at", "updated_at", "priority", "status"}
        if sort_by not in valid_sort:
            sort_by = "updated_at"
        if sort_order.upper() not in ("ASC", "DESC"):
            sort_order = "DESC"

        query += f" ORDER BY {sort_by} {sort_order.upper()}"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [self._row_to_rule(row) for row in cursor.fetchall()]

    def count_rules(
        self,
        group_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM rules WHERE 1=1"
        params: list = []

        if group_id is not None:
            query += " AND group_id=?"
            params.append(group_id)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        if keyword:
            query += " AND (name LIKE ? OR description LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()[0]

    def load_all_active_rules(self) -> list[ProcessRule]:
        """加载所有启用状态的规则（用于LNN引擎启动时加载）"""
        return self.list_rules(status="active", sort_by="priority", sort_order="DESC", limit=DEFAULT_QUERY_LIMIT)
