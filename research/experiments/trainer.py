"""
训练器模块
实现两阶段训练策略
"""

import sys
import torch
import torch.nn as nn
import numpy as np
from torch.utils.data import DataLoader
from typing import Dict, List, Tuple
import os
from datetime import datetime
import json

# 确保项目根目录在 path 中（用于导入 app 模块）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# experiments 自身目录必须保持在 research 之前：research/models 是同名包，
# 会遮蔽 experiments/models.py（create_model 所在模块），导致 trainer 及
# 依赖它的复现脚本（如 run_realdata_quick.py）ImportError
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR in sys.path:
    sys.path.remove(_THIS_DIR)
sys.path.insert(0, _THIS_DIR)

from training.reproducibility import set_global_seed, get_worker_init_fn
from training.experiment_tracker import (
    start_run, log_params, log_metric, log_metrics, log_model, is_enabled,
)

from models import create_model
from losses import PCC_Loss
from metrics import MetricsTracker, ChatterMetrics
from config import ExperimentConfig


class DLLNNTrainer:
    """
    DL-LNN 训练器
    实现两阶段训练策略
    """
    
    def __init__(
        self,
        config: ExperimentConfig,
        device: str = "cuda"
    ):
        # 设置全局随机种子，确保可复现
        seed = getattr(config, 'seed', 42)
        set_global_seed(seed)

        self.config = config
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")

        # 记录实验参数到 MLflow（软依赖，未安装时静默跳过）
        if is_enabled():
            log_params(self.config.__dict__)

        # 创建模型
        self.model = create_model("DL-LNN", config).to(self.device)
        
        # 损失函数
        self.criterion = PCC_Loss(
            epsilon_phys=config.model.epsilon_phys,
            lambda_phys=config.model.lambda_phys,
            lambda_pcc=config.model.lambda_pcc
        )
        
        # 优化器
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=config.model.learning_rate,
            weight_decay=config.model.weight_decay
        )

        # 梯度裁剪：真实数据刚性段会让 dopri5 数值发散进而梯度爆炸
        # （《真实数据验证现状》§五），裁剪是该问题的第二道防线
        self.grad_clip_norm = float(getattr(config, "grad_clip_norm", 1.0))

        # 学习率调度器
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer,
            T_max=config.model.num_epochs_stage2,
            eta_min=1e-5
        )
        
        # 训练历史
        self.history = {
            'train_loss': [],
            'val_loss': [],
            'train_metrics': [],
            'val_metrics': []
        }
        
        # 最佳模型
        self.best_val_loss = float('inf')
        self.best_model_state = None

        # Target 归一化统计量（解决 a_lim 范围 [0.1, 20] 与归一化输入 [0,1] 尺度不匹配问题）
        # 在 train_stage1 开始时从训练数据计算，训练时归一化 y_true/y_physics，
        # 评估时通过 denormalize() 反归一化 y_pred 到原始尺度
        self.target_mean: float = 0.0
        self.target_std: float = 1.0
        self._target_stats_computed: bool = False

    def _compute_target_stats(self, train_loader: DataLoader) -> None:
        """从训练数据计算 y_true 的均值和标准差，用于 target 归一化。

        必须在 train_stage1/train_stage2 之前调用一次。归一化使 y_true 和 y_physics
        与归一化输入特征 [0,1] 尺度对齐，避免 DL-LNN 因尺度差异过大无法收敛。
        """
        if self._target_stats_computed:
            return
        all_y = []
        for batch in train_loader:
            if len(batch) == 3:
                _, y_true, _ = batch
            else:
                _, y_true = batch
            all_y.append(y_true.cpu().numpy().reshape(-1))
        all_y = np.concatenate(all_y, axis=0)
        self.target_mean = float(np.mean(all_y))
        self.target_std = float(np.std(all_y) + 1e-8)  # 防除零
        self._target_stats_computed = True
        print(f"  [Target 归一化] mean={self.target_mean:.4f}, std={self.target_std:.4f} "
              f"(min={all_y.min():.4f}, max={all_y.max():.4f})")

    def denormalize(self, y: np.ndarray) -> np.ndarray:
        """将归一化空间的模型输出反归一化到原始 a_lim 尺度 (mm)。

        供 run_experiment.py 在评估阶段调用，确保 MAE/RMSE/R² 在真实尺度上计算。
        """
        if not self._target_stats_computed:
            return y
        return y * self.target_std + self.target_mean

    def train_stage1(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        num_epochs: int = None
    ) -> Dict:
        """
        阶段一：解析预训练
        使用合成数据训练，仅用数据损失
        
        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            num_epochs: 训练轮数
        
        Returns:
            训练历史
        """
        if num_epochs is None:
            num_epochs = self.config.model.num_epochs_stage1

        # 每阶段重置种子，确保可复现
        set_global_seed(getattr(self.config, 'seed', 42))

        # 计算 target 归一化统计量（仅首次调用时计算，后续 train_stage2 复用）
        self._compute_target_stats(train_loader)

        print(f"\n{'='*60}")
        print(f"阶段一：解析预训练 ({num_epochs} epochs)")
        print(f"{'='*60}")

        for epoch in range(num_epochs):
            # 训练
            train_loss, train_metrics = self._train_epoch(
                train_loader, stage=1
            )

            # 验证
            val_loss, val_metrics = self._validate(val_loader, stage=1)

            # 记录历史
            self.history['train_loss'].append(train_loss)
            self.history['val_loss'].append(val_loss)
            self.history['train_metrics'].append(train_metrics)
            self.history['val_metrics'].append(val_metrics)

            # MLflow 记录每轮指标（软依赖）
            if is_enabled():
                log_metrics({
                    "stage1_train_loss": train_loss,
                    "stage1_val_loss": val_loss,
                }, step=epoch)

            # 打印进度
            if (epoch + 1) % 10 == 0 or epoch == 0:
                print(f"Epoch [{epoch+1}/{num_epochs}]")
                print(f"  Train Loss: {train_loss:.4f} | MAE: {train_metrics['mae']:.4f}")
                print(f"  Val Loss: {val_loss:.4f} | MAE: {val_metrics['mae']:.4f}")

        return self.history
    
    def train_stage2(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        num_epochs: int = None
    ) -> Dict:
        """
        阶段二：物理残差微调
        使用真实数据训练，使用完整损失（数据+物理+梯度）
        
        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            num_epochs: 训练轮数
        
        Returns:
            训练历史
        """
        if num_epochs is None:
            num_epochs = self.config.model.num_epochs_stage2

        # 每阶段重置种子，确保可复现
        set_global_seed(getattr(self.config, 'seed', 42))

        print(f"\n{'='*60}")
        print(f"阶段二：物理残差微调 ({num_epochs} epochs)")
        print(f"{'='*60}")

        for epoch in range(num_epochs):
            # 训练
            train_loss, train_metrics = self._train_epoch(
                train_loader, stage=2
            )

            # 验证
            val_loss, val_metrics = self._validate(val_loader, stage=2)

            # 记录历史
            self.history['train_loss'].append(train_loss)
            self.history['val_loss'].append(val_loss)
            self.history['train_metrics'].append(train_metrics)
            self.history['val_metrics'].append(val_metrics)

            # MLflow 记录每轮指标（软依赖）
            if is_enabled():
                log_metrics({
                    "stage2_train_loss": train_loss,
                    "stage2_val_loss": val_loss,
                }, step=epoch)

            # 保存最佳模型
            if val_loss < self.best_val_loss:
                self.best_val_loss = val_loss
                self.best_model_state = self.model.state_dict().copy()
                print(f"  ✓ 保存最佳模型 (Val Loss: {val_loss:.4f})")

            # 打印进度
            if (epoch + 1) % 10 == 0 or epoch == 0:
                print(f"Epoch [{epoch+1}/{num_epochs}]")
                print(f"  Train Loss: {train_loss:.4f} | MAE: {train_metrics['mae']:.4f} | PCC: {train_metrics.get('pcc', 0):.4f}")
                print(f"  Val Loss: {val_loss:.4f} | MAE: {val_metrics['mae']:.4f} | PCC: {val_metrics.get('pcc', 0):.4f}")

            # 更新学习率
            self.scheduler.step()

        # 加载最佳模型
        if self.best_model_state is not None:
            self.model.load_state_dict(self.best_model_state)

        # MLflow 记录最终模型（软依赖）
        if is_enabled():
            log_model(self.model, "dl_lnn")

        return self.history
    
    def _train_epoch(
        self,
        train_loader: DataLoader,
        stage: int = 1
    ) -> Tuple[float, Dict]:
        """
        训练一个epoch
        
        Args:
            train_loader: 训练数据加载器
            stage: 训练阶段 (1或2)
        
        Returns:
            平均损失和指标字典
        """
        self.model.train()
        metrics_tracker = MetricsTracker()
        total_loss = 0.0
        num_batches = 0
        
        for batch in train_loader:
            # 解包数据
            x, y_true, y_physics = self._unpack_batch(batch)
            
            # 阶段二需要计算梯度，为输入启用梯度
            if stage == 2:
                x.requires_grad_(True)
            
            # 前向传播
            self.optimizer.zero_grad()
            
            if stage == 1:
                # 阶段一：仅使用数据损失
                y_pred, _ = self.model(x)
                loss = torch.mean(torch.abs(y_pred - y_true))
            else:
                # 阶段二：使用完整损失（数据+物理+梯度）
                # 必须传入 physics_pred=y_physics 才能激活门控融合逻辑，
                # 否则 model.forward 会走 None 分支仅返回 ltc_pred，
                # 导致 Full 的自适应门控 α(x) 与 A6 的固定 α 都不生效（bug 修复）。
                y_pred, _ = self.model(x, physics_pred=y_physics)
                # 计算可微物理预测（依赖 x）用于 L_pcc 梯度一致性（AR-05 修复）
                # 论文公式 L_pcc = ||∇_x y_pred - ∇_x y_physics||² 要求 y_physics 可微
                y_physics_diff = None
                if hasattr(self.model, "compute_differentiable_physics"):
                    y_physics_diff = self.model.compute_differentiable_physics(x)
                    # 应用 target 归一化，与 y_pred/y_true 同尺度
                    if self._target_stats_computed:
                        y_physics_diff = (y_physics_diff - self.target_mean) / self.target_std
                loss, _ = self.criterion(
                    y_pred, y_true, y_physics, x, self.model,
                    y_physics_diff=y_physics_diff
                )
            
            # 反向传播
            loss.backward()
            if self.grad_clip_norm > 0:
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip_norm)
            self.optimizer.step()
            
            # 记录指标
            total_loss += loss.item()
            metrics_tracker.update(y_pred, y_true, y_physics)
            num_batches += 1
        
        # 计算平均指标
        avg_loss = total_loss / num_batches
        metrics = metrics_tracker.compute()
        
        return avg_loss, metrics
    
    def _validate(
        self,
        val_loader: DataLoader,
        stage: int = 1
    ) -> Tuple[float, Dict]:
        """
        验证
        
        Args:
            val_loader: 验证数据加载器
            stage: 训练阶段
        
        Returns:
            平均损失和指标字典
        """
        self.model.eval()
        metrics_tracker = MetricsTracker()
        total_loss = 0.0
        num_batches = 0
        
        if stage == 1:
            # 阶段一：仅使用数据损失，可以完全在 no_grad 下进行
            with torch.no_grad():
                for batch in val_loader:
                    x, y_true, y_physics = self._unpack_batch(batch)
                    y_pred, _ = self.model(x)
                    loss = torch.mean(torch.abs(y_pred - y_true))
                    
                    total_loss += loss.item()
                    metrics_tracker.update(y_pred, y_true, y_physics)
                    num_batches += 1
        else:
            # 阶段二验证：为避免 PCC_Loss 的二阶 autograd 计算图
            # （create_graph=True + retain_graph=True）在验证循环中累积导致内存泄漏，
            # 验证时只用数据损失（MAE），不计算梯度损失。
            # 训练时仍使用完整 PCC Loss（含梯度一致性），不影响模型训练质量。
            # val_loss 含义与阶段一统一（均为 MAE），最佳模型选择标准一致。
            # PCC 指标仍通过 metrics_tracker 正常计算（不依赖 loss 计算）。
            with torch.no_grad():
                for batch in val_loader:
                    x, y_true, y_physics = self._unpack_batch(batch)
                    # 阶段二验证也必须传入 physics_pred 以激活门控融合，
                    # 保持与训练阶段一致的 forward 语义（bug 修复）。
                    y_pred, _ = self.model(x, physics_pred=y_physics)
                    loss = torch.mean(torch.abs(y_pred - y_true))

                    total_loss += loss.item()
                    metrics_tracker.update(y_pred, y_true, y_physics)
                    num_batches += 1

        # 计算平均指标
        avg_loss = total_loss / num_batches
        metrics = metrics_tracker.compute()

        return avg_loss, metrics
    
    def _unpack_batch(self, batch):
        """
        解包批次数据，并对 target 应用归一化
        
        Args:
            batch: 批次数据
            
        Returns:
            x, y_true, y_physics （y_true/y_physics 已归一化到 mean=0, std=1 空间）
        """
        if len(batch) == 3:
            x, y_true, y_physics = batch
            x = x.to(self.device)
            y_true = y_true.to(self.device)
            y_physics = y_physics.to(self.device)
            # 应用 target 归一化（y_true 和 y_physics 使用相同统计量，保证 PCC Loss 在同一尺度）
            if self._target_stats_computed:
                y_true = (y_true - self.target_mean) / self.target_std
                y_physics = (y_physics - self.target_mean) / self.target_std
        else:
            x, y_true = batch
            x = x.to(self.device)
            y_true = y_true.to(self.device)
            y_physics = None
            if self._target_stats_computed:
                y_true = (y_true - self.target_mean) / self.target_std
        
        return x, y_true, y_physics
    
    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader
    ) -> Dict:
        """
        完整训练流程（两阶段）
        
        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
        
        Returns:
            训练历史
        """
        # 阶段一：解析预训练
        self.train_stage1(train_loader, val_loader)
        
        # 阶段二：物理残差微调
        self.train_stage2(train_loader, val_loader)
        
        return self.history
    
    def evaluate(
        self,
        test_loader: DataLoader
    ) -> Dict[str, float]:
        """
        评估模型
        
        Args:
            test_loader: 测试数据加载器
        
        Returns:
            评估指标字典
        """
        self.model.eval()
        all_preds = []
        all_targets = []
        all_phys = []
        
        with torch.no_grad():
            for batch in test_loader:
                x, y_true, y_physics = self._unpack_batch(batch)
                y_pred, _ = self.model(x)
                
                all_preds.extend(y_pred.cpu().detach().numpy())
                all_targets.extend(y_true.cpu().numpy())
                if y_physics is not None:
                    all_phys.extend(y_physics.cpu().numpy())
        
        import numpy as np
        all_preds = np.array(all_preds)
        all_targets = np.array(all_targets)
        all_phys = np.array(all_phys) if all_phys else all_targets
        
        # 反归一化到原始 a_lim 尺度 (mm)，确保 MAE/RMSE/R² 在真实物理量级上计算
        if self._target_stats_computed:
            all_preds = all_preds * self.target_std + self.target_mean
            all_targets = all_targets * self.target_std + self.target_mean
            all_phys = all_phys * self.target_std + self.target_mean
        
        # 计算指标
        from metrics import ChatterMetrics
        metrics = {
            'MAE': ChatterMetrics.mae(all_preds, all_targets),
            'RMSE': ChatterMetrics.rmse(all_preds, all_targets),
            'R²': ChatterMetrics.r2_score(all_preds, all_targets),
            'PCC': ChatterMetrics.physics_consistency_coefficient(all_preds, all_phys)
        }
        
        return metrics
    
    def save_checkpoint(self, path: str):
        """
        保存检查点
        
        Args:
            path: 保存路径
        """
        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'scheduler_state_dict': self.scheduler.state_dict(),
            'history': self.history,
            'best_val_loss': self.best_val_loss,
            'config': self.config.__dict__
        }
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save(checkpoint, path)
        print(f"检查点已保存: {path}")
    
    def load_checkpoint(self, path: str):
        """
        加载检查点
        
        Args:
            path: 检查点路径
        """
        checkpoint = torch.load(path, map_location=self.device)
        
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        self.scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
        self.history = checkpoint['history']
        self.best_val_loss = checkpoint['best_val_loss']
        
        print(f"检查点已加载: {path}")
    
    def save_history(self, path: str):
        """
        保存训练历史
        
        Args:
            path: 保存路径
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
        print(f"训练历史已保存: {path}")


class BaselineTrainer:
    """
    基线模型训练器
    用于训练LSTM、Transformer、PINN等基线模型
    """
    
    def __init__(
        self,
        model_name: str,
        config: ExperimentConfig,
        device: str = "cuda"
    ):
        # 设置全局随机种子，确保可复现
        seed = getattr(config, 'seed', 42)
        set_global_seed(seed)

        self.model_name = model_name
        self.config = config
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        
        # 创建模型
        self.model = create_model(model_name, config).to(self.device)
        
        # 损失函数（仅使用MSE）
        self.criterion = nn.MSELoss()
        
        # 优化器
        self.optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=config.model.learning_rate
        )

        # 与 DLLNNTrainer 一致的梯度裁剪（基线模型同样受益）
        self.grad_clip_norm = float(getattr(config, "grad_clip_norm", 1.0))

        # 训练历史
        self.history = {
            'train_loss': [],
            'val_loss': [],
            'train_metrics': [],
            'val_metrics': []
        }

        # Target 归一化统计量（与 DLLNNTrainer 一致，解决 y_true 范围 [0.1, 20] 尺度问题）
        self.target_mean: float = 0.0
        self.target_std: float = 1.0
        self._target_stats_computed: bool = False

    def _compute_target_stats(self, train_loader: DataLoader) -> None:
        """从训练数据计算 y_true 的均值和标准差，用于 target 归一化。"""
        if self._target_stats_computed:
            return
        all_y = []
        for batch in train_loader:
            if len(batch) == 3:
                _, y_true, _ = batch
            else:
                _, y_true = batch
            all_y.append(y_true.cpu().numpy().reshape(-1))
        all_y = np.concatenate(all_y, axis=0)
        self.target_mean = float(np.mean(all_y))
        self.target_std = float(np.std(all_y) + 1e-8)
        self._target_stats_computed = True
        print(f"  [Target 归一化] mean={self.target_mean:.4f}, std={self.target_std:.4f} "
              f"(min={all_y.min():.4f}, max={all_y.max():.4f})")

    def denormalize(self, y: np.ndarray) -> np.ndarray:
        """将归一化空间的模型输出反归一化到原始 a_lim 尺度 (mm)。"""
        if not self._target_stats_computed:
            return y
        return y * self.target_std + self.target_mean

    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        num_epochs: int = 100
    ) -> Dict:
        """
        训练基线模型
        
        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            num_epochs: 训练轮数
        
        Returns:
            训练历史
        """
        print(f"\n{'='*60}")
        print(f"训练 {self.model_name} ({num_epochs} epochs)")
        print(f"{'='*60}")
        
        # 计算 target 归一化统计量（仅首次调用时计算）
        self._compute_target_stats(train_loader)
        
        best_val_loss = float('inf')
        
        for epoch in range(num_epochs):
            # 训练
            self.model.train()
            train_loss = 0.0
            metrics_tracker = MetricsTracker()
            
            for batch in train_loader:
                x, y_true, _ = batch
                x = x.to(self.device)
                y_true = y_true.to(self.device)
                # 应用 target 归一化，与归一化输入特征 [0,1] 尺度对齐
                y_true = (y_true - self.target_mean) / self.target_std
                
                self.optimizer.zero_grad()
                y_pred = self.model(x)
                loss = self.criterion(y_pred, y_true)
                loss.backward()
                if self.grad_clip_norm > 0:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip_norm)
                self.optimizer.step()
                
                train_loss += loss.item()
                metrics_tracker.update(y_pred, y_true)
            
            train_loss /= len(train_loader)
            train_metrics = metrics_tracker.compute()
            
            # 验证
            self.model.eval()
            val_loss = 0.0
            metrics_tracker.reset()
            
            with torch.no_grad():
                for batch in val_loader:
                    x, y_true, _ = batch
                    x = x.to(self.device)
                    y_true = y_true.to(self.device)
                    # 验证集同样应用 target 归一化
                    y_true = (y_true - self.target_mean) / self.target_std
                    
                    y_pred = self.model(x)
                    loss = self.criterion(y_pred, y_true)
                    
                    val_loss += loss.item()
                    metrics_tracker.update(y_pred, y_true)
            
            val_loss /= len(val_loader)
            val_metrics = metrics_tracker.compute()
            
            # 记录历史
            self.history['train_loss'].append(train_loss)
            self.history['val_loss'].append(val_loss)
            self.history['train_metrics'].append(train_metrics)
            self.history['val_metrics'].append(val_metrics)
            
            # 打印进度
            if (epoch + 1) % 10 == 0 or epoch == 0:
                print(f"Epoch [{epoch+1}/{num_epochs}]")
                print(f"  Train Loss: {train_loss:.4f} | MAE: {train_metrics['mae']:.4f}")
                print(f"  Val Loss: {val_loss:.4f} | MAE: {val_metrics['mae']:.4f}")
        
        return self.history


# AR-04: sklearn 基线模型名称集合（非梯度训练，需走 fit/predict 路径）
SKLEARN_BASELINE_MODELS = {"SVR", "RF", "XGBoost", "GP"}


class SklearnBaselineTrainer:
    """sklearn/xgboost 基线模型训练器（AR-04）。

    与 ``BaselineTrainer`` 接口一致（``train(train_loader, val_loader, num_epochs=...)``），
    但底层调用 ``model.fit(X, y)`` 而非梯度下降。``num_epochs`` 参数仅为接口兼容保留：
    - 当 ``num_epochs > 1`` 时，对部分支持迭代增强的模型（XGBoost/RF）使用
      ``warm_start`` 进行多轮增量训练；
    - 当模型不支持增量训练（SVR/GP）时，仅执行一次 ``fit``，多余 epoch 被忽略并打印警告。

    这保证了 ``run_experiment.py`` 调度逻辑的统一性，同时不破坏 sklearn 模型的训练语义。
    """

    def __init__(
        self,
        model_name: str,
        config: ExperimentConfig,
        device: str = "cpu",
    ):
        if model_name not in SKLEARN_BASELINE_MODELS:
            raise ValueError(
                f"SklearnBaselineTrainer 仅支持 {SKLEARN_BASELINE_MODELS}，"
                f"收到: {model_name}"
            )

        # 设置全局随机种子，确保可复现
        seed = getattr(config, "seed", 42)
        set_global_seed(seed)

        self.model_name = model_name
        self.config = config
        # sklearn 模型始终在 CPU 上运行（不依赖 GPU）
        self.device = torch.device("cpu")

        # 创建模型（不做 .to(device)，sklearn 模型不支持）
        self.model = create_model(model_name, config)

        # 训练历史（保持与 BaselineTrainer 字段一致）
        self.history = {
            "train_loss": [],
            "val_loss": [],
            "train_metrics": [],
            "val_metrics": [],
        }

        # 记录实验参数到 MLflow（软依赖）
        if is_enabled():
            log_params(
                {
                    "model_name": model_name,
                    "trainer": "SklearnBaselineTrainer",
                    "input_dim": config.model.input_dim,
                }
            )

    @staticmethod
    def _collect_loader(loader: DataLoader):
        """将 DataLoader 展平为 (X: np.ndarray, y: np.ndarray)。

        兼容 (x, y, physics) 和 (x, y) 两种 batch 格式。
        """
        xs, ys = [], []
        for batch in loader:
            if len(batch) == 3:
                x, y_true, _ = batch
            else:
                x, y_true = batch
            xs.append(x.cpu().numpy())
            ys.append(y_true.cpu().numpy())
        X = np.concatenate(xs, axis=0)
        y = np.concatenate(ys, axis=0).reshape(-1)
        return X, y

    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        num_epochs: int = 1,
    ) -> Dict:
        """训练 sklearn 基线模型。

        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            num_epochs: 接口兼容参数。对支持 warm_start 的模型（RF/XGBoost）
                        进行增量训练；对不支持的模型（SVR/GP）仅训练一次。

        Returns:
            训练历史字典
        """
        print(f"\n{'='*60}")
        print(f"训练 {self.model_name} (sklearn 基线，AR-04)")
        print(f"{'='*60}")

        X_train, y_train = self._collect_loader(train_loader)
        X_val, y_val = self._collect_loader(val_loader)

        # 判断是否支持增量训练
        sklearn_model = self.model.sklearn_model
        supports_warm_start = getattr(sklearn_model, "warm_start", False)

        if num_epochs > 1 and not supports_warm_start:
            print(
                f"  [警告] {self.model_name} 不支持 warm_start 增量训练，"
                f"num_epochs={num_epochs} 将被忽略，仅执行一次 fit()"
            )
            effective_epochs = 1
        else:
            effective_epochs = max(1, num_epochs)

        # 增量训练需要开启 warm_start
        if effective_epochs > 1 and supports_warm_start:
            sklearn_model.set_params(warm_start=True)

        for epoch in range(effective_epochs):
            # 训练
            self.model.fit(X_train, y_train)

            # 评估训练集
            train_pred = self.model.predict(X_train)
            train_metrics = self._compute_metrics(train_pred, y_train)
            train_loss = float(np.mean((train_pred.reshape(-1) - y_train) ** 2))

            # 评估验证集
            val_pred = self.model.predict(X_val)
            val_metrics = self._compute_metrics(val_pred, y_val)
            val_loss = float(np.mean((val_pred.reshape(-1) - y_val) ** 2))

            # 记录历史
            self.history["train_loss"].append(train_loss)
            self.history["val_loss"].append(val_loss)
            self.history["train_metrics"].append(train_metrics)
            self.history["val_metrics"].append(val_metrics)

            # MLflow 记录每轮指标（软依赖）
            if is_enabled():
                log_metrics(
                    {
                        "train_loss": train_loss,
                        "val_loss": val_loss,
                        "train_mae": train_metrics.get("mae", 0.0),
                        "val_mae": val_metrics.get("mae", 0.0),
                    },
                    step=epoch,
                )

            # 打印进度
            if (epoch + 1) % 10 == 0 or epoch == 0:
                print(f"Epoch [{epoch+1}/{effective_epochs}]")
                print(
                    f"  Train Loss: {train_loss:.4f} | MAE: {train_metrics['mae']:.4f}"
                )
                print(
                    f"  Val Loss: {val_loss:.4f} | MAE: {val_metrics['mae']:.4f}"
                )

        # MLflow 记录最终模型（软依赖）
        if is_enabled():
            log_model(self.model, self.model_name.lower())

        return self.history

    @staticmethod
    def _compute_metrics(y_pred: np.ndarray, y_true: np.ndarray) -> Dict[str, float]:
        """计算 MAE/RMSE/R² 指标（与 ChatterMetrics 保持一致）。"""
        y_pred = np.asarray(y_pred).reshape(-1)
        y_true = np.asarray(y_true).reshape(-1)
        mae = float(np.mean(np.abs(y_pred - y_true)))
        rmse = float(np.sqrt(np.mean((y_pred - y_true) ** 2)))
        # R² 防止除零
        ss_res = float(np.sum((y_true - y_pred) ** 2))
        ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        return {"mae": mae, "rmse": rmse, "r2": r2}

    def evaluate(self, test_loader: DataLoader) -> Dict[str, float]:
        """评估模型，返回 MAE/RMSE/R²。"""
        X_test, y_test = self._collect_loader(test_loader)
        y_pred = self.model.predict(X_test)
        return self._compute_metrics(y_pred, y_test)

    def save_history(self, path: str):
        """保存训练历史（与 BaselineTrainer 保持接口一致）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
        print(f"训练历史已保存: {path}")


if __name__ == "__main__":
    # 测试训练器
    print("测试训练器...")
    
    config = ExperimentConfig()
    trainer = DLLNNTrainer(config, device="cpu")
    
    print(f"模型参数量: {sum(p.numel() for p in trainer.model.parameters()):,}")
    print(f"设备: {trainer.device}")
    
    print("\n训练器测试通过！")
