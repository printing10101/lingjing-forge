"""兼容性 re-export 入口。

原 God class（1516 行）已按职责拆分为 ``app/services/tool_wear/`` 子包：
- ``_constants.py``：MaterialParams / MATERIAL_PARAMS / TOOL_PARAMS / 查找函数
- ``curve_predictor.py``：WearCurvePredictor（磨损曲线预测）
- ``param_advisor.py``：ParameterAdvisor（切削参数建议）
- ``calibrator.py``：WearCalibrator（磨损标定）
- ``compensation_recommender.py``：CompensationRecommender（补偿推荐）
- ``ml_trainer.py``：WearMLTrainer（ML 训练）
- ``facade.py``：ToolWearPredictor（Facade，组合上述子服务）

本文件仅做 re-export，保证 ``from app.services.tool_wear_predictor import ToolWearPredictor``
等历史导入路径继续可用，业务逻辑零改动。
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
