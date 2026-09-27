"""
LOMO / LOCO 跨工况泛化实验脚本（v2，复用主实验基础设施）
=====================================================
重构说明：
    本脚本复用主实验的 data_generator / trainer / models / metrics 模块，
    确保 LOMO/LOCO 实验与主实验使用完全相同的模型架构、训练策略和评估指标，
    满足 Q1 期刊可复现性要求。

    v1 → v2 主要变更：
    - 数据加载：复用 TlustyAnalyticalModel + build_physics_features_7d
    - 模型训练：复用 DLLNNTrainer / BaselineTrainer / SklearnBaselineTrainer
    - 评估指标：复用 ChatterMetrics（MAE/RMSE/R²/PCC/MAPE）
    - Target 归一化：复用主实验的 target_mean/std 机制
    - 随机种子：复用 set_global_seed

    v2.1 变更（AR-02 OOD 修复）：
    - 引入物理引导的 target 缩放（Physics-guided Target Scaling）
    - 缩放因子 ks_scale = (hardness / 200) ** 0.8，与 Tlusty 物理模型一致
      （Tlusty: a_lim ∝ 1 / Ks_eff，Ks_eff = Ks_base * (H/200)^0.8）
    - 训练时 target = a_lim * ks_scale，推理时反缩放 a_lim = pred / ks_scale
    - 效果：不同硬度的材料 a_lim 被归一化到同一尺度，模型学习"材料归一化
      的极限切深"，显著改善 HRC52（硬度 520，原训练集 95-350）的 OOD 泛化
    - 由 --physics_aware 标志启用，默认启用（推荐用于论文最终结果）

支持的协议：
    - LOMO (Leave-One-Material-Out)：训练集含 N-1 种材料，测试集为第 N 种材料
    - LOCO (Leave-One-Condition-Out)：训练集含 N-1 个工况，测试集为剩余 1 个工况

用途：
    - 论文1（DL-LNN 主论文）第 5.2 节"跨工况泛化"实验
    - 论文3（双分支门控融合）第 5 节"跨工况评估"

运行方式：
    python lomo_loco_experiment.py --protocol LOMO --models DL-LNN \
                                   --dataset synthetic_multi \
                                   --output_dir 论文相关/脚本/results/lomo_loco

输出：
    - lomo_results.json / loco_results.json（完整结果）
    - lomo_summary.csv / loco_summary.csv（汇总表）
    - lomo_report.md / loco_report.md（Markdown 报告）
"""

import os
import sys
import json
import csv
import argparse
import warnings
import types
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass

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
from torch.utils.data import Dataset, DataLoader

warnings.filterwarnings("ignore")

# 添加项目路径（重构后：原 python/ 已拆分为 research/ 和 engineering/python/）
# 旧路径 python/ 和 python/experiments/ 已不存在，必须使用新路径
# 脚本位于 research/papers/论文相关/脚本/ 下，parents[4] 为项目根
PROJECT_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_DIR = PROJECT_ROOT / "research"
EXPERIMENTS_DIR = RESEARCH_DIR / "experiments"
ENGINEERING_PYTHON_DIR = PROJECT_ROOT / "engineering" / "python"

# sys.path 优先级（insert(0,...) 后插的在前，按优先级从低到高插入）：
# PROJECT_ROOT < ENGINEERING_PYTHON_DIR < RESEARCH_DIR < EXPERIMENTS_DIR
# 关键：EXPERIMENTS_DIR 必须在 RESEARCH_DIR 之前，否则 trainer.py 内的
# `from models import create_model` 会解析到 research/models/（包目录）
# 而非 research/experiments/models.py（实际模块）
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ENGINEERING_PYTHON_DIR))
sys.path.insert(0, str(RESEARCH_DIR))
sys.path.insert(0, str(EXPERIMENTS_DIR))

# 复用主实验模块
from training.reproducibility import set_global_seed, get_worker_init_fn
from experiments.config import get_config, ExperimentConfig
from experiments.data_generator import (
    TlustyAnalyticalModel,
    build_physics_features_7d,
)
from experiments.models import create_model
from experiments.trainer import (
    DLLNNTrainer,
    BaselineTrainer,
    SklearnBaselineTrainer,
    SKLEARN_BASELINE_MODELS,
)
from experiments.metrics import ChatterMetrics


# 材料与工况定义

# 5 种材料及其典型硬度（HB）——覆盖铝合金/钛合金/不锈钢/碳钢/淬硬钢
MATERIALS_CONFIG = {
    "6061-T6": {"hardness": 95.0, "Ks_factor": 1.0, "description": "铝合金"},
    "TC4": {"hardness": 350.0, "Ks_factor": 2.2, "description": "钛合金 Ti-6Al-4V"},
    "HRC52": {"hardness": 520.0, "Ks_factor": 3.0, "description": "淬硬不锈钢"},
    "45_Steel": {"hardness": 200.0, "Ks_factor": 1.5, "description": "45号碳钢"},
    "304_SS": {"hardness": 180.0, "Ks_factor": 1.8, "description": "304 奥氏体不锈钢"},
}

