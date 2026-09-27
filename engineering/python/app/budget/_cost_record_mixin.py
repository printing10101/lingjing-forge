"""_CostRecordMixin (split from MultiDimensionCostTracker)."""

from __future__ import annotations

import logging
import time
import json
from typing import Any
from app.utils.sqlite_retry import sqlite_retry

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


class _CostRecordMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any
    _unit_prices: Any

    def _calculate_cost(self, cost_type: str, resource_value: float) -> float:
        """根据资源类型和用量计算成本"""
        prices = self._unit_prices
        if cost_type == CostType.GPU_TIME.value:
            return resource_value * prices.gpu_time_per_second
        elif cost_type == CostType.GPU_MEMORY.value:
            return resource_value * prices.gpu_memory_per_gb_second
        elif cost_type == CostType.API_CALLS.value:
            return resource_value * prices.api_call_per_request
        elif cost_type == CostType.DATA_TRANSFER.value:
            return resource_value * prices.data_transfer_per_mb
        return 0.0

    @sqlite_retry()
    def record_cost(
        self,
        task_id: str,
        cost_type: str,
        resource_value: float,
        agent_id: str = "",
        project_id: str = "default",
        goal_id: str = "",
        provider: str = ProviderType.SYSTEM_INTERNAL.value,
        model: str = "",
        start_time: float | None = None,
        end_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CostEvent:
        """记录成本事件"""
        cost_value = self._calculate_cost(cost_type, resource_value)
        now = time.time()

        event = CostEvent(
            task_id=task_id,
            agent_id=agent_id,
            project_id=project_id,
            goal_id=goal_id,
            provider=provider,
            model=model,
            cost_type=cost_type,
            resource_value=resource_value,
            cost_value=cost_value,
            start_time=start_time,
            end_time=end_time,
            metadata=metadata or {},
            recorded_at=now,
        )

        cursor = self._conn.execute(
            """INSERT INTO cost_events
               (task_id, agent_id, project_id, goal_id, provider, model,
                cost_type, resource_value, cost_value, start_time, end_time,
                metadata, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.task_id,
                event.agent_id,
                event.project_id,
                event.goal_id,
                event.provider,
                event.model,
                event.cost_type,
                event.resource_value,
                event.cost_value,
                event.start_time,
                event.end_time,
                json.dumps(event.metadata),
                event.recorded_at,
            ),
        )
        self._conn.commit()

        event.event_id = cursor.lastrowid
        logger.debug(
            "Cost recorded: task=%s type=%s value=%.4f cost=%.6f",
            task_id,
            cost_type,
            resource_value,
            cost_value,
        )
        return event

    def record_gpu_time(
        self,
        task_id: str,
        gpu_seconds: float,
        agent_id: str = "",
        project_id: str = "default",
        model: str = "",
        provider: str = ProviderType.SYSTEM_INTERNAL.value,
        start_time: float | None = None,
        end_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CostEvent:
        """记录GPU计算时间成本"""
        return self.record_cost(
            task_id=task_id,
            cost_type=CostType.GPU_TIME.value,
            resource_value=gpu_seconds,
            agent_id=agent_id,
            project_id=project_id,
            provider=provider,
            model=model,
            start_time=start_time,
            end_time=end_time,
            metadata=metadata,
        )

    def record_gpu_memory(
        self,
        task_id: str,
        gb_seconds: float,
        agent_id: str = "",
        project_id: str = "default",
        model: str = "",
        provider: str = ProviderType.SYSTEM_INTERNAL.value,
        metadata: dict[str, Any] | None = None,
    ) -> CostEvent:
        """记录GPU内存使用成本（GB-秒）"""
        return self.record_cost(
            task_id=task_id,
            cost_type=CostType.GPU_MEMORY.value,
            resource_value=gb_seconds,
            agent_id=agent_id,
            project_id=project_id,
            provider=provider,
            model=model,
            metadata=metadata,
        )

    def record_gpu_usage(self, task_id: str, gpu_hours: float, agent_id: str | None = None) -> CostEvent:
        return self.record_gpu_time(
            task_id=task_id,
            gpu_seconds=gpu_hours * 3600.0,
            agent_id=agent_id or "",
        )

    def record_memory_usage(self, task_id: str, memory_mb: float, agent_id: str | None = None) -> CostEvent:
        return self.record_gpu_memory(
            task_id=task_id,
            gb_seconds=memory_mb / 1024.0,
            agent_id=agent_id or "",
        )

    def record_api_call(
        self,
        task_id: str,
        count: int = 1,
        agent_id: str = "",
        project_id: str = "default",
        provider: str = ProviderType.OLLAMA_LOCAL.value,
        model: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CostEvent:
        """记录API调用成本"""
        return self.record_cost(
            task_id=task_id,
            cost_type=CostType.API_CALLS.value,
            resource_value=float(count),
            agent_id=agent_id,
            project_id=project_id,
            provider=provider,
            model=model,
            metadata=metadata,
        )

    def record_data_transfer(
        self,
        task_id: str,
        mb_amount: float,
        agent_id: str = "",
        project_id: str = "default",
        direction: str = "upload",
        metadata: dict[str, Any] | None = None,
    ) -> CostEvent:
        """记录数据传输成本"""
        meta = metadata or {}
        meta["direction"] = direction
        return self.record_cost(
            task_id=task_id,
            cost_type=CostType.DATA_TRANSFER.value,
            resource_value=mb_amount,
            agent_id=agent_id,
            project_id=project_id,
            metadata=meta,
        )
