"""RL 训练管线包入口.

对应 ADR-017 第 4 节。基于阶段 1 ``Workflow`` + 阶段 2 ``Snapshot``
构建离线 RL 训练管线：

    collect_episodes → build_replay_buffer → train_policy → evaluate → snapshot

子模块
------
- ``reward``：奖励函数设计（颤振惩罚 + 磨损惩罚 + 质量奖励 + 材料去除率 + 安全惩罚）
- ``replay_buffer``：经验回放缓冲区（离线 RL 数据存储，支持优先级采样）
- ``trainer``：PPO 训练器（实现 clipped objective + GAE 优势估计）

设计原则
--------
1. **离线优先**：v1 仅支持基于历史数据 + 仿真环境的离线 RL
2. **可复现**：训练随机种子固定，snapshot 持久化策略版本
3. **工程现实**：训练 Workflow 走 BackgroundTasks 异步；snapshot 每 1000 步自动保存
4. **学术诚信**：训练指标通过 MLflow / SnapshotStore 跟踪
"""

from __future__ import annotations

__version__ = "1.0.0"

from .replay_buffer import Experience, ReplayBuffer, ReplayBufferStats
from .reward import RewardConfig, RewardFunction, RewardBreakdown
from .trainer import PPOTrainer, TrainingConfig, TrainingMetrics, TrainingSnapshot

__all__ = [
    "Experience",
    "ReplayBuffer",
    "ReplayBufferStats",
    "RewardConfig",
    "RewardFunction",
    "RewardBreakdown",
    "PPOTrainer",
    "TrainingConfig",
    "TrainingMetrics",
    "TrainingSnapshot",
    "__version__",
]
