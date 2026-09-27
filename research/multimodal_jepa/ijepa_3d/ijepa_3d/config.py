"""I-JEPA 3D模型配置模块。

定义I-JEPA 3D几何提取系统的所有可配置参数，包括模型架构、
训练策略、损失权重、数据增强等超参数。

Typical usage example:
    >>> config = IJEPA3DConfig()
    >>> model = IJEPA3DModel(config)
"""

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class IJEPA3DConfig:
    """I-JEPA 3D几何提取系统全局配置。

    涵盖模型架构、训练策略、损失权重、数据增强等所有超参数。
    可通过关键字参数覆盖默认值。

    Attributes:
        # 输入参数
        image_size: 输入图像分辨率（宽, 高）
        in_channels: 输入图像通道数（RGB=3）

        # CNN骨干网络 (ResNet-18)
        cnn_output_channels: CNN骨干输出通道数

        # ViT编码器 (ViT-Small)
        vit_patch_size: ViT patch大小
        vit_embed_dim: ViT嵌入维度
        vit_depth: Transformer层数
        vit_num_heads: 注意力头数
        vit_mlp_ratio: MLP扩展比例

        # 预测器网络
        predictor_hidden_dim: 预测器隐藏层维度
        predictor_output_dim: 预测器输出维度（=vit_embed_dim）
        predictor_depth: 预测器MLP层数

        # 掩码策略
        mask_block_size: 基础掩码块大小（像素）
        mask_ratio_start: 初始掩码比例
        mask_ratio_end: 最终掩码比例
        predict_target_size: 预测目标块大小（像素）
        progressive_masking: 是否使用渐进式掩码增强

        # 视图融合
        view_fusion_num_heads: 交叉注意力头数
        view_fusion_dropout: 注意力dropout率
        front_view_weight: 正视视图贡献权重
        side_view_weight: 侧视视图贡献权重
        top_view_weight: 俯视视图贡献权重

        # 输出头
        bbox_output_dim: 边界框输出维度（x,y,z + l,w,h = 6）
        num_keypoints: 关键特征点数
        keypoint_output_dim: 特征点输出维度（每点3D坐标）

        # 损失权重
        lambda_reconstruction: 嵌入空间预测损失权重
        lambda_embedding: VICReg正则化损失权重
        lambda_geometry: 几何参数回归损失权重

        # VICReg参数
        vicreg_variance_weight: 方差损失权重
        vicreg_covariance_weight: 协方差损失权重
        vicreg_invariance_weight: 不变性损失权重

        # EMA参数
        ema_decay: EMA衰减率

        # Smooth L1参数
        smooth_l1_delta: Smooth L1过渡阈值

        # 训练参数
        stage1_epochs: 阶段一训练轮数
        stage2_epochs: 阶段二训练轮数
        stage1_lr: 阶段一学习率
        stage2_lr: 阶段二学习率
        stage1_batch_size: 阶段一批量大小
        stage2_batch_size: 阶段二批量大小
        frozen_layers: 阶段一冻结的编码器层数

        # 数据增强
        rotation_range: 随机旋转角度范围（度）
        scale_range: 随机缩放范围
        brightness_range: 亮度调整范围
        gaussian_noise_std: 高斯噪声标准差

        # 保存与日志
        save_every_n_epochs: 模型保存间隔
        log_every_n_steps: 日志记录间隔

        # 推理
        target_inference_time_ms: 目标推理时间（ms）
    """

    # 输入参数
    image_size: Tuple[int, int] = (256, 256)
    in_channels: int = 3

    # CNN骨干网络 (ResNet-18)
    cnn_output_channels: int = 64

    # ViT编码器 (ViT-Small)
    vit_patch_size: int = 16
    vit_embed_dim: int = 512
    vit_depth: int = 12
    vit_num_heads: int = 8
    vit_mlp_ratio: int = 4

    # 预测器网络
    predictor_hidden_dim: int = 1024
    predictor_output_dim: int = 512
    predictor_depth: int = 3

    # 掩码策略
    mask_block_size: int = 16
    mask_ratio_start: float = 0.10
    mask_ratio_end: float = 0.30
    predict_target_size: int = 64
    progressive_masking: bool = True

    # 视图融合
    view_fusion_num_heads: int = 8
    view_fusion_dropout: float = 0.1
    front_view_weight: float = 0.60
    side_view_weight: float = 0.20
    top_view_weight: float = 0.20

    # 输出头
    bbox_output_dim: int = 6
    num_keypoints: int = 10
    keypoint_output_dim: int = 30  # 10 * 3

    # 损失权重
    lambda_reconstruction: float = 1.0
    lambda_embedding: float = 0.5
    lambda_geometry: float = 2.0

    # VICReg参数
    vicreg_variance_weight: float = 1.0
    vicreg_covariance_weight: float = 1.0
    vicreg_invariance_weight: float = 25.0

    # EMA参数
    ema_decay: float = 0.996

    # Smooth L1参数
    smooth_l1_delta: float = 1.0

    # 训练参数
    stage1_epochs: int = 100
    stage2_epochs: int = 200
    stage1_lr: float = 5e-4
    stage2_lr: float = 1e-4
    stage1_batch_size: int = 32
    stage2_batch_size: int = 16
    frozen_layers: int = 8

    # 数据增强
    rotation_range: float = 15.0
    scale_range: Tuple[float, float] = (0.8, 1.2)
    brightness_range: float = 0.20
    gaussian_noise_std: float = 0.05

    # 保存与日志
    save_every_n_epochs: int = 50
    log_every_n_steps: int = 10

    # 推理
    target_inference_time_ms: float = 100.0

    # 数据集配置
    min_samples_per_class: int = 100
    total_samples: int = 500
    part_type_distribution: dict = field(
        default_factory=lambda: {
            "bracket": 0.20,  # 支架类
            "flange": 0.20,  # 法兰类
            "stepped_shaft": 0.20,  # 阶梯轴类
            "gear_blank": 0.20,  # 齿轮毛坯类
            "housing": 0.20,  # 壳体类
        }
    )

    @property
    def vit_patches_per_side(self) -> int:
        """计算每边的patch数量。"""
        return self.image_size[0] // self.vit_patch_size

    @property
    def vit_num_patches(self) -> int:
        """计算总patch数量。"""
        return self.vit_patches_per_side**2

    @property
    def num_mask_blocks_per_side(self) -> int:
        """计算每边的掩码块数量。"""
        return self.image_size[0] // self.mask_block_size

    @property
    def total_mask_blocks(self) -> int:
        """计算总掩码块数量。"""
        return self.num_mask_blocks_per_side**2
