"""JEPA World Model 工艺规划模块。

基于JEPA (Joint Embedding Predictive Architecture)的工艺规划World Model，
能够精准预测制造过程中的状态变化并支持多步工艺规划决策。

核心组件：
    - ManufacturingState: 制造系统完整状态表示
    - ManufacturingAction: 标准化工艺操作表示
    - JEPAPredictor: JEPA状态预测器
    - CEMPlanner: Cross-Entropy Method多步规划器
    - WorldModelTrainer: World Model训练器
    - JEPAWorldModelConfig: 全局配置
"""

from app.ai.jepa_world_model.config import JEPAWorldModelConfig
from app.ai.jepa_world_model.state import ManufacturingState
from app.ai.jepa_world_model.action import ManufacturingAction
from app.ai.jepa_world_model.predictor import JEPAPredictor
from app.ai.jepa_world_model.planner import CEMPlanner, PlanningResult, MultiStepPlanningResult
from app.ai.jepa_world_model.trainer import WorldModelTrainer

__all__ = [
    "JEPAWorldModelConfig",
    "ManufacturingState",
    "ManufacturingAction",
    "JEPAPredictor",
    "CEMPlanner",
    "PlanningResult",
    "MultiStepPlanningResult",
    "WorldModelTrainer",
]
