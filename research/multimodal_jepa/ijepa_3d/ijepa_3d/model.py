"""I-JEPA 3D完整模型组装模块。
将所有子模块组合成完整的端到端模型：
CNN骨干 -> I-JEPA编码器 -> 预测器 -> 视图融合 -> 几何回归头
支持两阶段训练：
- 阶段一：冻结编码器前8层，训练预测器和融合模块
- 阶段二：解冻所有层，联合训练
Key components:
    - IJEPA3DModel: 完整的I-JEPA 3D几何提取模型

Example:
    >>> config = IJEPA3DConfig()
    >>> model = IJEPA3DModel(config)
    >>> output = model(front_img, side_img, top_img)
"""

import torch
import torch.nn as nn
from typing import Dict, Optional, Tuple
import logging

# Q2 修复：原 `from app.ai.ijepa_3d.xxx` 为悬空 import（app/ai/ijepa_3d/ 不存在）。
# 改为同包内相对导入。
from .config import IJEPA3DConfig
from .resnet_backbone import ResNetBackbone
from .ijepa_encoder import IJEPAEncoder
from .predictor import Predictor
from .view_fusion import ViewFusion
from .geometry_head import GeometryHead
from .masking import MultiScaleMasking
from .losses import IJPELoss

logger = logging.getLogger(__name__)


