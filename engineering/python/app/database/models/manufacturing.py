"""
SQLAlchemy ORM models for manufacturing domain.

Defines Material, Equipment, EquipmentAlarm, MaintenancePlan,
QualityRecord, QualityAnomaly, ProductionRecord, WorkOrder,
ProcessRoute, ProcessStep, and Document models.

本模块为门面：实现已拆分至 _material_models / _equipment_models / _quality_models / _production_models / _document_models。
"""

from __future__ import annotations

from app.database.models._document_models import Document  # noqa: F401
from app.database.models._equipment_models import (  # noqa: F401
    Equipment,
    EquipmentAlarm,
    MaintenancePlan,
)
from app.database.models._material_models import Material  # noqa: F401
from app.database.models._production_models import (  # noqa: F401
    ProcessRoute,
    ProcessStep,
    ProductionRecord,
    WorkOrder,
)
from app.database.models._quality_models import (  # noqa: F401
    QualityAnomaly,
    QualityRecord,
)
