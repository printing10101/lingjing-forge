"""V-JEPA加工异常检测训练器。

训练策略：
- 优化器：AdamW，初始学习率1e-4
- 学习率调度：余弦退火 + 预热
- 批处理大小：>= 16
- 训练轮次：至少100 epoch
- 早停机制：验证集F1值5 epoch无提升则停止
- 正则化：Dropout(0.3) + L2正则化(1e-5)
"""

import torch
from torch.utils.data import DataLoader
from typing import Any, Dict, List, Optional, Tuple
import os
import json
import logging
import numpy as np
from datetime import datetime
from sklearn.metrics import f1_score, precision_score, recall_score, confusion_matrix

from app.ai.vjepa_machining.config import VJEPAMachiningConfig
from app.ai.vjepa_machining.model import VJEPAMachiningModel

logger = logging.getLogger(__name__)


class CosineWarmupScheduler:
    """余弦退火+预热学习率调度器。"""

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        warmup_epochs: int,
        total_epochs: int,
        base_lr: float = 1e-4,
        min_lr: float = 1e-7,
    ):
        self.optimizer = optimizer
        self.warmup_epochs = warmup_epochs
        self.total_epochs = total_epochs
        self.base_lr = base_lr
        self.min_lr = min_lr
        self.current_epoch = 0

    def step(self):
        self.current_epoch += 1
        lr = self._get_lr()
        for param_group in self.optimizer.param_groups:
            param_group["lr"] = lr
        return lr

    def _get_lr(self) -> float:
        if self.current_epoch < self.warmup_epochs:
            return self.base_lr * (self.current_epoch + 1) / self.warmup_epochs
        progress = (self.current_epoch - self.warmup_epochs) / max(
            self.total_epochs - self.warmup_epochs, 1,
        )
        return self.min_lr + 0.5 * (self.base_lr - self.min_lr) * (1 + np.cos(np.pi * progress))


