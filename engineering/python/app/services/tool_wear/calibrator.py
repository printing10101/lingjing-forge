"""磨损标定子服务：从测量数据 / 实时传感器数据校准磨损模型。"""

from typing import Any

from app.services.tool_wear._constants import get_material_params
from app.services.tool_wear.curve_predictor import WearCurvePredictor


class WearCalibrator:
    """使用测量值或实时传感器特征对磨损预测曲线进行动态校正。"""

    def __init__(self, curve_predictor: WearCurvePredictor) -> None:
        self._curve_predictor = curve_predictor

    def calibrate_with_measurement(
        self, measured_wear: float, elapsed_time: float, input_parameters: dict
    ) -> dict[str, Any]:
        input_parameters.get("material_type", "steel_45")

        predicted_curve = self._curve_predictor.predict_wear_curve(input_parameters)
        predicted_at_time = None
        for point in predicted_curve.data_points:
            if abs(point.time - elapsed_time) < 1.0:
                predicted_at_time = point.wear
                break

        if predicted_at_time is None:
            _wr_avg = predicted_curve.model_info.get("wear_rate_avg", 0.0) if predicted_curve.model_info else 0.0
            predicted_at_time = _wr_avg * elapsed_time

        deviation = measured_wear - predicted_at_time
        deviation_percent = (deviation / max(predicted_at_time, 0.001)) * 100.0

        calibrated_params = input_parameters.copy()
        correction_factor = 1.0 + (deviation_percent / 200.0)
        calibrated_params["material_hardness_adjustment"] = round(correction_factor, 3)

        recalibrated_curve = self._curve_predictor.predict_wear_curve(calibrated_params)

        return {
            "measured_wear": measured_wear,
            "predicted_wear_at_time": round(predicted_at_time, 4),
            "deviation": round(deviation, 4),
            "deviation_percent": round(deviation_percent, 2),
            "correction_factor": round(correction_factor, 3),
            "calibrated_curve": recalibrated_curve.to_dict(),
        }

    def calibrate_with_real_time_data(
        self,
        real_time_wear: float,
        sensor_features: dict[str, float],
        elapsed_time: float,
        input_parameters: dict,
    ) -> dict[str, Any]:
        """使用实时传感器数据动态校正磨损预测。

        采用指数加权移动平均（EWMA）融合模型预测值与实测值，
        并结合传感器特征（振动 RMS、切削力、温度）进行综合校正。

        Args:
            real_time_wear: 实时测量的磨损量 (mm)
            sensor_features: 传感器特征字典，可包含：
                - vibration_rms: 振动 RMS (g)
                - cutting_force: 切削力 (N)
                - temperature: 切削温度 (°C)
                - acoustic_emission: 声发射信号
            elapsed_time: 已加工时间 (min)
            input_parameters: 切削参数字典

        Returns:
            校正后的预测结果，包含：
            - measured_wear: 实测磨损量
            - predicted_wear_at_time: 原始预测值
            - corrected_wear: 校正后的磨损值
            - deviation: 偏差
            - deviation_ratio: 偏差比率
            - corrected_wear_rate: 校正后的磨损率
            - sensor_adjustment: 传感器修正因子
            - calibrated_curve: 校正后的磨损曲线
            - confidence: 置信度
        """
        cutting_speed = input_parameters.get("cutting_speed", 150.0)
        material_type = input_parameters.get("material_type", "steel_45")
        material = get_material_params(material_type)

        # 1. 获取原始预测曲线
        predicted_curve = self._curve_predictor.predict_wear_curve(input_parameters)

        # 2. 找到对应时间的预测值
        predicted_at_time = None
        for point in predicted_curve.data_points:
            if abs(point.time - elapsed_time) < 1.0:
                predicted_at_time = point.wear
                break

        if predicted_at_time is None:
            _wr_avg = predicted_curve.model_info.get("wear_rate_avg", 0.0) if predicted_curve.model_info else 0.0
            predicted_at_time = _wr_avg * elapsed_time

        # 3. 计算模型偏差
        deviation = real_time_wear - predicted_at_time
        deviation_ratio = deviation / max(predicted_at_time, 0.001)

        # 4. 传感器特征修正因子计算
        sensor_adjustment = 1.0
        adjustment_reasons: list[str] = []

        vibration_rms = sensor_features.get("vibration_rms", 0.0)
        if vibration_rms > 0:
            # 振动 RMS 超过阈值时加速磨损
            if vibration_rms > 2.0:
                sensor_adjustment *= 1.15
                adjustment_reasons.append(f"振动RMS={vibration_rms:.2f}g超阈值，磨损加速15%")
            elif vibration_rms > 1.0:
                sensor_adjustment *= 1.05
                adjustment_reasons.append(f"振动RMS={vibration_rms:.2f}g偏高，磨损加速5%")

        cutting_force = sensor_features.get("cutting_force", 0.0)
        if cutting_force > 0:
            # 切削力超过预期时加速磨损
            expected_force = material.hardness_factor * 100.0
            if cutting_force > expected_force * 1.5:
                sensor_adjustment *= 1.20
                adjustment_reasons.append(f"切削力{cutting_force:.0f}N远超预期{expected_force:.0f}N，磨损加速20%")
            elif cutting_force > expected_force * 1.2:
                sensor_adjustment *= 1.10
                adjustment_reasons.append(f"切削力{cutting_force:.0f}N高于预期{expected_force:.0f}N，磨损加速10%")

        temperature = sensor_features.get("temperature", 0.0)
        if temperature > 0:
            # 温度过高加速磨损（热磨损机制）
            if temperature > 800.0:
                sensor_adjustment *= 1.25
                adjustment_reasons.append(f"切削温度{temperature:.0f}°C过高，热磨损加速25%")
            elif temperature > 600.0:
                sensor_adjustment *= 1.10
                adjustment_reasons.append(f"切削温度{temperature:.0f}°C偏高，热磨损加速10%")

        acoustic_emission = sensor_features.get("acoustic_emission", 0.0)
        if acoustic_emission > 0:
            # 声发射信号异常表明刀具可能崩刃
            if acoustic_emission > 0.8:
                sensor_adjustment *= 1.30
                adjustment_reasons.append(f"声发射信号{acoustic_emission:.2f}异常，刀具可能崩刃，磨损加速30%")

        # 5. EWMA 融合预测值与实测值
        alpha = 0.3  # 平滑系数，实测值权重
        measured_rate = real_time_wear / max(elapsed_time, 0.01)
        _wr_avg = predicted_curve.model_info.get("wear_rate_avg", 0.0) if predicted_curve.model_info else 0.0
        corrected_wear_rate = alpha * measured_rate * sensor_adjustment + (1.0 - alpha) * _wr_avg
        corrected_wear_rate = max(1e-6, min(0.05, corrected_wear_rate))

        # 6. 生成校正后的预测曲线
        calibrated_params = input_parameters.copy()
        correction_factor = 1.0 + (deviation_ratio / 200.0) * sensor_adjustment
        calibrated_params["material_hardness_adjustment"] = round(correction_factor, 3)

        recalibrated_curve = self._curve_predictor.predict_wear_curve(calibrated_params)

        # 7. 计算综合置信度
        confidence = self._curve_predictor._compute_confidence(real_time_wear, cutting_speed, material)
        # 传感器数据齐全时提高置信度
        sensor_coverage = sum(1 for v in sensor_features.values() if v > 0) / max(len(sensor_features), 1)
        confidence = min(0.98, confidence + 0.05 * sensor_coverage)

        return {
            "measured_wear": round(real_time_wear, 4),
            "predicted_wear_at_time": round(predicted_at_time, 4),
            "corrected_wear": round(predicted_at_time + deviation * sensor_adjustment, 4),
            "deviation": round(deviation, 4),
            "deviation_ratio": round(deviation_ratio, 4),
            "corrected_wear_rate": round(corrected_wear_rate, 6),
            "sensor_adjustment": round(sensor_adjustment, 3),
            "adjustment_reasons": adjustment_reasons,
            "calibrated_curve": recalibrated_curve.to_dict(),
            "confidence": round(confidence, 2),
            "sensor_coverage": round(sensor_coverage, 2),
        }
