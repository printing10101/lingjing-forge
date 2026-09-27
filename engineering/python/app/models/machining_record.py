"""MachiningRecord Pydantic models.

Unified data model for machining records.  Provides a Pydantic-level view
of the entity used by the API/service layer, decoupled from the persistence
layer (see :mod:`app.database.models.machining_record` for the SQLAlchemy
mapping).

设计要点：
    - 核心字段 5-7 个，简洁明确：machine_id / tool_id / material /
      timestamp / spindle_speed / feed_rate / tdengine_series_id。
    - 时序数据通过 ``tdengine_series_id`` 引用 TDengine 存储的传感器数据，
      不在 PostgreSQL 中冗余保存。
    - ``process_params`` 字段使用 Pydantic ``dict`` 映射，持久化时由
      SQLAlchemy 转为 PostgreSQL JSONB 类型。
    - 字段范围校验遵循 Pydantic v2 ``Field`` 约束（``ge`` / ``le`` /
      ``min_length`` / ``max_length``）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.utils.id import new_id


def _utc_now() -> datetime:
    """返回带时区的当前时间戳。"""
    return datetime.now(timezone.utc)


def _new_record_id() -> str:
    """生成记录主键 ID（UUID4 字符串）。"""
    return new_id("mrec")


class MachiningRecordBase(BaseModel):
    """MachiningRecord 字段基础模型，定义核心字段与校验规则。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        json_schema_extra={
            "example": {
                "machine_id": "CNC-01",
                "tool_id": "T-EM-10",
                "material": "45号钢",
                "spindle_speed": 4500.0,
                "feed_rate": 800.0,
                "tdengine_series_id": "ts_2026_06_11_001",
                "process_params": {
                    "depth_of_cut": 1.5,
                    "coolant": True,
                    "operation": "face_milling",
                },
            }
        },
    )

    machine_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="机床标识，对应 machines.json 中的 machine.id",
    )
    tool_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="刀具标识，对应 tools.json 中的 tool.id",
    )
    material: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="工件材料名称，例如 45号钢 / 6061铝合金",
    )
    timestamp: datetime = Field(
        default_factory=_utc_now,
        description="加工记录发生时间（带时区）",
    )
    spindle_speed: float = Field(
        ...,
        ge=0.0,
        le=200000.0,
        description="主轴转速，单位 RPM，物理范围约束",
    )
    feed_rate: float = Field(
        ...,
        ge=0.0,
        le=50000.0,
        description="进给速度，单位 mm/min，物理范围约束",
    )
    tdengine_series_id: str | None = Field(
        default=None,
        max_length=128,
        description="TDengine 时序数据引用 ID（spindle_actual / feed_actual / "
        "vibration 等高频数据存储于 TDengine，本字段仅保存引用）",
    )
    process_params: dict[str, Any] = Field(
        default_factory=dict,
        description="附加工艺参数（depth_of_cut / coolant / operation 等），持久化时映射到 PostgreSQL JSONB",
    )


class MachiningRecordCreate(MachiningRecordBase):
    """创建 MachiningRecord 时的入参模型。"""

    record_id: str | None = Field(
        default=None,
        max_length=64,
        description="可选的记录 ID；为空时由仓储层自动生成 UUID",
    )


class MachiningRecordUpdate(BaseModel):
    """局部更新模型；所有字段可选，None 表示不修改。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    spindle_speed: float | None = Field(
        default=None,
        ge=0.0,
        le=200000.0,
        description="主轴转速，单位 RPM",
    )
    feed_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=50000.0,
        description="进给速度，单位 mm/min",
    )
    tdengine_series_id: str | None = Field(
        default=None,
        max_length=128,
        description="TDengine 时序数据引用 ID",
    )
    process_params: dict[str, Any] | None = Field(
        default=None,
        description="附加工艺参数；提供时整体替换",
    )


class MachiningRecordRead(MachiningRecordBase):
    """读取/返回模型，包含数据库生成的字段。"""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    record_id: str = Field(
        default_factory=_new_record_id,
        max_length=64,
        description="记录主键 ID",
    )
    created_at: datetime | None = Field(
        default=None,
        description="记录入库时间（由数据库生成）",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="记录最后更新时间（由数据库自动维护）",
    )

    @classmethod
    def from_attributes(cls, obj: Any) -> "MachiningRecordRead":
        """兼容 SQLAlchemy ORM 实例的构造方法（model_config.from_attributes）。"""
        return cls.model_validate(obj)


__all__ = [
    "MachiningRecordBase",
    "MachiningRecordCreate",
    "MachiningRecordUpdate",
    "MachiningRecordRead",
    "_utc_now",
    "_new_record_id",
]
