"""V-JEPA加工异常检测完整模型组装。

将所有子模块组合成完整的端到端模型：
视频输入 -> 时空掩码 -> 时空ViT编码器 -> 动作条件预测器 -> 异常检测头

流程：
1. 接收16帧视频序列 (B, 16, 3, 224, 224)
2. 时空掩码模块随机掩盖时间/空间区域
3. 时空ViT编码器提取可见区域的上下文嵌入
4. 动作条件预测器基于当前动作预测被遮挡区域嵌入
5. EMA编码器提供目标嵌入
6. 异常检测头对比预测嵌入与观测嵌入判定异常

Key components:
    - VJEPAMachiningModel: 完整的V-JEPA加工异常检测模型
"""

import torch
import torch.nn as nn
from typing import Dict, Optional
import logging

from app.ai.vjepa_machining.config import VJEPAMachiningConfig
from app.ai.vjepa_machining.spatiotemporal_vit import SpatioTemporalViT
from app.ai.vjepa_machining.masking_3d import SpatioTemporalMasking
from app.ai.vjepa_machining.action_predictor import ActionConditionedPredictor
from app.ai.vjepa_machining.anomaly_head import AnomalyDetectionHead
from app.ai.vjepa_machining.feature_engineering import MachiningFeatureEngineering
from app.ai.vjepa_machining.losses import VJEPALosses, AnomalyClassificationLoss

logger = logging.getLogger(__name__)