# 9 种工况（3 参数 × 3 水平）
CONDITIONS_CONFIG = {
    "low_speed": {"spindle_range": (1000, 3000), "feed_range": (0.05, 0.15), "depth_range": (0.5, 2.0)},
    "mid_speed": {"spindle_range": (3000, 6000), "feed_range": (0.15, 0.30), "depth_range": (2.0, 5.0)},
    "high_speed": {"spindle_range": (6000, 10000), "feed_range": (0.30, 0.50), "depth_range": (5.0, 10.0)},
    "low_feed": {"spindle_range": (2000, 8000), "feed_range": (0.05, 0.12), "depth_range": (1.0, 6.0)},
    "mid_feed": {"spindle_range": (2000, 8000), "feed_range": (0.12, 0.25), "depth_range": (1.0, 6.0)},
    "high_feed": {"spindle_range": (2000, 8000), "feed_range": (0.25, 0.50), "depth_range": (1.0, 6.0)},
    "low_depth": {"spindle_range": (2000, 8000), "feed_range": (0.10, 0.30), "depth_range": (0.5, 1.5)},
    "mid_depth": {"spindle_range": (2000, 8000), "feed_range": (0.10, 0.30), "depth_range": (1.5, 4.0)},
    "high_depth": {"spindle_range": (2000, 8000), "feed_range": (0.10, 0.30), "depth_range": (4.0, 10.0)},
}

ALL_MODELS = ["SVR", "RF", "XGBoost", "GP", "BPNN", "LSTM", "Transformer", "PINN", "DL-LNN"]


# LOMO/LOCO 专用数据集


