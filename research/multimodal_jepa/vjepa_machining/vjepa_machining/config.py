"""V-JEPA Machining Anomaly Detection Configuration.

定义加工过程异常检测系统的所有可配置参数。
"""

from dataclasses import dataclass, field
from typing import Tuple, List


@dataclass
class VJEPAMachiningConfig:
    """V-JEPA加工过程异常检测系统全局配置。

    Attributes:
        # 视频输入参数
        num_frames: 输入帧数（处理单元大小）
        frame_size: 每帧分辨率
        in_channels: 输入通道数（RGB=3）

        # 时空ViT编码器
        temporal_patch_size: 时间维度patch大小
        spatial_patch_size: 空间维度patch大小
        vit_embed_dim: ViT嵌入维度（输出512维）
        vit_depth: Transformer层数
        vit_num_heads: 注意力头数
        vit_mlp_ratio: MLP扩展比例
        vit_dropout: ViT dropout率

        # 时空掩码策略
        temporal_mask_ratio_start: 时间掩码起始比例（10%）
        temporal_mask_ratio_end: 时间掩码结束比例（30%）
        spatial_mask_ratio_start: 空间掩码起始比例（15%）
        spatial_mask_ratio_end: 空间掩码结束比例（40%）
        spatial_mask_block_size: 空间掩码块大小
        progressive_masking: 渐进式掩码增强

        # 动作条件预测器
        num_action_types: 动作类型数（刀具移动/换刀/暂停）
        action_embed_dim: 动作嵌入维度
        predictor_hidden_dim: 预测器隐藏层维度
        predictor_depth: 预测器MLP层数

        # 异常检测头
        num_anomaly_types: 异常类型数（断刀/振动异常/过切/撞刀）
        anomaly_hidden_dim: 异常检测隐藏层维度
        cosine_similarity_threshold: 正常判定余弦相似度阈值（0.92）
        euclidean_threshold_initial: 初始欧氏距离阈值

        # 异常严重程度
        severity_levels: 严重程度等级
        severity_thresholds: 严重程度阈值分位数

        # 传感器特征
        num_sensor_channels: 传感器通道数
        sensor_feature_dim: 传感器特征维度

        # 对比损失
        lambda_triplet: 三元组对比损失权重（λ=0.3）
        triplet_margin: 三元组损失边距

        # EMA参数
        ema_decay: EMA衰减率

        # 训练参数
        epochs: 训练轮次（至少100）
        initial_lr: 初始学习率（1e-4）
        batch_size: 批处理大小
        warmup_epochs: 学习率预热轮数
        early_stopping_patience: 早停耐心值（F1值5轮无提升则停止）
        dropout: Dropout率（0.3）
        weight_decay: L2正则化系数（1e-5）

        # 数据增强
        rotation_range: 随机旋转角度范围
        brightness_range: 亮度调整范围
        gaussian_noise_std: 高斯噪声标准差

        # 数据集
        train_val_test_split: 训练/验证/测试集划分比例

        # 推理
        target_inference_time_ms: 目标推理时间（ms）

        # 保存与日志
        save_every_n_epochs: 模型保存间隔
        log_every_n_steps: 日志记录间隔
    """

    # 视频输入参数
    num_frames: int = 16
    frame_size: Tuple[int, int] = (224, 224)
    in_channels: int = 3

    # 时空ViT编码器
    temporal_patch_size: int = 2
    spatial_patch_size: int = 16
    vit_embed_dim: int = 512
    vit_depth: int = 12
    vit_num_heads: int = 8
    vit_mlp_ratio: int = 4
    vit_dropout: float = 0.1

    # 时空掩码策略
    temporal_mask_ratio_start: float = 0.10
    temporal_mask_ratio_end: float = 0.30
    spatial_mask_ratio_start: float = 0.15
    spatial_mask_ratio_end: float = 0.40
    spatial_mask_block_size: int = 32
    progressive_masking: bool = True

    # 动作条件预测器
    num_action_types: int = 3
    action_embed_dim: int = 64
    predictor_hidden_dim: int = 1024
    predictor_depth: int = 3

    # 异常检测头
    num_anomaly_types: int = 4
    anomaly_hidden_dim: int = 256
    cosine_similarity_threshold: float = 0.92
    euclidean_threshold_initial: float = 1.5

    # 异常严重程度
    severity_levels: List[str] = field(
        default_factory=lambda: [
            "normal",
            "mild",
            "moderate",
            "severe",
            "danger",
        ]
    )
    severity_thresholds: List[float] = field(
        default_factory=lambda: [
            0.92,
            0.75,
            0.55,
            0.30,
        ]
    )

    # 传感器特征
    num_sensor_channels: int = 6
    sensor_feature_dim: int = 128

    # 对比损失
    lambda_triplet: float = 0.30
    triplet_margin: float = 0.5

    # EMA参数
    ema_decay: float = 0.996

    # 训练参数
    epochs: int = 100
    initial_lr: float = 1e-4
    batch_size: int = 16
    warmup_epochs: int = 5
    early_stopping_patience: int = 5
    dropout: float = 0.30
    weight_decay: float = 1e-5

    # 数据增强
    rotation_range: float = 10.0
    brightness_range: float = 0.15
    gaussian_noise_std: float = 0.03

    # 数据集
    train_val_test_split: Tuple[float, float, float] = (0.70, 0.15, 0.15)

    # 推理
    target_inference_time_ms: float = 100.0

    # 保存与日志
    save_every_n_epochs: int = 10
    log_every_n_steps: int = 20

    # 计算属性

    @property
    def spatial_patches_per_side(self) -> int:
        """空间维度每边patch数。"""
        return self.frame_size[0] // self.spatial_patch_size

    @property
    def num_spatial_patches(self) -> int:
        """空间维度总patch数。"""
        return self.spatial_patches_per_side**2

    @property
    def num_temporal_patches(self) -> int:
        """时间维度patch数。"""
        return self.num_frames // self.temporal_patch_size

    @property
    def total_patches(self) -> int:
        """总时空patch数。"""
        return self.num_temporal_patches * self.num_spatial_patches

    @property
    def spatial_mask_blocks_per_side(self) -> int:
        """空间维度每边掩码块数。"""
        return self.frame_size[0] // self.spatial_mask_block_size

    @property
    def total_spatial_mask_blocks(self) -> int:
        """空间维度总掩码块数。"""
        return self.spatial_mask_blocks_per_side**2
