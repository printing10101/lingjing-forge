"""
Phase 1 实验配置 (v2)

相比原始 config.py 的变更：
    - 新增 learnable_tau 参数组（延迟嵌入）
    - 新增三阶段训练参数（num_epochs_stage3, 退火权重）
    - 新增 prediction_horizon（长时间预测步数）
    - 新增 L_freq 频域损失权重
"""

from dataclasses import dataclass, field
import os


@dataclass
class Phase1Config:
    """Phase 1 完整实验配置"""

    # 实验标识
    experiment_name: str = "phase1_dlnn_v2"
    seed: int = 42

    # 模型参数
    input_dim: int = 7
    hidden_dim: int = 128
    num_layers: int = 3
    ltc_dt: float = 0.1
    dropout: float = 0.2

    # 延迟嵌入参数（Phase 1 核心创新）
    tau_init: float = 0.1  # 初始 τ (s)，对应 n≈600 rpm
    tau_phys_enabled: bool = True  # 是否启用物理 τ 正则化
    lambda_tau_reg: float = 0.01  # τ 正则化系数
    delay_buffer_size: int = 128  # 延迟历史缓冲区大小

    # 预测目标参数
    prediction_horizon: int = 50  # 预测未来帧数（长时间预测）
    prediction_stride: int = 1  # 预测步长

    # 三阶段训练参数
    batch_size: int = 32
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4

    # 阶段 1：数据预训练（仅 MAE）
    num_epochs_stage1: int = 100

    # 阶段 2：物理引导（加入 L_phys + L_pcc）
    num_epochs_stage2: int = 150

    # 阶段 3：频域精调（加入 L_freq，权重退火）
    num_epochs_stage3: int = 50

    # 物理损失权重（课程式退火）
    # 论文原始权重：λ_data=1.0, λ_phys=0.5, λ_pcc=0.1
    lambda_data: float = 1.0
    lambda_phys: float = 0.5
    lambda_pcc: float = 0.0  # 暂禁用（retain_graph 导致跨 epoch 崩溃，后续修复）

    # Stage 3 新增频域损失权重（从小开始，逐渐增大）
    lambda_freq_start: float = 0.01
    lambda_freq_end: float = 0.1
    epsilon_phys: float = 0.1

    # 优化器参数
    lr_stage1: float = 1e-3
    lr_stage2: float = 5e-4
    lr_stage3: float = 1e-4

    # 数据集参数
    dataset_name: str = "Synthetic"
    num_samples: int = 10000
    train_ratio: float = 0.7
    val_ratio: float = 0.15

    # 设备
    device: str = "cuda"

    # 输出路径
    output_dir: str = "phase1_dlnn_v2/results"
    checkpoint_dir: str = "phase1_dlnn_v2/checkpoints"

    def __post_init__(self):
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.checkpoint_dir, exist_ok=True)


DEFAULT_PHASE1_CONFIG = Phase1Config()


if __name__ == "__main__":
    cfg = DEFAULT_PHASE1_CONFIG
    print(f"Phase 1 配置: {cfg.experiment_name}")
    print(f"  输入维度: {cfg.input_dim}")
    print(f"  隐藏维度: {cfg.hidden_dim}")
    print(f"  LTC 层数: {cfg.num_layers}")
    print(f"  预测时域: {cfg.prediction_horizon} 步")
    print(f"  延迟初始值: {cfg.tau_init}s")
    print(f"  三阶段 epochs: {cfg.num_epochs_stage1} → {cfg.num_epochs_stage2} → {cfg.num_epochs_stage3}")
