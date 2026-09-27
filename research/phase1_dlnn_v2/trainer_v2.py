"""
三阶段退火训练器 （Phase 1 核心）

实现课程式训练：
    Stage 1: 仅数据损失（MAE），解析预训练
    Stage 2: 加入 L_phys + L_pcc，退火 ramp-in
    Stage 3: 加入 L_freq + τ 正则化，退火 ramp-in

与原始 DLLNNTrainer 接口兼容，新增：
    - 自动阶段检测与切换
    - 每层 τ 值实时监控
    - 长时间预测指标（RMSE over horizon）
"""

import sys
import os
import torch
import torch.nn as nn
import numpy as np
from torch.utils.data import DataLoader
from typing import Dict, Tuple, List, Optional
import json
from datetime import datetime

# 导入项目模块
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from training.reproducibility import set_global_seed
except ImportError:

    def set_global_seed(seed: int):
        torch.manual_seed(seed)
        np.random.seed(seed)


from models_v2 import DLLNNModelV2, DLLNNWithPhysicsV2, LTCWithDelayCell
from losses_v2 import CurriculumPhysicsLoss, compute_tau_regularization_all_layers
from config_v2 import Phase1Config

# 尝试导入原始指标模块
try:
    from experiments.metrics import MetricsTracker, ChatterMetrics

    _HAS_METRICS = True
except ImportError:
    _HAS_METRICS = False


