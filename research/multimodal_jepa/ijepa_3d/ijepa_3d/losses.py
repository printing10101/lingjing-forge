"""I-JEPA组合损失函数模块。
实现完整的训练损失：
L_total = lambda1 * L_reconstruction + lambda2 * L_embedding + lambda3 * L_geometry

其中：
- L_reconstruction: 嵌入空间预测损失（L1损失）
- L_embedding: VICReg正则化损失（方差+协方差+不变性）
- L_geometry: 几何参数回归损失（Smooth L1损失）
Key components:
    - VICRegLoss: VICReg正则化损失
    - ReconstructionLoss: 嵌入重构损失（L1）
    - GeometryLoss: 几何参数回归损失（Smooth L1）
    - IJPELoss: 组合损失

Example:
    >>> loss_fn = IJPELoss(config)
    >>> total_loss, loss_dict = loss_fn(pred_emb, target_emb, pred_bbox, gt_bbox, ...)
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, Tuple
# Q2 修复：原 `from app.ai.ijepa_3d.config` 为悬空 import，改为同包相对导入。
from .config import IJEPA3DConfig


class VICRegLoss(nn.Module):
    """VICReg正则化损失。
    防止I-JEPA自监督训练中的表示坍缩。
    包含三个组件：
    - 方差损失：确保嵌入在批次维度上有足够的方差
    - 协方差损失：解相关嵌入的不同维度
    - 不变性损失：确保不同增强视图的嵌入一致
    VICReg总损失 = alpha*variance + beta*covariance + gamma*invariance

    Attributes:
        variance_weight: 方差损失权重 (alpha)
        covariance_weight: 协方差损失权重 (beta)
        invariance_weight: 不变性损失权重 (gamma)
        eps: 数值稳定小量
    """

    def __init__(
        self,
        variance_weight: float = 1.0,
        covariance_weight: float = 1.0,
        invariance_weight: float = 25.0,
        eps: float = 1e-4,
    ):
        """初始化VICReg损失。
        Args:
            variance_weight: 方差损失权重（默认1.0）
            covariance_weight: 协方差损失权重（默认1.0）
            invariance_weight: 不变性损失权重（默认25.0）
            eps: 数值稳定小量
        """
        super().__init__()
        self.variance_weight = variance_weight
        self.covariance_weight = covariance_weight
        self.invariance_weight = invariance_weight
        self.eps = eps

    def variance_loss(self, z: torch.Tensor) -> torch.Tensor:
        """计算方差损失。
        鼓励嵌入在批次维度上具有单位方差。
        使用hinge损失：max(0, gamma - std(z))

        Args:
            z: 嵌入向量 (B, D)

        Returns:
            方差损失标量
        """
        std = torch.sqrt(z.var(dim=0) + self.eps)
        return torch.mean(F.relu(1.0 - std))

    def covariance_loss(self, z: torch.Tensor) -> torch.Tensor:
        """计算协方差损失。
        解相关嵌入的不同维度，使协方差矩阵的非对角元素趋近于零。
        Args:
            z: 嵌入向量 (B, D)

        Returns:
            协方差损失标量
        """
        B, D = z.shape
        z_centered = z - z.mean(dim=0)  # (B, D)
        cov = (z_centered.T @ z_centered) / (B - 1)  # (D, D)

        # 非对角元素的平方和
        off_diag = cov - torch.diag(torch.diag(cov))
        return off_diag.pow(2).sum() / D

    def invariance_loss(
        self,
        z1: torch.Tensor,
        z2: torch.Tensor,
    ) -> torch.Tensor:
        """计算不变性损失。
        确保两个不同视图/版本的嵌入保持一致。
        Args:
            z1: 第一个视图的嵌入 (B, D)
            z2: 第二个视图的嵌入 (B, D)

        Returns:
            不变性损失标量（MSE）
        """
        return F.mse_loss(z1, z2)

    def forward(
        self,
        embeddings: torch.Tensor,
        target_embeddings: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算VICReg总损失。
        Args:
            embeddings: 在线编码器的嵌入 (B, D)
            target_embeddings: EMA目标编码器的嵌入 (B, D)

        Returns:
            total_loss: VICReg总损失
            loss_dict: 各组件损失值字典
        """
        var_loss = self.variance_loss(embeddings)
        cov_loss = self.covariance_loss(embeddings)
        inv_loss = self.invariance_loss(embeddings, target_embeddings)

        total = (
            self.variance_weight * var_loss
            + self.covariance_weight * cov_loss
            + self.invariance_weight * inv_loss
        )

        loss_dict = {
            "vicreg_variance": var_loss.item(),
            "vicreg_covariance": cov_loss.item(),
            "vicreg_invariance": inv_loss.item(),
            "vicreg_total": total.item(),
        }

        return total, loss_dict


