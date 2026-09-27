"""进给速率优化器。

根据切削条件动态优化进给速率。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CuttingConditions:
    """切削条件参数。"""

    material: str
    tool_diameter: float
    tool_material: str
    depth_of_cut: float
    width_of_cut: float
    spindle_speed: float
    feed_rate: float

    @property
    def surface_speed(self) -> float:
        """计算切削速度 (m/min)。"""
        import math

        return (math.pi * self.tool_diameter * self.spindle_speed) / 1000.0

    @property
    def feed_per_tooth(self) -> float:
        """计算每齿进给量 (mm/tooth)。"""
        # Assume 2 flutes as default
        flute_count = 2
        return self.feed_rate / (self.spindle_speed * flute_count)


class FeedRateOptimizer:
    """进给速率优化器。"""

    def __init__(self) -> None:
        """初始化优化器。"""
        self._material_database: dict[str, dict[str, Any]] = {}
        self._load_default_database()

    def _load_default_database(self) -> None:
        """加载默认材料切削参数数据库。"""
        # 加载完整的材料切削参数数据库
        # 当前使用简化的内置数据库，后续可从外部配置文件或数据库加载
        self._material_database = {
            "aluminum": {
                "surface_speed_range": (150, 600),  # m/min
                "feed_per_tooth_range": (0.05, 0.3),  # mm/tooth
            },
            "steel": {
                "surface_speed_range": (30, 150),
                "feed_per_tooth_range": (0.05, 0.2),
            },
            "stainless": {
                "surface_speed_range": (20, 80),
                "feed_per_tooth_range": (0.03, 0.15),
            },
            "titanium": {
                "surface_speed_range": (15, 60),
                "feed_per_tooth_range": (0.02, 0.1),
            },
        }

    def optimize_feed_rate(
        self,
        conditions: CuttingConditions,
        optimization_goal: str = "efficiency",
        tool_wear_factor: float = 1.0,
        machine_power_kw: float = 10.0,
        surface_finish_ra: float = 3.2,
    ) -> float:
        """优化进给速率。

        Args:
            conditions: 切削条件
            optimization_goal: 优化目标 ("efficiency", "tool_life", "surface_finish")
            tool_wear_factor: 刀具磨损因子 (1.0=新刀, >1.0=磨损刀具)
            machine_power_kw: 机床功率限制 (kW)
            surface_finish_ra: 表面粗糙度要求 (μm)

        Returns:
            优化后的进给速率 (mm/min)
        """
        material_data = self._material_database.get(
            conditions.material.lower(),
            {"surface_speed_range": (50, 200), "feed_per_tooth_range": (0.05, 0.2)},
        )

        # 获取推荐进给范围
        feed_range = material_data["feed_per_tooth_range"]
        base_feed_per_tooth = (feed_range[0] + feed_range[1]) / 2.0

        # 根据优化目标调整进给率
        if optimization_goal == "efficiency":
            # 效率优先：使用较高进给（范围上限70%）
            optimization_factor = 0.7
        elif optimization_goal == "tool_life":
            # 刀具寿命优先：使用中等进给（范围中间值）
            optimization_factor = 0.5
            # 刀具磨损补偿：磨损刀具适当降低进给
            if tool_wear_factor > 1.0:
                wear_compensation = 1.0 / (1.0 + 0.1 * (tool_wear_factor - 1.0))
                optimization_factor *= wear_compensation
        elif optimization_goal == "surface_finish":
            # 表面质量优先：使用较低进给（范围下限30%）
            optimization_factor = 0.3
            # 表面粗糙度补偿：要求越高，进给越低
            if surface_finish_ra < 1.6:
                finish_factor = 1.6 / surface_finish_ra
                optimization_factor /= finish_factor
        else:
            optimization_factor = 0.5

        # 计算机床功率限制下的最大进给
        # 简化模型：切削力 F = Kc * a * f (Kc=比切削力, a=切深, f=每齿进给)
        # 功率 P = F * v / 1000 (kW)
        # 假设比切削力 Kc = 2000 N/mm² (中等硬度钢)
        Kc = 2000  # N/mm²
        cutting_force_per_feed = Kc * conditions.depth_of_cut * conditions.width_of_cut / 1000  # N per mm/tooth
        cutting_speed_m_s = conditions.surface_speed / 60  # m/s

        # 最大功率下的进给限制
        if cutting_force_per_feed > 0 and cutting_speed_m_s > 0:
            max_feed_from_power = (machine_power_kw * 1000) / (cutting_force_per_feed * cutting_speed_m_s)
            # 限制每齿进给不超过功率限制
            power_limited_feed = min(base_feed_per_tooth * optimization_factor, max_feed_from_power)
        else:
            power_limited_feed = base_feed_per_tooth * optimization_factor

        # 确保进给在推荐范围内
        optimized_feed_per_tooth = max(feed_range[0], min(feed_range[1], power_limited_feed))

        # 计算最终进给率 (mm/min)
        # 假设默认2刃刀具，如果条件中有刀具信息则使用
        flute_count = getattr(conditions, "flute_count", 2)
        optimized_feed = optimized_feed_per_tooth * conditions.spindle_speed * flute_count

        logger.debug(
            "Feed rate optimized: %.1f -> %.1f mm/min (goal=%s, wear_factor=%.2f, power_limit=%.1fkW)",
            conditions.feed_rate,
            optimized_feed,
            optimization_goal,
            tool_wear_factor,
            machine_power_kw,
        )

        return optimized_feed

    def validate_conditions(self, conditions: CuttingConditions) -> tuple[bool, list[str]]:
        """验证切削条件是否在推荐范围内。

        Args:
            conditions: 切削条件

        Returns:
            (是否有效, 警告信息列表)
        """
        warnings: list[str] = []

        material_data = self._material_database.get(conditions.material.lower())
        if material_data is None:
            warnings.append(f"Material '{conditions.material}' not in database")
            return True, warnings

        surface_speed = conditions.surface_speed
        speed_range = material_data["surface_speed_range"]
        if surface_speed < speed_range[0] or surface_speed > speed_range[1]:
            warnings.append(
                f"Surface speed {surface_speed:.1f} m/min outside recommended range "
                f"{speed_range[0]}-{speed_range[1]} m/min"
            )

        feed_per_tooth = conditions.feed_per_tooth
        feed_range = material_data["feed_per_tooth_range"]
        if feed_per_tooth < feed_range[0] or feed_per_tooth > feed_range[1]:
            warnings.append(
                f"Feed per tooth {feed_per_tooth:.3f} mm/tooth outside recommended range "
                f"{feed_range[0]}-{feed_range[1]} mm/tooth"
            )

        return len(warnings) == 0, warnings