class LomoLocoDataset(Dataset):
    """LOMO/LOCO 实验专用数据集。

    复用主实验的 TlustyAnalyticalModel 和 build_physics_features_7d，
    生成按材料/工况分组的合成数据。

    每个样本返回 (features[7], a_lim[1], a_lim_physics[1]) 三元组，
    与主实验 SyntheticChatterDataset / IndustrialChatterDataset 接口一致。

    物理引导的 target 缩放（AR-02 修复）：
        为支持 OOD 材料泛化，数据集额外维护每个样本的物理缩放因子
        ``ks_scale = (hardness / 200) ** 0.8``。该因子与 Tlusty 物理模型中
        ``a_lim ∝ 1 / Ks_eff`` 的关系一致。下游训练 / 评估通过该因子对
        target 做缩放，使不同硬度材料的 a_lim 在同一尺度上对齐。
    """

    def __init__(
        self,
        samples_per_group: int = 200,
        materials: Optional[List[str]] = None,
        conditions: Optional[List[str]] = None,
        noise_level: float = 0.02,
        seed: int = 42,
    ):
        super().__init__()
        self.samples_per_group = samples_per_group
        self.materials = materials or list(MATERIALS_CONFIG.keys())
        self.conditions = conditions or list(CONDITIONS_CONFIG.keys())
        self.noise_level = noise_level
        self.dataset_name = "LOMO_LOCO_Synthetic"

        np.random.seed(seed)
        self.data = self._generate_data()
        # 记录每个样本所属的材料和工况
        self.sample_materials = self.data["sample_materials"]
        self.sample_conditions = self.data["sample_conditions"]
        # AR-02: 物理引导缩放因子（与 Tlusty Ks_eff = Ks_base * (H/200)^0.8 一致）
        self.sample_ks_scale = self.data["sample_ks_scale"]

    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成按材料×工况分组的合成数据。"""
        tlusty = TlustyAnalyticalModel()
        features_list = []
        a_lim_list = []
        a_lim_clean_list = []
        materials_list = []
        conditions_list = []
        ks_scale_list = []

        for material_name in self.materials:
            mat_cfg = MATERIALS_CONFIG[material_name]
            hardness_base = mat_cfg["hardness"]

            for cond_name in self.conditions:
                cond_cfg = CONDITIONS_CONFIG[cond_name]

                # 采样加工参数
                spindle_speed = np.random.uniform(
                    cond_cfg["spindle_range"][0],
                    cond_cfg["spindle_range"][1],
                    self.samples_per_group,
                )
                feed_rate = np.random.uniform(
                    cond_cfg["feed_range"][0],
                    cond_cfg["feed_range"][1],
                    self.samples_per_group,
                )
                axial_depth = np.random.uniform(
                    cond_cfg["depth_range"][0],
                    cond_cfg["depth_range"][1],
                    self.samples_per_group,
                )
                radial_depth = np.random.uniform(0.5, 8.0, self.samples_per_group)
                hardness = hardness_base + np.random.randn(self.samples_per_group) * 3.0
                tool_diameter = np.random.uniform(6.0, 16.0, self.samples_per_group)
                num_teeth = np.random.randint(2, 7, self.samples_per_group).astype(float)

                # Tlusty 极限切深（复用主实验模型）
                a_lim_clean = tlusty.compute_limiting_depth(
                    spindle_speed,
                    hardness=hardness,
                    tool_diameter=tool_diameter,
                    num_teeth=num_teeth,
                    feed_rate=feed_rate,
                    radial_depth=radial_depth,
                )

                # 添加噪声
                a_lim = a_lim_clean * (1 + np.random.randn(self.samples_per_group) * self.noise_level)
                a_lim = np.maximum(a_lim, 0.01)

                # 7 维物理特征（复用主实验特征构造）
                feats = build_physics_features_7d(
                    spindle_speed=spindle_speed,
                    feed_rate=feed_rate,
                    axial_depth=axial_depth,
                    radial_depth=radial_depth,
                    hardness=hardness,
                    tool_diameter=tool_diameter,
                    num_teeth=num_teeth,
                )

                # AR-02: 物理引导缩放因子 (hardness/200)^0.8
                # 与 Tlusty Ks_eff 公式一致，用于 target 物理缩放
                ks_scale = (hardness / 200.0) ** 0.8

                features_list.append(feats)
                a_lim_list.append(a_lim.astype(np.float32))
                a_lim_clean_list.append(a_lim_clean.astype(np.float32))
                ks_scale_list.append(ks_scale.astype(np.float32))
                materials_list.extend([material_name] * self.samples_per_group)
                conditions_list.extend([cond_name] * self.samples_per_group)

        return {
            "features": np.concatenate(features_list, axis=0).astype(np.float32),
            "a_lim": np.concatenate(a_lim_list, axis=0).astype(np.float32),
            "a_lim_clean": np.concatenate(a_lim_clean_list, axis=0).astype(np.float32),
            "sample_ks_scale": np.concatenate(ks_scale_list, axis=0).astype(np.float32),
            "sample_materials": np.array(materials_list),
            "sample_conditions": np.array(conditions_list),
        }

    def __len__(self) -> int:
        return len(self.data["features"])

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))
        return features, a_lim, a_lim_physics


def make_subset_dataset(
    parent: LomoLocoDataset,
    indices: np.ndarray,
) -> "SubsetDataset":
    """从父数据集创建子集（复用父数据集的底层数据）。"""
    return SubsetDataset(parent, indices)


class SubsetDataset(Dataset):
    """父数据集的子集视图，用于 LOMO/LOCO 划分。"""

    def __init__(self, parent: LomoLocoDataset, indices: np.ndarray):
        self.parent = parent
        self.indices = indices

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.parent[self.indices[idx]]


# 通用训练与评估（复用主实验 Trainer）


def train_and_evaluate(
    model_name: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    y_phys_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    y_phys_test: np.ndarray,
    config: ExperimentConfig,
    seed: int = 42,
    stage1_epochs: int = 50,
    stage2_epochs: int = 100,
    baseline_epochs: int = 150,
    ks_scale_train: Optional[np.ndarray] = None,
    ks_scale_test: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """训练并评估单个模型，复用主实验的 Trainer 体系。

    Args:
        model_name: 模型名称
        X_train, y_train, y_phys_train: 训练数据（特征/标签/物理标签）
        X_test, y_test, y_phys_test: 测试数据
        config: 实验配置
        seed: 随机种子
        stage1_epochs: DL-LNN 阶段一轮数（LOMO 默认缩减为 50）
        stage2_epochs: DL-LNN 阶段二轮数（LOMO 默认缩减为 100）
        baseline_epochs: 非 sklearn 基线训练轮数
        ks_scale_train: 训练集每个样本的物理引导缩放因子（AR-02 修复）。
            None 或全 1.0 表示不启用物理引导缩放（v2 原行为）。
        ks_scale_test: 测试集每个样本的物理引导缩放因子。

    Returns:
        评估指标字典 {mae, rmse, r2, mape, pcc}

    AR-02 物理引导缩放原理：
        训练时 target ← target * ks_scale，使不同硬度材料的 a_lim 在同一尺度上对齐，
        模型学习"材料归一化的极限切深"；推理时将模型输出 / ks_scale 反缩放到原始 a_lim。
        ks_scale = (hardness/200)^0.8 与 Tlusty 物理模型 Ks_eff 公式一致。
    """
    set_global_seed(seed)

    # AR-02: 物理引导 target 缩放
    # None 或空数组视为不启用（保持 v2 原行为，便于对照实验）
    if ks_scale_train is None:
        ks_scale_train = np.ones(len(y_train), dtype=np.float32)
    if ks_scale_test is None:
        ks_scale_test = np.ones(len(y_test), dtype=np.float32)
    ks_scale_train = ks_scale_train.astype(np.float32).reshape(-1)
    ks_scale_test = ks_scale_test.astype(np.float32).reshape(-1)

    # 应用物理引导缩放：训练/验证 target 乘以 ks_scale
    # （trainer 后续会在缩放后的空间计算 mean/std 并再次归一化，二者协同）
    y_train_scaled = (y_train * ks_scale_train).astype(np.float32)
    y_phys_train_scaled = (y_phys_train * ks_scale_train).astype(np.float32)
    # 测试集 target 不缩放——保留原始 a_lim 用于最终指标计算
    # 模型在缩放空间预测，推理后通过 / ks_scale_test 反缩放回原始尺度

    # 构造 TensorDataset 风格的简单 Dataset
    class _SimpleDataset(Dataset):
        def __init__(self, X, y, y_phys):
            self.X = torch.from_numpy(X).float()
            self.y = torch.from_numpy(y).float().reshape(-1, 1)
            self.y_phys = torch.from_numpy(y_phys).float().reshape(-1, 1)

        def __len__(self):
            return len(self.X)

        def __getitem__(self, idx):
            return self.X[idx], self.y[idx], self.y_phys[idx]

    # 从训练集划分 15% 作为验证集
    n_train = len(X_train)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n_train)
    n_val = max(1, int(0.15 * n_train))
    val_idx = perm[:n_val]
    train_idx = perm[n_val:]

    train_ds = _SimpleDataset(X_train[train_idx], y_train_scaled[train_idx], y_phys_train_scaled[train_idx])
    val_ds = _SimpleDataset(X_train[val_idx], y_train_scaled[val_idx], y_phys_train_scaled[val_idx])
    test_ds = _SimpleDataset(X_test, y_test, y_phys_test)

    train_loader = DataLoader(train_ds, batch_size=32, shuffle=True, generator=torch.Generator().manual_seed(seed))
    val_loader = DataLoader(val_ds, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=32, shuffle=False)

    # 选择 Trainer
    if model_name == "DL-LNN":
        # 将 config 的 stage2 轮数同步为实际训练轮数，
        # 保证 CosineAnnealingLR 的 T_max 与 train_stage2 的 num_epochs 一致
        # （否则 LOMO 默认 stage2_epochs=100 < config.num_epochs_stage2=200，
        # 调度器无法完成完整退火周期，影响收敛质量与可复现性）
        config.model.num_epochs_stage2 = stage2_epochs
        trainer = DLLNNTrainer(config, device=config.model.device)
        trainer.train_stage1(train_loader, val_loader, num_epochs=stage1_epochs)
        trainer.train_stage2(train_loader, val_loader, num_epochs=stage2_epochs)
        model = trainer.model
    elif model_name in SKLEARN_BASELINE_MODELS:
        trainer = SklearnBaselineTrainer(model_name, config, device="cpu")
        trainer.train(train_loader, val_loader, num_epochs=1)
        model = trainer.model
    else:
        trainer = BaselineTrainer(model_name, config, device=config.model.device)
        trainer.train(train_loader, val_loader, num_epochs=baseline_epochs)
        model = trainer.model

    # 评估
    model.eval()
    all_preds = []
    all_targets = []
    all_phys = []
    all_ks_scale = []  # AR-02: 收集每个测试样本的 ks_scale 用于反缩放

    if model_name in SKLEARN_BASELINE_MODELS:
        for batch_idx, batch in enumerate(test_loader):
            x, y_true, y_phys = batch
            x_numpy = x.cpu().numpy()
            y_pred = model.predict(x_numpy)
            all_preds.append(y_pred)
            all_targets.append(y_true.numpy())
            all_phys.append(y_phys.numpy())
            # 按 batch 切片对应的 ks_scale（test_loader batch_size=32, shuffle=False）
            start = batch_idx * 32
            end = start + len(y_true)
            all_ks_scale.append(ks_scale_test[start:end])
    else:
        with torch.no_grad():
            for batch_idx, batch in enumerate(test_loader):
                x, y_true, y_phys = batch
                x = x.to(config.model.device)
                y_pred, _ = model(x) if model_name == "DL-LNN" else (model(x), None)
                all_preds.append(y_pred.cpu().numpy())
                all_targets.append(y_true.numpy())
                all_phys.append(y_phys.numpy())
                start = batch_idx * 32
                end = start + len(y_true)
                all_ks_scale.append(ks_scale_test[start:end])

    all_preds = np.concatenate(all_preds, axis=0).reshape(-1, 1)
    all_targets = np.concatenate(all_targets, axis=0).reshape(-1, 1)
    all_phys = np.concatenate(all_phys, axis=0).reshape(-1, 1)
    all_ks_scale = np.concatenate(all_ks_scale, axis=0).reshape(-1, 1)

    # 神经网络模型反归一化：trainer.denormalize 把模型输出从 mean/std 归一化空间
    # 反归一化到"训练时的缩放空间"（即 a_lim * ks_scale 空间）
    if model_name not in SKLEARN_BASELINE_MODELS and hasattr(trainer, "denormalize"):
        all_preds = trainer.denormalize(all_preds)
    # AR-02: 再除以 ks_scale_test 反缩放到原始 a_lim 尺度
    # （sklearn 模型直接输出 pred_scaled，同样需要此步）
    all_preds = all_preds / all_ks_scale

    metrics_calc = ChatterMetrics()
    metrics = metrics_calc.compute_all(all_preds, all_targets, all_phys)
    return metrics


# Checkpoint 机制（方案 A，防止进程崩溃丢失全部 fold 结果）
# 设计：每个 fold 完成后立即将 results_per_fold 写入 checkpoint JSON 文件，
# 进程重启后从 checkpoint 加载已完成的 fold，跳过已完成部分，仅训练剩余 fold。
# 所有 fold 完成后删除 checkpoint 文件以区分"已完成"与"中断续跑"状态。
#
# checkpoint 文件命名：{protocol.lower()}_ckpt_{model_name}{suffix}.json
# 其中 suffix = "_physics_aware" 或 "_baseline"，与最终结果文件命名一致。


def _make_ckpt_path(output_dir: str, protocol: str, model_name: str, physics_aware: bool) -> str:
    """构造 checkpoint 文件路径。"""
    suffix = "_physics_aware" if physics_aware else "_baseline"
    safe_model = model_name.replace("/", "_").replace("\\", "_")
    return os.path.join(output_dir, f"{protocol.lower()}_ckpt_{safe_model}{suffix}.json")


def _load_checkpoint(ckpt_path: str) -> List[Dict]:
    """加载 checkpoint，返回已完成的 fold 结果列表。

    若 checkpoint 不存在或损坏，返回空列表（从头开始）。
    """
    if not os.path.exists(ckpt_path):
        return []
    try:
        with open(ckpt_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        folds = data.get("completed_folds", [])
        print(f"  [CKPT] 检测到 checkpoint: {ckpt_path}")
        print(f"  [CKPT] 已完成 {len(folds)} 个 fold，将跳过这些 fold 续跑")
        return folds
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"  [CKPT] checkpoint 损坏 ({e})，从头开始训练")
        return []


def _save_checkpoint(ckpt_path: str, completed_folds: List[Dict], total_folds: int) -> None:
    """保存 checkpoint（原子写入：先写临时文件再 rename，防止写中途崩溃损坏）。"""
    tmp_path = ckpt_path + ".tmp"
    payload = {
        "completed_folds": completed_folds,
        "total_folds": total_folds,
        "saved_at": str(np.datetime64("now", "s")),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp_path, ckpt_path)  # Windows 上 os.replace 是原子操作
    except OSError:
        # 写 checkpoint 失败不应中断实验，仅打印警告
        print(f"  [CKPT] 警告: 保存 checkpoint 失败，继续训练不影响结果")


def _cleanup_checkpoint(ckpt_path: str) -> None:
    """所有 fold 完成后删除 checkpoint 文件。"""
    try:
        if os.path.exists(ckpt_path):
            os.remove(ckpt_path)
            print(f"  [CKPT] 所有 fold 完成，已清理 checkpoint: {ckpt_path}")
    except OSError as e:
        print(f"  [CKPT] 警告: 清理 checkpoint 失败 ({e})")


# LOMO 协议


def run_lomo_experiment(
    model_name: str,
    dataset: LomoLocoDataset,
    config: ExperimentConfig,
    seed: int = 42,
    stage1_epochs: int = 50,
    stage2_epochs: int = 100,
    baseline_epochs: int = 150,
    physics_aware: bool = True,
    output_dir: Optional[str] = None,
) -> Dict:
    """执行 LOMO (Leave-One-Material-Out) 实验。

    Args:
        physics_aware: 是否启用物理引导 target 缩放（AR-02 修复，默认 True）。
            False 时所有样本 ks_scale=1.0，等价于 v2 原行为，用于对照实验。
        output_dir: 输出目录。传入则启用 fold 级 checkpoint（方案 A），
            进程崩溃重启后可从 checkpoint 续跑。None 则禁用 checkpoint。
    """
    set_global_seed(seed)
    materials = dataset.materials
    n_materials = len(materials)

    # === Checkpoint 加载（方案 A）===
    ckpt_path = _make_ckpt_path(output_dir, "LOMO", model_name, physics_aware) if output_dir else None
    completed_folds = _load_checkpoint(ckpt_path) if ckpt_path else []
    completed_materials = {f["test_material"] for f in completed_folds}

    results_per_fold = list(completed_folds)  # 复用已完成结果
    print(f"\n[LOMO] 模型: {model_name}, 材料数: {n_materials}, physics_aware: {physics_aware}")
    if completed_folds:
        print(f"  [CKPT] 从 checkpoint 恢复 {len(completed_folds)} 个 fold: {sorted(completed_materials)}")

    for i, test_material in enumerate(materials):
        # 跳过已完成的 fold（checkpoint 续跑）
        if test_material in completed_materials:
            print(f"  Fold {i + 1}/{n_materials}: 留出 {test_material} ... [CKPT 跳过]")
            continue

        print(f"  Fold {i + 1}/{n_materials}: 留出 {test_material} ...")

        # 按材料划分
        train_mask = dataset.sample_materials != test_material
        test_mask = dataset.sample_materials == test_material

        X_train = dataset.data["features"][train_mask]
        y_train = dataset.data["a_lim"][train_mask]
        y_phys_train = dataset.data["a_lim_clean"][train_mask]
        X_test = dataset.data["features"][test_mask]
        y_test = dataset.data["a_lim"][test_mask]
        y_phys_test = dataset.data["a_lim_clean"][test_mask]

        # AR-02: 提取物理引导缩放因子
        ks_scale_train = dataset.data["sample_ks_scale"][train_mask] if physics_aware else None
        ks_scale_test = dataset.data["sample_ks_scale"][test_mask] if physics_aware else None

        if len(X_test) < 5:
            print(f"    [跳过] 测试集样本过少 ({len(X_test)})")
            continue

        metrics = train_and_evaluate(
            model_name,
            X_train,
            y_train,
            y_phys_train,
            X_test,
            y_test,
            y_phys_test,
            config,
            seed=seed,
            stage1_epochs=stage1_epochs,
            stage2_epochs=stage2_epochs,
            baseline_epochs=baseline_epochs,
            ks_scale_train=ks_scale_train,
            ks_scale_test=ks_scale_test,
        )
        metrics["test_material"] = test_material
        metrics["train_size"] = int(len(X_train))
        metrics["test_size"] = int(len(X_test))
        metrics["physics_aware"] = bool(physics_aware)
        results_per_fold.append(metrics)

        print(
            f"    MAE = {metrics['mae']:.4f}, RMSE = {metrics['rmse']:.4f}, "
            f"R² = {metrics['r2']:.4f}, PCC = {metrics.get('pcc', 0):.4f}"
        )

        # Checkpoint 保存（方案 A）：每个 fold 完成后立即写入
        if ckpt_path:
            _save_checkpoint(ckpt_path, results_per_fold, n_materials)

    if not results_per_fold:
        return {"protocol": "LOMO", "model": model_name, "per_fold": [], "summary": {}}

    # === 所有 fold 完成，清理 checkpoint（方案 A）===
    if ckpt_path:
        _cleanup_checkpoint(ckpt_path)

    mae_list = [r["mae"] for r in results_per_fold]
    rmse_list = [r["rmse"] for r in results_per_fold]
    r2_list = [r["r2"] for r in results_per_fold]
    pcc_list = [r.get("pcc", 0.0) for r in results_per_fold]

    summary = {
        "protocol": "LOMO",
        "model": model_name,
        "n_folds": len(results_per_fold),
        "per_fold": results_per_fold,
        "summary": {
            "mae_mean": float(np.mean(mae_list)),
            "mae_std": float(np.std(mae_list)),
            "rmse_mean": float(np.mean(rmse_list)),
            "rmse_std": float(np.std(rmse_list)),
            "r2_mean": float(np.mean(r2_list)),
            "r2_std": float(np.std(r2_list)),
            "pcc_mean": float(np.mean(pcc_list)),
            "pcc_std": float(np.std(pcc_list)),
        },
    }
    return summary


# LOCO 协议


def run_loco_experiment(
    model_name: str,
    dataset: LomoLocoDataset,
    config: ExperimentConfig,
    seed: int = 42,
    stage1_epochs: int = 50,
    stage2_epochs: int = 100,
    baseline_epochs: int = 150,
    physics_aware: bool = True,
    output_dir: Optional[str] = None,
) -> Dict:
    """执行 LOCO (Leave-One-Condition-Out) 实验。

    Args:
        physics_aware: 是否启用物理引导 target 缩放（AR-02 修复，默认 True）。
        output_dir: 输出目录。传入则启用 fold 级 checkpoint（方案 A），
            进程崩溃重启后可从 checkpoint 续跑。None 则禁用 checkpoint。
    """
    set_global_seed(seed)
    conditions = dataset.conditions
    n_conditions = len(conditions)

    # === Checkpoint 加载（方案 A）===
    ckpt_path = _make_ckpt_path(output_dir, "LOCO", model_name, physics_aware) if output_dir else None
    completed_folds = _load_checkpoint(ckpt_path) if ckpt_path else []
    completed_conditions = {f["test_condition"] for f in completed_folds}

    results_per_fold = list(completed_folds)
    print(f"\n[LOCO] 模型: {model_name}, 工况数: {n_conditions}, physics_aware: {physics_aware}")
    if completed_folds:
        print(f"  [CKPT] 从 checkpoint 恢复 {len(completed_folds)} 个 fold: {sorted(completed_conditions)}")

    for i, test_cond in enumerate(conditions):
        # 跳过已完成的 fold（checkpoint 续跑）
        if test_cond in completed_conditions:
            print(f"  Fold {i + 1}/{n_conditions}: 留出 {test_cond} ... [CKPT 跳过]")
            continue

        print(f"  Fold {i + 1}/{n_conditions}: 留出 {test_cond} ...")

        train_mask = dataset.sample_conditions != test_cond
        test_mask = dataset.sample_conditions == test_cond

        X_train = dataset.data["features"][train_mask]
        y_train = dataset.data["a_lim"][train_mask]
        y_phys_train = dataset.data["a_lim_clean"][train_mask]
        X_test = dataset.data["features"][test_mask]
        y_test = dataset.data["a_lim"][test_mask]
        y_phys_test = dataset.data["a_lim_clean"][test_mask]

        # AR-02: 提取物理引导缩放因子
        ks_scale_train = dataset.data["sample_ks_scale"][train_mask] if physics_aware else None
        ks_scale_test = dataset.data["sample_ks_scale"][test_mask] if physics_aware else None

        if len(X_test) < 5:
            print(f"    [跳过] 测试集样本过少 ({len(X_test)})")
            continue

        metrics = train_and_evaluate(
            model_name,
            X_train,
            y_train,
            y_phys_train,
            X_test,
            y_test,
            y_phys_test,
            config,
            seed=seed,
            stage1_epochs=stage1_epochs,
            stage2_epochs=stage2_epochs,
            baseline_epochs=baseline_epochs,
            ks_scale_train=ks_scale_train,
            ks_scale_test=ks_scale_test,
        )
        metrics["test_condition"] = test_cond
        metrics["train_size"] = int(len(X_train))
        metrics["test_size"] = int(len(X_test))
        metrics["physics_aware"] = bool(physics_aware)
        results_per_fold.append(metrics)

        print(f"    MAE = {metrics['mae']:.4f}, PCC = {metrics.get('pcc', 0):.4f}")

        # Checkpoint 保存（方案 A）：每个 fold 完成后立即写入
        if ckpt_path:
            _save_checkpoint(ckpt_path, results_per_fold, n_conditions)

    if not results_per_fold:
        return {"protocol": "LOCO", "model": model_name, "per_fold": [], "summary": {}}

    # === 所有 fold 完成，清理 checkpoint（方案 A）===
    if ckpt_path:
        _cleanup_checkpoint(ckpt_path)

    mae_list = [r["mae"] for r in results_per_fold]
    pcc_list = [r.get("pcc", 0.0) for r in results_per_fold]

    summary = {
        "protocol": "LOCO",
        "model": model_name,
        "n_folds": len(results_per_fold),
        "per_fold": results_per_fold,
        "summary": {
            "mae_mean": float(np.mean(mae_list)),
            "mae_std": float(np.std(mae_list)),
            "pcc_mean": float(np.mean(pcc_list)),
            "pcc_std": float(np.std(pcc_list)),
        },
    }
    return summary


# 汇总与报告生成


def save_summary_csv(all_results: Dict, protocol: str, output_dir: str, file_suffix: str = "") -> str:
    """保存汇总表为 CSV（便于直接粘贴论文表格）。"""
    csv_path = os.path.join(output_dir, f"{protocol.lower()}_summary{file_suffix}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Model", "MAE_mean", "MAE_std", "RMSE_mean", "RMSE_std", "R2_mean", "R2_std", "PCC_mean", "PCC_std"]
        )
        for model_name, result in all_results.items():
            s = result.get("summary", {})
            writer.writerow(
                [
                    model_name,
                    f"{s.get('mae_mean', 0):.4f}",
                    f"{s.get('mae_std', 0):.4f}",
                    f"{s.get('rmse_mean', 0):.4f}",
                    f"{s.get('rmse_std', 0):.4f}",
                    f"{s.get('r2_mean', 0):.4f}",
                    f"{s.get('r2_std', 0):.4f}",
                    f"{s.get('pcc_mean', 0):.4f}",
                    f"{s.get('pcc_std', 0):.4f}",
                ]
            )
    return csv_path


def save_report_md(all_results: Dict, protocol: str, output_dir: str, dataset_name: str, file_suffix: str = "") -> str:
    """生成 Markdown 报告。"""
    md_path = os.path.join(output_dir, f"{protocol.lower()}_report{file_suffix}.md")
    physics_aware_note = ""
    # 从任一 per_fold 推断 physics_aware 状态
    for model_name, result in all_results.items():
        per_fold = result.get("per_fold", [])
        if per_fold:
            physics_aware_note = (
                " (physics_aware=ON, AR-02 修复)"
                if per_fold[0].get("physics_aware", False)
                else " (physics_aware=OFF, v2 基线)"
            )
            break

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {protocol} 跨工况泛化实验报告{physics_aware_note}\n\n")
        f.write(f"**数据集**: {dataset_name}\n\n")
        f.write(f"**协议**: {protocol}\n\n")
        f.write(f"**日期**: {np.datetime64('now', 'D')}\n\n")
        if physics_aware_note:
            f.write(
                "**AR-02 修复**: 启用物理引导 target 缩放 (ks_scale=(hardness/200)^0.8，与 Tlusty 物理模型一致)\n\n"
            )
        f.write("## 汇总结果\n\n")
        f.write("| 模型 | MAE (mean±std) | RMSE (mean±std) | R² (mean±std) | PCC (mean±std) |\n")
        f.write("|------|----------------|-----------------|---------------|----------------|\n")
        for model_name, result in all_results.items():
            s = result.get("summary", {})
            f.write(
                f"| {model_name} | "
                f"{s.get('mae_mean', 0):.4f}±{s.get('mae_std', 0):.4f} | "
                f"{s.get('rmse_mean', 0):.4f}±{s.get('rmse_std', 0):.4f} | "
                f"{s.get('r2_mean', 0):.4f}±{s.get('r2_std', 0):.4f} | "
                f"{s.get('pcc_mean', 0):.4f}±{s.get('pcc_std', 0):.4f} |\n"
            )
        f.write("\n## 各折详细结果\n\n")
        for model_name, result in all_results.items():
            f.write(f"### {model_name}\n\n")
            for fold in result.get("per_fold", []):
                f.write(
                    f"- 留出 {fold.get('test_material', fold.get('test_condition', 'N/A'))}: "
                    f"MAE={fold['mae']:.4f}, R²={fold['r2']:.4f}, "
                    f"PCC={fold.get('pcc', 0):.4f}\n"
                )
            f.write("\n")
    return md_path


def run_all_experiments(
    protocol: str,
    models: List[str],
    dataset_name: str,
    output_dir: str,
    seed: int = 42,
    samples_per_group: int = 200,
    stage1_epochs: int = 50,
    stage2_epochs: int = 100,
    baseline_epochs: int = 150,
    physics_aware: bool = True,
) -> Dict:
    """对所有模型执行 LOMO 或 LOCO 实验。

    Args:
        physics_aware: 是否启用物理引导 target 缩放（AR-02 修复，默认 True）。
            启用后输出目录会追加 _physics_aware 后缀以区分对照实验。
    """
    os.makedirs(output_dir, exist_ok=True)
    print("=" * 70)
    print(f"{protocol} 跨工况泛化实验（v2.1，AR-02 物理引导缩放={'ON' if physics_aware else 'OFF'}）")
    print(f"数据集: {dataset_name}")
    print(f"模型: {', '.join(models)}")
    print(f"训练轮数: DL-LNN stage1={stage1_epochs}, stage2={stage2_epochs}; 基线={baseline_epochs}")
    print(f"物理引导缩放: {'启用 (ks_scale=(H/200)^0.8)' if physics_aware else '禁用 (v2 原行为)'}")
    print("=" * 70)

    # 构造数据集
    dataset = LomoLocoDataset(
        samples_per_group=samples_per_group,
        seed=seed,
    )
    print(f"\n数据集统计: 总样本 {len(dataset)}, 材料 {len(dataset.materials)}, 工况 {len(dataset.conditions)}")

    # 获取配置
    config = get_config("lomo_loco_experiment")
    config.model.device = "cuda" if torch.cuda.is_available() else "cpu"

    all_results = {}
    for model_name in models:
        if protocol == "LOMO":
            result = run_lomo_experiment(
                model_name,
                dataset,
                config,
                seed=seed,
                stage1_epochs=stage1_epochs,
                stage2_epochs=stage2_epochs,
                baseline_epochs=baseline_epochs,
                physics_aware=physics_aware,
                output_dir=output_dir,  # 方案 A：启用 fold 级 checkpoint
            )
        else:
            result = run_loco_experiment(
                model_name,
                dataset,
                config,
                seed=seed,
                stage1_epochs=stage1_epochs,
                stage2_epochs=stage2_epochs,
                baseline_epochs=baseline_epochs,
                physics_aware=physics_aware,
                output_dir=output_dir,  # 方案 A：启用 fold 级 checkpoint
            )
        all_results[model_name] = result

    # 保存 JSON（文件名标记 physics_aware 状态）
    suffix = "_physics_aware" if physics_aware else "_baseline"
    output_file = os.path.join(output_dir, f"{protocol.lower()}_results{suffix}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[OK] 结果已保存至: {output_file}")

    # 保存 CSV
    csv_path = save_summary_csv(all_results, protocol, output_dir, file_suffix=suffix)
    print(f"[OK] 汇总表已保存至: {csv_path}")

    # 保存 Markdown 报告
    md_path = save_report_md(all_results, protocol, output_dir, dataset_name, file_suffix=suffix)
    print(f"[OK] Markdown 报告已保存至: {md_path}")

    # 打印汇总表
    print("\n" + "=" * 70)
    print(f"{protocol} 汇总结果")
    print("=" * 70)
    print(f"{'Model':<15} {'MAE (mean±std)':<22} {'R² (mean±std)':<22} {'PCC (mean±std)':<22}")
    print("-" * 81)
    for model_name in models:
        if model_name in all_results:
            s = all_results[model_name]["summary"]
            print(
                f"{model_name:<15} "
                f"{s['mae_mean']:.4f}±{s['mae_std']:.4f}       "
                f"{s.get('r2_mean', 0):.4f}±{s.get('r2_std', 0):.4f}       "
                f"{s.get('pcc_mean', 0):.4f}±{s.get('pcc_std', 0):.4f}"
            )

    return all_results


def main():
    parser = argparse.ArgumentParser(description="LOMO/LOCO 跨工况泛化实验（v2.1，AR-02 物理引导缩放）")
    parser.add_argument("--protocol", type=str, default="LOMO", choices=["LOMO", "LOCO"], help="评估协议")
    parser.add_argument("--models", type=str, nargs="+", default=ALL_MODELS, help="要评估的模型列表")
    parser.add_argument(
        "--dataset",
        type=str,
        default="synthetic_multi",
        choices=["synthetic_multi"],
        help="数据集名称（v2 仅支持 synthetic_multi）",
    )
    parser.add_argument("--output_dir", type=str, default="论文相关/脚本/results/lomo_loco", help="输出目录")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--samples_per_group", type=int, default=200, help="每种材料×工况的样本数")
    parser.add_argument("--stage1_epochs", type=int, default=50, help="DL-LNN 阶段一轮数（缩减以加速 LOMO）")
    parser.add_argument("--stage2_epochs", type=int, default=100, help="DL-LNN 阶段二轮数（缩减以加速 LOMO）")
    parser.add_argument("--baseline_epochs", type=int, default=150, help="非 sklearn 基线训练轮数")
    # AR-02 修复：物理引导 target 缩放开关
    parser.add_argument(
        "--physics_aware",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用物理引导 target 缩放（AR-02 修复，默认启用）。使用 --no-physics_aware 关闭以运行 v2 原行为对照实验。",
    )
    args = parser.parse_args()

    run_all_experiments(
        protocol=args.protocol,
        models=args.models,
        dataset_name=args.dataset,
        output_dir=args.output_dir,
        seed=args.seed,
        samples_per_group=args.samples_per_group,
        stage1_epochs=args.stage1_epochs,
        stage2_epochs=args.stage2_epochs,
        baseline_epochs=args.baseline_epochs,
        physics_aware=args.physics_aware,
    )


if __name__ == "__main__":
    main()
