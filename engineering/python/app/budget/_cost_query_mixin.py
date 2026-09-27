"""_CostQueryMixin (split from MultiDimensionCostTracker)."""

from __future__ import annotations

import logging
import time
import json
import sqlite3
from typing import Any
from app.budget.sql_safety import validate_cost_dimension_column

from app.budget._cost_models import (  # noqa: F401
    CostDimension,
    CostType,
    ProviderType,
    ModelType,
    CostUnitPrice,
    CostEvent,
    CostSummary,
    BudgetEvent,
)

logger = logging.getLogger(__name__)


class _CostQueryMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any

    def get_task_costs(self, task_id: str) -> list[dict[str, Any]]:
        """获取任务的所有成本记录"""
        rows = self._conn.execute(
            "SELECT * FROM cost_events WHERE task_id = ? ORDER BY recorded_at ASC",
            (task_id,),
        ).fetchall()
        return [self._row_to_cost_dict(row) for row in rows]

    def get_task_total_cost(self, task_id: str) -> float:
        """获取任务总成本"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_value), 0) as total FROM cost_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row["total"] if row else 0.0

    def get_cost_summary(
        self,
        dimension: CostDimension,
        scope_id: str = "",
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> CostSummary:
        """获取指定维度的成本汇总"""
        dim_column = {
            CostDimension.AGENT: "agent_id",
            CostDimension.PROJECT: "project_id",
            CostDimension.GOAL: "goal_id",
            CostDimension.TASK: "task_id",
            CostDimension.PROVIDER: "provider",
            CostDimension.MODEL: "model",
        }.get(dimension, "agent_id")
        # 深度防御：即使 dim_column 来自可控字典，仍进行白名单校验
        dim_column = validate_cost_dimension_column(dim_column)

        conditions = [f"{dim_column} = ?"]
        params: list[Any] = [scope_id]

        if start_time is not None:
            conditions.append("recorded_at >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("recorded_at <= ?")
            params.append(end_time)

        where = " AND ".join(conditions)

        rows = self._conn.execute(
            f"""SELECT cost_type,
                       SUM(resource_value) as total_resource,
                       SUM(cost_value) as total_cost,
                       COUNT(DISTINCT task_id) as task_count
                FROM cost_events
                WHERE {where}
                GROUP BY cost_type""",
            params,
        ).fetchall()

        summary = CostSummary(dimension=dimension, scope_id=scope_id)

        for row in rows:
            ct = row["cost_type"]
            if ct == CostType.GPU_TIME.value:
                summary.gpu_time_cost = row["total_cost"]
                summary.total_gpu_seconds = row["total_resource"]
            elif ct == CostType.GPU_MEMORY.value:
                summary.gpu_memory_cost = row["total_cost"]
                summary.total_gpu_memory_gb_seconds = row["total_resource"]
            elif ct == CostType.API_CALLS.value:
                summary.api_calls_cost = row["total_cost"]
                summary.total_api_calls = int(row["total_resource"])
            elif ct == CostType.DATA_TRANSFER.value:
                summary.data_transfer_cost = row["total_cost"]
                summary.total_data_transfer_mb = row["total_resource"]
            summary.total_cost += row["total_cost"]
            if row["task_count"] > summary.task_count:
                summary.task_count = row["task_count"]

        return summary

    def get_all_summaries(
        self,
        dimension: CostDimension,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> list[CostSummary]:
        """获取某维度下所有范围的成本汇总"""
        dim_column = {
            CostDimension.AGENT: "agent_id",
            CostDimension.PROJECT: "project_id",
            CostDimension.GOAL: "goal_id",
            CostDimension.TASK: "task_id",
            CostDimension.PROVIDER: "provider",
            CostDimension.MODEL: "model",
        }.get(dimension, "agent_id")
        # 深度防御：白名单校验
        dim_column = validate_cost_dimension_column(dim_column)

        conditions = []
        params = []

        if start_time is not None:
            conditions.append("recorded_at >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("recorded_at <= ?")
            params.append(end_time)

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = self._conn.execute(
            f"""SELECT {dim_column} as scope_id,
                       cost_type,
                       SUM(resource_value) as total_resource,
                       SUM(cost_value) as total_cost,
                       COUNT(DISTINCT task_id) as task_count
                FROM cost_events
                WHERE {where}
                GROUP BY {dim_column}, cost_type
                ORDER BY total_cost DESC""",
            params,
        ).fetchall()

        summary_map: dict[str, CostSummary] = {}

        for row in rows:
            sid = row["scope_id"] or "(unknown)"
            if sid not in summary_map:
                summary_map[sid] = CostSummary(dimension=dimension, scope_id=sid)

            sm = summary_map[sid]
            ct = row["cost_type"]
            if ct == CostType.GPU_TIME.value:
                sm.gpu_time_cost = row["total_cost"]
                sm.total_gpu_seconds = row["total_resource"]
            elif ct == CostType.GPU_MEMORY.value:
                sm.gpu_memory_cost = row["total_cost"]
                sm.total_gpu_memory_gb_seconds = row["total_resource"]
            elif ct == CostType.API_CALLS.value:
                sm.api_calls_cost = row["total_cost"]
                sm.total_api_calls = int(row["total_resource"])
            elif ct == CostType.DATA_TRANSFER.value:
                sm.data_transfer_cost = row["total_cost"]
                sm.total_data_transfer_mb = row["total_resource"]
            sm.total_cost += row["total_cost"]
            if row["task_count"] > sm.task_count:
                sm.task_count = row["task_count"]

        return sorted(summary_map.values(), key=lambda s: s.total_cost, reverse=True)

    def get_cost_trend(self, days: int = 30, interval_hours: int = 24) -> list[dict[str, Any]]:
        """获取成本趋势数据"""
        cutoff = time.time() - (days * 86400)

        rows = self._conn.execute(
            """SELECT
                   CAST(recorded_at / ? AS INTEGER) * ? as bucket,
                   cost_type,
                   SUM(cost_value) as total_cost,
                   SUM(resource_value) as total_resource,
                   COUNT(*) as event_count
               FROM cost_events
               WHERE recorded_at >= ?
               GROUP BY bucket, cost_type
               ORDER BY bucket ASC""",
            (interval_hours * 3600, interval_hours * 3600, cutoff),
        ).fetchall()

        trend: dict[int, dict[str, Any]] = {}
        for row in rows:
            bucket = row["bucket"]
            if bucket not in trend:
                trend[bucket] = {
                    "timestamp": bucket,
                    "gpu_time_cost": 0.0,
                    "gpu_memory_cost": 0.0,
                    "api_calls_cost": 0.0,
                    "data_transfer_cost": 0.0,
                    "total_cost": 0.0,
                    "event_count": 0,
                }
            ct = row["cost_type"]
            entry = trend[bucket]
            entry["total_cost"] += row["total_cost"]
            entry["event_count"] += row["event_count"]
            if ct == CostType.GPU_TIME.value:
                entry["gpu_time_cost"] = row["total_cost"]
            elif ct == CostType.GPU_MEMORY.value:
                entry["gpu_memory_cost"] = row["total_cost"]
            elif ct == CostType.API_CALLS.value:
                entry["api_calls_cost"] = row["total_cost"]
            elif ct == CostType.DATA_TRANSFER.value:
                entry["data_transfer_cost"] = row["total_cost"]

        return sorted(trend.values(), key=lambda x: x["timestamp"])

    def _row_to_cost_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
        return d
