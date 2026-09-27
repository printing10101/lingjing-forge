"""
消融实验脚本（v2，复用主实验基础设施）
=====================================================
重构说明（v1 → v2）：
    本脚本复用主实验的 data_generator / trainer / models / metrics / losses 模块，
    确保消融实验与主实验使用完全相同的模型架构、训练策略和评估指标，
    满足 Q1 期刊可复现性要求。

    v1 问题：
    - 独立数据加载（不同 7 维特征语义 [v,f,ap,fr,damping,Ks,kn]）
    - 独立 Tlusty 解析模型（简化版，与 data_generator.TlustyAnalyticalModel 不同）
    - 独立 DLLNNModel（自带 MLP/CNN/LTC backbone，不是主实验的 DLLNNWithPhysics）
    - 独立损失计算（不使用 losses.PCC_Loss）
    - 无 target 归一化机制
    - 无 DLLNNTrainer 两阶段训练基础设施

    v2 修复：
    - 数据加载：复用 TlustyAnalyticalModel + build_physics_features_7d
    - 模型训练：复用 DLLNNTrainer（含 target 归一化 + 两阶段训练）
    - 评估指标：复用 ChatterMetrics（MAE/RMSE/R²/PCC/MAPE）
    - 损失函数：复用 PCC_Loss（通过修改 config.model.lambda_phys/lambda_pcc 实现消融）
    - 随机种子：复用 set_global_seed

支持的消融配置：
    Full:   完整 DL-LNN（LTC + 物理分支 + 自适应门控 + 两阶段 + L_data + L_phys + L_pcc）
    A1:     去除 L_phys（λ₂=0，仅 L_data + L_pcc）
    A2:     去除 L_pcc（λ₃=0，等价于 PINN，L_data + L_phys）
    A3:     去除两阶段训练（单阶段 300 epochs，从头用完整损失训练）
    A4:     λ₃ 敏感性分析（λ₃ ∈ {0.01, 0.05, 0.1, 0.5, 1.0}）
    A5:     去除门控正则化 L_gate（注：主实验 PCC_Loss 不含 L_gate，此配置在当前架构下为 N/A，
            保留接口以备未来架构升级时启用）
    A6:     门控策略对比（固定 α ∈ {0,0.25,0.5,0.75,1.0} vs 输入自适应 α(x)）
    A7:     主干网络对比（LTC vs MLP vs CNN，保持物理分支+门控+PCC Loss 不变）

覆盖论文：
    - 论文1（DL-LNN 主论文）第 5.4 节"消融实验"
    - 论文2（PCC Loss 通用方法论）第 5.4 节"消融实验"
    - 论文3（双分支门控融合）第 5 节"门控策略消融"

运行方式：
    python ablation_experiment.py --dataset synthetic \
        --ablations Full A1 A2 A3 A4 A6 A7 \
        --output_dir 论文相关/脚本/results/ablation

    python ablation_experiment.py --dataset industrial_6061 \
        --ablations A2 A3 A7_MLP \
        --stage1_epochs 100 --stage2_epochs 200 \
        --output_dir 论文相关/脚本/results/ablation

输出：
    - ablation_results.json（完整结果，含训练历史摘要）
    - ablation_summary.csv（汇总表，可直接粘贴论文表格）
    - ablation_report.md（Markdown 报告，可嵌入论文初稿）
"""

import os
import sys
import json
import csv
import argparse
import warnings
import types
import copy
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

# === WinSock 损坏绕过补丁（必须在 import torch 之前执行）===
# 本机 Python 3.11 + Windows 存在系统级 WinSock 损坏，`_overlapped` C 扩展模块
# 导入失败（WinError 10038），导致 `torch asyncio _overlapped` 导入链断裂。
# 此补丁必须在 import torch/numpy 之前注入空实现到 sys.modules，否则 torch
# 导入时会触发 asyncio.windows_events _overlapped 崩溃。
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。")

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

warnings.filterwarnings("ignore")

# 添加项目路径 —— 自动定位项目根目录
# 脚本路径：项目根/research/papers/论文相关/脚本/ablation_experiment.py
# parents[2]=research/papers, parents[3]=research, parents[4]=项目根
# 但为稳健起见，向上查找含 research/training/reproducibility.py 的目录
_current = Path(__file__).resolve()
PROJECT_ROOT = _current
for _ in range(6):
    if (PROJECT_ROOT / "research" / "training" / "reproducibility.py").exists():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
