"""磨损曲线预测子服务：Usui + Taylor 混合模型拟合、外推、置信区间。"""

import math
from typing import Any

from app.models.validation import (
    WearCurve,
    WearDataPoint,
    WearPhase,
)
from app.services.tool_wear._constants import (
    DEFAULT_REPLACEMENT_THRESHOLD,
    MaterialParams,
    get_material_params,
    get_tool_params,
)


class WearCurvePredictor:
    """
    自适应刀具磨损曲线预测器
    组合修正Taylor模型与Usui磨损率模型
    VB < 0.2mm: Usui模型主导 (加速磨损阶段)
    VB >= 0.2mm: Taylor模型主导 (稳态磨损阶段)
    """

    USUI_TAYLOR_SWITCH_THRESHOLD = 0.2

    def __init__(self) -> None:
        self.default_replacement_threshold = DEFAULT_REPLACEMENT_THRESHOLD

    # 私有：物理模型计算

    def _get_temperature(
        self,
        cutting_speed: float,
        feed_rate: float,
        depth_of_cut: float,
        material: MaterialParams,
    ) -> float:
        base_temp = 400.0
        speed_effect = cutting_speed * 2.5
        feed_effect = feed_rate * 150.0
        depth_effect = depth_of_cut * 50.0
        temp = base_temp + speed_effect + feed_effect + depth_effect
        temp *= material.hardness_factor
        return max(300.0, min(1200.0, temp))

    def _usui_wear_rate(
        self,
        cutting_speed: float,
        feed_rate: float,
        depth_of_cut: float,
        temperature: float,
        material: MaterialParams,
        tool: dict,
    ) -> float:
        thermal_energy = material.usui_B / max(temperature, 300.0)
        exponential = math.exp(-thermal_energy)
        contact_pressure = feed_rate * depth_of_cut * material.hardness_factor * 100.0
        sliding_velocity = cutting_speed * 16.667
        rate = material.usui_A * exponential * contact_pressure * sliding_velocity
        rate *= tool["wear_factor"]
        return max(1e-6, min(0.01, rate))

    def _taylor_wear_rate(
        self,
        current_vb: float,
        cutting_speed: float,
        feed_rate: float,
        depth_of_cut: float,
        material: MaterialParams,
        tool: dict,
    ) -> float:
        effective_C = material.taylor_C / (tool["wear_factor"] ** 0.5)
        effective_n = material.taylor_n
        feed_correction = 1.0 + (feed_rate - 0.2) * 0.8
        depth_correction = 1.0 + (depth_of_cut - 1.0) * 0.15
        corrected_speed = cutting_speed * feed_correction * depth_correction
        equivalent_life = (effective_C / max(corrected_speed, 1.0)) ** (1.0 / effective_n)
        wear_progress = current_vb / self.default_replacement_threshold
        acceleration = 1.0 + 2.0 * (wear_progress**2)
        effective_vb = max(current_vb, 0.001)
        wear_rate = (effective_vb / max(equivalent_life, 1.0)) * acceleration
        wear_rate *= material.hardness_factor * 0.01
        return max(1e-5, min(0.02, wear_rate))

    def _determine_phase(self, vb: float) -> str:
        if vb < 0.05:
            return WearPhase.INITIAL
        elif vb < 0.2:
            return WearPhase.STEADY
        else:
            return WearPhase.ACCELERATED

    def _compute_confidence(
        self,
        current_wear: float | None,
        cutting_speed: float,
        material: MaterialParams,
    ) -> float:
        confidence = 0.85
        if cutting_speed > 250:
            confidence -= 0.1
        elif cutting_speed > 150:
            confidence -= 0.05
        if material.hardness_factor > 1.5:
            confidence -= 0.1
        elif material.hardness_factor > 1.2:
            confidence -= 0.05
        if current_wear is not None:
            if current_wear > 0.2:
                confidence -= 0.08
            elif current_wear > 0.1:
                confidence -= 0.03
            confidence += 0.05
        return max(0.5, min(0.98, confidence))

    # 公开 API

    def predict_wear_curve(self, input_parameters: dict) -> WearCurve:
        cutting_speed = input_parameters.get("cutting_speed", 150.0)
        feed_rate = input_parameters.get("feed_rate", 0.2)
        depth_of_cut = input_parameters.get("depth_of_cut", 1.5)
        material_type = input_parameters.get("material_type", "steel_45")
        tool_type = input_parameters.get("tool_type", "carbide")
        current_wear = input_parameters.get("current_wear", 0.0)
        time_step = input_parameters.get("time_step", 1.0)
        max_time = input_parameters.get("max_time", 300.0)

        material = get_material_params(material_type)
        tool = get_tool_params(tool_type)
        wear_threshold = tool.get("max_vb", self.default_replacement_threshold)

        data_points = []
        current_vb = max(0.0, current_wear)
        time = 0.0
        total_wear = 0.0
        time_to_threshold = None

        while time <= max_time and current_vb < wear_threshold:
            temperature = self._get_temperature(cutting_speed, feed_rate, depth_of_cut, material)

            if current_vb < self.USUI_TAYLOR_SWITCH_THRESHOLD:
                usui_rate = self._usui_wear_rate(cutting_speed, feed_rate, depth_of_cut, temperature, material, tool)
                taylor_rate = self._taylor_wear_rate(current_vb, cutting_speed, feed_rate, depth_of_cut, material, tool)
                usui_weight = 1.0 - (current_vb / self.USUI_TAYLOR_SWITCH_THRESHOLD)
                usui_weight = max(0.3, min(0.9, usui_weight))
                wear_rate = usui_weight * usui_rate + (1.0 - usui_weight) * taylor_rate
            else:
                taylor_rate = self._taylor_wear_rate(current_vb, cutting_speed, feed_rate, depth_of_cut, material, tool)
                usui_rate = self._usui_wear_rate(cutting_speed, feed_rate, depth_of_cut, temperature, material, tool)
                progress = (current_vb - self.USUI_TAYLOR_SWITCH_THRESHOLD) / (
                    wear_threshold - self.USUI_TAYLOR_SWITCH_THRESHOLD
                )
                taylor_weight = max(0.6, min(0.95, 0.5 + 0.45 * progress))
                wear_rate = taylor_weight * taylor_rate + (1.0 - taylor_weight) * usui_rate

            phase = self._determine_phase(current_vb)

            point = WearDataPoint(
                time=round(time, 2),
                wear=round(current_vb, 4),
                wear_rate=round(wear_rate, 6),
                metadata={"phase": phase},
            )
            data_points.append(point)

            if current_vb >= wear_threshold and time_to_threshold is None:
                time_to_threshold = round(time, 2)

            current_vb += wear_rate * time_step
            total_wear += wear_rate * time_step
            time += time_step

        if time_to_threshold is None:
            time_to_threshold = round(time, 2)

        total_life = round(time, 2)
        avg_wear_rate = round(total_wear / max(total_life, 0.01), 6)
        confidence = self._compute_confidence(current_wear, cutting_speed, material)

        return WearCurve(
            data_points=data_points,
            total_time=total_life,
            max_wear=round(current_wear, 4),
            confidence=round(confidence, 2),
            model_info={
                "total_life": total_life,
                "time_to_threshold": time_to_threshold,
                "wear_rate_avg": avg_wear_rate,
                "wear_threshold": wear_threshold,
            },
        )

    def predict_remaining_life(self, current_wear: float, input_parameters: dict) -> float:
        input_parameters.get("material_type", "steel_45")
        tool_type = input_parameters.get("tool_type", "carbide")

        tool = get_tool_params(tool_type)
        wear_threshold = tool.get("max_vb", self.default_replacement_threshold)

        remaining_wear = max(0.0, wear_threshold - current_wear)
        if remaining_wear <= 0:
            return 0.0

        temp_params = input_parameters.copy()
        temp_params["current_wear"] = current_wear
        temp_params["time_step"] = 0.5
        temp_params["max_time"] = 500.0

        simulated_curve = self.predict_wear_curve(temp_params)
        elapsed = 0.0
        for point in simulated_curve.data_points:
            if point.wear >= current_wear:
                break
            elapsed = point.time

        # time_to_threshold 存储在 model_info 中（参见 WearCurve 数据类定义）
        t_threshold = simulated_curve.model_info.get("time_to_threshold", 0.0) if simulated_curve.model_info else 0.0
        return max(0.0, round(t_threshold - elapsed, 2))

    def get_replacement_threshold(self, material_type: str | None = None) -> float:
        if material_type is None:
            return self.default_replacement_threshold

        material = get_material_params(material_type)
        if material.hardness_factor > 1.5:
            return 0.25
        elif material.hardness_factor > 1.2:
            return 0.28
        elif material.hardness_factor < 0.7:
            return 0.35
        else:
            return self.default_replacement_threshold

    def get_supported_models(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "Usui Wear Rate Model",
                "formula": "dW/dt = A * exp(-B/T) * sigma * v",
                "dominant_range": "VB < 0.2mm",
                "description": "基于热激活理论的磨损率模型，适用于初期和稳定磨损阶段",
            },
            {
                "name": "Modified Taylor Tool Life Model",
                "formula": "V * T^n = C",
                "dominant_range": "VB >= 0.2mm",
                "description": "经典的刀具寿命模型，经材料硬度和刀具类型修正，适用于加速磨损阶段",
            },
            {
                "name": "Hybrid Adaptive Model",
                "formula": "w * Usui + (1-w) * Taylor",
                "dominant_range": "Full range",
                "description": "自适应权重混合模型，根据当前磨损量动态调整两模型权重",
            },
        ]
