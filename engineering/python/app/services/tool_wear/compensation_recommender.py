"""补偿推荐子服务：刀具补偿量与切削参数调整方案计算。"""

import math
from typing import Any

from app.services.tool_wear._constants import (
    DEFAULT_REPLACEMENT_THRESHOLD,
    get_material_params,
    get_tool_params,
)


class CompensationRecommender:
    """基于磨损状态与机床能力推荐切削参数补偿方案。"""

    def __init__(self) -> None:
        self.default_replacement_threshold = DEFAULT_REPLACEMENT_THRESHOLD

    def get_compensation_recommendations(
        self,
        current_wear: float,
        input_parameters: dict,
        machine_capabilities: dict | None = None,
    ) -> dict[str, Any]:
        """基于当前磨损状态推荐切削参数补偿方案。

        综合考虑磨损程度、机床能力限制和加工质量要求，
        提供可执行的参数调整建议。

        Args:
            current_wear: 当前磨损量 (mm)
            input_parameters: 当前切削参数
            machine_capabilities: 机床能力限制（可选），包含：
                - max_spindle_speed: 最大主轴转速
                - max_feed_rate: 最大进给速度
                - max_power: 最大功率
                - max_torque: 最大扭矩

        Returns:
            参数补偿建议字典
        """
        cutting_speed = input_parameters.get("cutting_speed", 150.0)
        feed_rate = input_parameters.get("feed_rate", 0.2)
        depth_of_cut = input_parameters.get("depth_of_cut", 1.5)
        material_type = input_parameters.get("material_type", "steel_45")
        tool_type = input_parameters.get("tool_type", "carbide")

        material = get_material_params(material_type)
        tool = get_tool_params(tool_type)
        wear_threshold = tool.get("max_vb", self.default_replacement_threshold)
        wear_ratio = current_wear / wear_threshold

        # 机床能力限制（提供默认值）
        if machine_capabilities is None:
            machine_capabilities = {
                "max_spindle_speed": 24000,
                "max_feed_rate": 20000,
                "max_power": 15.0,
                "max_torque": 100.0,
            }

        # 根据磨损程度确定调整策略
        if wear_ratio > 0.9:
            strategy = "replace_tool"
            urgency = "critical"
            speed_reduction = 0.40
            feed_reduction = 0.30
            depth_reduction = 0.25
        elif wear_ratio > 0.7:
            strategy = "aggressive_compensation"
            urgency = "critical"
            speed_reduction = 0.30
            feed_reduction = 0.20
            depth_reduction = 0.15
        elif wear_ratio > 0.5:
            strategy = "moderate_compensation"
            urgency = "warning"
            speed_reduction = 0.15
            feed_reduction = 0.10
            depth_reduction = 0.05
        elif wear_ratio > 0.3:
            strategy = "slight_compensation"
            urgency = "normal"
            speed_reduction = 0.05
            feed_reduction = 0.0
            depth_reduction = 0.0
        else:
            strategy = "no_adjustment"
            urgency = "normal"
            speed_reduction = 0.0
            feed_reduction = 0.0
            depth_reduction = 0.0

        # 计算调整后的参数
        new_speed = cutting_speed * (1.0 - speed_reduction)
        new_feed = feed_rate * (1.0 - feed_reduction)
        new_depth = depth_of_cut * (1.0 - depth_reduction)

        # 机床能力限制校验
        warnings: list[str] = []
        if new_speed > 0:
            # 计算主轴转速
            tool_diameter = input_parameters.get("tool_diameter", 10.0)
            if tool_diameter > 0:
                spindle_speed = (new_speed * 1000.0) / (math.pi * tool_diameter)
                max_spindle = machine_capabilities.get("max_spindle_speed", 24000)
                if spindle_speed > max_spindle:
                    warnings.append(f"调整后主轴转速{spindle_speed:.0f}RPM超过机床最大值{max_spindle}RPM，已自动限制")
                    spindle_speed = max_spindle
                    new_speed = (spindle_speed * math.pi * tool_diameter) / 1000.0

        max_feed = machine_capabilities.get("max_feed_rate", 20000)
        new_feed_mm_min = new_feed * 1000.0  # 转换为 mm/min
        if new_feed_mm_min > max_feed:
            warnings.append(f"调整后进给速度{new_feed_mm_min:.0f}mm/min超过机床最大值{max_feed}mm/min，已自动限制")
            new_feed = max_feed / 1000.0

        # 计算预期寿命延长
        life_extension = 0.0
        if speed_reduction > 0 and strategy != "no_adjustment":
            n = material.taylor_n
            speed_factor = (1.0 - speed_reduction) ** (-1.0 / n)
            life_extension = (speed_factor - 1.0) * 100.0

        # 生成建议项
        suggestions: list[dict[str, Any]] = []

        if speed_reduction > 0:
            suggestions.append(
                {
                    "param": "cutting_speed",
                    "current": round(cutting_speed, 2),
                    "recommended": round(new_speed, 2),
                    "change_percent": round(-speed_reduction * 100, 1),
                    "reason": f"磨损率{wear_ratio * 100:.0f}%，降低切削速度以延长寿命",
                    "expected_life_extension_percent": round(life_extension, 1),
                }
            )

        if feed_reduction > 0:
            suggestions.append(
                {
                    "param": "feed_rate",
                    "current": round(feed_rate, 3),
                    "recommended": round(new_feed, 3),
                    "change_percent": round(-feed_reduction * 100, 1),
                    "reason": "降低进给量以减少切削力",
                }
            )

        if depth_reduction > 0:
            suggestions.append(
                {
                    "param": "depth_of_cut",
                    "current": round(depth_of_cut, 2),
                    "recommended": round(new_depth, 2),
                    "change_percent": round(-depth_reduction * 100, 1),
                    "reason": "降低切深以改善散热",
                }
            )

        if strategy == "replace_tool":
            suggestions.append(
                {
                    "param": "tool_replacement",
                    "current": "current_tool",
                    "recommended": "new_tool",
                    "change_percent": 0,
                    "reason": "刀具磨损已达临界值，必须立即更换",
                }
            )

        return {
            "current_wear": round(current_wear, 4),
            "wear_ratio": round(wear_ratio, 3),
            "strategy": strategy,
            "urgency": urgency,
            "suggestions": suggestions,
            "expected_life_extension_percent": round(life_extension, 1),
            "warnings": warnings,
            "machine_capability_checked": True,
        }