else:
    # 回退到 parents[4]（当前已知路径）
    PROJECT_ROOT = _current.parents[4]

# experiments 模块实际位于 research/experiments（不是 python/experiments）
RESEARCH_DIR = PROJECT_ROOT / "research"
EXPERIMENTS_DIR = RESEARCH_DIR / "experiments"
PYTHON_DIR = PROJECT_ROOT / "python"  # 兼容旧路径，可能不存在但不会报错
ENGINEERING_PYTHON_DIR = PROJECT_ROOT / "engineering" / "python"  # app 模块所在
# sys.path 优先级（insert(0,...) 后插的在前，按优先级从低到高插入）：
# PROJECT_ROOT < ENGINEERING_PYTHON_DIR < RESEARCH_DIR < EXPERIMENTS_DIR
# 关键：EXPERIMENTS_DIR 必须在 RESEARCH_DIR 之前，否则 trainer.py 内的
# `from models import create_model` 会解析到 research/models/（包目录）
# 而非 research/experiments/models.py（实际模块）
sys.path.insert(0, str(PROJECT_ROOT))  # 让 `from xxx import` 工作
sys.path.insert(0, str(ENGINEERING_PYTHON_DIR))  # 让 `from app.xxx import` 工作
sys.path.insert(0, str(RESEARCH_DIR))  # 让 `from experiments.xxx import` 工作（experiments 是 research 下的包）
sys.path.insert(0, str(EXPERIMENTS_DIR))  # 兼容 trainer.py 内 `from models import`（直接从 experiments/ 目录找）
sys.path.insert(0, str(PYTHON_DIR))  # 兼容旧路径

# 复用主实验模块
from training.reproducibility import set_global_seed
from experiments.config import get_config, ExperimentConfig, ModelConfig
from experiments.data_generator import (
    TlustyAnalyticalModel,
    build_physics_features_7d,
    SyntheticChatterDataset,
    IndustrialChatterDataset,
    PHM2010Dataset,
)
from experiments.models import (
    create_model,
    DLLNNWithPhysics,
    DLLNNModel,
    BaselineBPNN,
    BaselineCNN,
)
from experiments.trainer import DLLNNTrainer, BaselineTrainer
from experiments.losses import PCC_Loss
from experiments.metrics import ChatterMetrics


# 消融配置定义


@dataclass
class AblationSpec:
    """单个消融实验的规格定义。

    通过修改 config / trainer / model 三个层面实现消融，
    而非独立实现整个训练流程。
    """

    name: str  # 消融配置名称（Full / A1 / A2 / A3 / A4_x / A5 / A6_fixed_x / A7_xxx）
    description: str  # 消融描述（写入报告）
    # 损失权重覆盖（None 表示不修改，使用 config 默认值）
    lambda_phys: Optional[float] = None
    lambda_pcc: Optional[float] = None
    # 训练策略
    two_stage: bool = True  # False = 单阶段（A3）
    single_stage_epochs: int = 300  # A3 单阶段训练总轮数
    # 模型变体
    model_variant: str = "default"  # default / fixed_gate / mlp_backbone / cnn_backbone
    fixed_alpha: Optional[float] = None  # A6: 固定门控值
    # 标记
    is_na: bool = False  # 当前架构下不适用（A5）


