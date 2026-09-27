"""工业标准刀具与材料数据库模块。

提供材料、刀具、机床参数查询以及LNN输出物理约束校验。
"""

from __future__ import annotations

from app.database.materials import MaterialDatabase, MaterialEntry
from app.database.tools import ToolDatabase, ToolEntry
from app.database.machines import MachineDatabase, MachineEntry
from app.database.constraints import (
    ConstraintResult,
    ConstraintViolation,
    CuttingConstraintValidator,
)
from app.database.models.machining_record import (
    Base as MachiningRecordBase,
    MachiningRecord,
)

__all__ = [
    "MaterialDatabase",
    "MaterialEntry",
    "ToolDatabase",
    "ToolEntry",
    "MachineDatabase",
    "MachineEntry",
    "CuttingConstraintValidator",
    "ConstraintResult",
    "ConstraintViolation",
    "MachiningRecordBase",
    "MachiningRecord",
]
