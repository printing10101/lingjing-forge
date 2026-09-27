"""成本追踪数据类（从 cost_tracker 拆出，波次2）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CostDimension(str, Enum):
    """成本统计维度"""

    AGENT = "agent"
    PROJECT = "project"
    GOAL = "goal"
    TASK = "task"
    PROVIDER = "provider"
    MODEL = "model"


class CostType(str, Enum):
    """成本类型"""

    GPU_TIME = "gpu_time"
    GPU_MEMORY = "gpu_memory"
    API_CALLS = "api_calls"
    DATA_TRANSFER = "data_transfer"


class ProviderType(str, Enum):
    """服务提供商"""

    OLLAMA_LOCAL = "ollama_local"
    OPENAI_API = "openai_api"
    CUSTOM_EXTERNAL = "custom_external"
    SYSTEM_INTERNAL = "system_internal"


class ModelType(str, Enum):
    """模型类型"""

    CFC = "CFC"
    LTC = "LTC"
    HYBRID_LNN = "HybridLNN"
    TRANSFORMER = "Transformer"
    CUSTOM = "Custom"


@dataclass
class CostUnitPrice:
    """成本单价配置"""

    gpu_time_per_second: float = 0.0001
    gpu_memory_per_gb_second: float = 0.00005
    api_call_per_request: float = 0.001
    data_transfer_per_mb: float = 0.0001

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu_time_per_second": self.gpu_time_per_second,
            "gpu_memory_per_gb_second": self.gpu_memory_per_gb_second,
            "api_call_per_request": self.api_call_per_request,
            "data_transfer_per_mb": self.data_transfer_per_mb,
        }


@dataclass
class CostEvent:
    """成本事件"""

    event_id: int | None = None
    task_id: str = ""
    agent_id: str = ""
    project_id: str = "default"
    goal_id: str = ""
    provider: str = ProviderType.SYSTEM_INTERNAL.value
    model: str = ""
    cost_type: str = ""
    resource_value: float = 0.0
    cost_value: float = 0.0
    start_time: float | None = None
    end_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    recorded_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "goal_id": self.goal_id,
            "provider": self.provider,
            "model": self.model,
            "cost_type": self.cost_type,
            "resource_value": self.resource_value,
            "cost_value": self.cost_value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "metadata": self.metadata,
            "recorded_at": self.recorded_at,
        }


@dataclass
class CostSummary:
    """成本汇总"""

    dimension: CostDimension
    scope_id: str
    total_cost: float = 0.0
    gpu_time_cost: float = 0.0
    gpu_memory_cost: float = 0.0
    api_calls_cost: float = 0.0
    data_transfer_cost: float = 0.0
    total_gpu_seconds: float = 0.0
    total_gpu_memory_gb_seconds: float = 0.0
    total_api_calls: int = 0
    total_data_transfer_mb: float = 0.0
    task_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "scope_id": self.scope_id,
            "total_cost": round(self.total_cost, 6),
            "gpu_time_cost": round(self.gpu_time_cost, 6),
            "gpu_memory_cost": round(self.gpu_memory_cost, 6),
            "api_calls_cost": round(self.api_calls_cost, 6),
            "data_transfer_cost": round(self.data_transfer_cost, 6),
            "total_gpu_seconds": round(self.total_gpu_seconds, 2),
            "total_gpu_memory_gb_seconds": round(self.total_gpu_memory_gb_seconds, 4),
            "total_api_calls": self.total_api_calls,
            "total_data_transfer_mb": round(self.total_data_transfer_mb, 2),
            "task_count": self.task_count,
        }


@dataclass
class BudgetEvent:
    """预算事件（超限/警告记录）"""

    event_id: int | None = None
    budget_level: str = "global"
    scope_id: str = "default"
    resource_type: str = ""
    current_usage: float = 0.0
    limit_value: float = 0.0
    usage_ratio: float = 0.0
    status: str = "ok"
    recorded_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "budget_level": self.budget_level,
            "scope_id": self.scope_id,
            "resource_type": self.resource_type,
            "current_usage": self.current_usage,
            "limit_value": self.limit_value,
            "usage_ratio": self.usage_ratio,
            "status": self.status,
            "recorded_at": self.recorded_at,
        }