class VJEPAMachiningModel(nn.Module):
    """V-JEPA加工过程异常检测模型。

    完整架构：
    1. 输入：视频序列 (B, T, C, H, W) + 动作类型 + 传感器数据（可选）
    2. 时空掩码：随机掩盖时间帧和空间区域
    3. 时空ViT编码器：提取上下文时空嵌入
    4. EMA编码器：生成预测目标嵌入
    5. 动作条件预测器：预测被遮挡区域嵌入
    6. 特征工程：多模态视觉+传感器特征
    7. 异常检测头：对比预测/观测嵌入，判定异常

    Attributes:
        config: 模型配置
        encoder: 时空ViT编码器
        masking: 时空掩码生成器
        predictor: 动作条件预测器
        anomaly_head: 异常检测头
        feature_engineering: 多模态特征工程
        loss_fn: JEPA预训练损失
        classification_loss: 异常分类损失
    """

    def __init__(self, config: VJEPAMachiningConfig):
        super().__init__()
        self.config = config

        # 时空ViT编码器
        self.encoder = SpatioTemporalViT(
            num_frames=config.num_frames,
            frame_size=config.frame_size[0],
            temporal_patch_size=config.temporal_patch_size,
            spatial_patch_size=config.spatial_patch_size,
            in_channels=config.in_channels,
            embed_dim=config.vit_embed_dim,
            depth=config.vit_depth,
            num_heads=config.vit_num_heads,
            mlp_ratio=config.vit_mlp_ratio,
            dropout=config.vit_dropout,
            ema_decay=config.ema_decay,
        )

        # 时空掩码
        self.masking = SpatioTemporalMasking(
            num_frames=config.num_frames,
            frame_size=config.frame_size[0],
            temporal_patch_size=config.temporal_patch_size,
            spatial_patch_size=config.spatial_patch_size,
            spatial_mask_block_size=config.spatial_mask_block_size,
        )

        # 动作条件预测器
        self.predictor = ActionConditionedPredictor(
            input_dim=config.vit_embed_dim,
            hidden_dim=config.predictor_hidden_dim,
            output_dim=config.vit_embed_dim,
            num_layers=config.predictor_depth,
            num_action_types=config.num_action_types,
            action_embed_dim=config.action_embed_dim,
            num_positions=config.total_patches,
        )

        # 异常检测头
        self.anomaly_head = AnomalyDetectionHead(
            embed_dim=config.vit_embed_dim,
            hidden_dim=config.anomaly_hidden_dim,
            num_anomaly_types=config.num_anomaly_types,
            cosine_threshold=config.cosine_similarity_threshold,
            dropout=config.dropout,
        )

        # 多模态特征工程
        self.feature_engineering = MachiningFeatureEngineering(
            embed_dim=config.vit_embed_dim,
            sensor_input_channels=config.num_sensor_channels,
            sensor_feature_dim=config.sensor_feature_dim,
        )

        # 损失函数
        self.loss_fn = VJEPALosses(
            lambda_triplet=config.lambda_triplet,
            triplet_margin=config.triplet_margin,
        )
        self.classification_loss = AnomalyClassificationLoss()

        # 初始化EMA编码器
        self.encoder.init_ema_encoder()

    def _rearrange_video(self, video: torch.Tensor) -> torch.Tensor:
        """将视频重排为 (B, C, T, H, W) 格式。

        输入可以为 (B, T, C, H, W) 或 (B, C, T, H, W)。
        """
        if video.dim() == 5:
            if video.shape[1] == self.config.num_frames:
                # (B, T, C, H, W) -> (B, C, T, H, W)
                video = video.permute(0, 2, 1, 3, 4)
        return video

    def extract_embeddings(
        self,
        video: torch.Tensor,
    ) -> torch.Tensor:
        """提取视频的时空嵌入。

        Args:
            video: (B, C, T, H, W)

        Returns:
            (B, num_patches, D)
        """
        return self.encoder.forward_embeddings(video, return_all=True)

    def get_target_embeddings(self, video: torch.Tensor) -> torch.Tensor:
        """获取EMA目标嵌入。

        Args:
            video: (B, C, T, H, W)

        Returns:
            (B, num_patches, D)
        """
        return self.encoder.get_target_embeddings(video)

    def predict_masked(
        self,
        context_embeddings: torch.Tensor,
        action_ids: torch.Tensor,
        target_positions: torch.Tensor,
    ) -> torch.Tensor:
        """预测被遮挡区域的嵌入。

        Args:
            context_embeddings: (B, N_context, D)
            action_ids: (B,)
            target_positions: (B, N_target)

        Returns:
            (B, N_target, D)
        """
        return self.predictor(context_embeddings, action_ids, target_positions)

    def pre_train_forward(
        self,
        video: torch.Tensor,
        action_ids: torch.Tensor,
        temporal_mask_ratio: float = 0.20,
        spatial_mask_ratio: float = 0.25,
    ) -> Dict[str, torch.Tensor]:
        """自监督预训练前向传播。

        Args:
            video: (B, T, C, H, W) 或 (B, C, T, H, W)
            action_ids: (B,) 动作类型ID
            temporal_mask_ratio: 时间掩码比例
            spatial_mask_ratio: 空间掩码比例

        Returns:
            训练输出字典
        """
        video = self._rearrange_video(video)
        B = video.shape[0]
        device = video.device

        # 1. 时空掩码
        masked_video, combined_mask = self.masking(
            video, temporal_mask_ratio, spatial_mask_ratio,
        )

        # 2. 提取上下文嵌入（掩码视频）
        context_embeddings = self.encoder(masked_video, combined_mask)

        # 3. 获取目标嵌入（完整视频，EMA编码器）
        target_embeddings = self.get_target_embeddings(video)

        # 4. 生成预测目标位置
        num_targets = max(1, self.config.total_patches // 4)
        target_positions = torch.stack([
            torch.randperm(self.config.total_patches, device=device)[:num_targets]
            for _ in range(B)
        ])

        # 5. 预测被遮挡区域嵌入
        # 统一上下文嵌入大小
        max_context = max(c.shape[0] for c in context_embeddings)
        padded_context = torch.zeros(B, max_context, self.config.vit_embed_dim, device=device)
        for b, c in enumerate(context_embeddings):
            padded_context[b, :c.shape[0]] = c

        pred_embeddings = self.predictor(padded_context, action_ids, target_positions)

        # 6. 提取目标位置对应的target嵌入
        tgt_selected = torch.stack([
            target_embeddings[b, target_positions[b]] for b in range(B)
        ])

        return {
            "pred_embeddings": pred_embeddings,
            "target_embeddings": tgt_selected,
            "context_embeddings": padded_context,
            "mask": combined_mask,
            "target_positions": target_positions,
        }

    def anomaly_detection_forward(
        self,
        video: torch.Tensor,
        action_ids: torch.Tensor,
        sensor_data: Optional[torch.Tensor] = None,
        anomaly_labels: Optional[torch.Tensor] = None,
        type_labels: Optional[torch.Tensor] = None,
        severity_labels: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """异常检测前向传播。

        Args:
            video: (B, T, C, H, W)
            action_ids: (B,)
            sensor_data: (B, num_channels, T_window)
            anomaly_labels: (B,) 0=正常, 1=异常
            type_labels: (B,) 异常类型
            severity_labels: (B,) 严重程度

        Returns:
            检测输出字典
        """
        video = self._rearrange_video(video)

        # 1. 提取观测嵌入（完整视频）
        observed_embeddings = self.extract_embeddings(video)
        observed_global = observed_embeddings.mean(dim=1)  # (B, D)

        # 2. 预测嵌入（通过掩码+预测获取）
        pre_train_output = self.pre_train_forward(video, action_ids)
        predicted_global = pre_train_output["pred_embeddings"].mean(dim=1)  # (B, D)

        # 3. 多模态特征工程
        frames = video.permute(0, 2, 1, 3, 4)  # (B, T, C, H, W)
        features = self.feature_engineering(frames, sensor_data)

        # 4. 异常检测
        anomaly_output = self.anomaly_head(predicted_global, observed_global)

        # 5. 融合特征工程的异常分数
        if anomaly_labels is not None:
            # 训练模式：计算损失
            anomaly_output["features"] = features
            anomaly_output["observed_embeddings"] = observed_global
            anomaly_output["predicted_embeddings"] = predicted_global

        return {
            **anomaly_output,
            "observed_global": observed_global,
            "predicted_global": predicted_global,
            "features": features,
            "pre_train_output": pre_train_output,
        }

    def forward(
        self,
        video: torch.Tensor,
        action_ids: torch.Tensor,
        sensor_data: Optional[torch.Tensor] = None,
        temporal_mask_ratio: float = 0.20,
        spatial_mask_ratio: float = 0.25,
        anomaly_labels: Optional[torch.Tensor] = None,
        type_labels: Optional[torch.Tensor] = None,
        severity_labels: Optional[torch.Tensor] = None,
        return_loss: bool = False,
    ) -> Dict[str, torch.Tensor]:
        """统一前向传播。

        Args:
            video: 视频输入
            action_ids: 动作类型
            sensor_data: 传感器数据
            temporal_mask_ratio: 时间掩码比例
            spatial_mask_ratio: 空间掩码比例
            anomaly_labels: 异常标签
            type_labels: 类型标签
            severity_labels: 严重程度标签
            return_loss: 是否计算损失

        Returns:
            输出字典
        """
        video = self._rearrange_video(video)

        # 时空掩码 + 预测
        pre_train_output = self.pre_train_forward(
            video, action_ids, temporal_mask_ratio, spatial_mask_ratio,
        )

        # 观测嵌入
        observed_embeddings = self.extract_embeddings(video)
        observed_global = observed_embeddings.mean(dim=1)
        predicted_global = pre_train_output["pred_embeddings"].mean(dim=1)

        # 异常检测
        anomaly_output = self.anomaly_head(predicted_global, observed_global)

        frames = video.permute(0, 2, 1, 3, 4)
        features = self.feature_engineering(frames, sensor_data)

        output = {
            **anomaly_output,
            "observed_global": observed_global,
            "predicted_global": predicted_global,
            "features": features,
            "pre_train_output": pre_train_output,
        }

        if return_loss and anomaly_labels is not None:
            # JEPA预训练损失
            jepa_loss, jepa_dict = self.loss_fn(
                pre_train_output["pred_embeddings"],
                pre_train_output["target_embeddings"],
            )

            # 异常分类损失
            cls_loss, cls_dict = self.classification_loss(
                anomaly_output["anomaly_prob"],
                anomaly_labels.float(),
                anomaly_output.get("anomaly_type_logits"),
                type_labels,
                anomaly_output.get("severity_probs"),
                severity_labels,
            )

            total_loss = jepa_loss + cls_loss
            output["total_loss"] = total_loss
            output["loss_dict"] = {**jepa_dict, **cls_dict}

        return output

    @torch.no_grad()
    def infer(
        self,
        video: torch.Tensor,
        action_ids: torch.Tensor,
        sensor_data: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """推理模式（无掩码，不计算损失）。

        Args:
            video: (B, T, C, H, W)
            action_ids: (B,)
            sensor_data: (B, num_channels, T_window)

        Returns:
            检测结果
        """
        video = self._rearrange_video(video)

        observed_embeddings = self.extract_embeddings(video)
        observed_global = observed_embeddings.mean(dim=1)

        # 通过预测获取对比嵌入
        masked_video, combined_mask = self.masking(
            video, temporal_mask_ratio=0.1, spatial_mask_ratio=0.1,
        )
        context_embeddings = self.encoder(masked_video, combined_mask)

        max_context = max(c.shape[0] for c in context_embeddings)
        B = video.shape[0]
        padded_context = torch.zeros(B, max_context, self.config.vit_embed_dim, device=video.device)
        for b, c in enumerate(context_embeddings):
            padded_context[b, :c.shape[0]] = c

        target_positions = torch.stack([
            torch.randperm(self.config.total_patches, device=video.device)[:max(1, self.config.total_patches // 4)]
            for _ in range(B)
        ])

        pred_embeddings = self.predictor(padded_context, action_ids, target_positions)
        predicted_global = pred_embeddings.mean(dim=1)

        anomaly_output = self.anomaly_head(predicted_global, observed_global)

        return {
            **anomaly_output,
            "observed_global": observed_global,
            "predicted_global": predicted_global,
        }

    def freeze_encoder_layers(self, num_layers: int = 8):
        """冻结编码器前N层（阶段训练）。"""
        for i, block in enumerate(self.encoder.blocks):
            if i < num_layers:
                for p in block.parameters():
                    p.requires_grad = False
        logger.info(f"Frozen first {num_layers} encoder layers")

    def unfreeze_all(self):
        """解冻所有层。"""
        for p in self.parameters():
            p.requires_grad = True
        logger.info("All layers unfrozen")

    def count_parameters(self) -> Dict[str, int]:
        """统计各组件参数量。"""
        counts = {}
        for name, module in [
            ("encoder", self.encoder),
            ("predictor", self.predictor),
            ("anomaly_head", self.anomaly_head),
            ("feature_engineering", self.feature_engineering),
        ]:
            counts[name] = sum(p.numel() for p in module.parameters())
        counts["total"] = sum(p.numel() for p in self.parameters())
        counts["trainable"] = sum(p.numel() for p in self.parameters() if p.requires_grad)
        return counts

    def update_ema(self):
        """更新EMA编码器。"""
        self.encoder.update_ema()
