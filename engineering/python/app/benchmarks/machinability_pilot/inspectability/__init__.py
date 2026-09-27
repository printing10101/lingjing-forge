"""可检性评定层（S6）：几何决定的认证所需测量不确定度。

见 :mod:`access` 的模块文档。判据冻结在 ``criteria_frozen_v1.json``。
"""

from app.benchmarks.machinability_pilot.inspectability.access import (
    CriteriaError,
    GeometryCriteria,
    assess_case,
    optic_penalty_field,
    tactile_reach_field,
    view_directions,
)

__all__ = [
    "CriteriaError",
    "GeometryCriteria",
    "assess_case",
    "optic_penalty_field",
    "tactile_reach_field",
    "view_directions",
]