def get_ablation_specs() -> Dict[str, AblationSpec]:
    """返回所有支持的消融配置。"""
    specs = {
        "Full": AblationSpec(
            name="Full",
            description="完整 DL-LNN（LTC + 物理分支 + 自适应门控 + 两阶段 + L_data + L_phys + L_pcc）",
        ),
        "A1": AblationSpec(
            name="A1",
            description="去除 L_phys（λ₂=0，仅 L_data + L_pcc）",
            lambda_phys=0.0,
        ),
        "A2": AblationSpec(
            name="A2",
            description="去除 L_pcc（λ₃=0，等价于 PINN，L_data + L_phys）",
            lambda_pcc=0.0,
        ),
        "A3": AblationSpec(
            name="A3",
            description="去除两阶段训练（单阶段 300 epochs，从头用完整损失训练）",
            two_stage=False,
        ),
        "A5": AblationSpec(
            name="A5",
            description="去除门控正则化 L_gate（当前架构 PCC_Loss 不含 L_gate，N/A）",
            is_na=True,
        ),
    }
    # A4: λ₃ 敏感性分析（5 个子配置）
    for lam in [0.01, 0.05, 0.1, 0.5, 1.0]:
        key = f"A4_lam{lam}"
        specs[key] = AblationSpec(
            name=key,
            description=f"λ₃ 敏感性分析：λ₃={lam}（默认 0.1）",
            lambda_pcc=lam,
        )
    # A6: 门控策略对比（5 个固定 α + 1 个自适应）
    for alpha in [0.0, 0.25, 0.5, 0.75, 1.0]:
        key = f"A6_fixed{alpha}"
        specs[key] = AblationSpec(
            name=key,
            description=f"门控策略：固定 α={alpha}（vs 自适应 α(x)）",
            model_variant="fixed_gate",
            fixed_alpha=alpha,
        )
    # A7: 主干网络对比
    specs["A7_LTC"] = AblationSpec(
        name="A7_LTC",
        description="主干网络：LTC（= 完整 DL-LNN，对照组）",
        model_variant="default",
    )
    specs["A7_MLP"] = AblationSpec(
        name="A7_MLP",
        description="主干网络：MLP（替换 LTC，保留物理分支+门控+PCC Loss）",
        model_variant="mlp_backbone",
    )
    specs["A7_CNN"] = AblationSpec(
        name="A7_CNN",
        description="主干网络：CNN（替换 LTC，保留物理分支+门控+PCC Loss）",
        model_variant="cnn_backbone",
    )
    return specs


# A6/A7 模型变体（DLLNNWithPhysics 的子类，保持接口一致）


class DLLNNWithPhysicsFixedGate(DLLNNWithPhysics):
    """A6: 固定 α 门控（用于与输入自适应门控对比）。

    覆盖 gate 为固定常数 α，其余架构（LTC 分支 + 物理分支）与 DL-LNN 完全一致。
    """

    def __init__(self, fixed_alpha: float = 0.5, **kwargs):
        super().__init__(**kwargs)
        self.fixed_alpha = float(fixed_alpha)

    def forward(self, x: torch.Tensor, physics_pred: Optional[torch.Tensor] = None):
        # LTC 分支预测
        ltc_pred = self.ltc_branch(x)
        if physics_pred is None:
            return ltc_pred, ltc_pred
        # 固定 α 门控（不可学习）
        alpha = torch.full((x.size(0), 1), self.fixed_alpha, device=x.device, dtype=x.dtype)
        final_pred = alpha * ltc_pred + (1 - alpha) * (self.physics_scale * physics_pred + self.physics_bias)
        return final_pred, ltc_pred


class DLLNNWithPhysicsMLP(DLLNNWithPhysics):
    """A7: MLP 主干（替换 LTC，保留物理分支+门控+PCC Loss）。

    将 LTC 分支替换为标准 MLP，其余架构（物理分支 + 自适应门控）与 DL-LNN 完全一致。
    用于隔离 LTC 连续时间动力学对性能的贡献。
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 64,
        num_layers: int = 3,
        output_dim: int = 1,
        dt: float = 0.1,
        dropout: float = 0.2,
        **kwargs,
    ):
        super().__init__(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dt=dt,
            dropout=dropout,
            **kwargs,
        )
        # 替换 LTC 分支为 MLP
        layers = []
        in_dim = input_dim
        for _ in range(num_layers):
            layers.append(nn.Linear(in_dim, hidden_dim))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
            in_dim = hidden_dim
        layers.append(nn.Linear(in_dim, output_dim))
        self.ltc_branch = nn.Sequential(*layers)


class DLLNNWithPhysicsCNN(DLLNNWithPhysics):
    """A7: CNN 主干（替换 LTC，保留物理分支+门控+PCC Loss）。

    将 LTC 分支替换为 1D-CNN，其余架构（物理分支 + 自适应门控）与 DL-LNN 完全一致。
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 64,
        num_layers: int = 3,
        output_dim: int = 1,
        dt: float = 0.1,
        dropout: float = 0.2,
        **kwargs,
    ):
        super().__init__(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dt=dt,
            dropout=dropout,
            **kwargs,
        )
        # 替换 LTC 分支为 1D-CNN
        self.ltc_branch = nn.Sequential(
            nn.Conv1d(1, hidden_dim, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, output_dim),
        )

    def forward(self, x: torch.Tensor, physics_pred: Optional[torch.Tensor] = None):
        # CNN 需要 [B, C, L] 格式
        x_seq = x.unsqueeze(1)  # [B, 1, input_dim]
        ltc_pred = self.ltc_branch(x_seq)
        if physics_pred is None:
            return ltc_pred, ltc_pred
        alpha = self.gate(x)
        final_pred = alpha * ltc_pred + (1 - alpha) * (self.physics_scale * physics_pred + self.physics_bias)
        return final_pred, ltc_pred


