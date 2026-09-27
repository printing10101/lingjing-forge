"""切削参数建议子服务：基于磨损状态推荐参数调整。"""

from app.models.validation import (
    AdjustmentSuggestion,
    AdjustmentSuggestionItem,
    UrgencyLevel,
)
from app.services.tool_wear._constants import (
    DEFAULT_REPLACEMENT_THRESHOLD,
    get_material_params,
    get_tool_params,
)


class ParameterAdvisor:
    """根据当前磨损量与剩余寿命给出切削参数调整建议。"""

    def __init__(self) -> None:
        self.default_replacement_threshold = DEFAULT_REPLACEMENT_THRESHOLD

    def suggest_parameter_adjustment(
        self, current_wear: float, remaining_life: float, current_parameters: dict
    ) -> AdjustmentSuggestion:
        cutting_speed = current_parameters.get("cutting_speed", 150.0)
        feed_rate = current_parameters.get("feed_rate", 0.2)
        depth_of_cut = current_parameters.get("depth_of_cut", 1.5)
        coolant_flow = current_parameters.get("coolant_flow", 10.0)
        material_type = current_parameters.get("material_type", "steel_45")
        tool_type = current_parameters.get("tool_type", "carbide")

        material = get_material_params(material_type)
        tool = get_tool_params(tool_type)
        wear_threshold = tool.get("max_vb", self.default_replacement_threshold)
        wear_ratio = current_wear / wear_threshold

        if wear_ratio > 0.8:
            urgency = UrgencyLevel.CRITICAL
            speed_reduction = 0.30
            feed_reduction = 0.20
            depth_reduction = 0.15
            coolant_increase = 0.50
        elif wear_ratio > 0.5:
            urgency = UrgencyLevel.WARNING
            speed_reduction = 0.15
            feed_reduction = 0.10
            depth_reduction = 0.05
            coolant_increase = 0.25
        else:
            urgency = UrgencyLevel.NORMAL
            speed_reduction = 0.05
            feed_reduction = 0.0
            depth_reduction = 0.0
            coolant_increase = 0.10

        new_speed = round(cutting_speed * (1.0 - speed_reduction), 1)
        new_feed = round(feed_rate * (1.0 - feed_reduction), 3)
        new_depth = round(depth_of_cut * (1.0 - depth_reduction), 2)
        new_coolant = round(coolant_flow * (1.0 + coolant_increase), 1)

        estimated_life_extension = 0.0
        if speed_reduction > 0:
            n = material.taylor_n
            speed_factor = (1.0 - speed_reduction) ** (-1.0 / n)
            estimated_life_extension = (speed_factor - 1.0) * 100.0

        suggestions = []

        if speed_reduction > 0:
            suggestions.append(
                AdjustmentSuggestionItem(
                    parameter="cutting_speed",
                    current_value=cutting_speed,
                    suggested_value=new_speed,
                    change_percent=round(-speed_reduction * 100, 1),
                    reason=f"预计延长刀具寿命{round(estimated_life_extension, 1)}%",
                )
            )

        if feed_reduction > 0:
            feed_effect = round(feed_reduction * 80, 1)
            suggestions.append(
                AdjustmentSuggestionItem(
                    parameter="feed_rate",
                    current_value=feed_rate,
                    suggested_value=new_feed,
                    change_percent=round(-feed_reduction * 100, 1),
                    reason=f"减少切削力，降低磨损率约{feed_effect}%",
                )
            )

        if depth_reduction > 0:
            suggestions.append(
                AdjustmentSuggestionItem(
                    parameter="depth_of_cut",
                    current_value=depth_of_cut,
                    suggested_value=new_depth,
                    change_percent=round(-depth_reduction * 100, 1),
                    reason="减少切削负荷，改善散热条件",
                )
            )

        suggestions.append(
            AdjustmentSuggestionItem(
                parameter="coolant_flow",
                current_value=coolant_flow,
                suggested_value=new_coolant,
                change_percent=round(coolant_increase * 100, 1),
                reason="增强冷却效果，降低切削温度，减缓月牙洼磨损",
            )
        )

        if urgency == UrgencyLevel.CRITICAL:
            suggestions.append(
                AdjustmentSuggestionItem(
                    parameter="tool_inspection",
                    current_value=0,
                    suggested_value=0,
                    change_percent=0,
                    reason="立即安排刀具检查，准备更换刀具",
                )
            )

        return AdjustmentSuggestion(
            suggestions=suggestions,
            summary=(
                f"当前磨损 {round(current_wear, 4)} mm，剩余寿命 "
                f"{round(remaining_life, 2)} min，建议等级 {str(urgency)}"
            ),
            expected_improvement=(
                f"预计延长刀具寿命 {round(estimated_life_extension, 1)}%"
                if estimated_life_extension > 0
                else "保持当前参数，监测磨损趋势"
            ),
        )
