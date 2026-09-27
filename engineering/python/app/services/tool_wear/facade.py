"""ToolWearPredictor facade：组合 5 个职责单一的子服务，保持对外 API 不变。"""

import logging
from typing import Any

from app.services.tool_wear._constants import (
    DEFAULT_REPLACEMENT_THRESHOLD,
    MATERIAL_PARAMS,
    TOOL_PARAMS,
)
from app.services.tool_wear.calibrator import WearCalibrator
from app.services.tool_wear.compensation_recommender import CompensationRecommender
from app.services.tool_wear.curve_predictor import WearCurvePredictor
from app.services.tool_wear.ml_trainer import WearMLTrainer
from app.services.tool_wear.param_advisor import ParameterAdvisor


class ToolWearPredictor:
    """
    自适应刀具磨损预测系统（Facade）
    组合修正Taylor模型与Usui磨损率模型
    VB < 0.2mm: Usui模型主导 (加速磨损阶段)
    VB >= 0.2mm: Taylor模型主导 (稳态磨损阶段)

    本类是 Facade，将 5 类职责委托给独立子服务：
    - WearCurvePredictor：磨损曲线预测（Usui + Taylor 混合模型）
    - ParameterAdvisor：切削参数建议
    - WearCalibrator：磨损标定（测量值 / 实时传感器数据校正）
    - CompensationRecommender：补偿推荐
    - WearMLTrainer：ML 训练（Bosch CNC + Uniwear，基于 sklearn）

    注意：本类是基于 sklearn 的传统 ML 磨损预测服务，model_type 参数
    支持 'random_forest' / 'xgboost' / 'svm' / 'gradient_boosting' / 'linear'。
    与 lnn_workflow.yaml 中注册的 LNN 模型 'wear_prediction'（type: "ltc"，
    基于 LTC 神经网络，通过 /api/v1/lnn/predict 调用）是两套独立的系统，
    两者命名空间互不相关，不应混淆。
    """

    USUI_TAYLOR_SWITCH_THRESHOLD = 0.2

    def __init__(self):
        # 共享常量（对外保留为实例属性，部分外部代码直接读取）
        self.material_params = MATERIAL_PARAMS
        self.tool_params = TOOL_PARAMS
        self.default_replacement_threshold = DEFAULT_REPLACEMENT_THRESHOLD
        self._logger = logging.getLogger(self.__class__.__name__)

        # 组合 5 个子服务
        self._curve_predictor = WearCurvePredictor()
        self._param_advisor = ParameterAdvisor()
        self._calibrator = WearCalibrator(self._curve_predictor)
        self._compensation_recommender = CompensationRecommender()
        self._ml_trainer = WearMLTrainer(self._curve_predictor)

    # 磨损曲线预测（委托 WearCurvePredictor）

    def predict_wear_curve(self, input_parameters: dict):
        return self._curve_predictor.predict_wear_curve(input_parameters)

    def predict_remaining_life(self, current_wear: float, input_parameters: dict) -> float:
        return self._curve_predictor.predict_remaining_life(current_wear, input_parameters)

    def get_replacement_threshold(self, material_type: str | None = None) -> float:
        return self._curve_predictor.get_replacement_threshold(material_type)

    def get_supported_models(self) -> list[dict[str, Any]]:
        return self._curve_predictor.get_supported_models()

    # 参数建议（委托 ParameterAdvisor）

    def suggest_parameter_adjustment(self, current_wear: float, remaining_life: float, current_parameters: dict):
        return self._param_advisor.suggest_parameter_adjustment(current_wear, remaining_life, current_parameters)

    # 磨损标定（委托 WearCalibrator）

    def calibrate_with_measurement(
        self, measured_wear: float, elapsed_time: float, input_parameters: dict
    ) -> dict[str, Any]:
        return self._calibrator.calibrate_with_measurement(measured_wear, elapsed_time, input_parameters)

    def calibrate_with_real_time_data(
        self,
        real_time_wear: float,
        sensor_features: dict[str, float],
        elapsed_time: float,
        input_parameters: dict,
    ) -> dict[str, Any]:
        return self._calibrator.calibrate_with_real_time_data(
            real_time_wear,
            sensor_features,
            elapsed_time,
            input_parameters,
        )

    # 补偿推荐（委托 CompensationRecommender）

    def get_compensation_recommendations(
        self,
        current_wear: float,
        input_parameters: dict,
        machine_capabilities: dict | None = None,
    ) -> dict[str, Any]:
        return self._compensation_recommender.get_compensation_recommendations(
            current_wear, input_parameters, machine_capabilities
        )

    # ML 训练与推理（委托 WearMLTrainer）

    def train_with_bosch_data(
        self,
        data_dir: str = "python/data/datasets/bosch_cnc",
        machines: list[str] | None = None,
        processes: list[str] | None = None,
        test_size: float = 0.2,
        model_type: str = "random_forest",
    ) -> dict:
        return self._ml_trainer.train_with_bosch_data(
            data_dir=data_dir,
            machines=machines,
            processes=processes,
            test_size=test_size,
            model_type=model_type,
        )

    def predict_vibration_anomaly(
        self,
        vibration_data,
    ) -> dict:
        return self._ml_trainer.predict_vibration_anomaly(vibration_data)

    def get_process_baseline(self, process: str, machine: str = "M01") -> dict:
        return self._ml_trainer.get_process_baseline(process, machine)

    def get_uniwear_material_params(self) -> dict:
        return self._ml_trainer.get_uniwear_material_params()

    def train_with_uniwear_data(
        self,
        data_dir: str = "python/data/uniwear",
        model_type: str = "random_forest",
        test_size: float = 0.2,
    ) -> dict:
        return self._ml_trainer.train_with_uniwear_data(
            data_dir=data_dir,
            model_type=model_type,
            test_size=test_size,
        )

    def predict_wear_from_signals(
        self,
        signal_features: dict[str, float],
        material: str = "tc4",
    ) -> dict:
        return self._ml_trainer.predict_wear_from_signals(signal_features, material)

    def cross_dataset_analysis(self) -> dict:
        return self._ml_trainer.cross_dataset_analysis()