class IJEPA3DModel(nn.Module):
    """I-JEPA 3D几何参数提取模型。
    完整模型架构：
    1. CNN骨干（ResNet-18）提取三视图64通道特征图
    2. I-JEPA编码器（ViT-Small）生成上下文语义嵌入
    3. 预测器预测被遮挡区域嵌入
    4. 交叉注意力融合三视图特征
    5. 几何回归头输出3D边界框和关键特征点
    Attributes:
        config: 模型配置
        backbone: CNN骨干网络
        encoder: I-JEPA ViT编码器
        predictor: 嵌入预测器
        masking: 多尺度掩码生成器
        view_fusion: 三视图融合模块
        geometry_head: 3D几何回归头
        loss_fn: 组合损失函数
    """

    def __init__(self, config: IJEPA3DConfig):
        """初始化I-JEPA 3D模型。
        Args:
            config: 模型配置对象
        """
        super().__init__()
        self.config = config

        # CNN骨干网络（三视图共享权重）
        self.backbone = ResNetBackbone(
            output_channels=config.cnn_output_channels,
        )

        # I-JEPA编码器（三视图共享权重）
        self.encoder = IJEPAEncoder(
            img_size=64,  # CNN输出特征图大小
            patch_size=4,  # ViT patch大小
            in_channels=config.cnn_output_channels,
            embed_dim=config.vit_embed_dim,
            depth=config.vit_depth,
            num_heads=config.vit_num_heads,
            mlp_ratio=config.vit_mlp_ratio,
            ema_decay=config.ema_decay,
        )

        # 预测器
        self.predictor = Predictor(
            input_dim=config.vit_embed_dim,
            hidden_dim=config.predictor_hidden_dim,
            output_dim=config.predictor_output_dim,
            num_layers=config.predictor_depth,
            num_positions=config.vit_num_patches,
        )

        # 掩码模块
        self.masking = MultiScaleMasking(
            image_size=config.image_size[0],
            block_size=config.mask_block_size,
            target_block_size=config.predict_target_size,
        )

        # 视图融合模块
        self.view_fusion = ViewFusion(
            embed_dim=config.vit_embed_dim,
            num_heads=config.view_fusion_num_heads,
            dropout=config.view_fusion_dropout,
            front_weight=config.front_view_weight,
            side_weight=config.side_view_weight,
            top_weight=config.top_view_weight,
        )

        # 几何回归头
        self.geometry_head = GeometryHead(
            feature_dim=config.vit_embed_dim,
            num_keypoints=config.num_keypoints,
            dropout=config.view_fusion_dropout,
        )

        # 损失函数
        self.loss_fn = IJPELoss(config)

        # 初始化EMA编码器
        self.encoder.init_ema_encoder()

    def extract_view_features(
        self,
        images: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """提取单视图的CNN特征和编码器嵌入。
        Args:
            images: 单视图图像 (B, 3, 256, 256)

        Returns:
            cnn_features: CNN特征图 (B, 64, 64, 64)
            encoder_embeddings: 编码器patch嵌入 (B, num_patches, 512)
        """
        cnn_features = self.backbone(images)
        encoder_embeddings = self.encoder.forward_features(cnn_features)
        return cnn_features, encoder_embeddings

    def get_target_embeddings(
        self,
        cnn_features: torch.Tensor,
    ) -> torch.Tensor:
        """使用EMA编码器获取目标嵌入（用于I-JEPA预训练）。
        Args:
            cnn_features: CNN特征图 (B, 64, 64, 64)

        Returns:
            target_embeddings: EMA编码器的目标嵌入
        """
        return self.encoder.get_target_embeddings(cnn_features)

    def predict_masked_embeddings(
        self,
        context_embeddings: torch.Tensor,
        target_positions: torch.Tensor,
    ) -> torch.Tensor:
        """预测被遮挡区域的嵌入。
        Args:
            context_embeddings: 上下文嵌入 (B, N_visible, D)
            target_positions: 目标位置索引 (B, N_target)

        Returns:
            predicted: 预测嵌入 (B, N_target, D)
        """
        return self.predictor(context_embeddings, target_positions)

    def forward(
        self,
        front_images: torch.Tensor,
        side_images: torch.Tensor,
        top_images: torch.Tensor,
        mask_ratio: Optional[float] = None,
        return_loss: bool = False,
    ) -> Dict[str, torch.Tensor]:
        """前向传播。
        Args:
            front_images: 正视视图 (B, 3, 256, 256)
            side_images: 侧视视图 (B, 3, 256, 256)
            top_images: 俯视视图 (B, 3, 256, 256)
            mask_ratio: 掩码比例（训练时使用）
            return_loss: 是否返回损失（训练模式）

        Returns:
            输出字典，包含：
            - bbox_pred: 预测边界框 (B, 6)
            - keypoints_pred: 预测关键点 (B, 10, 3)
            - view_weights: 视图融合权重 (3,)
            - encoder_embeddings: 编码器嵌入列表
            （训练模式额外返回）:
            - pred_embeddings: 预测嵌入
            - target_embeddings: 目标嵌入
            - total_loss: 总损失
            - loss_dict: 详细损失
        """
        _ = front_images.shape[0]
        device = front_images.device

        # 1. CNN特征提取（三视图共享backbone）
        cnn_front = self.backbone(front_images)
        cnn_side = self.backbone(side_images)
        cnn_top = self.backbone(top_images)

        # 2. 编码器嵌入
        emb_front = self.encoder.forward_features(cnn_front)
        emb_side = self.encoder.forward_features(cnn_side)
        emb_top = self.encoder.forward_features(cnn_top)

        # 3. 视图融合
        fused_features, view_weights = self.view_fusion(
            emb_front, emb_side, emb_top,
        )

        # 4. 几何参数回归
        bbox_pred, keypoints_pred = self.geometry_head(fused_features)

        output = {
            "bbox_pred": bbox_pred,
            "keypoints_pred": keypoints_pred,
            "view_weights": view_weights,
            "encoder_embeddings": [emb_front, emb_side, emb_top],
        }

        # 5. I-JEPA自监督部分（训练时）
        if mask_ratio is not None and mask_ratio > 0:
            # 对正视视图进行掩码（主要视图）
            masked_front, context_mask, target_mask = self.masking(
                front_images, mask_ratio,
            )

            # 掩码图像的CNN特征和编码器嵌入
            cnn_masked = self.backbone(masked_front)
            emb_masked = self.encoder.forward_features(cnn_masked)

            # 完整图像的目标嵌入（EMA编码器）
            target_emb = self.encoder.get_target_embeddings(cnn_front)

            # 提取可见patch的上下文嵌入
            B_ctx = emb_masked.shape[0]
            context_list = []
            for b in range(B_ctx):
                # 将块级掩码映射到patch级
                block_mask_2d = context_mask[b].view(
                    self.masking.num_blocks_per_side,
                    self.masking.num_blocks_per_side,
                )
                # 每个patch对应多少个掩码块
                blocks_per_patch = self.config.mask_block_size // self.config.vit_patch_size
                # 下采样块掩码到patch分辨率
                patch_mask = torch.nn.functional.max_pool2d(
                    block_mask_2d.float().unsqueeze(0).unsqueeze(0),
                    kernel_size=blocks_per_patch,
                    stride=blocks_per_patch,
                ).squeeze().bool().flatten()

                visible_idx = ~patch_mask
                context_list.append(emb_masked[b, visible_idx])

            # 目标位置
            num_patches = self.config.vit_num_patches
            num_targets = max(1, num_patches // 4)
            target_positions = torch.stack([
                torch.randperm(num_patches)[:num_targets]
                for _ in range(B_ctx)
            ]).to(device)

            # 预测被遮挡区域嵌入
            # 首先pad上下文嵌入到统一大小
            max_context = max(c.shape[0] for c in context_list)
            padded_context = torch.zeros(
                B_ctx, max_context, self.config.vit_embed_dim, device=device,
            )
            for b, c in enumerate(context_list):
                padded_context[b, :c.shape[0]] = c

            pred_emb = self.predictor(padded_context, target_positions)

            # 提取对应的目标嵌入
            tgt_list = []
            for b in range(B_ctx):
                tgt_list.append(target_emb[b, target_positions[b]])
            tgt_emb = torch.stack(tgt_list)

            output["pred_embeddings"] = pred_emb
            output["target_embeddings"] = tgt_emb

        if return_loss:
            output["loss_dict"] = {}

        return output

    def forward_inference(
        self,
        front_images: torch.Tensor,
        side_images: torch.Tensor,
        top_images: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """推理模式前向传播（无掩码，无梯度）。
        Args:
            front_images: 正视视图 (B, 3, 256, 256)
            side_images: 侧视视图 (B, 3, 256, 256)
            top_images: 俯视视图 (B, 3, 256, 256)

        Returns:
            bbox_pred: 边界框 (B, 6)
            keypoints_pred: 关键点 (B, 10, 3)
            view_weights: 视图权重 (3,)
        """
        with torch.no_grad():
            output = self.forward(front_images, side_images, top_images)
        return output["bbox_pred"], output["keypoints_pred"], output["view_weights"]

    def freeze_encoder_layers(self, num_layers: int = 8) -> None:
        """冻结编码器的前N层（阶段一训练）。
        Args:
            num_layers: 要冻结的Transformer层数
        """
        frozen_count = 0
        # 冻结CNN骨干
        for param in self.backbone.parameters():
            param.requires_grad = False
            frozen_count += 1

        # 冻结前num_layers个Transformer块
        for i, block in enumerate(self.encoder.blocks):
            if i < num_layers:
                for param in block.parameters():
                    param.requires_grad = False

        # 冻结patch嵌入
        for param in self.encoder.patch_embed.parameters():
            param.requires_grad = False

        # 冻结位置编码和cls_token
        self.encoder.pos_embed.requires_grad = False
        self.encoder.cls_token.requires_grad = False

        logger.info(
            f"Frozen backbone + first {num_layers} encoder transformer layers",
        )

    def unfreeze_all(self) -> None:
        """解冻所有层（阶段二训练）。"""
        for param in self.parameters():
            param.requires_grad = True
        logger.info("All layers unfrozen for stage 2 training")

    def count_parameters(self) -> Dict[str, int]:
        """统计模型各组件参数数量。
        Returns:
            参数字典 {"component_name": param_count}
        """
        counts = {}
        for name, module in [
            ("backbone", self.backbone),
            ("encoder", self.encoder),
            ("predictor", self.predictor),
            ("view_fusion", self.view_fusion),
            ("geometry_head", self.geometry_head),
        ]:
            counts[name] = sum(p.numel() for p in module.parameters())
        counts["total"] = sum(p.numel() for p in self.parameters())
        counts["trainable"] = sum(
            p.numel() for p in self.parameters() if p.requires_grad
        )
        return counts

    def get_optimizer_groups(
        self,
        stage: int = 1,
        lr: float = 5e-4,
    ) -> list:
        """获取优化器参数组（支持分层学习率）。
        Args:
            stage: 训练阶段（1或2）
            lr: 基础学习率
        Returns:
            参数组列表
        """
        if stage == 1:
            # 阶段一：预测器和融合模块使用更高学习率
            return [
                {
                    "params": self.predictor.parameters(),
                    "lr": lr,
                    "name": "predictor",
                },
                {
                    "params": self.view_fusion.parameters(),
                    "lr": lr,
                    "name": "view_fusion",
                },
                {
                    "params": self.geometry_head.parameters(),
                    "lr": lr,
                    "name": "geometry_head",
                },
                {
                    "params": [
                        p for i, block in enumerate(self.encoder.blocks)
                        for p in block.parameters() if i >= self.config.frozen_layers
                    ],
                    "lr": lr * 0.5,
                    "name": "encoder_unfrozen",
                },
            ]
        else:
            # 阶段二：所有层联合训练
            return [
                {
                    "params": self.parameters(),
                    "lr": lr,
                    "name": "all",
                },
            ]
