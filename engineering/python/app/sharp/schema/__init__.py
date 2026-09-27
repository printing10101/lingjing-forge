"""SHARP Schema 子模块（M1）。

封装领域本体、约束校验与战略规划器，是 SHARP 四大组件中的"Schema-Aware"基础。

导出
----
- `EntityType` / `RelationType`          实体与关系类型枚举
- `Triple`                                三元组结构
- `DomainSchema`                          领域 Schema（实体/关系/域值域映射）
- `SchemaConstraints`                     约束校验器
- `StrategicPlanner` / `VerificationStrategy`  战略规划器
"""

from __future__ import annotations

from app.sharp.schema.domain_schema import (
    DEFAULT_SCHEMA,
    DomainSchema,
    EntityType,
    RelationType,
    Triple,
)
from app.sharp.schema.schema_constraints import (
    SchemaConstraints,
    ValidationError,
    ValidationResult,
)
from app.sharp.schema.strategic_planner import (
    StrategicPlanner,
    VerificationStrategy,
)

__all__ = [
    "DEFAULT_SCHEMA",
    "DomainSchema",
    "EntityType",
    "RelationType",
    "Triple",
    "SchemaConstraints",
    "ValidationError",
    "ValidationResult",
    "StrategicPlanner",
    "VerificationStrategy",
]