def create_ablation_model(spec: AblationSpec, config: ExperimentConfig) -> nn.Module:
    """根据消融规格创建模型变体。"""
    model_cfg = config.model
    common_kwargs = dict(
        input_dim=model_cfg.input_dim,
        hidden_dim=model_cfg.hidden_dim,
        num_layers=model_cfg.num_layers,
        output_dim=model_cfg.output_dim,
        dt=model_cfg.ltc_dt,
        dropout=model_cfg.dropout,
    )
    variant = spec.model_variant
    if variant == "default":
        return create_model("DL-LNN", config)
    elif variant == "fixed_gate":
        return DLLNNWithPhysicsFixedGate(fixed_alpha=spec.fixed_alpha, **common_kwargs)
    elif variant == "mlp_backbone":
        return DLLNNWithPhysicsMLP(**common_kwargs)
    elif variant == "cnn_backbone":
        return DLLNNWithPhysicsCNN(**common_kwargs)
    else:
        raise ValueError(f"未知模型变体: {variant}")


# 数据加载（复用主实验数据集）


def load_ablation_dataset(name: str, seed: int = 42) -> Dict[str, np.ndarray]:
    """加载消融实验数据集，返回训练/验证/测试三折。

    复用主实验的 SyntheticChatterDataset / IndustrialChatterDataset / PHM2010Dataset，
    确保消融实验与主实验使用完全相同的数据分布和特征构造。

    Args:
        name: 数据集名称 ∈ {synthetic, industrial_6061, phm2010}
        seed: 随机种子（用于数据划分）

    Returns:
        包含 X_train/y_train/y_phys_train/X_val/.../X_test/... 的字典
    """
    set_global_seed(seed)

    # 创建数据集
    if name == "synthetic":
        full_dataset = SyntheticChatterDataset()
    elif name == "industrial_6061":
        full_dataset = IndustrialChatterDataset()
    elif name == "phm2010":
        full_dataset = PHM2010Dataset()
    else:
        raise ValueError(f"未知数据集: {name}")

    # 70/15/15 划分（与主实验一致）
    n_total = len(full_dataset)
    train_size = int(0.7 * n_total)
    val_size = int(0.15 * n_total)
    test_size = n_total - train_size - val_size

    split_generator = torch.Generator().manual_seed(seed)
    train_subset, val_subset, test_subset = torch.utils.data.random_split(
        full_dataset, [train_size, val_size, test_size], generator=split_generator
    )

    # 提取为 numpy 数组
    def subset_to_arrays(subset) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        loader = DataLoader(subset, batch_size=128, shuffle=False)
        X_list, y_list, yphys_list = [], [], []
        for batch in loader:
            if len(batch) == 3:
                x, y, yp = batch
                yphys_list.append(yp.numpy())
            else:
                x, y = batch
                yphys_list.append(y.numpy())  # 无物理标签时用 y 占位
            X_list.append(x.numpy())
            y_list.append(y.numpy())
        X = np.concatenate(X_list, axis=0).astype(np.float32)
        y = np.concatenate(y_list, axis=0).astype(np.float32).reshape(-1, 1)
        yp = np.concatenate(yphys_list, axis=0).astype(np.float32).reshape(-1, 1)
        return X, y, yp

    X_train, y_train, yp_train = subset_to_arrays(train_subset)
    X_val, y_val, yp_val = subset_to_arrays(val_subset)
    X_test, y_test, yp_test = subset_to_arrays(test_subset)

    return {
        "X_train": X_train,
        "y_train": y_train,
        "y_phys_train": yp_train,
        "X_val": X_val,
        "y_val": y_val,
        "y_phys_val": yp_val,
        "X_test": X_test,
        "y_test": y_test,
        "y_phys_test": yp_test,
    }


# 通用训练与评估（复用主实验 DLLNNTrainer）


