"""
评价指标模块
实现MAE、RMSE、R²、PCC等评价指标
"""

import torch
import numpy as np
from typing import Dict, Tuple


def _mae_np(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """平均绝对误差（numpy 实现，避免 torch+sklearn 双 BLAS 段错误）"""
    return float(np.mean(np.abs(np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float))))


def _mse_np(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """均方误差（numpy 实现）"""
    return float(np.mean((np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)) ** 2))


def _r2_np(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """R²（复刻 sklearn.metrics.r2_score 语义：常数目标时 numerator/denominator 均 0 → 1.0）"""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    num = float(np.sum((yt - yp) ** 2))
    den = float(np.sum((yt - np.mean(yt)) ** 2))
    if den == 0.0:
        return 1.0 if num == 0.0 else 0.0
    return 1.0 - num / den


class ChatterMetrics:
    """
    颤振预测评价指标
    """
    
    def __init__(self):
        pass
    
    def compute_all(
        self,
        y_pred: np.ndarray,
        y_true: np.ndarray,
        y_physics: np.ndarray = None
    ) -> Dict[str, float]:
        """
        计算所有评价指标
        
        Args:
            y_pred: 模型预测值
            y_true: 真实值
            y_physics: 物理模型预测值（可选）
        
        Returns:
            评价指标字典
        """
        metrics = {}
        
        # 基础回归指标
        metrics['mae'] = self.mae(y_pred, y_true)
        metrics['rmse'] = self.rmse(y_pred, y_true)
        metrics['r2'] = self.r2_score(y_pred, y_true)
        metrics['mape'] = self.mape(y_pred, y_true)
        
        # 物理一致性指标
        if y_physics is not None:
            metrics['pcc'] = self.physics_consistency_coefficient(y_pred, y_physics)
            metrics['phys_mae'] = self.mae(y_pred, y_physics)
            metrics['phys_within_threshold'] = self.phys_within_threshold(
                y_pred, y_physics, threshold=0.05
            )
        
        return metrics
    
    @staticmethod
    def mae(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        """平均绝对误差"""
        return _mae_np(y_true, y_pred)
    
    @staticmethod
    def rmse(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        """均方根误差"""
        return float(np.sqrt(_mse_np(y_true, y_pred)))
    
    @staticmethod
    def r2_score(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        """R² 决定系数"""
        return _r2_np(y_true, y_pred)
    
    @staticmethod
    def mape(y_pred: np.ndarray, y_true: np.ndarray) -> float:
        """平均绝对百分比误差"""
        epsilon = 1e-8
        mape = np.mean(np.abs((y_true - y_pred) / (y_true + epsilon))) * 100
        return float(mape)
    
    @staticmethod
    def physics_consistency_coefficient(
        y_pred: np.ndarray,
        y_physics: np.ndarray
    ) -> float:
        """
        物理一致性系数 (PCC)
        PCC = 1 - mean(|y_pred - y_physics| / |y_physics|)
        """
        epsilon = 1e-8
        relative_error = np.mean(np.abs(y_pred - y_physics) / (np.abs(y_physics) + epsilon))
        pcc = 1.0 - relative_error
        return float(pcc)
    
    @staticmethod
    def phys_within_threshold(
        y_pred: np.ndarray,
        y_physics: np.ndarray,
        threshold: float = 0.05
    ) -> float:
        """
        物理阈值内样本比例
        返回 |y_pred - y_physics| <= threshold 的样本占比
        """
        diff = np.abs(y_pred - y_physics)
        within_threshold = np.mean(diff <= threshold)
        return float(within_threshold)
    
    @staticmethod
    def gradient_consistency(
        y_pred: torch.Tensor,
        y_physics: torch.Tensor,
        x: torch.Tensor
    ) -> float:
        """
        梯度一致性
        计算预测梯度和物理梯度的差异
        """
        # 计算预测梯度
        grad_pred = torch.autograd.grad(
            outputs=y_pred.sum(),
            inputs=x,
            create_graph=False,
            retain_graph=True
        )[0]
        
        # 计算物理梯度
        grad_physics = torch.autograd.grad(
            outputs=y_physics.sum(),
            inputs=x,
            create_graph=False,
            retain_graph=True
        )[0]
        
        # 梯度差异
        grad_diff = torch.abs(grad_pred - grad_physics)
        consistency = 1.0 - torch.mean(grad_diff).item()
        
        return float(consistency)


class MetricsTracker:
    """
    评价指标跟踪器
    用于在训练过程中跟踪多个批次的指标
    """
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """重置所有指标"""
        self.predictions = []
        self.targets = []
        self.physics_predictions = []
        self.count = 0
    
    def update(
        self,
        y_pred: torch.Tensor,
        y_true: torch.Tensor,
        y_physics: torch.Tensor = None
    ):
        """
        更新指标
        
        Args:
            y_pred: 模型预测
            y_true: 真实标签
            y_physics: 物理预测
        """
        self.predictions.append(y_pred.detach().cpu().numpy())
        self.targets.append(y_true.detach().cpu().numpy())
        
        if y_physics is not None:
            self.physics_predictions.append(y_physics.detach().cpu().numpy())
        
        self.count += y_pred.size(0)
    
    def compute(self) -> Dict[str, float]:
        """
        计算累积的所有指标
        
        Returns:
            评价指标字典
        """
        y_pred = np.concatenate(self.predictions, axis=0)
        y_true = np.concatenate(self.targets, axis=0)
        
        y_physics = None
        if len(self.physics_predictions) > 0:
            y_physics = np.concatenate(self.physics_predictions, axis=0)
        
        metrics_calculator = ChatterMetrics()
        metrics = metrics_calculator.compute_all(y_pred, y_true, y_physics)
        
        return metrics


if __name__ == "__main__":
    # 测试评价指标
    print("测试评价指标...")
    
    # 生成测试数据
    np.random.seed(42)
    n_samples = 100
    
    y_true = np.random.uniform(0.5, 5.0, (n_samples, 1))
    y_pred = y_true + np.random.normal(0, 0.1, (n_samples, 1))
    y_physics = y_true + np.random.normal(0, 0.2, (n_samples, 1))
    
    # 计算指标
    metrics_calculator = ChatterMetrics()
    metrics = metrics_calculator.compute_all(y_pred, y_true, y_physics)
    
    print("\n评价指标结果:")
    for key, value in metrics.items():
        print(f"  {key}: {value:.4f}")
    
    print("\n评价指标测试通过！")
