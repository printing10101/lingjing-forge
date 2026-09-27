"""
物理损失函数模块
实现PCC Loss等物理约束损失
"""

import torch
import torch.nn as nn
import torch.autograd as autograd
from typing import Optional, Tuple
import numpy as np


class PCC_Loss(nn.Module):
    """
    物理一致性损失 (Physics-Consistency Loss)
    包含数值层和梯度层双重约束
    """
    
    def __init__(
        self,
        epsilon_phys: float = 0.1,
        lambda_phys: float = 0.5,
        lambda_pcc: float = 0.1
    ):
        """
        初始化PCC损失

        默认值与 config.py 的 ModelConfig 保持一致，确保未显式传参时
        trainer 与论文报告的超参数一致（学术诚信要求）。

        论文第3节声明：λ₁=1.0, λ₂=0.5, λ₃=0.1
        （见 ACADEMIC_REVIEW_REPORT.md AR-01）

        Args:
            epsilon_phys: 物理容忍阈值 (mm)，默认 0.1
            lambda_phys: 物理损失权重 λ₂，默认 0.5
            lambda_pcc: 梯度一致性损失权重 λ₃，默认 0.1
        """
        super().__init__()

        self.epsilon_phys = epsilon_phys
        self.lambda_phys = lambda_phys
        self.lambda_pcc = lambda_pcc
    
    def forward(
        self,
        y_pred: torch.Tensor,
        y_true: torch.Tensor,
        y_physics: torch.Tensor,
        x: torch.Tensor,
        model: nn.Module,
        y_physics_diff: Optional[torch.Tensor] = None
    ) -> Tuple[torch.Tensor, dict]:
        """
        计算PCC损失

        Args:
            y_pred: 模型预测 [batch_size, output_dim]
            y_true: 真实标签 [batch_size, output_dim]
            y_physics: 物理模型预测（预计算值，用于 L_phys 数值约束）
                      [batch_size, output_dim]
            x: 输入特征 [batch_size, input_dim]
            model: 神经网络模型
            y_physics_diff: 可微物理预测（依赖 x，用于 L_pcc 梯度一致性）
                           [batch_size, output_dim]；若提供则真正实现论文公式
                           L_pcc = ||∇_x y_pred - ∇_x y_physics_diff||²，
                           否则降级为幅度约束（AR-05 修复前的旧行为）

        Returns:
            total_loss: 总损失
            loss_dict: 各项损失字典
        """
        # 数据损失 (MAE)
        loss_data = torch.mean(torch.abs(y_pred - y_true))

        # 物理损失 (数值层) —— 使用预计算的精确 y_physics
        # L_phys = max(0, |y_pred - y_physics| - epsilon)
        phys_diff = torch.abs(y_pred - y_physics)
        loss_phys = torch.mean(torch.clamp(phys_diff - self.epsilon_phys, min=0.0))

        # 梯度一致性损失 (梯度层)
        # 若提供可微 y_physics_diff，真正实现论文公式；否则降级为幅度约束
        loss_pcc = self._compute_gradient_loss(y_pred, y_physics_diff, x, model)

        # 总损失
        total_loss = (
            1.0 * loss_data +
            self.lambda_phys * loss_phys +
            self.lambda_pcc * loss_pcc
        )

        loss_dict = {
            'total': total_loss.item(),
            'data': loss_data.item(),
            'phys': loss_phys.item(),
            'pcc': loss_pcc.item()
        }

        return total_loss, loss_dict

    def _compute_gradient_loss(
        self,
        y_pred: torch.Tensor,
        y_physics_diff: Optional[torch.Tensor],
        x: torch.Tensor,
        model: nn.Module
    ) -> torch.Tensor:
        """
        计算梯度一致性损失（论文第3节方法描述的真正实现）

        论文公式：L_pcc = ||∇_x y_pred - ∇_x y_physics||²

        约束模型预测对输入的梯度方向与物理模型预测对输入的梯度方向一致，
        保证 DL-LNN 学到的输入-输出映射在局部敏感度上与解析物理模型吻合。

        Args:
            y_pred: 模型预测 [batch_size, output_dim]
            y_physics_diff: 可微物理预测 [batch_size, output_dim]，必须依赖 x
                           若为 None，降级为幅度约束（不满足论文声明，仅向后兼容）
            x: 输入特征 [batch_size, input_dim]，requires_grad=True
            model: 神经网络模型（保留接口签名，本实现未使用）

        Returns:
            梯度一致性损失（标量）
        """
        # 预测对输入的梯度：∇_x y_pred
        grad_pred = autograd.grad(
            outputs=y_pred.sum(),
            inputs=x,
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]

        # 若未提供可微 y_physics，降级为幅度约束（AR-05 修复前的旧行为）
        if y_physics_diff is None:
            # 降级路径：使用预测梯度幅度作为正则（无物理方向约束）
            # 注意：此路径不满足论文第3节"梯度方向一致性"声明
            if grad_pred.dim() > 1:
                loss_pcc = torch.mean(torch.norm(grad_pred, dim=1) ** 2)
            else:
                loss_pcc = torch.mean(grad_pred ** 2)
            return loss_pcc

        # 物理预测对输入的梯度：∇_x y_physics_diff
        # y_physics_diff 必须是 x 的可微函数（DifferentiableTlustyPhysics 满足此条件）
        grad_physics = autograd.grad(
            outputs=y_physics_diff.sum(),
            inputs=x,
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
        )[0]

        # 论文公式：L_pcc = ||∇_x y_pred - ∇_x y_physics||²
        # 逐元素差异的 L2 范数平方，对批次取均值
        grad_diff = grad_pred - grad_physics
        loss_pcc = torch.mean(grad_diff ** 2)

        return loss_pcc