class _SimpleDataset(Dataset):
    """将 numpy 数组包装为 Dataset，返回 (x, y_true, y_physics) 三元组。"""

    def __init__(self, X, y, y_phys):
        self.X = torch.from_numpy(X).float()
        self.y = torch.from_numpy(y).float().reshape(-1, 1)
        self.y_phys = torch.from_numpy(y_phys).float().reshape(-1, 1)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx], self.y_phys[idx]


def train_and_evaluate_ablation(
    spec: AblationSpec,
    data: Dict[str, np.ndarray],
    base_config: ExperimentConfig,
    seed: int = 42,
    stage1_epochs: int = 100,
    stage2_epochs: int = 200,
) -> Dict[str, Any]:
    """训练并评估单个消融配置，复用主实验 DLLNNTrainer。

    实现策略：
        1. 深拷贝 base_config，根据 spec 修改 lambda_phys/lambda_pcc
        2. 创建模型变体（A6/A7）或使用默认 DL-LNN
        3. 创建 DLLNNTrainer，若使用模型变体则替换 trainer.model 并重建优化器
        4. 根据 spec.two_stage 决定训练策略：
           - True: 两阶段（stage1 预训练 + stage2 微调）
           - False: 单阶段（跳过 stage1，手动计算 target stats，stage2 用完整损失训练）

    Returns:
        结果字典 {spec_name, description, metrics, train_history_summary, elapsed_sec}
    """
    if spec.is_na:
        return {
            "spec_name": spec.name,
            "description": spec.description,
            "status": "N/A",
            "reason": "当前架构 PCC_Loss 不含 L_gate，此消融不适用",
            "metrics": {},
        }

    set_global_seed(seed)
    start_time = time.time()

    # 1. 深拷贝 config 并应用消融修改
    config = copy.deepcopy(base_config)

    # 修改 stage 轮数
    config.model.num_epochs_stage1 = stage1_epochs
    config.model.num_epochs_stage2 = stage2_epochs

    # 应用损失权重消融
    if spec.lambda_phys is not None:
        config.model.lambda_phys = spec.lambda_phys
    if spec.lambda_pcc is not None:
        config.model.lambda_pcc = spec.lambda_pcc

    # A3 单阶段训练：将 stage2 轮数设为 stage1+stage2，保证调度器 T_max 与实际训练轮数一致
    if not spec.two_stage:
        config.model.num_epochs_stage2 = stage1_epochs + stage2_epochs

    # 2. 构造数据加载器
    train_ds = _SimpleDataset(data["X_train"], data["y_train"], data["y_phys_train"])
    val_ds = _SimpleDataset(data["X_val"], data["y_val"], data["y_phys_val"])
    test_ds = _SimpleDataset(data["X_test"], data["y_test"], data["y_phys_test"])

    train_loader = DataLoader(
        train_ds,
        batch_size=32,
        shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    val_loader = DataLoader(val_ds, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=32, shuffle=False)

    # 3. 创建 Trainer
    trainer = DLLNNTrainer(config, device=config.model.device)

    # 若使用模型变体（A6/A7），替换 trainer.model 并重建优化器
    if spec.model_variant != "default":
        variant_model = create_ablation_model(spec, config).to(trainer.device)
        trainer.model = variant_model
        # 重建优化器（绑定新模型参数）
        trainer.optimizer = torch.optim.AdamW(
            trainer.model.parameters(),
            lr=config.model.learning_rate,
            weight_decay=config.model.weight_decay,
        )
        trainer.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            trainer.optimizer,
            T_max=config.model.num_epochs_stage2,
            eta_min=1e-5,
        )

    # 4. 训练
    if spec.two_stage:
        # 两阶段：stage1 预训练 + stage2 微调
        trainer.train_stage1(train_loader, val_loader, num_epochs=stage1_epochs)
        trainer.train_stage2(train_loader, val_loader, num_epochs=stage2_epochs)
    else:
        # A3: 单阶段 —— 跳过 stage1，手动计算 target stats，stage2 用完整损失训练
        # 必须先调用 _compute_target_stats 以启用 target 归一化
        trainer._compute_target_stats(train_loader)
        # 单阶段训练：stage2 用完整 PCC Loss 从头训练
        # 总轮数 = stage1 + stage2，保证总训练量与两阶段一致
        single_epochs = stage1_epochs + stage2_epochs
        trainer.train_stage2(train_loader, val_loader, num_epochs=single_epochs)

    # 5. 评估
    trainer.model.eval()
    all_preds = []
    all_targets = []
    all_phys = []

    with torch.no_grad():
        for batch in test_loader:
            x, y_true, y_phys = batch
            x = x.to(trainer.device)
            y_true = y_true.to(trainer.device)
            y_phys = y_phys.to(trainer.device)
            # 应用 target 归一化，与训练时 _unpack_batch 一致（bug 修复）
            # 必须传入归一化后的 physics_pred 才能激活门控融合，
            # 否则 Full 的自适应门控和 A6 的固定 α 都不生效。
            if trainer._target_stats_computed:
                y_phys_norm = (y_phys - trainer.target_mean) / trainer.target_std
            else:
                y_phys_norm = y_phys
            # DL-LNN 及其变体返回 (final_pred, ltc_pred)
            y_pred, _ = trainer.model(x, physics_pred=y_phys_norm)
            all_preds.append(y_pred.cpu().numpy())
            all_targets.append(y_true.cpu().numpy())
            all_phys.append(y_phys.cpu().numpy())

    all_preds = np.concatenate(all_preds, axis=0)
    all_targets = np.concatenate(all_targets, axis=0)
    all_phys = np.concatenate(all_phys, axis=0)

    # 反归一化到原始 a_lim 尺度（与主实验评估流程一致）
    if hasattr(trainer, "denormalize"):
        all_preds = trainer.denormalize(all_preds)

    metrics_calc = ChatterMetrics()
    metrics = metrics_calc.compute_all(all_preds, all_targets, all_phys)

    elapsed = time.time() - start_time

    # 训练历史摘要（最终 5 个 epoch 的平均 loss）
    train_losses = trainer.history.get("train_loss", [])
    val_losses = trainer.history.get("val_loss", [])
    history_summary = {
        "total_epochs": len(train_losses),
        "final_train_loss": float(train_losses[-1]) if train_losses else None,
        "final_val_loss": float(val_losses[-1]) if val_losses else None,
        "best_val_loss": float(trainer.best_val_loss),
    }

    return {
        "spec_name": spec.name,
        "description": spec.description,
        "status": "completed",
        "metrics": {k: float(v) for k, v in metrics.items()},
        "train_history_summary": history_summary,
        "elapsed_sec": round(elapsed, 1),
        "config_overrides": {
            "lambda_phys": config.model.lambda_phys,
            "lambda_pcc": config.model.lambda_pcc,
            "two_stage": spec.two_stage,
            "model_variant": spec.model_variant,
        },
    }


