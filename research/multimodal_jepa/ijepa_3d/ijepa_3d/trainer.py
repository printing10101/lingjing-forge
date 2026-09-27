"""I-JEPA 3D训练流程模块。
实现两阶段微调策略：
- 阶段一：冻结编码器前8层，训练预测器与融合模块
  - 学习率: 5e-4, 批量大小: 32, 训练100 epoch
- 阶段二：解冻所有层，联合训练
  - 学习率: 1e-4, 批量大小: 16, 训练200 epoch

支持渐进式掩码增强、EMA更新、模型检查点保存和训练日志。
Key components:
    - IJEPA3DTrainer: 训练流程管理器
Example:
    >>> trainer = IJEPA3DTrainer(model, config, device="cuda")
    >>> history = trainer.train(train_loader, val_loader)
"""

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from typing import Any, Dict, List, Optional, Tuple
import os
import json
import logging
from datetime import datetime
# Q2 修复：原 `from app.ai.ijepa_3d.xxx` 为悬空 import，改为同包相对导入。
from .config import IJEPA3DConfig
from .model import IJEPA3DModel

logger = logging.getLogger(__name__)


class IJEPA3DTrainer:
    """I-JEPA 3D模型训练器。
    管理完整的两阶段训练流程，包括：
    - 优化器配置
    - 学习率调整
    - 混合精度训练（AMP）
    - 梯度裁剪
    - 检查点管理
    - EMA更新
    - 训练日志

    Attributes:
        model: I-JEPA 3D模型
        config: 模型配置
        device: 计算设备
        optimizer: 优化器
        lr_scheduler: 学习率调度器
        scaler: AMP梯度缩放器
        current_epoch: 当前训练轮次
        global_step: 全局训练步数
        best_val_loss: 最佳验证损失
        training_history: 训练历史记录
    """

    def __init__(
        self,
        model: IJEPA3DModel,
        config: IJEPA3DConfig,
        device: str = "cuda",
        output_dir: str = "./checkpoints/ijepa_3d/",
        use_amp: bool = True,
        gradient_clip_value: float = 1.0,
    ):
        """初始化训练器。
        Args:
            model: I-JEPA 3D模型
            config: 模型配置
            device: 计算设备（cuda/cpu）
            output_dir: 模型保存目录
            use_amp: 是否启用自动混合精度
            gradient_clip_value: 梯度裁剪阈值
        """
        self.model = model
        self.config = config
        self.device = torch.device(device)
        self.output_dir = output_dir
        self.use_amp = (
            use_amp and self.device.type == "cuda" and torch.cuda.is_available()
        )
        self.gradient_clip_value = gradient_clip_value

        self.model = self.model.to(self.device)

        # 训练状态
        self.optimizer: Optional[torch.optim.Optimizer] = None
        self.lr_scheduler: Optional[torch.optim.lr_scheduler._LRScheduler] = None
        self.scaler = torch.cuda.amp.GradScaler() if self.use_amp else None

        self.current_epoch = 0
        self.global_step = 0
        self.current_stage = 0
        self.best_val_loss = float("inf")
        self.patience_counter = 0

        # 训练历史
        self.training_history: Dict[str, List[float]] = {
            "train_loss": [],
            "val_loss": [],
            "train_bbox_error": [],
            "val_bbox_error": [],
            "train_kp_error": [],
            "val_kp_error": [],
            "learning_rate": [],
            "mask_ratio": [],
        }

        # 创建保存目录
        os.makedirs(output_dir, exist_ok=True)

        self._log_device_info()

    def _log_device_info(self) -> None:
        """记录设备信息。"""
        if self.device.type == "cuda":
            props = torch.cuda.get_device_properties(self.device)
            vram_mb = props.total_memory / (1024 ** 2)
            logger.info(
                f"Training on GPU: {props.name} "
                f"(VRAM: {vram_mb:.0f}MB, CUDA: {torch.version.cuda})"
            )
            if self.use_amp:
                logger.info("AMP (mixed precision) enabled")
        else:
            logger.info("Training on CPU")

    def _create_optimizer(
        self,
        stage: int,
        lr: float,
    ) -> torch.optim.Optimizer:
        """创建阶段优化器。
        Args:
            stage: 训练阶段（1或2）
            lr: 学习率
        Returns:
            优化器实例
        """
        param_groups = self.model.get_optimizer_groups(stage=stage, lr=lr)
        return torch.optim.AdamW(param_groups, weight_decay=1e-4)

    def _create_lr_scheduler(
        self,
        optimizer: torch.optim.Optimizer,
        total_epochs: int,
    ) -> torch.optim.lr_scheduler._LRScheduler:
        """创建余弦退火学习率调度器。
        Args:
            optimizer: 优化器
            total_epochs: 总训练轮数
        Returns:
            学习率调度器
        """
        return torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=total_epochs, eta_min=1e-6,
        )

    def _get_mask_ratio(self, epoch: int, total_epochs: int) -> float:
        """获取当前训练阶段的掩码比例。
        实现渐进式掩码增强。
        Args:
            epoch: 当前轮次
            total_epochs: 总轮数
        Returns:
            掩码比例
        """
        if not self.config.progressive_masking:
            return self.config.mask_ratio_end

        progress = min(epoch / max(total_epochs - 1, 1), 1.0)
        return (
            self.config.mask_ratio_start
            + (self.config.mask_ratio_end - self.config.mask_ratio_start) * progress
        )

    def train_epoch(
        self,
        train_loader: DataLoader,
        epoch: int,
        total_epochs: int,
        stage: int,
    ) -> Dict[str, float]:
        """训练一个epoch。
        Args:
            train_loader: 训练数据加载器
            epoch: 当前轮次
            total_epochs: 总轮数
            stage: 训练阶段

        Returns:
            训练指标字典
        """
        self.model.train()

        total_loss = 0.0
        total_bbox_error = 0.0
        total_kp_error = 0.0
        num_batches = len(train_loader)
        mask_ratio = self._get_mask_ratio(epoch, total_epochs)

        for batch_idx, batch in enumerate(train_loader):
            # 数据传输到设备
            front = batch["front_image"].to(self.device)
            side = batch["side_image"].to(self.device)
            top = batch["top_image"].to(self.device)
            gt_bbox = batch["bbox"].to(self.device)
            gt_kp = batch["keypoints"].to(self.device)

            # 前向传播
            if self.use_amp:
                with torch.cuda.amp.autocast():
                    loss, loss_dict = self._forward_with_loss(
                        front, side, top, gt_bbox, gt_kp, mask_ratio,
                    )
            else:
                loss, loss_dict = self._forward_with_loss(
                    front, side, top, gt_bbox, gt_kp, mask_ratio,
                )

            # 反向传播
            self.optimizer.zero_grad()

            if self.use_amp:
                self.scaler.scale(loss).backward()
                if self.gradient_clip_value:
                    self.scaler.unscale_(self.optimizer)
                    torch.nn.utils.clip_grad_norm_(
                        self.model.parameters(), self.gradient_clip_value,
                    )
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                loss.backward()
                if self.gradient_clip_value:
                    torch.nn.utils.clip_grad_norm_(
                        self.model.parameters(), self.gradient_clip_value,
                    )
                self.optimizer.step()

            # EMA更新（每个batch后）
            self.model.encoder.update_ema()

            # 累积指标
            total_loss += loss.item()
            total_bbox_error += loss_dict.get("geometry_bbox", 0.0)
            total_kp_error += loss_dict.get("geometry_keypoint", 0.0)
            self.global_step += 1

            # 日志记录
            if batch_idx % self.config.log_every_n_steps == 0:
                logger.info(
                    f"Stage{stage} Epoch {epoch}/{total_epochs} "
                    f"[{batch_idx}/{num_batches}] "
                    f"Loss: {loss.item():.4f} "
                    f"BBox: {loss_dict.get('geometry_bbox', 0):.4f} "
                    f"KP: {loss_dict.get('geometry_keypoint', 0):.4f} "
                    f"Mask: {mask_ratio:.2f}"
                )

        # 计算平均指标
        avg_loss = total_loss / num_batches
        avg_bbox_error = total_bbox_error / num_batches
        avg_kp_error = total_kp_error / num_batches

        metrics = {
            "loss": avg_loss,
            "bbox_error": avg_bbox_error,
            "kp_error": avg_kp_error,
            "mask_ratio": mask_ratio,
            "lr": self.optimizer.param_groups[0]["lr"],
        }

        return metrics

    @torch.no_grad()
    def validate(
        self,
        val_loader: DataLoader,
    ) -> Dict[str, float]:
        """验证模型。
        Args:
            val_loader: 验证数据加载器
        Returns:
            验证指标字典
        """
        self.model.eval()

        total_loss = 0.0
        total_bbox_error = 0.0
        total_kp_error = 0.0
        num_batches = len(val_loader)

        for batch in val_loader:
            front = batch["front_image"].to(self.device)
            side = batch["side_image"].to(self.device)
            top = batch["top_image"].to(self.device)
            gt_bbox = batch["bbox"].to(self.device)
            gt_kp = batch["keypoints"].to(self.device)

            # 推理（无掩码）
            bbox_pred, kp_pred, _ = self.model.forward_inference(
                front, side, top,
            )

            # 计算验证损失
            bbox_loss = nn.SmoothL1Loss(beta=self.config.smooth_l1_delta)(
                bbox_pred, gt_bbox,
            )
            kp_loss = nn.SmoothL1Loss(beta=self.config.smooth_l1_delta)(
                kp_pred, gt_kp,
            )
            loss = bbox_loss + kp_loss

            total_loss += loss.item()
            total_bbox_error += bbox_loss.item()
            total_kp_error += kp_loss.item()

        avg_loss = total_loss / num_batches
        avg_bbox_error = total_bbox_error / num_batches
        avg_kp_error = total_kp_error / num_batches

        return {
            "loss": avg_loss,
            "bbox_error": avg_bbox_error,
            "kp_error": avg_kp_error,
        }

    def _forward_with_loss(
        self,
        front: torch.Tensor,
        side: torch.Tensor,
        top: torch.Tensor,
        gt_bbox: torch.Tensor,
        gt_kp: torch.Tensor,
        mask_ratio: float,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """前向传播并计算损失。
        Args:
            front: 正视视图
            side: 侧视视图
            top: 俯视视图
            gt_bbox: 真实边界框
            gt_kp: 真实关键点
            mask_ratio: 掩码比例

        Returns:
            total_loss: 总损失
            loss_dict: 详细损失字典
        """
        output = self.model.forward(
            front, side, top, mask_ratio=mask_ratio,
        )

        bbox_pred = output["bbox_pred"]
        kp_pred = output["keypoints_pred"]

        # I-JEPA重构损失
        if "pred_embeddings" in output and "target_embeddings" in output:
            pred_emb = output["pred_embeddings"]
            tgt_emb = output["target_embeddings"]
            enc_global = output["encoder_embeddings"][0]

            total_loss, loss_dict = self.model.loss_fn(
                pred_emb, tgt_emb, enc_global,
                bbox_pred, gt_bbox, kp_pred, gt_kp,
            )
        else:
            # 仅计算几何损失
            geom_total = nn.SmoothL1Loss(beta=self.config.smooth_l1_delta)(
                bbox_pred, gt_bbox,
            ) + nn.SmoothL1Loss(beta=self.config.smooth_l1_delta)(kp_pred, gt_kp)
            total_loss = geom_total
            loss_dict = {
                "total_loss": geom_total.item(),
                "geometry_bbox": nn.SmoothL1Loss(
                    beta=self.config.smooth_l1_delta,
                )(bbox_pred, gt_bbox).item(),
                "geometry_keypoint": nn.SmoothL1Loss(
                    beta=self.config.smooth_l1_delta,
                )(kp_pred, gt_kp).item(),
            }

        return total_loss, loss_dict

    def train_stage(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        stage: int,
        epochs: int,
        lr: float,
        early_stopping_patience: int = 10,
    ) -> Dict[str, Any]:
        """执行单个训练阶段。
        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            stage: 训练阶段（1或2）
            epochs: 训练轮数
            lr: 学习率
            early_stopping_patience: 早停耐心值
        Returns:
            训练结果字典
        """
        self.current_stage = stage

        # 设置阶段特定配置
        if stage == 1:
            self.model.freeze_encoder_layers(self.config.frozen_layers)
        else:
            self.model.unfreeze_all()

        self.optimizer = self._create_optimizer(stage, lr)
        self.lr_scheduler = self._create_lr_scheduler(self.optimizer, epochs)
        self.patience_counter = 0
        stage_best_loss = float("inf")

        logger.info("=" * 60)
        logger.info(f"Starting Stage {stage} Training: {epochs} epochs, lr={lr}")
        logger.info("=" * 60)

        for epoch in range(epochs):
            self.current_epoch = epoch

            # 训练
            train_metrics = self.train_epoch(
                train_loader, epoch, epochs, stage,
            )

            # 验证
            val_metrics = self.validate(val_loader)

            # 学习率调整
            self.lr_scheduler.step()

            # 记录历史
            self.training_history["train_loss"].append(train_metrics["loss"])
            self.training_history["val_loss"].append(val_metrics["loss"])
            self.training_history["train_bbox_error"].append(
                train_metrics["bbox_error"],
            )
            self.training_history["val_bbox_error"].append(val_metrics["bbox_error"])
            self.training_history["train_kp_error"].append(
                train_metrics["kp_error"],
            )
            self.training_history["val_kp_error"].append(val_metrics["kp_error"])
            self.training_history["learning_rate"].append(train_metrics["lr"])
            self.training_history["mask_ratio"].append(train_metrics["mask_ratio"])

            # 打印epoch摘要
            logger.info(
                f"Stage{stage} Epoch {epoch}/{epochs} | "
                f"Train Loss: {train_metrics['loss']:.4f} | "
                f"Val Loss: {val_metrics['loss']:.4f} | "
                f"BBox Err: {val_metrics['bbox_error']:.4f} | "
                f"KP Err: {val_metrics['kp_error']:.4f} | "
                f"LR: {train_metrics['lr']:.2e}"
            )

            # 早停检查
            if val_metrics["loss"] < stage_best_loss:
                stage_best_loss = val_metrics["loss"]
                self.patience_counter = 0

                # 保存最佳模型
                self.save_checkpoint("best.pth", stage, epoch, val_metrics)
            else:
                self.patience_counter += 1
                if self.patience_counter >= early_stopping_patience:
                    logger.info(
                        f"Early stopping at epoch {epoch} "
                        f"(patience={early_stopping_patience})",
                    )
                    break

            # 定期保存检查点
            if (epoch + 1) % self.config.save_every_n_epochs == 0:
                self.save_checkpoint(
                    f"stage{stage}_epoch{epoch + 1}.pth",
                    stage, epoch, val_metrics,
                )

        # 更新全局最佳损失
        if stage_best_loss < self.best_val_loss:
            self.best_val_loss = stage_best_loss

        return {
            "stage": stage,
            "best_val_loss": stage_best_loss,
            "total_epochs": epoch + 1,
        }

    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
    ) -> Dict[str, Any]:
        """执行完整的两阶段训练流程。
        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
        Returns:
            训练结果字典
        """
        logger.info("=" * 60)
        logger.info("I-JEPA 3D Training Pipeline Starting")
        logger.info(f"Model params: {self.model.count_parameters()}")
        logger.info("=" * 60)

        # 阶段一：冻结编码器前8层
        stage1_result = self.train_stage(
            train_loader=train_loader,
            val_loader=val_loader,
            stage=1,
            epochs=self.config.stage1_epochs,
            lr=self.config.stage1_lr,
        )

        # 阶段二：全模型联合训练
        stage2_result = self.train_stage(
            train_loader=train_loader,
            val_loader=val_loader,
            stage=2,
            epochs=self.config.stage2_epochs,
            lr=self.config.stage2_lr,
        )

        # 保存训练历史
        self.save_history()

        logger.info("=" * 60)
        logger.info("Training Complete!")
        logger.info(
            f"Stage 1 Best: {stage1_result['best_val_loss']:.4f}, "
            f"Stage 2 Best: {stage2_result['best_val_loss']:.4f}",
        )
        logger.info("=" * 60)

        return {"stage1": stage1_result, "stage2": stage2_result}

    def save_checkpoint(
        self,
        filename: str,
        stage: int,
        epoch: int,
        metrics: Dict[str, float],
    ) -> None:
        """保存模型检查点。
        Args:
            filename: 文件名
            stage: 训练阶段
            epoch: 当前轮次
            metrics: 验证指标
        """
        checkpoint_path = os.path.join(self.output_dir, filename)

        checkpoint = {
            "epoch": epoch,
            "stage": stage,
            "global_step": self.global_step,
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": (
                self.optimizer.state_dict() if self.optimizer else None
            ),
            "metrics": metrics,
            "config": self.config,
            "timestamp": datetime.now().isoformat(),
        }

        torch.save(checkpoint, checkpoint_path)
        logger.info(f"Checkpoint saved: {checkpoint_path}")

    def load_checkpoint(self, checkpoint_path: str) -> Dict[str, Any]:
        """加载模型检查点。
        Args:
            checkpoint_path: 检查点路径

        Returns:
            检查点信息字典
        """
        checkpoint = torch.load(checkpoint_path, map_location=self.device)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.current_epoch = checkpoint.get("epoch", 0)
        self.global_step = checkpoint.get("global_step", 0)

        logger.info(
            f"Checkpoint loaded from {checkpoint_path} "
            f"(epoch={self.current_epoch})"
        )
        return checkpoint

    def save_history(self) -> None:
        """保存训练历史到JSON文件。"""
        history_path = os.path.join(self.output_dir, "training_history.json")
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(self.training_history, f, indent=2)
        logger.info(f"Training history saved: {history_path}")
