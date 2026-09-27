"""世界模型插件清单.

定义插件元数据、能力声明、依赖与配置 schema，供 PluginLoader 加载时校验。
与 ``__init__.PLUGIN_MANIFEST`` 保持一致。
"""

from __future__ import annotations

from typing import Any


MANIFEST: dict[str, Any] = {
    "id": "world_model",
    "name": "World Model Plugin",
    "version": "1.0.0",
    "author": "灵境制造团队",
    "description": (
        "加工过程世界模型：预测状态轨迹（颤振概率/刀具磨损/表面质量）在未来 N 步的演化，为 RL agent 提供想象环境。"
    ),
    "entry_point": "app.plugins.world_model:WorldModelPlugin",
    "plugin_type": "analyzer",
    "capabilities": ["gpu_access"],
    "dependencies": [
        # torch 为可选依赖：net.py/predictor.py/plugin.py 均有 HAS_TORCH=False 降级路径
        # （NumPy 回退版 WorldModelNet + 零向量融合降级）。
        # required=False 保证 PluginLoader 在缺 torch 时仍加载插件，
        # 由运行时按 HAS_TORCH 自动选择 torch/NumPy 路径。
        {"name": "torch", "version": ">=2.0.0", "required": False},
        {"name": "numpy", "version": ">=1.21.0", "required": True},
    ],
    "task_types": ["wm_predict_state"],
    "config_schema": {
        "type": "object",
        "properties": {
            "default_horizon": {
                "type": "integer",
                "description": "默认预测步长",
                "default": 10,
                "minimum": 1,
                "maximum": 100,
            },
            "default_model_uri": {
                "type": "string",
                "description": "默认世界模型 URI（model://world_model/<version>）",
                "default": "model://world_model/1.0.0",
            },
            "device": {
                "type": "string",
                "enum": ["auto", "cuda", "cpu", "mps"],
                "description": "推理设备",
                "default": "auto",
            },
            "max_trajectory_length": {
                "type": "integer",
                "description": "最大轨迹长度（防止显存爆炸）",
                "default": 100,
                "minimum": 1,
                "maximum": 1000,
            },
            # ADR-020 思路 1：统一表示融合模式配置（P3 默认启用）
            "use_fusion": {
                "type": "boolean",
                "description": (
                    "是否启用 ADR-020 思路 1 的统一表示融合模式。"
                    "P3 起默认 True（融合架构真实发挥效用）。"
                    "torch 不可用时由 predictor/plugin 层自动降级到传统路径"
                    "（原 state_dim 字段拼接路径），保证生产路径不崩溃。"
                    "启用后 LSTM 输入层接受融合 embedding，"
                    "LTC 解码器自回归路径仍用 state_dim + action_dim，"
                    "state_head 输出仍是 state_dim 维，保证 ADR-017 输出契约不变。"
                ),
                "default": True,
            },
            "feature_dim": {
                "type": "integer",
                "description": ("几何特征向量维度（ADR-007 平面/圆柱/孔统计向量）。仅 use_fusion=True 时生效。"),
                "default": 32,
                "minimum": 1,
            },
            "d_model": {
                "type": "integer",
                "description": ("GeometryEncoder/DynamicsEncoder 输出维度。仅 use_fusion=True 时生效。"),
                "default": 64,
                "minimum": 1,
            },
            "fused_dim": {
                "type": "integer",
                "description": ("FusionLayer 输出维度（融合 embedding 维度）。仅 use_fusion=True 时生效。"),
                "default": 128,
                "minimum": 1,
            },
        },
        "additionalProperties": False,
    },
    "min_core_version": "1.0.0",
    "max_core_version": "99.99.99",
}


def get_manifest() -> dict[str, Any]:
    """返回插件清单副本."""
    import copy

    return copy.deepcopy(MANIFEST)
