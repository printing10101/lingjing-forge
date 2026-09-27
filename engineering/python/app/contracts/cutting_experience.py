"""Cutting experience contract (P2-1): 实测加工数据 Schema.

数据飞轮核心契约：一次切削加工的全要素记录 —— 工艺参数、实测结果、
异常情况、以及用于飞轮优化的元数据。契约设计目标：

1. **可追溯**：每个记录关联 job / machine / tool / program，支撑 ISO 9001 审计。
2. **可训练**：字段直接可作为 LNN 参数优化模型的监督信号（X=参数, y=结果）。
3. **可扩展**：`anomalies` 与 `tags` 为自由形态，兼容未来传感器扩展
   （MTConnect 振动/温度/功率）。
4. **稳定**：Schema 一经发布只向后兼容扩展（新增字段必须有默认值）。

参考文档：docs/development/数据飞轮-Schema 设计.md（计划中）
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# 枚举


class MachiningType(str, Enum):
    """加工类型。"""

    MILLING = "milling"
    TURNING = "turning"
    DRILLING = "drilling"
    TAPPING = "tapping"
    BORING = "boring"
    GROOVING = "grooving"
    THREADING = "threading"


class CoolantMode(str, Enum):
    """冷却液模式。"""

    OFF = "off"
    FLOOD = "flood"
    MIST = "mist"
    THROUGH_TOOL = "through_tool"


class MachiningResult(str, Enum):
    """加工结果判定。"""

    OK = "ok"
    REWORK = "rework"
    SCRAP = "scrap"


# 参数与结果


class CuttingParameters(BaseModel):
    """切削工艺参数（独立于具体机床的工艺要素）。

    这些字段直接构成参数优化模型的输入特征。
    """

    model_config = ConfigDict(extra="forbid")

    depth_of_cut_mm: float = Field(gt=0, description="切深 (mm)")
    feed_mm_per_rev: float = Field(gt=0, description="每转进给 (mm/rev)")
    spindle_rpm: float = Field(gt=0, description="主轴转速 (RPM)")
    cutting_speed_m_min: float | None = Field(default=None, gt=0, description="切削速度 (m/min)，可推导但允许显式给定")
    stepover_mm: float | None = Field(default=None, gt=0, description="步距 (mm)")
    coolant: CoolantMode = Field(default=CoolantMode.FLOOD, description="冷却液模式")


class CuttingResults(BaseModel):
    """加工实测结果（飞轮优化模型的监督信号）。"""

    model_config = ConfigDict(extra="forbid")

    cycle_time_s: float = Field(gt=0, description="实际加工节拍 (s)")
    surface_roughness_ra: float | None = Field(default=None, ge=0, description="表面粗糙度 Ra (μm)")
    tool_wear_percent: float | None = Field(default=None, ge=0, le=100, description="刀具磨损百分比 (0-100)")
    dimensional_error_mm: float | None = Field(default=None, ge=0, description="尺寸误差绝对值 (mm)")
    result: MachiningResult = Field(default=MachiningResult.OK, description="加工结果")


# 异常记录


class MachiningAnomaly(BaseModel):
    """加工异常快照（颤振/过载/报警）。"""

    model_config = ConfigDict(extra="forbid")

    anomaly_type: str = Field(description="异常类型：chatter/overload/temperature/alarm…")
    severity: int = Field(default=1, ge=1, le=10, description="严重程度 1-10")
    message: str = Field(default="", description="异常描述")
    measured_value: float | None = Field(default=None, description="实测值（如振动 mm/s）")
    threshold_value: float | None = Field(default=None, description="触发阈值")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="发生时间")


# 主记录


class CuttingExperience(BaseModel):
    """一次切削加工的全要素记录。

    既是数据库持久化对象（SQLAlchemy 转换源），也是采集 API 的请求/响应契约。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID = Field(default_factory=uuid4, description="记录 ID")
    job_id: UUID | None = Field(default=None, description="关联工艺任务 ID")
    machine_id: str = Field(min_length=1, max_length=64, description="机床标识")
    program_number: str = Field(default="", max_length=32, description="NC 程序号")
    tool_id: str = Field(min_length=1, max_length=64, description="刀具标识")
    material: str = Field(default="", max_length=64, description="工件材料")

    machining_type: MachiningType = Field(default=MachiningType.MILLING)
    parameters: CuttingParameters = Field(description="切削工艺参数")
    results: CuttingResults = Field(description="加工实测结果")

    anomalies: list[MachiningAnomaly] = Field(default_factory=list, description="异常快照列表")
    tags: dict[str, Any] = Field(default_factory=dict, description="自由扩展元数据")

    operator: str | None = Field(default=None, max_length=64, description="操作员")
    source: str = Field(default="manual", max_length=32, description="数据来源：manual/mtconnect/api")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="创建时间")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="更新时间")


# 查询与统计


class ExperienceQuery(BaseModel):
    """cutting_experience 查询条件。"""

    model_config = ConfigDict(extra="forbid")

    machine_id: str | None = None
    tool_id: str | None = None
    material: str | None = None
    machining_type: MachiningType | None = None
    result: MachiningResult | None = None
    has_anomaly: bool | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ExperienceStats(BaseModel):
    """聚合统计结果。"""

    model_config = ConfigDict(extra="forbid")

    total_records: int
    avg_cycle_time_s: float | None = None
    avg_surface_roughness_ra: float | None = None
    avg_tool_wear_percent: float | None = None
    ok_rate: float | None = Field(default=None, ge=0, le=1, description="合格率 0-1")
    anomaly_rate: float | None = Field(default=None, ge=0, le=1, description="异常率 0-1")


__all__ = [
    "CoolantMode",
    "CuttingExperience",
    "CuttingParameters",
    "CuttingResults",
    "ExperienceQuery",
    "ExperienceStats",
    "MachiningAnomaly",
    "MachiningResult",
    "MachiningType",
]
