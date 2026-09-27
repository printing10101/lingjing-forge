"""世界模型插件包.

对应 ADR-017 第 1 节。世界模型用于预测加工过程的状态轨迹
（颤振概率 / 刀具磨损 / 表面质量 在未来 N 步的演化），
为 RL agent 提供"想象"环境，支持离线 RL 训练与在线决策。

任务类型：``wm_predict_state``（在 ``core-contracts-design.md`` 阶段 8 预留）.

模块结构
--------
- ``manifest.py``：插件清单（id/version/capabilities/dependencies/config_schema）
- ``plugin.py``：``WorldModelPlugin`` 实现 ``TaskHandler`` 协议
- ``net.py``：``WorldModelNet`` 网络结构（LSTM + LTC 混合）
- ``predictor.py``：``TrajectoryPredictor`` 自回归轨迹预测器
- ``unified_state.py``：ADR-020 思路 1 统一状态表示（几何 + 动力学融合）
- ``geometry_encoder.py`` / ``dynamics_encoder.py`` / ``fusion_layer.py``：
  ADR-020 思路 1 融合编码器（torch 可选，缺失时 WorldModelNet 走 NumPy 回退）

工程现实约束（来自 ADR-017 第 7 节）
-----------------------------------
- v1 仅离线 RL，世界模型用于离线训练，不直接接 CNC 控制器
- 世界模型预测的轨迹仅供 RL agent 决策参考，最终参数需经 CAM 软件二次验证
- 物理执行需"持证操作员 + 导师签字 + 保险"，本插件不涉及物理执行环节
"""

from __future__ import annotations

from app.plugins.world_model.plugin import WorldModelPlugin
from app.plugins.world_model.net import WorldModelNet, WorldModelConfig
from app.plugins.world_model.predictor import TrajectoryPredictor
from app.plugins.world_model.unified_state import (
    UnifiedState,
    GeometryFeatures,
    DynamicsState,
    UNIFIED_STATE_SCHEMA,
)

__all__ = [
    "WorldModelPlugin",
    "WorldModelNet",
    "WorldModelConfig",
    "TrajectoryPredictor",
    # ADR-020 思路 1：统一表示融合
    "UnifiedState",
    "GeometryFeatures",
    "DynamicsState",
    "UNIFIED_STATE_SCHEMA",
    "PLUGIN_MANIFEST",
    "__version__",
]

__version__ = "1.0.0"

# 插件清单（与 manifest.py 中保持一致，便于上层直接导入）
PLUGIN_MANIFEST = {
    "id": "world_model",
    "name": "World Model Plugin",
    "version": __version__,
    "description": "加工过程世界模型：预测状态轨迹（颤振/磨损/质量）",
    "author": "灵境制造团队",
    "plugin_type": "analyzer",
    "capabilities": ["gpu_access"],
    "task_types": ["wm_predict_state"],
    "min_core_version": "1.0.0",
}
