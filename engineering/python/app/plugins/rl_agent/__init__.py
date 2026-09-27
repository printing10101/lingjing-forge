"""RL Agent 插件包入口.

实现 ``rl_act`` 任务类型：基于当前加工状态（世界模型预测的轨迹）
输出下一步切削参数动作。采用 PPO 策略 + 安全硬约束过滤。

设计要点
--------
- 离线 RL 优先：v1 仅支持基于历史数据 + 仿真环境的离线 RL，
  在线 RL 列入 v2（见 ADR-017）
- SafetyShield 硬约束：强制过滤违反安全约束的动作，不可被 RL 策略覆盖
- 双路径实现：torch 不可用时回退到 NumPy 朴素实现（仅推理，无梯度）
- 与 WorldModelPlugin 解耦：通过 TaskContext 输入接收状态，不直接调用
  世界模型插件（编排器负责串联感知→预测→决策）
"""

from __future__ import annotations

__version__ = "1.0.0"

from .manifest import MANIFEST, get_manifest

PLUGIN_MANIFEST = {
    "id": "rl_agent",
    "name": "RL Agent Plugin",
    "version": __version__,
    "description": "切削参数强化学习决策器：PPO 策略 + 安全硬约束",
    "author": "灵境制造团队",
    "plugin_type": "analyzer",
    "capabilities": ["gpu_access"],
    "task_types": ["rl_act"],
    "min_core_version": "1.0.0",
}

__all__ = ["MANIFEST", "get_manifest", "PLUGIN_MANIFEST", "__version__"]