class Phase1Metrics:
    """Phase 1 专用指标计算器（不依赖原 metrics 模块）。"""

    @staticmethod
    def mae(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        return float(np.mean(np.abs(y_pred - y_true)))

    @staticmethod
    def rmse(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        return float(np.sqrt(np.mean((y_pred - y_true) ** 2)))

    @staticmethod
    def r2(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        ss_res = float(np.sum((y_true - y_pred) ** 2))
        ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
        return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    @staticmethod
    def rmse_over_horizon(y_pred: np.ndarray, y_true: np.ndarray) -> np.ndarray:
        """逐预测步的 RMSE [horizon]"""
        horizon = y_pred.shape[1]
        return np.array([np.sqrt(np.mean((y_pred[:, i] - y_true[:, i]) ** 2)) for i in range(horizon)])

    @staticmethod
    def mae_over_horizon(y_pred: np.ndarray, y_true: np.ndarray) -> np.ndarray:
        horizon = y_pred.shape[1]
        return np.array([np.mean(np.abs(y_pred[:, i] - y_true[:, i])) for i in range(horizon)])


class Phase1Trainer:
    """
    Phase 1 三阶段训练器。

    Args:
        config: Phase 1 配置
        model: DLLNNWithPhysicsV2 或 DLLNNModelV2 实例
        device: 训练设备
    """

    def __init__(
        self,
        config: Phase1Config,
        model: nn.Module,
        device: str = "cuda",
    ):
        set_global_seed(config.seed)

        self.config = config
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.model = model.to(self.device)

        # 设置 τ 物理参考值（修复：之前 tau_phys=None 导致 L_tau_reg 始终为 0）
        tau_phys_ref = 60.0 / 5000.0  # 物理参考：τ=60/n，n=5000rpm → 0.012s
        if hasattr(self.model, "ltc_branch") and hasattr(self.model.ltc_branch, "ltc_cells"):
            for cell in self.model.ltc_branch.ltc_cells:
                if hasattr(cell, "delay_embedding"):
                    cell.delay_embedding.tau_phys = tau_phys_ref

        # 课程式损失
        self.criterion = CurriculumPhysicsLoss(
            epsilon_phys=config.epsilon_phys,
            lambda_phys_max=config.lambda_phys,
            lambda_pcc_max=config.lambda_pcc,
            lambda_freq_max=config.lambda_freq_end,
            lambda_tau_reg=config.lambda_tau_reg,
            stage1_epochs=config.num_epochs_stage1,
            stage2_epochs=config.num_epochs_stage2,
            stage3_epochs=config.num_epochs_stage3,
        )

        # 全局 epoch 计数器（跨阶段累计）
        self.global_epoch = 0
        self.current_stage = 1

        # 训练历史
        self.history: Dict[str, list] = {
            "epoch": [],
            "stage": [],
            "train_loss": [],
            "val_loss": [],
            "train_mae": [],
            "val_mae": [],
            "train_r2": [],
            "val_r2": [],
            "tau_layer1": [],
            "tau_layer2": [],
            "tau_layer3": [],
            "tau_reg": [],
            "freq_loss": [],
        }

        # 最佳模型
        self.best_val_loss = float("inf")
        self.best_model_state = None

        # Target 归一化
        self.target_mean = 0.0
        self.target_std = 1.0
        self._target_stats_computed = False

    def _compute_target_stats(self, train_loader: DataLoader) -> None:
        """计算 target 归一化统计量。"""
        if self._target_stats_computed:
            return
        all_y = []
        for batch in train_loader:
            if len(batch) == 3:
                _, y_true, _ = batch
            else:
                _, y_true = batch
            if y_true.dim() > 2:
                y_true_flat = y_true[:, 0, :]  # [B, 20, 1] → [B, 1]
            elif y_true.dim() > 1 and y_true.shape[1] > 1:
                y_true_flat = y_true[:, 0:1]
            else:
                y_true_flat = y_true
            all_y.append(y_true_flat.cpu().numpy().reshape(-1))
        all_y = np.concatenate(all_y, axis=0)
        self.target_mean = float(np.mean(all_y))
        self.target_std = float(np.std(all_y) + 1e-8)
        self._target_stats_computed = True
        print(f"  [Target 归一化] mean={self.target_mean:.4f}, std={self.target_std:.4f}")

    def denormalize(self, y: np.ndarray) -> np.ndarray:
        if not self._target_stats_computed:
            return y
        return y * self.target_std + self.target_mean

    def _get_optimizer(self, lr: float) -> torch.optim.Optimizer:
        return torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=self.config.weight_decay)

    def _log_tau_values(self, stage_label: str) -> None:
        """打印当前各层 τ 值。"""
        if hasattr(self.model, "all_taus"):
            taus = self.model.all_taus
            tau_str = ", ".join([f"L{i + 1}={t.item():.4f}s" for i, t in enumerate(taus)])
            print(f"  [{stage_label}] τ: {tau_str}")

    # Stage 1: 解析预训练

    def train_stage1(self, train_loader: DataLoader, val_loader: DataLoader) -> Dict:
        """阶段一：仅数据损失预训练。"""
        set_global_seed(self.config.seed)
        self._compute_target_stats(train_loader)

        n_epochs = self.config.num_epochs_stage1
        optimizer = self._get_optimizer(self.config.lr_stage1)

        print(f"\n{'=' * 60}")
        print(f"Phase 1 Stage 1: 解析预训练 ({n_epochs} epochs, LR={self.config.lr_stage1})")
        print(f"{'=' * 60}")

        for ep in range(n_epochs):
            self.global_epoch += 1
            self.current_stage = 1

            train_loss, train_metrics = self._train_epoch(train_loader, optimizer, stage=1)
            val_loss, val_metrics = self._validate(val_loader, stage=1)

            self._log_metrics(ep, n_epochs, train_loss, val_loss, train_metrics, val_metrics)

            if (ep + 1) % 20 == 0:
                self._log_tau_values("S1")

        return self.history

    # Stage 2: 物理引导微调

    def train_stage2(self, train_loader: DataLoader, val_loader: DataLoader) -> Dict:
        """阶段二：加入 L_phys + L_pcc。"""
        n_epochs = self.config.num_epochs_stage2
        optimizer = self._get_optimizer(self.config.lr_stage2)

        print(f"\n{'=' * 60}")
        print(f"Phase 1 Stage 2: 物理引导微调 ({n_epochs} epochs, LR={self.config.lr_stage2})")
        print(f"{'=' * 60}")

        for ep in range(n_epochs):
            self.global_epoch += 1
            self.current_stage = 2

            train_loss, train_metrics = self._train_epoch(train_loader, optimizer, stage=2)
            val_loss, val_metrics = self._validate(val_loader, stage=2)

            self._log_metrics(ep, n_epochs, train_loss, val_loss, train_metrics, val_metrics)

            if (ep + 1) % 20 == 0:
                self._log_tau_values("S2")

        return self.history

    # Stage 3: 频域精调

    def train_stage3(self, train_loader: DataLoader, val_loader: DataLoader) -> Dict:
        """阶段三：加入 L_freq + τ 正则化。"""
        n_epochs = self.config.num_epochs_stage3
        optimizer = self._get_optimizer(self.config.lr_stage3)

        print(f"\n{'=' * 60}")
        print(f"Phase 1 Stage 3: 频域精调 ({n_epochs} epochs, LR={self.config.lr_stage3})")
        print(f"{'=' * 60}")

        best_val = float("inf")
        for ep in range(n_epochs):
            self.global_epoch += 1
            self.current_stage = 3

            train_loss, train_metrics = self._train_epoch(train_loader, optimizer, stage=3)
            val_loss, val_metrics = self._validate(val_loader, stage=3)

            self._log_metrics(ep, n_epochs, train_loss, val_loss, train_metrics, val_metrics)

            # 保存 Stage 3 最佳模型
            if val_loss < best_val:
                best_val = val_loss
                self.best_model_state = {k: v.cpu().clone() for k, v in self.model.state_dict().items()}
                self.best_val_loss = val_loss
                print(f"  [S3] 最佳模型 (val_loss={val_loss:.4f})")

            if (ep + 1) % 10 == 0:
                self._log_tau_values("S3")

        # 加载最佳模型
        if self.best_model_state is not None:
            self.model.load_state_dict(self.best_model_state)
            print(f"  => 已加载最佳模型 (best_val_loss={self.best_val_loss:.4f})")

        return self.history

    # 训练循环内部

    def _train_epoch(
        self, train_loader: DataLoader, optimizer: torch.optim.Optimizer, stage: int
    ) -> Tuple[float, Dict]:
        self.model.train()
        total_loss = 0.0
        n_batches = 0
        all_preds, all_targets = [], []

        for batch in train_loader:
            x, y_true, y_physics = self._unpack_batch(batch)

            # Stage 2+ 需要 x 的梯度
            if stage >= 2:
                x.requires_grad_(True)

            optimizer.zero_grad()

            if stage == 1:
                # 纯 MAE，无物理分支
                y_pred, _ = self.model(x, use_horizon=False)
                yt = (
                    y_true[:, 0, :]
                    if y_true.dim() > 2
                    else (y_true[:, 0:1] if y_true.dim() > 1 and y_true.shape[1] > 1 else y_true)
                )
                loss = nn.functional.l1_loss(y_pred, yt)
            else:
                # 使用物理分支进行门控融合（标量模式：y_physics 压缩为 [B,1]）
                y_phys_scalar = y_physics
                if y_physics is not None and y_physics.dim() > 2:
                    y_phys_scalar = y_physics[:, 0, :]  # [B, H, 1] → [B, 1]
                y_pred, _ = self.model(x, physics_pred=y_phys_scalar, use_horizon=False)

                # 可微物理预测
                y_physics_diff = None
                if hasattr(self.model, "compute_differentiable_physics"):
                    y_physics_diff = self.model.compute_differentiable_physics(x)
                    if self._target_stats_computed:
                        y_physics_diff = (y_physics_diff - self.target_mean) / self.target_std

                # τ 正则化
                tau_reg_term = torch.tensor(0.0, device=x.device)
                if hasattr(self.model, "ltc_branch"):
                    tau_reg_term = compute_tau_regularization_all_layers(
                        self.model.ltc_branch.ltc_cells,
                        spindle_speed=x[:, 0:1],  # 第一维 = n
                    )

                yt = (
                    y_true[:, 0, :]
                    if y_true.dim() > 2
                    else (y_true[:, 0:1] if y_true.dim() > 1 and y_true.shape[1] > 1 else y_true)
                )
                loss, _ = self.criterion(y_pred, yt, y_physics, x, y_physics_diff, tau_reg_term, self.global_epoch)

            loss.backward()
            # 梯度裁剪：防止 R²0.99 后梯度爆炸导致 NaN
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
            optimizer.step()

            total_loss += loss.item()
            n_batches += 1
            all_preds.append(y_pred.detach().cpu().numpy())
            all_targets.append(
                y_true[:, 0, :].detach().cpu().numpy()
                if y_true.dim() > 2
                else (
                    y_true[:, 0:1].detach().cpu().numpy()
                    if y_true.dim() > 1 and y_true.shape[1] > 1
                    else y_true.detach().cpu().numpy()
                )
            )

        avg_loss = total_loss / max(1, n_batches)
        all_preds_np = np.concatenate(all_preds, axis=0)
        all_targets_np = np.concatenate(all_targets, axis=0)

        metrics = {
            "mae": Phase1Metrics.mae(all_preds_np, all_targets_np),
            "rmse": Phase1Metrics.rmse(all_preds_np, all_targets_np),
            "r2": Phase1Metrics.r2(all_preds_np, all_targets_np),
        }

        return avg_loss, metrics

    def _validate(self, val_loader: DataLoader, stage: int) -> Tuple[float, Dict]:
        self.model.eval()
        total_loss = 0.0
        n_batches = 0
        all_preds, all_targets = [], []

        with torch.no_grad():
            for batch in val_loader:
                x, y_true, y_physics = self._unpack_batch(batch)

                y_phys_scalar = y_physics
                if stage >= 2 and y_physics is not None and y_physics.dim() > 2:
                    y_phys_scalar = y_physics[:, 0, :]
                y_pred, _ = self.model(
                    x,
                    physics_pred=y_phys_scalar if stage >= 2 else None,
                    use_horizon=False,
                )

                yt = (
                    y_true[:, 0, :]
                    if y_true.dim() > 2
                    else (y_true[:, 0:1] if y_true.dim() > 1 and y_true.shape[1] > 1 else y_true)
                )
                loss = nn.functional.l1_loss(y_pred, yt)

                total_loss += loss.item()
                n_batches += 1
                all_preds.append(y_pred.cpu().numpy())
                all_targets.append(yt.cpu().numpy())

        avg_loss = total_loss / max(1, n_batches)
        all_preds_np = np.concatenate(all_preds, axis=0)
        all_targets_np = np.concatenate(all_targets, axis=0)

        metrics = {
            "mae": Phase1Metrics.mae(all_preds_np, all_targets_np),
            "rmse": Phase1Metrics.rmse(all_preds_np, all_targets_np),
            "r2": Phase1Metrics.r2(all_preds_np, all_targets_np),
        }

        return avg_loss, metrics

    def _unpack_batch(self, batch):
        """解包批次并应用归一化。"""
        if len(batch) == 3:
            x, y_true, y_physics = batch
        elif len(batch) == 2:
            x, y_true = batch
            y_physics = None
        else:
            x = batch[0]
            y_true = batch[1]
            y_physics = batch[2] if len(batch) > 2 else None

        x = x.to(self.device)
        y_true = y_true.to(self.device)

        if y_physics is not None:
            y_physics = y_physics.to(self.device)
            if self._target_stats_computed:
                y_physics = (y_physics - self.target_mean) / self.target_std

        if self._target_stats_computed:
            y_true = (y_true - self.target_mean) / self.target_std

        return x, y_true, y_physics

    def _log_metrics(
        self,
        ep: int,
        total: int,
        train_loss: float,
        val_loss: float,
        train_m: Dict,
        val_m: Dict,
    ):
        """记录指标到历史。"""
        self.history["epoch"].append(self.global_epoch)
        self.history["stage"].append(self.current_stage)
        self.history["train_loss"].append(train_loss)
        self.history["val_loss"].append(val_loss)
        self.history["train_mae"].append(train_m["mae"])
        self.history["val_mae"].append(val_m["mae"])
        self.history["train_r2"].append(train_m["r2"])
        self.history["val_r2"].append(val_m["r2"])

        # τ 值记录
        if hasattr(self.model, "all_taus"):
            taus = self.model.all_taus
            for i in range(min(3, len(taus))):
                key = f"tau_layer{i + 1}"
                if key in self.history:
                    self.history[key].append(taus[i].item())

        if (ep + 1) % 10 == 0 or ep == 0:
            stage_str = ["", "S1", "S2", "S3"][self.current_stage]
            print(
                f"[{stage_str}] Epoch {ep + 1}/{total} | "
                f"Train Loss: {train_loss:.4f} MAE: {train_m['mae']:.4f} R²: {train_m['r2']:.4f} | "
                f"Val Loss: {val_loss:.4f} MAE: {val_m['mae']:.4f} R²: {val_m['r2']:.4f}"
            )

    # 完整训练

    def train(self, train_loader: DataLoader, val_loader: DataLoader) -> Dict:
        """执行完整三阶段训练。"""
        self.train_stage1(train_loader, val_loader)
        self.train_stage2(train_loader, val_loader)
        self.train_stage3(train_loader, val_loader)

        # 最终 τ 报告
        if hasattr(self.model, "all_taus"):
            taus = self.model.all_taus
            print(f"\n{'=' * 60}")
            print(f"最终 τ 值（完整训练后）:")
            for i, t in enumerate(taus):
                print(f"  层 {i + 1}: τ = {t.item():.6f}s")
                if self.config.tau_phys_enabled:
                    tau_phys_val = 60.0 / (5000.0)  # 基于基准转速 5000 rpm
                    print(f"         物理参考: {tau_phys_val:.6f}s (60/5000)")
                    print(f"         偏差: {abs(t.item() - tau_phys_val):.6f}s")
            print(f"{'=' * 60}")

        return self.history

    def save_checkpoint(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        checkpoint = {
            "model_state_dict": self.model.state_dict(),
            "history": self.history,
            "best_val_loss": self.best_val_loss,
            "config": self.config.__dict__,
            "global_epoch": self.global_epoch,
            "target_mean": self.target_mean,
            "target_std": self.target_std,
        }
        if hasattr(self.model, "all_taus"):
            checkpoint["final_taus"] = [t.item() for t in self.model.all_taus]
        torch.save(checkpoint, path)
        print(f"检查点已保存: {path}")

    def save_history(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
        print(f"训练历史已保存: {path}")


if __name__ == "__main__":
    from data_generator_v2 import LongHorizonChatterDataset
    from torch.utils.data import DataLoader

    print("测试 Phase1Trainer...")
    config = Phase1Config(
        num_epochs_stage1=3,
        num_epochs_stage2=3,
        num_epochs_stage3=2,
        num_samples=500,
        prediction_horizon=20,
        hidden_dim=64,
    )

    dataset = LongHorizonChatterDataset(num_samples=500, prediction_horizon=20, noise_level=0.02, seed=42)
    n = len(dataset)
    n_train = int(n * 0.7)
    n_val = n - n_train
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])
    train_loader = DataLoader(train_ds, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=32, shuffle=False)

    model = DLLNNWithPhysicsV2(
        input_dim=7,
        hidden_dim=64,
        num_layers=3,
        prediction_horizon=20,
        dt=0.1,
        tau_init=0.1,
    )

    trainer = Phase1Trainer(config, model, device="cpu")
    history = trainer.train(train_loader, val_loader)
    print(f"\n训练完成！总 epoch: {len(history['epoch'])}")
    print("测试通过！")
