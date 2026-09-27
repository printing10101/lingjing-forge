"""工艺规划阈值参数配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _float_env, _int_env


@dataclass
class ProcessPlanningConfig:
    surface_roughness_ra_default: float = field(default_factory=lambda: _float_env("LNN_PP_RA_DEFAULT", 3.2))
    min_plane_area_mm2: float = field(default_factory=lambda: _float_env("LNN_PP_MIN_PLANE_AREA", 1.0))
    min_cavity_dimension_mm: float = field(default_factory=lambda: _float_env("LNN_PP_MIN_CAVITY_DIM", 0.5))
    min_boss_diameter_mm: float = field(default_factory=lambda: _float_env("LNN_PP_MIN_BOSS_DIAM", 1.0))
    min_hole_diameter_mm: float = field(default_factory=lambda: _float_env("LNN_PP_MIN_HOLE_DIAM", 0.5))
    standard_drill_point_angle_deg: float = field(default_factory=lambda: _float_env("LNN_PP_DRILL_ANGLE", 118.0))
    gcode_default_program_number: int = field(default_factory=lambda: _int_env("LNN_PP_GCODE_PROG_NUM", 1000))