# Checkpoint 增量保存与恢复（防止中断丢失结果）


def get_checkpoint_path(output_dir: Path, dataset_name: str) -> Path:
    """获取 checkpoint 文件路径。"""
    return Path(output_dir) / f"ablation_checkpoint_{dataset_name}.json"


def load_checkpoint(output_dir: Path, dataset_name: str) -> Dict[str, Dict[str, Any]]:
    """加载已完成的配置结果。

    Returns:
        Dict[spec_name, result_dict] 已完成配置的结果映射
    """
    ckpt_path = get_checkpoint_path(output_dir, dataset_name)
    if not ckpt_path.exists():
        return {}
    try:
        with open(ckpt_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        completed = data.get("completed", {})
        print(f"[Checkpoint] 检测到 {len(completed)} 个已完成配置: {list(completed.keys())}")
        return completed
    except Exception as e:
        print(f"[Checkpoint] 加载失败（忽略）: {e}")
        return {}


def save_checkpoint(
    completed: Dict[str, Dict[str, Any]],
    output_dir: Path,
    dataset_name: str,
) -> None:
    """增量保存已完成配置的结果。每完成一个配置即调用一次。"""
    ckpt_path = get_checkpoint_path(output_dir, dataset_name)
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    # 原子写入：先写临时文件再重命名，防止写入过程中断损坏
    tmp_path = ckpt_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "dataset": dataset_name,
                "last_update": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": completed,
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    tmp_path.replace(ckpt_path)


# 报告生成


def save_results(
    results: List[Dict[str, Any]],
    dataset_name: str,
    output_dir: Path,
) -> None:
    """保存 JSON / CSV / Markdown 三种格式报告。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = output_dir / "ablation_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "dataset": dataset_name,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\n[已保存] JSON: {json_path}")

    # CSV
    csv_path = output_dir / "ablation_summary.csv"
    metric_keys = set()
    for r in results:
        if r.get("metrics"):
            metric_keys.update(r["metrics"].keys())
    metric_keys = sorted(metric_keys)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        header = ["Config", "Description", "Status"] + metric_keys + ["Elapsed(s)"]
        writer.writerow(header)
        for r in results:
            row = [r["spec_name"], r["description"], r["status"]]
            if r.get("metrics"):
                for k in metric_keys:
                    val = r["metrics"].get(k, "")
                    row.append(f"{val:.4f}" if isinstance(val, (int, float)) else "")
            else:
                row.extend([""] * len(metric_keys))
            row.append(r.get("elapsed_sec", ""))
            writer.writerow(row)
    print(f"[已保存] CSV:  {csv_path}")

    # Markdown
    md_path = output_dir / "ablation_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 消融实验报告\n\n")
        f.write(f"- **数据集**: {dataset_name}\n")
        f.write(f"- **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **实验数量**: {len(results)}\n\n")

        f.write("## 结果汇总表\n\n")
        f.write("| 配置 | 描述 | 状态 |")
        for k in metric_keys:
            f.write(f" {k.upper()} |")
        f.write(f" 耗时(s) |\n")
        f.write("|------|------|------|")
        for _ in metric_keys:
            f.write("------|")
        f.write("------|\n")

        for r in results:
            row = f"| {r['spec_name']} | {r['description']} | {r['status']} |"
            if r.get("metrics"):
                for k in metric_keys:
                    val = r["metrics"].get(k, "")
                    row += f" {val:.4f} |" if isinstance(val, (int, float)) else " - |"
            else:
                row += " - |" * len(metric_keys)
            row += f" {r.get('elapsed_sec', '-')} |\n"
            f.write(row)

        f.write("\n## 详细结果\n\n")
        for r in results:
            f.write(f"### {r['spec_name']}: {r['description']}\n\n")
            f.write(f"- **状态**: {r['status']}\n")
            if r.get("metrics"):
                f.write("- **指标**:\n")
                for k, v in r["metrics"].items():
                    f.write(f"  - {k.upper()}: {v:.4f}\n")
            if r.get("train_history_summary"):
                hs = r["train_history_summary"]
                f.write("- **训练摘要**:\n")
                f.write(f"  - 总轮数: {hs.get('total_epochs')}\n")
                f.write(f"  - 最终训练损失: {hs.get('final_train_loss')}\n")
                f.write(f"  - 最终验证损失: {hs.get('final_val_loss')}\n")
                f.write(f"  - 最佳验证损失: {hs.get('best_val_loss')}\n")
            if r.get("config_overrides"):
                co = r["config_overrides"]
                f.write("- **配置覆盖**:\n")
                f.write(f"  - λ₂ (lambda_phys): {co.get('lambda_phys')}\n")
                f.write(f"  - λ₃ (lambda_pcc): {co.get('lambda_pcc')}\n")
                f.write(f"  - 两阶段训练: {co.get('two_stage')}\n")
                f.write(f"  - 模型变体: {co.get('model_variant')}\n")
            f.write(f"- **耗时**: {r.get('elapsed_sec', '-')} 秒\n\n")
    print(f"[已保存] MD:   {md_path}")


# 主入口


def parse_args():
    parser = argparse.ArgumentParser(description="DL-LNN 消融实验脚本 v2（复用主实验基础设施）")
    parser.add_argument(
        "--dataset",
        type=str,
        default="synthetic",
        choices=["synthetic", "industrial_6061", "phm2010"],
        help="数据集名称",
    )
    parser.add_argument(
        "--ablations",
        type=str,
        nargs="+",
        default=["Full", "A1", "A2", "A3", "A4_lam0.1", "A6_fixed0.5", "A7_MLP"],
        help="消融配置名称列表（用空格分隔）。支持: Full A1 A2 A3 A4_lam{x} A5 A6_fixed{x} A7_LTC A7_MLP A7_CNN",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="论文相关/脚本/results/ablation",
        help="结果输出目录",
    )
    parser.add_argument(
        "--stage1_epochs",
        type=int,
        default=100,
        help="阶段一（解析预训练）轮数",
    )
    parser.add_argument(
        "--stage2_epochs",
        type=int,
        default=200,
        help="阶段二（物理残差微调）轮数",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 checkpoint 恢复，跳过已完成的配置",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="忽略已有 checkpoint，从头开始（会覆盖）",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 80)
    print("DL-LNN 消融实验 v2（复用主实验基础设施）")
    print("=" * 80)
    print(f"数据集: {args.dataset}")
    print(f"消融配置: {args.ablations}")
    print(f"阶段一轮数: {args.stage1_epochs}")
    print(f"阶段二轮数: {args.stage2_epochs}")
    print(f"随机种子: {args.seed}")
    print(f"设备: {'cuda' if torch.cuda.is_available() else 'cpu'}")
    print("=" * 80)

    # 加载基础配置
    base_config = get_config()
    base_config.model.device = "cuda" if torch.cuda.is_available() else "cpu"
    base_config.seed = args.seed

    # 加载数据集（所有消融共用同一数据划分，保证公平比较）
    print(f"\n[加载数据集] {args.dataset} ...")
    data = load_ablation_dataset(args.dataset, seed=args.seed)
    print(f"  训练集: {len(data['X_train'])} 样本")
    print(f"  验证集: {len(data['X_val'])} 样本")
    print(f"  测试集: {len(data['X_test'])} 样本")

    # 获取消融规格
    all_specs = get_ablation_specs()

    # 验证消融名称
    invalid = [a for a in args.ablations if a not in all_specs]
    if invalid:
        print(f"[错误] 未知消融配置: {invalid}")
        print(f"       可用配置: {list(all_specs.keys())}")
        sys.exit(1)

    # Checkpoint 恢复逻辑
    output_dir = Path(args.output_dir)
    if args.fresh:
        # 清除旧 checkpoint
        ckpt_path = get_checkpoint_path(output_dir, args.dataset)
        if ckpt_path.exists():
            ckpt_path.unlink()
            print(f"[Checkpoint] 已清除旧 checkpoint: {ckpt_path.name}")
        completed = {}
    elif args.resume:
        completed = load_checkpoint(output_dir, args.dataset)
    else:
        # 默认自动检测：若存在 checkpoint 则提示
        ckpt_path = get_checkpoint_path(output_dir, args.dataset)
        if ckpt_path.exists():
            print(f"[Checkpoint] 检测到已有 checkpoint（使用 --resume 续跑 或 --fresh 重跑）")
            completed = load_checkpoint(output_dir, args.dataset)
        else:
            completed = {}

    # 运行消融实验
    results = []
    for i, ablation_name in enumerate(args.ablations):
        spec = all_specs[ablation_name]

        # 恢复逻辑：若已存在该配置的结果，直接复用
        if ablation_name in completed:
            print(f"\n[Checkpoint] [{i + 1}/{len(args.ablations)}] 跳过已完成的 {ablation_name}")
            results.append(completed[ablation_name])
            continue

        print(f"\n{'=' * 60}")
        print(f"[{i + 1}/{len(args.ablations)}] 消融配置: {spec.name}")
        print(f"  描述: {spec.description}")
        print(f"{'=' * 60}")

        try:
            result = train_and_evaluate_ablation(
                spec=spec,
                data=data,
                base_config=base_config,
                seed=args.seed,
                stage1_epochs=args.stage1_epochs,
                stage2_epochs=args.stage2_epochs,
            )
            if result.get("metrics"):
                m = result["metrics"]
                print(f"\n[结果] {spec.name}:")
                print(f"  MAE: {m.get('mae', 'N/A'):.4f} | RMSE: {m.get('rmse', 'N/A'):.4f}")
                print(f"  R²:  {m.get('r2', 'N/A'):.4f} | PCC: {m.get('pcc', 'N/A'):.4f}")
                print(f"  耗时: {result.get('elapsed_sec', 'N/A')} s")
        except Exception as e:
            print(f"[错误] 消融 {spec.name} 执行失败: {e}")
            import traceback

            traceback.print_exc()
            result = {
                "spec_name": spec.name,
                "description": spec.description,
                "status": "failed",
                "reason": str(e),
                "metrics": {},
            }
        results.append(result)

        # 增量保存 checkpoint
        completed[ablation_name] = result
        save_checkpoint(completed, output_dir, args.dataset)
        print(f"[Checkpoint] 已保存进度（{len(completed)}/{len(args.ablations)}）")

    # 保存最终结果（JSON/CSV/MD）
    save_results(results, args.dataset, output_dir)

    print(f"\n{'=' * 80}")
    print(f"消融实验完成！共 {len(results)} 个配置")
    print(f"结果保存至: {output_dir.resolve()}")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