class PhysicsLoss(nn.Module):
    """
    纯物理损失（无数值层）
    """

    def __init__(self, epsilon_phys: float = 0.1):
        super().__init__()
        self.epsilon_phys = epsilon_phys
    
    def forward(
        self,
        y_pred: torch.Tensor,
        y_physics: torch.Tensor
    ) -> torch.Tensor:
        """
        计算物理损失
        
        Args:
            y_pred: 模型预测
            y_physics: 物理模型预测
        
        Returns:
            物理损失
        """
        phys_diff = torch.abs(y_pred - y_physics)
        loss = torch.mean(torch.clamp(phys_diff - self.epsilon_phys, min=0.0))
        return loss


class GradientLoss(nn.Module):
    """
    纯梯度损失
    """
    
    def __init__(self):
        super().__init__()
    
    def forward(
        self,
        y_pred: torch.Tensor,
        y_physics: torch.Tensor,
        x: torch.Tensor,
        model: nn.Module
    ) -> torch.Tensor:
        """
        计算梯度损失
        
        Args:
            y_pred: 模型预测
            y_physics: 物理模型预测
            x: 输入特征
            model: 神经网络模型
        
        Returns:
            梯度损失
        """
        # 计算预测梯度
        grad_pred = autograd.grad(
            outputs=y_pred.sum(),
            inputs=x,
            create_graph=True,
            retain_graph=True
        )[0]
        
        # 计算物理梯度
        grad_physics = autograd.grad(
            outputs=y_physics.sum(),
            inputs=x,
            create_graph=True,
            retain_graph=True
        )[0]
        
        # 梯度差异
        grad_diff = torch.abs(grad_pred - grad_physics)
        loss = torch.mean(grad_diff)
        
        return loss


class StabilityLoss(nn.Module):
    """
    稳定性分类损失
    用于预测稳定性标签（稳定/不稳定）
    """
    
    def __init__(self):
        super().__init__()
        self.bce = nn.BCEWithLogitsLoss()
    
    def forward(
        self,
        logits: torch.Tensor,
        labels: torch.Tensor
    ) -> torch.Tensor:
        """
        计算稳定性损失
        
        Args:
            logits: 模型输出logits [batch_size, 1]
            labels: 真实标签 [batch_size, 1]
        
        Returns:
            损失
        """
        return self.bce(logits, labels)