class VJEPATrainer:
    """V-JEPA模型训练器。

    管理完整训练流程：
    - 优化器配置（AdamW）
    - 学习率调度（余弦退火+预热）
    - 混合精度训练
    - 梯度裁剪
    - EMA更新
    - 早停机制（基于验证集F1值）
    - 检查点管理
    """

    def __init__(
        self,
        model: VJEPAMachiningModel,
        config: VJEPAMachiningConfig,
        device: str = "cuda",
        output_dir: str = "./checkpoints/vjepa_machining/",
        use_amp: bool = True,
        gradient_clip_value: float = 1.0,
    ):
        self.model = model
        self.config = config
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.output_dir = output_dir
        self.use_amp = use_amp and self.device.type == "cuda"
        self.gradient_clip_value = gradient_clip_value

        self.model = self.model.to(self.device)

        self.optimizer: Optional[torch.optim.Optimizer] = None
        self.lr_scheduler: Optional[CosineWarmupScheduler] = None
        self.scaler = torch.cuda.amp.GradScaler() if self.use_amp else None

        self.current_epoch = 0
        self.global_step = 0
        self.best_val_f1 = 0.0
        self.patience_counter = 0

        self.training_history: Dict[str, List[float]] = {
            "train_loss": [], "val_loss": [],
            "train_f1": [], "val_f1": [],
            "train_precision": [], "val_precision": [],
            "train_recall": [], "val_recall": [],
            "learning_rate": [],
        }

        os.makedirs(output_dir, exist_ok=True)
        self._log_device_info()

    def _log_device_info(self):
        if self.device.type == "cuda":
            props = torch.cuda.get_device_properties(self.device)
            vram_mb = props.total_memory / (1024 ** 2)
            logger.info(f"Training on GPU: {props.name} (VRAM: {vram_mb:.0f}MB)")
            if self.use_amp:
                logger.info("AMP enabled")
        else:
            logger.info("Training on CPU")

    def _create_optimizer(self):
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=self.config.initial_lr,
            weight_decay=self.config.weight_decay,
        )

    def _create_scheduler(self):
        self.lr_scheduler = CosineWarmupScheduler(
            self.optimizer,
            warmup_epochs=self.config.warmup_epochs,
            total_epochs=self.config.epochs,
            base_lr=self.config.initial_lr,
        )

    def _get_mask_ratios(self, epoch: int, total_epochs: int) -> Tuple[float, float]:
        """获取渐进式掩码比例。"""
        if not self.config.progressive_masking:
            return self.config.temporal_mask_ratio_end, self.config.spatial_mask_ratio_end

        progress = min(epoch / max(total_epochs - 1, 1), 1.0)
        temporal = self.config.temporal_mask_ratio_start + (
            self.config.temporal_mask_ratio_end - self.config.temporal_mask_ratio_start
        ) * progress
        spatial = self.config.spatial_mask_ratio_start + (
            self.config.spatial_mask_ratio_end - self.config.spatial_mask_ratio_start
        ) * progress
        return temporal, spatial

    def train_epoch(self, train_loader: DataLoader, epoch: int, total_epochs: int) -> Dict[str, float]:
        self.model.train()
        total_loss = 0.0
        all_anomaly_preds = []
        all_anomaly_labels = []
        t_mask, s_mask = self._get_mask_ratios(epoch, total_epochs)

        for batch_idx, batch in enumerate(train_loader):
            video = batch["video"].to(self.device)  # (B, T, C, H, W)
            action_ids = batch["action_id"].to(self.device)
            anomaly_labels = batch["is_anomaly"].to(self.device)
            type_labels = batch["anomaly_type"].to(self.device)
            severity_labels = batch["severity"].to(self.device)

            video_input = video.permute(0, 2, 1, 3, 4)  # (B, C, T, H, W)

            if self.use_amp:
                with torch.cuda.amp.autocast():
                    output = self.model(
                        video_input, action_ids,
                        temporal_mask_ratio=t_mask,
                        spatial_mask_ratio=s_mask,
                        anomaly_labels=anomaly_labels,
                        type_labels=type_labels,
                        severity_labels=severity_labels,
                        return_loss=True,
                    )
                    loss = output["total_loss"]
            else:
                output = self.model(
                    video_input, action_ids,
                    temporal_mask_ratio=t_mask,
                    spatial_mask_ratio=s_mask,
                    anomaly_labels=anomaly_labels,
                    type_labels=type_labels,
                    severity_labels=severity_labels,
                    return_loss=True,
                )
                loss = output["total_loss"]

            self.optimizer.zero_grad()
            if self.use_amp:
                self.scaler.scale(loss).backward()
                if self.gradient_clip_value:
                    self.scaler.unscale_(self.optimizer)
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip_value)
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                loss.backward()
                if self.gradient_clip_value:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip_value)
                self.optimizer.step()

            self.model.update_ema()

            total_loss += loss.item()
            all_anomaly_preds.extend((output["anomaly_prob"] > 0.5).int().cpu().tolist())
            all_anomaly_labels.extend(anomaly_labels.int().cpu().tolist())
            self.global_step += 1

            if batch_idx % self.config.log_every_n_steps == 0:
                logger.info(
                    f"Epoch {epoch}/{total_epochs} [{batch_idx}/{len(train_loader)}] "
                    f"Loss: {loss.item():.4f} t_mask={t_mask:.2f} s_mask={s_mask:.2f}"
                )

        avg_loss = total_loss / len(train_loader)
        f1 = f1_score(all_anomaly_labels, all_anomaly_preds, average="binary", zero_division=0)
        precision = precision_score(all_anomaly_labels, all_anomaly_preds, average="binary", zero_division=0)
        recall = recall_score(all_anomaly_labels, all_anomaly_preds, average="binary", zero_division=0)

        return {"loss": avg_loss, "f1": f1, "precision": precision, "recall": recall}

    @torch.no_grad()
    def validate(self, val_loader: DataLoader) -> Dict[str, float]:
        self.model.eval()
        total_loss = 0.0
        all_anomaly_preds = []
        all_anomaly_labels = []
        all_type_preds = []
        all_type_labels = []

        for batch in val_loader:
            video = batch["video"].to(self.device)
            action_ids = batch["action_id"].to(self.device)
            anomaly_labels = batch["is_anomaly"].to(self.device)
            type_labels = batch["anomaly_type"].to(self.device)

            video_input = video.permute(0, 2, 1, 3, 4)

            output = self.model(
                video_input, action_ids,
                temporal_mask_ratio=0.1,
                spatial_mask_ratio=0.1,
                anomaly_labels=anomaly_labels,
                type_labels=type_labels,
                severity_labels=batch["severity"].to(self.device),
                return_loss=True,
            )

            total_loss += output["total_loss"].item()
            preds = (output["anomaly_prob"] > 0.5).int()
            all_anomaly_preds.extend(preds.cpu().tolist())
            all_anomaly_labels.extend(anomaly_labels.int().cpu().tolist())
            all_type_preds.extend(output["anomaly_type_pred"].cpu().tolist())
            all_type_labels.extend(type_labels.cpu().tolist())

        avg_loss = total_loss / len(val_loader)
        f1 = f1_score(all_anomaly_labels, all_anomaly_preds, average="binary", zero_division=0)
        precision = precision_score(all_anomaly_labels, all_anomaly_preds, average="binary", zero_division=0)
        recall = recall_score(all_anomaly_labels, all_anomaly_preds, average="binary", zero_division=0)
        cm = confusion_matrix(all_anomaly_labels, all_anomaly_preds)

        return {
            "loss": avg_loss, "f1": f1, "precision": precision, "recall": recall,
            "confusion_matrix": cm.tolist(),
        }

    def train(self, train_loader: DataLoader, val_loader: DataLoader) -> Dict[str, Any]:
        logger.info("=" * 60)
        logger.info("V-JEPA Machining Anomaly Detection Training")
        logger.info(f"Model params: {self.model.count_parameters()}")
        logger.info("=" * 60)

        self._create_optimizer()
        self._create_scheduler()

        for epoch in range(self.config.epochs):
            self.current_epoch = epoch

            train_metrics = self.train_epoch(train_loader, epoch, self.config.epochs)
            val_metrics = self.validate(val_loader)

            self.lr_scheduler.step()
            current_lr = self.optimizer.param_groups[0]["lr"]

            # 记录历史
            for k in ["loss", "f1", "precision", "recall"]:
                self.training_history[f"train_{k}"].append(train_metrics[k])
                self.training_history[f"val_{k}"].append(val_metrics[k])
            self.training_history["learning_rate"].append(current_lr)

            logger.info(
                f"Epoch {epoch}/{self.config.epochs} | "
                f"Train Loss: {train_metrics['loss']:.4f} F1: {train_metrics['f1']:.4f} | "
                f"Val Loss: {val_metrics['loss']:.4f} F1: {val_metrics['f1']:.4f} | "
                f"LR: {current_lr:.2e}"
            )

            # 早停检查（基于验证集F1）
            if val_metrics["f1"] > self.best_val_f1:
                self.best_val_f1 = val_metrics["f1"]
                self.patience_counter = 0
                self.save_checkpoint("best.pth", epoch, val_metrics)
            else:
                self.patience_counter += 1
                if self.patience_counter >= self.config.early_stopping_patience:
                    logger.info(f"Early stopping at epoch {epoch} (patience={self.config.early_stopping_patience})")
                    break

            if (epoch + 1) % self.config.save_every_n_epochs == 0:
                self.save_checkpoint(f"epoch_{epoch + 1}.pth", epoch, val_metrics)

        # 保存训练历史
        self.save_history()

        logger.info("=" * 60)
        logger.info(f"Training Complete! Best Val F1: {self.best_val_f1:.4f}")
        logger.info("=" * 60)

        return {"best_val_f1": self.best_val_f1, "history": self.training_history}

    def save_checkpoint(self, filename: str, epoch: int, metrics: Dict[str, float]):
        checkpoint_path = os.path.join(self.output_dir, filename)
        torch.save({
            "epoch": epoch,
            "global_step": self.global_step,
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict() if self.optimizer else None,
            "metrics": metrics,
            "config": self.config,
            "timestamp": datetime.now().isoformat(),
        }, checkpoint_path)
        logger.info(f"Checkpoint saved: {checkpoint_path}")

    def load_checkpoint(self, checkpoint_path: str) -> Dict[str, Any]:
        checkpoint = torch.load(checkpoint_path, map_location=self.device)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.current_epoch = checkpoint.get("epoch", 0)
        self.global_step = checkpoint.get("global_step", 0)
        logger.info(f"Checkpoint loaded: epoch={self.current_epoch}")
        return checkpoint

    def save_history(self):
        history_path = os.path.join(self.output_dir, "training_history.json")
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(self.training_history, f, indent=2)
        logger.info(f"Training history saved: {history_path}")
