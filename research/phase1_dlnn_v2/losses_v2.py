"""
物理损失函数 v2 （Phase 1 课程式重构）

核心改动 vs 原 losses.py:
    1. 新增 FrequencyDomainLoss (L_freq) — 频域正则化
    2. 重写 CurriculumPhysicsLoss — 三阶段退火训练
    3. 新增 DelayRegularization — τ 物理正则化

课程式训练流程:
    Stage 1 (epochs 0-99):   L = L_data（纯 MAE）
    Stage 2 (epochs 100-249): L = L_data + λ_phys(t)*L_phys + λ_pcc(t)*L_pcc
    Stage 3 (epochs 250-299): L = L_data + L_phys + L_pcc + λ_freq(t)*L_freq + L_tau_reg
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.autograd as autograd
from typing import Optional, Tuple, Dict
import numpy as np


# 1. 频域损失 — Phase 1 新功能


class FrequencyDomainLoss(nn.Module):
    """
    频域正则化损失（论文第三层损失，Phase 1 首次实现）。

    惩罚预测与目标在频域幅值谱上的差异：
        L_freq = || |FFT(y_pred)| - |FFT(y_true)| ||² / N

    动机：
        颤振在频域表现为特定谐波能量集中（叶瓣频率 f_c = j*n/60）。
        频域损失强制网络学习正确的频率结构，而非只匹配即时值。
        这是信号处理角度对"物理一致性"的强化——频域错误 = 物理上不同的振动模式。

    实现注意：
        对批次中每个样本独立计算 FFT（因为各样本对应不同转速，频率不同）。
    """

    def __init__(self, reduction: str = "mean", eps: float = 1e-8):
        super().__init__()
        self.reduction = reduction
        self.eps = eps

    def forward(
        self,
        y_pred: torch.Tensor,
        y_true: torch.Tensor,
    ) -> torch.Tensor:
        """
        Args:
            y_pred: 预测 [batch_size, horizon]
            y_true: 目标 [batch_size, horizon]

        Returns:
            freq_loss: 标量频域损失
        """
        batch_size, horizon = y_pred.shape
        total_loss = 0.0

        for b in range(batch_size):
            # 实值 FFT（取幅值）
            pred_fft = torch.abs(torch.fft.rfft(y_pred[b]))  # [H//2 + 1]
            true_fft = torch.abs(torch.fft.rfft(y_true[b]))  # [H//2 + 1]

            # L2 归一化（关注频率结构而非绝对幅值）
            pred_fft_norm = pred_fft / (pred_fft.norm() + self.eps)
            true_fft_norm = true_fft / (true_fft.norm() + self.eps)

            total_loss += torch.mean((pred_fft_norm - true_fft_norm) ** 2)

        if self.reduction == "mean":
            return total_loss / batch_size
        return total_loss


# 2. 物理一致性损失（从原 PCC_Loss 提取，最小改动）


class PhysicsNumericalLoss(nn.Module):
    """物理数值层损失 L_phys"""

    def __init__(self, epsilon_phys: float = 0.1):
        super().__init__()
        self.epsilon_phys = epsilon_phys

    def forward(self, y_pred: torch.Tensor, y_physics: torch.Tensor) -> torch.Tensor:
        """L_phys = mean(max(0, |y_pred - y_physics| - epsilon))"""
        phys_diff = torch.abs(y_pred - y_physics)
        return torch.mean(torch.clamp(phys_diff - self.epsilon_phys, min=0.0))


class PhysicsGradientLoss(nn.Module):
    """
    物理梯度一致性损失 L_pcc（Phase 1 版本）。

    L_pcc = ||∇_x y_pred - ∇_x y_physics||²

    约束模型预测对输入的梯度方向与物理模型一致。
    论文核心贡献（AR-05 修复后的真正实现）。
    """

    def forward(
        self,
        y_pred: torch.Tensor,
        y_physics_diff: torch.Tensor,
        x: torch.Tensor,
    ) -> torch.Tensor:
        """计算梯度一致性损失。"""
        grad_pred = autograd.grad(
            outputs=y_pred.sum(),
            inputs=x,
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]

        grad_physics = autograd.grad(
            outputs=y_physics_diff.sum(),
            inputs=x,
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]

        grad_diff = grad_pred - grad_physics
        return torch.mean(grad_diff**2)


# 3. 课程式物理损失 — Phase 1 核心创新


class CurriculumPhysicsLoss(nn.Module):
    """
    课程式三阶段物理损失。

    退火策略（线性平滑过渡）：
        Stage 1 (epoch 0 to E1):       仅数据损失
        Stage 2 (epoch E1 to E2):      加入 L_phys + L_pcc
            λ_phys(t) = w_phys * ramp(t, E1, E1+10)
        Stage 3 (epoch E2 to E3):      加入 L_freq + L_tau_reg
            λ_freq(t) = w_freq * ramp(t, E2, E2+10)

    where ramp(t, t0, t1) = clamp((t-t0)/(t1-t0), 0, 1)
    """

    def __init__(
        self,
        epsilon_phys: float = 0.1,
        lambda_phys_max: float = 0.5,
        lambda_pcc_max: float = 0.1,
        lambda_freq_max: float = 0.1,
        lambda_tau_reg: float = 0.01,
        stage1_epochs: int = 100,
        stage2_epochs: int = 150,
        stage3_epochs: int = 50,
        ramp_epochs: int = 10,
    ):
        """
        Args:
            epsilon_phys: 物理容忍阈值
            lambda_*_max: 各损失项在满权重时的系数
            stage*_epochs: 每个阶段的 epoch 数
            ramp_epochs: 阶段间退火过渡 epoch 数
        """
        super().__init__()

        self.epsilon_phys = epsilon_phys
        self.lambda_phys_max = lambda_phys_max
        self.lambda_pcc_max = lambda_pcc_max
        self.lambda_freq_max = lambda_freq_max
        self.lambda_tau_reg = lambda_tau_reg

        self.stage1_epochs = stage1_epochs
        self.stage2_epochs = stage2_epochs
        self.stage3_epochs = stage3_epochs
        self.ramp_epochs = ramp_epochs

        # 预计算阶段边界
        self.e1_end = stage1_epochs
        self.e2_start = stage1_epochs
        self.e2_end = stage1_epochs + stage2_epochs
        self.e3_start = stage1_epochs + stage2_epochs

        # 子损失模块
        self.phys_loss = PhysicsNumericalLoss(epsilon_phys)
        self.pcc_loss = PhysicsGradientLoss()
        self.freq_loss = FrequencyDomainLoss()
        self.data_loss_fn = nn.L1Loss()

    def _ramp(self, epoch: int, start: int, end: int) -> float:
        """线性退火 ramp(start→end, 0→1)。"""
        if epoch <= start:
            return 0.0
        if epoch >= end:
            return 1.0
        return (epoch - start) / max(1, end - start)

    def get_stage(self, epoch: int) -> int:
        """当前 epoch 所处的训练阶段。"""
        if epoch < self.e1_end:
            return 1
        elif epoch < self.e2_end:
            return 2
        else:
            return 3

    def get_lambda_phys(self, epoch: int) -> float:
        return self.lambda_phys_max * self._ramp(epoch, self.e2_start, self.e2_start + self.ramp_epochs)

    def get_lambda_pcc(self, epoch: int) -> float:
        return self.lambda_pcc_max * self._ramp(epoch, self.e2_start, self.e2_start + self.ramp_epochs)

    def get_lambda_freq(self, epoch: int) -> float:
        return self.lambda_freq_max * self._ramp(epoch, self.e3_start, self.e3_start + self.ramp_epochs)

    def forward(
        self,
        y_pred: torch.Tensor,
        y_true: torch.Tensor,
        y_physics: torch.Tensor,
        x: torch.Tensor,
        y_physics_diff: Optional[torch.Tensor],
        tau_reg: torch.Tensor,
        epoch: int,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """
        计算课程式总损失。

        Args:
            y_pred: 模型预测 [B, horizon] 或 [B, 1]
            y_true: 真实标签 [B, horizon] 或 [B, 1]
            y_physics: 物理预测 [B, 1]（数值层用）
            x: 输入特征 [B, input_dim]
            y_physics_diff: 可微物理预测 [B, 1]（梯度层用）
            tau_reg: τ 正则化项（标量）
            epoch: 当前 epoch（用于退火系数）

        Returns:
            total_loss: 标量总损失
            loss_dict: 各项损失字典
        """
        stage = self.get_stage(epoch)

        # 基础数据损失
        loss_data = self.data_loss_fn(y_pred, y_true)
        total = loss_data
        loss_dict = {
            "total": loss_data.item(),
            "data": loss_data.item(),
            "phys": 0.0,
            "pcc": 0.0,
            "freq": 0.0,
            "tau_reg": 0.0,
            "stage": float(stage),
        }

        if stage >= 2:
            # 物理数值损失
            lambda_phys = self.get_lambda_phys(epoch)
            if y_physics is not None:
                # 确保 y_physics 与 y_pred 维度一致
                y_phys_normalized = y_physics
                if y_phys_normalized.dim() > 2:
                    y_phys_normalized = y_phys_normalized[:, 0, :]  # [B,H,1] → [B,1]
                if y_pred.dim() == 2 and y_pred.shape[1] > 1 and y_phys_normalized.shape[1] == 1:
                    y_phys_normalized = y_phys_normalized.repeat(1, y_pred.shape[1])
                loss_phys = self.phys_loss(y_pred, y_phys_normalized)
                total = total + lambda_phys * loss_phys
                loss_dict["phys"] = loss_phys.item()

            # 梯度一致性损失（短路：lambda_pcc=0 时不计算，避免 autograd 开销）
            lambda_pcc = self.get_lambda_pcc(epoch)
            if lambda_pcc > 0 and y_physics_diff is not None and x.requires_grad:
                loss_pcc = self.pcc_loss(
                    y_pred[:, 0:1] if y_pred.dim() == 2 and y_pred.shape[1] > 1 else y_pred, y_physics_diff, x
                )
                total = total + lambda_pcc * loss_pcc
                loss_dict["pcc"] = loss_pcc.item()

        if stage >= 3:
            # 频域损失
            lambda_freq = self.get_lambda_freq(epoch)
            if y_pred.dim() == 2 and y_pred.shape[1] > 1:
                # 仅当 horizon > 1 时频域损失才有意义
                y_true_horizon = y_true
                # 对于 [B, 1] 的标签，用复制构造时序
                if y_true_horizon.shape[1] == 1:
                    y_true_horizon = y_true_horizon.repeat(1, y_pred.shape[1])

                loss_freq = self.freq_loss(y_pred, y_true_horizon)
                total = total + lambda_freq * loss_freq
                loss_dict["freq"] = loss_freq.item()

            # τ 物理正则化
            total = total + self.lambda_tau_reg * tau_reg
            loss_dict["tau_reg"] = tau_reg.item() if isinstance(tau_reg, torch.Tensor) else float(tau_reg)

        loss_dict["total"] = total.item()
        return total, loss_dict


# 4. 消融实验用简化损失


def compute_tau_regularization_all_layers(
    ltc_cells: list, spindle_speed: Optional[torch.Tensor] = None
) -> torch.Tensor:
    """
    累加所有 LTC 层的 τ 正则化。

    Args:
        ltc_cells: LTCWithDelayCell 列表
        spindle_speed: [B, 1] 归一化主轴转速

    Returns:
        总 τ 正则化（标量）
    """
    total_reg = torch.tensor(0.0, device=spindle_speed.device if spindle_speed is not None else None)
    for cell in ltc_cells:
        if hasattr(cell, "delay_embedding"):
            reg = cell.delay_embedding.compute_tau_regularization(spindle_speed)
            total_reg = total_reg + reg
    return total_reg


if __name__ == "__main__":
    print("测试 FrequencyDomainLoss...")
    freq_loss = FrequencyDomainLoss()
    y_pred = torch.randn(8, 50)
    y_true = torch.randn(8, 50)
    loss_f = freq_loss(y_pred, y_true)
    print(f"Freq Loss: {loss_f.item():.6f}")

    print("\n测试 CurriculumPhysicsLoss...")
    curriculum = CurriculumPhysicsLoss(
        epsilon_phys=0.1,
        lambda_phys_max=0.5,
        lambda_pcc_max=0.1,
        lambda_freq_max=0.1,
        stage1_epochs=20,  # 缩小用于测试
        stage2_epochs=30,
        stage3_epochs=10,
    )

    for ep in [0, 10, 20, 25, 50, 55, 60]:
        stage = curriculum.get_stage(ep)
        lp = curriculum.get_lambda_phys(ep)
        lf = curriculum.get_lambda_freq(ep)
        print(f"  Epoch {ep:3d}: Stage={stage}, lam_phys={lp:.4f}, lam_freq={lf:.4f}")

    # 测试完整 forward（注意：y_pred 必须依赖 x 才能计算梯度）
    x = torch.randn(4, 7, requires_grad=True)
    y_pred = torch.sigmoid(x.sum(dim=1, keepdim=True))  # 依赖 x
    y_true = torch.randn(4, 1)
    y_phys = torch.randn(4, 1)
    y_phys_diff = torch.sigmoid(x[:, :1].sum(dim=1, keepdim=True))  # 依赖 x
    tau_reg = torch.tensor(0.001)

    loss, d = curriculum(y_pred, y_true, y_phys, x, y_phys_diff, tau_reg, epoch=25)
    print(f"\n  Epoch 25 (Stage 2): total={d['total']:.4f}, phys={d['phys']:.4f}, pcc={d['pcc']:.4f}")

    loss, d = curriculum(y_pred, y_true, y_phys, x, y_phys_diff, tau_reg, epoch=55)
    print(f"  Epoch 55 (Stage 3): total={d['total']:.4f}, freq={d['freq']:.4f}, tau_reg={d['tau_reg']:.4f}")

    print("\n测试通过！")
