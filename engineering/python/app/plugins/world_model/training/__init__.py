"""世界模型融合权重训练模块（ADR-020 思路 1 P1）.

提供融合路径（GeometryEncoder + DynamicsEncoder + FusionLayer → LSTM → LTC）
的权重训练基础设施，复用 ``app.ai.lnn.training`` 的 MLflow / 种子 / 设备
管理约定，但适配 ``WorldModelNet.forward`` 的 ``unified_states`` 输入契约。

子模块
------
- ``weights_resolver``: ``model://world_model/<version>`` URI → checkpoint
  文件路径解析（torch-free，独立可测）。
- ``fusion_dataset``: ``FusionTrajectoryDataset`` + collate，接受
  ``(geometry_seq, dynamics_seq, actions, target_trajectory)`` 四元组。
- ``fusion_trainer``: ``FusionWorldModelTrainer``，含 MLflow tracking /
  AMP / 梯度裁剪 / 早停 / per-epoch checkpoint。

工程边界
--------
- torch 不可用时 ``FusionWorldModelTrainer`` 与 ``FusionTrajectoryDataset``
  实例化抛 RuntimeError（训练必须 torch）；但 ``weights_resolver`` 不依赖
  torch，可在纯 numpy 环境下用于 plugin 层权重路径解析。
- checkpoint 格式与 ``LNNTrainer.save_checkpoint`` 对齐：
  ``{model_state_dict, optimizer_state_dict, training_history,
  model_config, metrics, timestamp, ...}``，便于 ``TrajectoryPredictor``
  通过 ``torch.load + load_state_dict`` 加载。
"""

from app.plugins.world_model.training.weights_resolver import (
    DEFAULT_MODELS_DIR,
    WeightsResolutionError,
    build_canonical_weights_path,
    resolve_world_model_weights_path,
)

# torch 依赖模块：torch 不可用时导入仍需成功（让 plugin 层在纯 numpy 环境
# 下能解析权重路径），调用方在实例化 FusionWorldModelTrainer /
# FusionTrajectoryDataset 前自行确认 torch 可用性，或通过
# pytest.importorskip("torch") 跳过相关测试。
try:
    from app.plugins.world_model.training.fusion_dataset import (
        DYNAMICS_INPUT_DIM,
        DEFAULT_GEO_INPUT_DIM,
        FusionTrajectoryDataset,
        fusion_collate_fn,
        validate_sample,
    )
    from app.plugins.world_model.training.fusion_trainer import (
        DEFAULT_FUSION_EXPERIMENT_NAME,
        FusionTrainerError,
        FusionWorldModelTrainer,
    )

    _HAS_TORCH = True
except ImportError:  # pragma: no cover - 测试环境通过 importorskip 跳过
    _HAS_TORCH = False

__all__ = [
    "DEFAULT_MODELS_DIR",
    "WeightsResolutionError",
    "build_canonical_weights_path",
    "resolve_world_model_weights_path",
]

if _HAS_TORCH:
    __all__ += [
        "DEFAULT_GEO_INPUT_DIM",
        "DYNAMICS_INPUT_DIM",
        "FusionTrajectoryDataset",
        "fusion_collate_fn",
        "validate_sample",
        "DEFAULT_FUSION_EXPERIMENT_NAME",
        "FusionTrainerError",
        "FusionWorldModelTrainer",
    ]