class ReconstructionLoss(nn.Module):
    """嵌入空间预测损失（L1损失）。
    计算预测嵌入与真实EMA嵌入之间的L1距离。
    Attributes:
        loss_fn: L1损失函数
    """

    def __init__(self):
        """初始化重构损失。"""
        super().__init__()
        self.loss_fn = nn.L1Loss()

    def forward(
        self,
        predicted_embeddings: torch.Tensor,
        target_embeddings: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算嵌入重构损失。
        Args:
            predicted_embeddings: 预测器输出的嵌入 (B, N_target, D)
            target_embeddings: EMA编码器的目标嵌入 (B, N_target, D)

        Returns:
            loss: L1重构损失
            loss_dict: 损失值字典
        """
        loss = self.loss_fn(predicted_embeddings, target_embeddings)
        return loss, {"reconstruction_l1": loss.item()}


class GeometryLoss(nn.Module):
    """几何参数回归损失（Smooth L1损失）。
    分别计算边界框和特征点的回归损失。
    Attributes:
        smooth_l1: Smooth L1损失函数
        delta: Smooth L1过渡阈值
    """

    def __init__(self, delta: float = 1.0):
        """初始化几何损失。
        Args:
            delta: Smooth L1过渡阈值（默认1.0）
        """
        super().__init__()
        self.delta = delta
        self.smooth_l1 = nn.SmoothL1Loss(beta=delta)

    def forward(
        self,
        pred_bbox: torch.Tensor,
        gt_bbox: torch.Tensor,
        pred_keypoints: torch.Tensor,
        gt_keypoints: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算几何参数回归的总损失。
        Args:
            pred_bbox: 预测的边界框 (B, 6)
            gt_bbox: 真实的边界框 (B, 6)
            pred_keypoints: 预测的关键点 (B, N_kp, 3)
            gt_keypoints: 真实的关键点 (B, N_kp, 3)

        Returns:
            total_loss: 几何回归总损失
            loss_dict: 各组件损失
        """
        bbox_loss = self.smooth_l1(pred_bbox, gt_bbox)
        kp_loss = self.smooth_l1(pred_keypoints, gt_keypoints)
        total = bbox_loss + kp_loss

        loss_dict = {
            "geometry_bbox": bbox_loss.item(),
            "geometry_keypoint": kp_loss.item(),
            "geometry_total": total.item(),
        }

        return total, loss_dict


class IJPELoss(nn.Module):
    """I-JEPA 3D组合损失函数。
    L_total = lambda1 * L_reconstruction + lambda2 * L_embedding + lambda3 * L_geometry

    Attributes:
        config: 模型配置
        recon_loss: 嵌入重构损失
        vicreg_loss: VICReg正则化损失
        geom_loss: 几何回归损失
    """

    def __init__(self, config: IJEPA3DConfig):
        """初始化组合损失。
        Args:
            config: I-JEPA 3D模型配置
        """
        super().__init__()
        self.config = config

        self.recon_loss = ReconstructionLoss()
        self.vicreg_loss = VICRegLoss(
            variance_weight=config.vicreg_variance_weight,
            covariance_weight=config.vicreg_covariance_weight,
            invariance_weight=config.vicreg_invariance_weight,
        )
        self.geom_loss = GeometryLoss(delta=config.smooth_l1_delta)

    def forward(
        self,
        predicted_embeddings: torch.Tensor,
        target_embeddings: torch.Tensor,
        encoder_embeddings: torch.Tensor,
        pred_bbox: torch.Tensor,
        gt_bbox: torch.Tensor,
        pred_keypoints: torch.Tensor,
        gt_keypoints: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算总损失。
        Args:
            predicted_embeddings: 预测器输出的嵌入 (B, N_t, D)
            target_embeddings: EMA编码器目标嵌入 (B, N_t, D)
            encoder_embeddings: 在线编码器嵌入 (B, N, D)
            pred_bbox: 预测边界框 (B, 6)
            gt_bbox: 真实边界框 (B, 6)
            pred_keypoints: 预测关键点 (B, N_kp, 3)
            gt_keypoints: 真实关键点 (B, N_kp, 3)

        Returns:
            total_loss: 总损失
            loss_dict: 详细损失字典
        """
        cfg = self.config

        # 1. 嵌入重构损失
        recon_loss_val, recon_dict = self.recon_loss(
            predicted_embeddings, target_embeddings,
        )

        # 2. VICReg正则化损失
        _ = encoder_embeddings.shape[0]
        enc_global = encoder_embeddings.mean(dim=1)  # (B, D)
        tgt_global = target_embeddings.mean(dim=1)  # (B, D)
        vicreg_loss_val, vicreg_dict = self.vicreg_loss(enc_global, tgt_global)

        # 3. 几何回归损失
        geom_loss_val, geom_dict = self.geom_loss(
            pred_bbox, gt_bbox, pred_keypoints, gt_keypoints,
        )

        # 组合总损失
        total = (
            cfg.lambda_reconstruction * recon_loss_val
            + cfg.lambda_embedding * vicreg_loss_val
            + cfg.lambda_geometry * geom_loss_val
        )

        # 聚合所有损失信息
        loss_dict = {
            "total_loss": total.item(),
            **recon_dict,
            **vicreg_dict,
            **geom_dict,
        }

        return total, loss_dict
