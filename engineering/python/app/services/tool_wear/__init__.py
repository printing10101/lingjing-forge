"""tool_wear 子包：刀具磨损预测服务的 facade + 5 个职责单一的子服务。

对外保持 ``from app.services.tool_wear_predictor import ToolWearPredictor`` 可用
（由 ``app/services/tool_wear_predictor.py`` 做 re-export）。
"""

from app.services.tool_wear._constants import (
    DEFAULT_REPLACEMENT_THRESHOLD,
    MATERIAL_PARAMS,
    TOOL_PARAMS,
    MaterialParams,
    get_material_params,
    get_tool_params,
)
from app.services.tool_wear.calibrator import WearCalibrator
from app.services.tool_wear.compensation_recommender import CompensationRecommender
from app.services.tool_wear.curve_predictor import WearCurvePredictor
from app.services.tool_wear.facade import ToolWearPredictor
from app.services.tool_wear.ml_trainer import WearMLTrainer
from app.services.tool_wear.param_advisor import ParameterAdvisor

__all__ = [
    "ToolWearPredictor",
    "WearCurvePredictor",
    "ParameterAdvisor",
    "WearCalibrator",
    "CompensationRecommender",
    "WearMLTrainer",
    "MaterialParams",
    "MATERIAL_PARAMS",
    "TOOL_PARAMS",
    "DEFAULT_REPLACEMENT_THRESHOLD",
    "get_material_params",
    "get_tool_params",
]