class CombinedLoss(nn.Module):
    """
    组合损失函数
    结合回归损失和分类损失
    """

    def __init__(
        self,
        lambda_reg: float = 1.0,
        lambda_cls: float = 0.5,
        lambda_phys: float = 0.5,
        lambda_pcc: float = 0.1,
        epsilon_phys: float = 0.1
    ):
        """
        初始化组合损失

        默认权重与 PCC_Loss / config.py 保持一致（AR-01）。

        Args:
            lambda_reg: 回归损失权重
            lambda_cls: 分类损失权重
            lambda_phys: 物理损失权重 λ₂
            lambda_pcc: 梯度损失权重 λ₃
            epsilon_phys: 物理阈值
        """
        super().__init__()
        
        self.lambda_reg = lambda_reg
        self.lambda_cls = lambda_cls
        self.lambda_phys = lambda_phys
        self.lambda_pcc = lambda_pcc
        
        self.pcc_loss = PCC_Loss(
            epsilon_phys=epsilon_phys,
            lambda_phys=lambda_phys,
            lambda_pcc=lambda_pcc
        )
        
        self.stability_loss = StabilityLoss()
    
    def forward(
        self,
        y_pred_reg: torch.Tensor,
        y_true_reg: torch.Tensor,
        y_physics: torch.Tensor,
        y_pred_cls: torch.Tensor,
        y_true_cls: torch.Tensor,
        x: torch.Tensor,
        model: nn.Module
    ) -> Tuple[torch.Tensor, dict]:
        """
        计算组合损失
        
        Args:
            y_pred_reg: 回归预测
            y_true_reg: 回归真实值
            y_physics: 物理预测
            y_pred_cls: 分类预测
            y_true_cls: 分类真实值
            x: 输入特征
            model: 模型
        
        Returns:
            total_loss: 总损失
            loss_dict: 损失字典
        """
        # 回归损失（含物理约束）
        loss_reg, reg_dict = self.pcc_loss(
            y_pred_reg, y_true_reg, y_physics, x, model
        )
        
        # 分类损失
        loss_cls = self.stability_loss(y_pred_cls, y_true_cls)
        
        # 总损失
        total_loss = self.lambda_reg * loss_reg + self.lambda_cls * loss_cls
        
        loss_dict = {
            'total': total_loss.item(),
            'reg_total': reg_dict['total'],
            'reg_data': reg_dict['data'],
            'reg_phys': reg_dict['phys'],
            'reg_pcc': reg_dict['pcc'],
            'cls': loss_cls.item()
        }
        
        return total_loss, loss_dict


if __name__ == "__main__":
    # 测试损失函数
    print("测试PCC损失...")

    # 使用与 config.py 一致的默认权重（AR-01：λ₂=0.5, λ₃=0.1）
    pcc_loss = PCC_Loss(epsilon_phys=0.1, lambda_phys=0.5, lambda_pcc=0.1)
    
    # 创建测试数据（使用与 config.py 一致的 7 维输入）
    batch_size = 32
    input_dim = 7
    output_dim = 1
    
    x = torch.randn(batch_size, input_dim, requires_grad=True)
    y_pred = torch.randn(batch_size, output_dim, requires_grad=True)
    y_true = torch.randn(batch_size, output_dim)
    y_physics = torch.randn(batch_size, output_dim)
    
    # 创建简单模型
    model = nn.Linear(input_dim, output_dim)
    
    # 计算损失
    total_loss, loss_dict = pcc_loss(y_pred, y_true, y_physics, x, model)
    
    print(f"总损失: {total_loss.item():.4f}")
    print(f"数据损失: {loss_dict['data']:.4f}")
    print(f"物理损失: {loss_dict['phys']:.4f}")
    print(f"PCC损失: {loss_dict['pcc']:.4f}")
    
    print("\n损失函数测试通过！")
