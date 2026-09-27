"""
贝叶斯 DL-LNN 包装器（适配 v4 的 DLLNNWithPhysics）
====================================================

与 prototypes/lnn_research/bayesian_lnn.py 的区别：
    - bayesian_lnn.py 适配 CFCModel（接口: model(x, dt, hidden_state) → (out, h)）
    - 本模块适配 DLLNNWithPhysics（接口: model(x, physics_pred=None) → (final, ltc)）
    - 处理 target 归一化反归一化（v4 trainer 的 mean/std 机制）
    - 同时支持数据分支和物理分支的 MC Dropout

核心思想：
    在推理时保持 Dropout 层激活，运行 N 次前向传播，
    输出均值（点估计）和标准差（不确定性估计）。

参考文献：
    Gal & Ghahramani, "Dropout as a Bayesian Approximation:
    Representing Model Uncertainty in Deep Learning", ICML 2016.
"""

import os
import sys
import types
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

import torch
import torch.nn as nn
import numpy as np

# WinSock 损坏绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch


class BayesianDLLNNWrapper(nn.Module):
    """DLLNNWithPhysics 的贝叶斯 MC Dropout 包装器。

    加载已训练的 DLLNNWithPhysics 权重，在推理时启用 MC Dropout，
    输出预测均值和不确定性（标准差）。

    Args:
        model: 已训练的 DLLNNWithPhysics 实例
        target_mean: 训练时计算的 target 均值（用于反归一化）
        target_std: 训练时计算的 target 标准差（用于反归一化）
        mc_dropout_prob: MC Dropout 概率（若模型原始 dropout < 此值，则替换）

    属性:
        base_model: 被包装的 DLLNNWithPhysics
        target_mean / target_std: target 归一化统计量
    """

    def __init__(
        self,
        model: nn.Module,
        target_mean: float = 0.0,
        target_std: float = 1.0,
        mc_dropout_prob: float = 0.1,
    ):
        super().__init__()
        self.base_model = model
        self.target_mean = target_mean
        self.target_std = target_std
        self.mc_dropout_prob = mc_dropout_prob

        # 确保模型中的 Dropout 层在推理时保持激活
        self._enable_mc_dropout()

    def _enable_mc_dropout(self) -> None:
        """将模型中所有 Dropout 层的 p 值至少设为 mc_dropout_prob。

        如果原始 dropout < mc_dropout_prob，替换为更高概率的 Dropout。
        如果原始 dropout = 0（Identity 或 p=0），注入新的 Dropout 层。

        额外修复：在 ltc_branch.output_proj 输入（即 LTC cells 输出 h）上
        注入一个额外的 Dropout。原因：output_proj 内部的 Dropout 位于 ReLU
        之后，当真实数据下 ReLU 输出全为 0 时（Linear 输出全部 ≤ 0），
        Dropout 对全 0 张量无效，导致 MC Dropout 不产生随机性。
        在 h 上注入 Dropout 可绕过 ReLU 截断，确保随机性传播到输出。
        """
        for module in self.base_model.modules():
            if isinstance(module, nn.Dropout):
                if module.p < self.mc_dropout_prob:
                    module.p = self.mc_dropout_prob
            elif isinstance(module, nn.Identity):
                # Identity 层不处理（DLLNNWithPhysics 的 dropout 在 DLLNNModel 内部）
                pass

        # 显式检查 LTC 分支的 dropout
        if hasattr(self.base_model, "ltc_branch"):
            ltc = self.base_model.ltc_branch
            for module in ltc.modules():
                if isinstance(module, nn.Dropout):
                    if module.p < self.mc_dropout_prob:
                        module.p = self.mc_dropout_prob

            # 在 output_proj 输入（h）上注入额外 Dropout，绕过 ReLU 截断问题
            if hasattr(ltc, "output_proj"):
                self._extra_dropout = nn.Dropout(p=self.mc_dropout_prob)
                self._extra_dropout_hook = ltc.output_proj.register_forward_pre_hook(self._inject_extra_dropout)

    def _inject_extra_dropout(self, module, inputs):
        """forward_pre_hook：在 output_proj 输入上应用额外 Dropout。

        inputs 是 output_proj 的输入 tuple，第一个元素是 h [batch, hidden_dim]。
        返回修改后的 tuple 以替换原始输入。
        """
        if isinstance(inputs, tuple) and len(inputs) > 0:
            h = inputs[0]
            return (self._extra_dropout(h),) + inputs[1:]
        return inputs

    def forward(
        self,
        x: torch.Tensor,
        physics_pred: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """单次前向传播（一次 MC 采样）。"""
        return self.base_model(x, physics_pred=physics_pred)

    def predict_with_uncertainty(
        self,
        x: torch.Tensor,
        physics_pred: Optional[torch.Tensor] = None,
        n_samples: int = 100,
        return_components: bool = False,
    ) -> Dict[str, torch.Tensor]:
        """MC Dropout 推理，返回均值、标准差和（可选）分量。

        保持 Dropout 激活，运行 n_samples 次前向传播，
        统计输出的均值和标准差。

        Args:
            x: 输入特征 [batch_size, 7]
            physics_pred: 物理分支预测（原始 a_lim 尺度，[batch_size, 1]）。
                若提供，将内部归一化后传入 base_model 以激活门控融合逻辑。
                若为 None，模型 forward 走 None 分支（仅返回 ltc_pred，
                门控融合未激活），结果不正确——必须传入。
            n_samples: MC 采样次数，默认 100
            return_components: 是否返回 LTC 分支和物理分支的独立不确定性

        Returns:
            字典，包含：
                - mean: 预测均值 [batch_size, 1]（归一化空间）
                - std: 预测标准差 [batch_size, 1]（归一化空间）
                - mean_denorm: 反归一化后的均值 [batch_size, 1]（原始 a_lim 尺度）
                - std_denorm: 反归一化后的标准差 [batch_size, 1]（原始尺度）
                - ltc_mean / ltc_std: LTC 分支独立统计（若 return_components=True）
        """
        was_training = self.training
        self.train()  # 保持 Dropout 激活

        batch_size = x.shape[0]
        device = x.device

        # 物理分支预测归一化（与训练时 trainer._unpack_batch 一致）
        if physics_pred is not None:
            physics_pred_norm = (physics_pred - self.target_mean) / self.target_std
            physics_pred_norm = physics_pred_norm.to(device)
            # 确保 shape 为 [batch_size, 1]，避免门控融合中广播产生 [B, B] 矩阵
            if physics_pred_norm.dim() == 1:
                physics_pred_norm = physics_pred_norm.unsqueeze(-1)
        else:
            physics_pred_norm = None

        # 收集 n_samples 次前向传播结果
        final_outputs = []
        ltc_outputs = []

        with torch.no_grad():
            for _ in range(n_samples):
                final_pred, ltc_pred = self.base_model(x, physics_pred=physics_pred_norm)
                final_outputs.append(final_pred)
                ltc_outputs.append(ltc_pred)

        # 堆叠: [n_samples, batch_size, 1]
        final_stack = torch.stack(final_outputs, dim=0)
        ltc_stack = torch.stack(ltc_outputs, dim=0)

        # 归一化空间统计
        mean = final_stack.mean(dim=0)
        std = final_stack.std(dim=0)

        # 反归一化到原始 a_lim 尺度
        # 注意：std 反归一化只需乘 target_std（平移不影响方差）
        mean_denorm = mean * self.target_std + self.target_mean
        std_denorm = std * self.target_std

        result = {
            "mean": mean,  # 归一化空间
            "std": std,  # 归一化空间
            "mean_denorm": mean_denorm,  # 原始 a_lim 尺度 (mm)
            "std_denorm": std_denorm,  # 原始尺度 (mm)
        }

        if return_components:
            ltc_mean = ltc_stack.mean(dim=0)
            ltc_std = ltc_stack.std(dim=0)
            result["ltc_mean"] = ltc_mean
            result["ltc_std"] = ltc_std
            result["ltc_mean_denorm"] = ltc_mean * self.target_std + self.target_mean
            result["ltc_std_denorm"] = ltc_std * self.target_std

        # 恢复原始模式
        if not was_training:
            self.eval()

        return result

    def predict_batch(
        self,
        X: np.ndarray,
        physics_pred: Optional[np.ndarray] = None,
        n_samples: int = 100,
        device: str = "cpu",
        batch_size: int = 256,
        return_components: bool = False,
    ) -> Dict[str, np.ndarray]:
        """批量 MC Dropout 推理（适配 numpy 输入）。

        Args:
            X: numpy 输入特征 [N, 7]
            physics_pred: 物理分支预测（原始尺度，[N, 1]）。
                必须传入，否则门控融合未激活，UQ 估计错误。
            n_samples: MC 采样次数
            device: 计算设备
            batch_size: 每批处理样本数（避免显存溢出）
            return_components: 是否返回分量不确定性

        Returns:
            字典，每个值为 numpy 数组 [N, 1]
        """
        self.train()  # 保持 Dropout
        device = torch.device(device)
        self.base_model.to(device)

        N = X.shape[0]
        all_mean, all_std, all_mean_denorm, all_std_denorm = [], [], [], []
        all_ltc_mean, all_ltc_std = [], []
        all_ltc_mean_denorm, all_ltc_std_denorm = [], []

        with torch.no_grad():
            for i in range(0, N, batch_size):
                x_batch = torch.from_numpy(X[i : i + batch_size].astype(np.float32)).to(device)
                if physics_pred is not None:
                    phys_batch = torch.from_numpy(physics_pred[i : i + batch_size].astype(np.float32)).to(device)
                else:
                    phys_batch = None
                result = self.predict_with_uncertainty(
                    x_batch,
                    physics_pred=phys_batch,
                    n_samples=n_samples,
                    return_components=return_components,
                )
                all_mean.append(result["mean"].cpu().numpy())
                all_std.append(result["std"].cpu().numpy())
                all_mean_denorm.append(result["mean_denorm"].cpu().numpy())
                all_std_denorm.append(result["std_denorm"].cpu().numpy())
                if return_components:
                    all_ltc_mean.append(result["ltc_mean"].cpu().numpy())
                    all_ltc_std.append(result["ltc_std"].cpu().numpy())
                    all_ltc_mean_denorm.append(result["ltc_mean_denorm"].cpu().numpy())
                    all_ltc_std_denorm.append(result["ltc_std_denorm"].cpu().numpy())

        out = {
            "mean": np.concatenate(all_mean, axis=0),
            "std": np.concatenate(all_std, axis=0),
            "mean_denorm": np.concatenate(all_mean_denorm, axis=0),
            "std_denorm": np.concatenate(all_std_denorm, axis=0),
        }
        if return_components:
            out["ltc_mean"] = np.concatenate(all_ltc_mean, axis=0)
            out["ltc_std"] = np.concatenate(all_ltc_std, axis=0)
            out["ltc_mean_denorm"] = np.concatenate(all_ltc_mean_denorm, axis=0)
            out["ltc_std_denorm"] = np.concatenate(all_ltc_std_denorm, axis=0)
        return out


def load_bayesian_dllnn(
    weights_path: str | Path,
    device: str = "cpu",
    mc_dropout_prob: float = 0.1,
) -> BayesianDLLNNWrapper:
    """从权重文件加载贝叶斯 DL-LNN。

    Args:
        weights_path: rerun_full_save_weights.py 输出的 .pt 文件路径
        device: 加载到的设备
        mc_dropout_prob: MC Dropout 概率

    Returns:
        BayesianDLLNNWrapper 实例
    """
    # 路径设置（复用主实验路径逻辑）
    _current = Path(__file__).resolve()
    project_root = _current
    for _ in range(6):
        if (project_root / "research" / "training" / "reproducibility.py").exists():
            break
        project_root = project_root.parent
    else:
        project_root = _current.parents[5]

    research_dir = project_root / "research"
    experiments_dir = research_dir / "experiments"
    engineering_python_dir = project_root / "engineering" / "python"

    for p in [str(project_root), str(engineering_python_dir), str(research_dir), str(experiments_dir)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    from experiments.models import DLLNNWithPhysics

    # 加载权重文件
    ckpt = torch.load(weights_path, map_location=device, weights_only=False)
    cfg = ckpt["config"]

    # 重建模型
    model = DLLNNWithPhysics(
        input_dim=cfg["input_dim"],
        hidden_dim=cfg["hidden_dim"],
        num_layers=cfg["num_layers"],
        output_dim=cfg["output_dim"],
        dt=cfg["dt"],
        dropout=cfg["dropout"],
    )
    model.load_state_dict(ckpt["model_state_dict"])
    model.to(device)
    model.eval()

    # 包装为贝叶斯模型
    bayesian_model = BayesianDLLNNWrapper(
        model=model,
        target_mean=ckpt["target_mean"],
        target_std=ckpt["target_std"],
        mc_dropout_prob=mc_dropout_prob,
    )
    bayesian_model.to(device)

    print(f"[加载] 贝叶斯 DL-LNN 权重: {weights_path}")
    print(f"  target_mean = {ckpt['target_mean']:.4f}, target_std = {ckpt['target_std']:.4f}")
    print(f"  MC Dropout prob = {mc_dropout_prob}")
    if "metrics" in ckpt:
        m = ckpt["metrics"]
        print(
            f"  原始模型指标: R²={m.get('r2', 'N/A'):.4f}, MAE={m.get('mae', 'N/A'):.4f}, PCC={m.get('pcc', 'N/A'):.4f}"
        )

    return bayesian_model
