"""RL Agent 插件清单.

供 PluginLoader 加载时校验，与 world_model 插件清单风格对齐。
"""

from __future__ import annotations

from typing import Any

MANIFEST: dict[str, Any] = {
    "id": "rl_agent",
    "name": "RL Agent Plugin",
    "version": "1.0.0",
    "description": "切削参数强化学习决策器：PPO 策略 + 安全硬约束",
    "author": "灵境制造团队",
    "plugin_type": "analyzer",
    "capabilities": ["gpu_access"],
    "task_types": ["rl_act"],
    "dependencies": [],
    "config_schema": {
        "default_model_uri": {
            "type": "string",
            "default": "model://rl-ppo-default",
            "description": "默认 RL 策略模型 URI",
        },
        "device": {
            "type": "string",
            "default": "auto",
            "description": "推理设备：auto / cpu / cuda / mps",
        },
        "safety_strict_mode": {
            "type": "boolean",
            "default": True,
            "description": "严格安全模式：违反约束的动作强制替换为安全回退动作",
        },
        "max_action_norm": {
            "type": "number",
            "default": 1.0,
            "description": "动作向量最大 L2 范数（PPO 策略输出裁剪）",
        },
    },
    "min_core_version": "1.0.0",
    "max_core_version": "99.99.99",
}


def get_manifest() -> dict[str, Any]:
    """返回插件清单副本."""
    import copy

    return copy.deepcopy(MANIFEST)


__all__ = ["MANIFEST", "get_manifest"]
