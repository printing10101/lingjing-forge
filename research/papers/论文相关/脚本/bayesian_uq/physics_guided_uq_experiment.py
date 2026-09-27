"""
物理引导不确定性量化实验（阶段 2）
====================================

核心创新：
    在 MC Dropout 不确定性基础上，融合物理残差信息，
    构建更准确的不确定性估计。

    公式：uq_physics = sqrt(ltc_std² + λ² · residual²)
    其中：
        - ltc_std = MC Dropout 产生的 LTC 分支不确定性（认知不确定性）
        - residual = |ltc_pred - physics_pred|（LTC 分支与物理分支的预测分歧）
        - λ = 混合系数（在 ID 材料上通过最小化 ECE 学习）

物理意义：
    - ltc_std 捕捉模型的认知不确定性（"模型不知道自己不知道什么"）
    - residual 捕捉模型偏差（"LTC 预测偏离物理规律"）
    - 两者结合形成更完整的不确定性估计

预期效果：
    - ID 材料：LTC 准确，残差小，uq_physics ≈ ltc_std（校准良好）
    - OOD 材料：LTC 偏离，残差大，uq_physics 显著增大（更好匹配实际误差）

对比阶段 1：
    - 阶段 1：纯 MC Dropout，OOD AUC=0.7764，但校准失败（ECE=0.31）
    - 阶段 2：物理引导 UQ，预期 OOD AUC 保持/提升，校准改善

运行方式：
    cd 项目根目录
    python -u research/papers/论文相关/脚本/bayesian_uq/physics_guided_uq_experiment.py
"""

import os
import sys
import json
import time
import types
import warnings
from pathlib import Path
from typing import Dict, List, Tuple, Any

# WinSock 损坏绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。")

import numpy as np
import torch

warnings.filterwarnings("ignore")

# 路径设置
_current = Path(__file__).resolve()
PROJECT_ROOT = _current
for _ in range(6):
    if (PROJECT_ROOT / "research" / "training" / "reproducibility.py").exists():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
else:
    PROJECT_ROOT = _current.parents[5]

RESEARCH_DIR = PROJECT_ROOT / "research"
EXPERIMENTS_DIR = RESEARCH_DIR / "experiments"
ENGINEERING_PYTHON_DIR = PROJECT_ROOT / "engineering" / "python"

for p in [str(PROJECT_ROOT), str(ENGINEERING_PYTHON_DIR), str(RESEARCH_DIR), str(EXPERIMENTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

_lomo_script_dir = PROJECT_ROOT / "research" / "papers" / "论文相关" / "脚本"
if str(_lomo_script_dir) not in sys.path:
    sys.path.insert(0, str(_lomo_script_dir))

from training.reproducibility import set_global_seed
from experiments.data_generator import TlustyAnalyticalModel, build_physics_features_7d
from lomo_loco_experiment import MATERIALS_CONFIG, CONDITIONS_CONFIG, LomoLocoDataset
from bayesian_dllnn_wrapper import load_bayesian_dllnn
from bayesian_uq_experiment import (
    compute_ece,
    compute_coverage,
    compute_ood_detection_auc,
    compute_uq_error_correlation,
    plot_calibration,
    plot_ood_detection,
    plot_uq_error_scatter,
    _norm_ppf,
)
from temperature_scaling_calibration import compute_temperature_numerical


# 物理引导 UQ 核心


def compute_physics_residual(
    ltc_mean: np.ndarray,
    physics_pred: np.ndarray,
) -> np.ndarray:
    """计算物理残差 = |ltc_pred - physics_pred|。

    Args:
        ltc_mean: LTC 分支预测均值（原始尺度）[N]
        physics_pred: 物理分支预测（原始尺度）[N]

    Returns:
        residual: 物理残差 [N]
    """
    return np.abs(ltc_mean - physics_pred)


def compute_physics_guided_uq(
    ltc_std: np.ndarray,
    residual: np.ndarray,
    lam: float,
) -> np.ndarray:
    """计算物理引导不确定性。

    uq_pg = sqrt(ltc_std² + λ² · residual²)

    Args:
        ltc_std: MC Dropout 的 LTC 分支不确定性 [N]
        residual: 物理残差 [N]
        lam: 混合系数 λ

    Returns:
        uq_pg: 物理引导不确定性 [N]
    """
    return np.sqrt(ltc_std**2 + (lam**2) * (residual**2))


def compute_lambda_numerical(
    mean_pred: np.ndarray,
    ltc_std: np.ndarray,
    residual: np.ndarray,
    true_value: np.ndarray,
    n_bins: int = 10,
) -> Tuple[float, float]:
    """数值优化法求混合系数 λ（最小化 ECE）。

    Args:
        mean_pred: 预测均值 [N]
        ltc_std: LTC 分支不确定性 [N]
        residual: 物理残差 [N]
        true_value: 真实值 [N]
        n_bins: ECE 分桶数

    Returns:
        (λ, 最小 ECE)
    """
    from scipy.optimize import minimize_scalar

    def ece_of_lam(lam: float) -> float:
        uq = compute_physics_guided_uq(ltc_std, residual, lam)
        return compute_ece(mean_pred, uq, true_value, n_bins=n_bins)["ece"]

    # λ ∈ [0, 10]：0=纯MC Dropout，1=等权混合，>1=残差主导
    result = minimize_scalar(
        ece_of_lam,
        bounds=(0.0, 10.0),
        method="bounded",
        options={"xatol": 1e-4},
    )
    return float(result.x), float(result.fun)


# 可视化


def plot_uq_comparison(
    std_mc: np.ndarray,
    std_pg: np.ndarray,
    errors: np.ndarray,
    save_path: Path,
    lam: float,
) -> None:
    """绘制 MC Dropout UQ vs 物理引导 UQ 的散点对比。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # 左图：MC Dropout UQ vs Error
    ax = axes[0]
    ax.scatter(std_mc, errors, alpha=0.3, s=8, c="steelblue")
    ax.set_xlabel("MC Dropout std (ltc_std)")
    ax.set_ylabel("Absolute error")
    ax.set_title(f"Stage 1: MC Dropout UQ\n(Pearson r={np.corrcoef(std_mc, errors)[0, 1]:.4f})")
    ax.grid(True, alpha=0.3)
    if len(std_mc) > 10:
        z = np.polyfit(std_mc, errors, 1)
        p = np.poly1d(z)
        x_sorted = np.sort(std_mc)
        ax.plot(x_sorted, p(x_sorted), "r--", alpha=0.8)

    # 右图：Physics-guided UQ vs Error
    ax = axes[1]
    ax.scatter(std_pg, errors, alpha=0.3, s=8, c="darkgreen")
    ax.set_xlabel(f"Physics-guided UQ (λ={lam:.3f})")
    ax.set_ylabel("Absolute error")
    ax.set_title(f"Stage 2: Physics-guided UQ\n(Pearson r={np.corrcoef(std_pg, errors)[0, 1]:.4f})")
    ax.grid(True, alpha=0.3)
    if len(std_pg) > 10:
        z = np.polyfit(std_pg, errors, 1)
        p = np.poly1d(z)
        x_sorted = np.sort(std_pg)
        ax.plot(x_sorted, p(x_sorted), "r--", alpha=0.8)

    fig.suptitle("Uncertainty vs Error: Stage 1 vs Stage 2", fontsize=14, y=1.02)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图] {save_path}")


def plot_residual_distribution(
    residual_by_material: Dict[str, np.ndarray],
    id_mats: List[str],
    ood_mats: List[str],
    save_path: Path,
) -> None:
    """绘制各材料的物理残差分布。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    labels = id_mats + ood_mats
    data = [residual_by_material.get(m, np.array([])) for m in labels]
    data = [d[d > 0] if len(d) > 0 else np.array([0]) for d in data]
    colors = ["#4CAF50"] * len(id_mats) + ["#F44336"] * len(ood_mats)

    try:
        bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, widths=0.6)
    except TypeError:
        bp = ax.boxplot(data, labels=labels, patch_artist=True, widths=0.6)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax.set_ylabel("|ltc_pred - physics_pred| (mm)")
    ax.set_title("Physics Residual by Material\n(Green=ID, Red=OOD)")
    ax.grid(True, axis="y", alpha=0.3)

    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#4CAF50", alpha=0.7, label="ID"),
        Patch(facecolor="#F44336", alpha=0.7, label="OOD"),
    ]
    ax.legend(handles=legend_elements, loc="upper right")

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图] {save_path}")


# 主实验流程


def run_physics_guided_uq(
    weights_path: Path,
    output_dir: Path,
    n_samples: int = 100,
    mc_dropout_prob: float = 0.1,
    seed: int = 42,
) -> Dict[str, Any]:
    """运行物理引导 UQ 实验。

    流程：
        1. 加载模型，跑 MC Dropout 获取 ltc_mean, ltc_std
        2. 计算物理残差 residual = |ltc_mean - physics_pred|
        3. 在 ID 材料上学习 λ（最小化 ECE）
        4. 计算物理引导 UQ = sqrt(ltc_std² + λ²·residual²)
        5. 对比阶段 1（纯 MC Dropout）和阶段 2（物理引导 UQ）
        6. 生成报告与可视化
    """
    set_global_seed(seed)

    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("=" * 70)
    print("物理引导不确定性量化实验（阶段 2）")
    print("Physics-guided UQ: sqrt(ltc_std² + λ²·residual²)")
    print("=" * 70)
    print(f"设备: {device}")
    print(f"MC 采样次数: {n_samples}")
    print(f"MC Dropout 概率: {mc_dropout_prob}")
    print()

    # 1. 加载模型
    print("[1/7] 加载贝叶斯 DL-LNN 权重...")
    bayesian_model = load_bayesian_dllnn(
        weights_path=weights_path,
        device=device,
        mc_dropout_prob=mc_dropout_prob,
    )

    # 2. 生成测试集
    print("\n[2/7] 生成 LOMO 测试集...")
    set_global_seed(seed)
    dataset = LomoLocoDataset(
        samples_per_group=200,
        materials=list(MATERIALS_CONFIG.keys()),
        conditions=list(CONDITIONS_CONFIG.keys()),
        noise_level=0.02,
        seed=seed,
    )
    ks_scale = dataset.sample_ks_scale
    materials_arr = dataset.sample_materials
    print(f"  总样本数: {len(dataset)}")

    # 3. MC Dropout 推理
    print(f"\n[3/7] 对 5 种材料跑 MC Dropout (n_samples={n_samples})...")
    results_by_material: Dict[str, Dict] = {}

    for mat_name in MATERIALS_CONFIG.keys():
        mask = materials_arr == mat_name
        if mask.sum() == 0:
            continue

        X = dataset.data["features"][mask]
        y_true = dataset.data["a_lim"][mask]
        y_phys = dataset.data["a_lim_clean"][mask]
        ks = ks_scale[mask]

        print(f"\n  材料: {mat_name} (硬度={MATERIALS_CONFIG[mat_name]['hardness']:.0f} HB, 样本数={mask.sum()})")

        uq_result = bayesian_model.predict_batch(
            X,
            physics_pred=y_phys,
            n_samples=n_samples,
            device=device,
            batch_size=256,
            return_components=True,
        )

        # 反物理引导缩放
        mean_orig = uq_result["mean_denorm"].flatten() / ks
        std_orig = uq_result["std_denorm"].flatten() / ks
        ltc_mean_orig = uq_result["ltc_mean_denorm"].flatten() / ks
        ltc_std_orig = uq_result["ltc_std_denorm"].flatten() / ks

        # 物理残差（原始尺度）
        # 使用 final_pred（门控融合后的实际预测）而非 ltc_pred 计算残差。
        # 原因：final_pred 是模型的实际输出，其偏离物理分支的程度才是真正的
        # "模型偏差"。用 ltc_pred 会导致 ID 材料也有大残差（因为门控 α 小，
        # LTC 分支预测被忽略，但 LTC 本身偏离物理分支），使 ID/OOD 残差
        # 分离不足（约 2:1）。改用 final_pred 后，ID 材料残差小（final≈physics，
        # 因 α 小），OOD 材料残差大（α 更大，final 偏离 physics 更多），
        # 分离比预计提升至 30:1。
        residual = compute_physics_residual(mean_orig, y_phys)

        mae = float(np.mean(np.abs(mean_orig - y_true)))
        ltc_mae = float(np.mean(np.abs(ltc_mean_orig - y_true)))
        residual_mean = float(np.mean(residual))

        print(f"    final MAE={mae:.4f}, ltc MAE={ltc_mae:.4f}")
        print(f"    ltc_std_mean={float(np.mean(ltc_std_orig)):.4f}")
        print(f"    residual_mean={residual_mean:.4f}")

        results_by_material[mat_name] = {
            "n_samples": int(mask.sum()),
            "hardness": MATERIALS_CONFIG[mat_name]["hardness"],
            "y_true": y_true,
            "mean_pred": mean_orig,
            "std_pred": std_orig,  # final_pred 的 std（阶段1用）
            "ltc_mean": ltc_mean_orig,
            "ltc_std": ltc_std_orig,  # LTC 分支的 std（阶段2用）
            "residual": residual,
            "y_phys": y_phys,
            "metrics": {
                "mae": mae,
                "ltc_mae": ltc_mae,
                "residual_mean": residual_mean,
                "ltc_std_mean": float(np.mean(ltc_std_orig)),
            },
        }

    # 4. 学习混合系数 λ
    print("\n[4/7] 学习混合系数 λ (在 ID 材料上)...")

    id_mats = ["45_Steel", "304_SS"]
    ood_mats = ["6061-T6"]

    id_mean = np.concatenate([results_by_material[m]["mean_pred"] for m in id_mats])
    id_ltc_std = np.concatenate([results_by_material[m]["ltc_std"] for m in id_mats])
    id_residual = np.concatenate([results_by_material[m]["residual"] for m in id_mats])
    id_true = np.concatenate([results_by_material[m]["y_true"] for m in id_mats])

    lam, min_ece_id = compute_lambda_numerical(id_mean, id_ltc_std, id_residual, id_true, n_bins=10)
    print(f"  最优 λ = {lam:.4f} (ID 材料上 ECE = {min_ece_id:.4f})")

    # 同时学习温度参数 T（用于校准 ltc_std 的绝对值）
    T_mc, ece_mc_id = compute_temperature_numerical(id_mean, id_ltc_std, id_true, n_bins=10)
    print(f"  纯 MC Dropout 的最优 T = {T_mc:.4f} (ID ECE = {ece_mc_id:.4f})")

    # 物理引导 UQ 是否还需要温度缩放？
    # 检查物理引导 UQ 在 ID 上的 ECE（不加温度缩放）
    id_uq_pg_raw = compute_physics_guided_uq(id_ltc_std, id_residual, lam)
    ece_pg_raw_id = compute_ece(id_mean, id_uq_pg_raw, id_true)["ece"]
    print(f"  物理引导 UQ（无温度缩放）ID ECE = {ece_pg_raw_id:.4f}")

    # 对物理引导 UQ 也做温度缩放
    # 但如果温度缩放后 ECE 比原始差（优化器到达搜索上限），则跳过温度缩放
    T_pg, ece_pg_id = compute_temperature_numerical(id_mean, id_uq_pg_raw, id_true, n_bins=10)
    if ece_pg_id > ece_pg_raw_id:
        print(
            f"  [警告] 温度缩放使 ECE 变差 ({ece_pg_raw_id:.4f} → {ece_pg_id:.4f})，"
            f"T_pg={T_pg:.2f} 达到搜索上限，跳过温度缩放 (T_pg=1.0)"
        )
        T_pg = 1.0
        ece_pg_id = ece_pg_raw_id
    print(f"  物理引导 UQ + 温度缩放 T_pg = {T_pg:.4f} (ID ECE = {ece_pg_id:.4f})")

    print(f"\n  最终参数: λ = {lam:.4f}, T_pg = {T_pg:.4f}")
    print(f"  公式: uq_final = {T_pg:.4f} × sqrt(ltc_std² + {lam:.4f}² × residual²)")

    # 5. 计算阶段 1 和阶段 2 的全局指标
    print("\n[5/7] 计算阶段 1 (纯 MC Dropout) 和阶段 2 (物理引导 UQ) 的对比指标...")

    all_mean = np.concatenate([results_by_material[m]["mean_pred"] for m in results_by_material])
    all_true = np.concatenate([results_by_material[m]["y_true"] for m in results_by_material])
    all_ltc_std = np.concatenate([results_by_material[m]["ltc_std"] for m in results_by_material])
    all_residual = np.concatenate([results_by_material[m]["residual"] for m in results_by_material])

    # 阶段 1：纯 MC Dropout（ltc_std × T_mc）
    std_stage1 = all_ltc_std * T_mc

    # 阶段 2：物理引导 UQ（sqrt(ltc_std² + λ²·residual²) × T_pg）
    uq_pg_raw = compute_physics_guided_uq(all_ltc_std, all_residual, lam)
    std_stage2 = uq_pg_raw * T_pg

    # 阶段 1 指标
    ece_s1 = compute_ece(all_mean, std_stage1, all_true)
    coverage_s1 = compute_coverage(all_mean, std_stage1, all_true)
    corr_s1 = compute_uq_error_correlation(all_mean, std_stage1, all_true)

    std_by_mat_s1 = {m: results_by_material[m]["ltc_std"] * T_mc for m in results_by_material}
    ood_s1 = compute_ood_detection_auc(std_by_mat_s1, id_mats, ood_mats)

    # 阶段 2 指标
    ece_s2 = compute_ece(all_mean, std_stage2, all_true)
    coverage_s2 = compute_coverage(all_mean, std_stage2, all_true)
    corr_s2 = compute_uq_error_correlation(all_mean, std_stage2, all_true)

    std_by_mat_s2 = {
        m: compute_physics_guided_uq(results_by_material[m]["ltc_std"], results_by_material[m]["residual"], lam) * T_pg
        for m in results_by_material
    }
    ood_s2 = compute_ood_detection_auc(std_by_mat_s2, id_mats, ood_mats)

    # 打印对比
    print(f"\n  {'指标':<25} {'阶段1(MC Dropout)':>18} {'阶段2(物理引导)':>18} {'变化':>12}")
    print("  " + "-" * 75)
    print(f"  {'ECE':<25} {ece_s1['ece']:>18.4f} {ece_s2['ece']:>18.4f} {(ece_s2['ece'] - ece_s1['ece']):>+12.4f}")
    print(f"  {'MCE':<25} {ece_s1['mce']:>18.4f} {ece_s2['mce']:>18.4f} {(ece_s2['mce'] - ece_s1['mce']):>+12.4f}")
    for level in [80, 90, 95, 99]:
        b = coverage_s1[f"coverage_{level}"]
        a = coverage_s2[f"coverage_{level}"]
        print(f"  {'Coverage ' + str(level) + '%':<25} {b:>18.4f} {a:>18.4f} {(a - b):>+12.4f}")
    print(
        f"  {'Spearman 相关':<25} {corr_s1['spearman_corr']:>18.4f} "
        f"{corr_s2['spearman_corr']:>18.4f} "
        f"{(corr_s2['spearman_corr'] - corr_s1['spearman_corr']):>+12.4f}"
    )
    print(
        f"  {'Pearson 相关':<25} {corr_s1['pearson_corr']:>18.4f} "
        f"{corr_s2['pearson_corr']:>18.4f} "
        f"{(corr_s2['pearson_corr'] - corr_s1['pearson_corr']):>+12.4f}"
    )
    print(
        f"  {'OOD AUC':<25} {ood_s1['auc_roc']:>18.4f} "
        f"{ood_s2['auc_roc']:>18.4f} "
        f"{(ood_s2['auc_roc'] - ood_s1['auc_roc']):>+12.4f}"
    )
    print(
        f"  {'分离比 (OOD/ID std)':<25} {ood_s1['separation_ratio']:>18.4f} "
        f"{ood_s2['separation_ratio']:>18.4f} "
        f"{(ood_s2['separation_ratio'] - ood_s1['separation_ratio']):>+12.4f}"
    )

    # 6. 各材料详细对比
    print("\n[6/7] 各材料详细对比...")
    print(f"\n  {'材料':<12} {'MAE':>8} {'残差均值':>10} {'std_s1':>10} {'std_s2':>10} {'变化':>10}")
    print("  " + "-" * 65)
    for mat in results_by_material:
        r = results_by_material[mat]
        m = r["metrics"]
        s1 = float(np.mean(std_by_mat_s1[mat]))
        s2 = float(np.mean(std_by_mat_s2[mat]))
        print(f"  {mat:<12} {m['mae']:>8.4f} {m['residual_mean']:>10.4f} {s1:>10.4f} {s2:>10.4f} {(s2 - s1):>+10.4f}")

    # 7. 可视化
    print("\n[7/7] 生成可视化图表...")

    # 校准曲线对比
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    confidence_levels = np.linspace(0.05, 0.99, 20)
    actuals_s1, actuals_s2 = [], []
    for c in confidence_levels:
        k = _norm_ppf((1 + c) / 2)
        lower_s1 = all_mean - k * std_stage1
        upper_s1 = all_mean + k * std_stage1
        actuals_s1.append(np.mean((all_true >= lower_s1) & (all_true <= upper_s1)))
        lower_s2 = all_mean - k * std_stage2
        upper_s2 = all_mean + k * std_stage2
        actuals_s2.append(np.mean((all_true >= lower_s2) & (all_true <= upper_s2)))

    fig, ax = plt.subplots(figsize=(8, 7))
    ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration", linewidth=1.5)
    ax.plot(
        confidence_levels,
        actuals_s1,
        "r-o",
        markersize=5,
        label=f"Stage 1: MC Dropout (ECE={ece_s1['ece']:.4f})",
        alpha=0.8,
    )
    ax.plot(
        confidence_levels,
        actuals_s2,
        "g-s",
        markersize=5,
        label=f"Stage 2: Physics-guided (ECE={ece_s2['ece']:.4f})",
        alpha=0.8,
    )
    ax.set_xlabel("Nominal confidence level", fontsize=12)
    ax.set_ylabel("Actual coverage", fontsize=12)
    ax.set_title("Calibration: Stage 1 vs Stage 2", fontsize=13)
    ax.legend(fontsize=10, loc="upper left")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(figures_dir / "stage_comparison_calibration.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图] {figures_dir / 'stage_comparison_calibration.png'}")

    # UQ-Error 散点对比
    errors = np.abs(all_mean - all_true).flatten()
    plot_uq_comparison(std_stage1, std_stage2, errors, figures_dir / "stage_comparison_uq_error.png", lam)

    # 物理残差分布
    residual_by_mat = {m: results_by_material[m]["residual"] for m in results_by_material}
    plot_residual_distribution(residual_by_mat, id_mats, ood_mats, figures_dir / "physics_residual_distribution.png")

    # 阶段 2 的 OOD 检测箱线图
    plot_ood_detection(std_by_mat_s2, id_mats, ood_mats, figures_dir / "ood_detection_stage2.png")

    # 汇总结果
    full_result = {
        "experiment": "Physics-guided UQ (Stage 2)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "n_samples": n_samples,
            "mc_dropout_prob": mc_dropout_prob,
            "seed": seed,
            "device": device,
            "weights_path": str(weights_path),
            "id_materials": id_mats,
            "ood_materials": ood_mats,
        },
        "parameters": {
            "lambda": lam,
            "T_mc": T_mc,
            "T_pg": T_pg,
            "id_ece_mc_only": ece_mc_id,
            "id_ece_pg_raw": ece_pg_raw_id,
            "id_ece_pg_calibrated": ece_pg_id,
        },
        "stage1_mc_dropout": {
            "ece": ece_s1,
            "coverage": coverage_s1,
            "uq_error_correlation": corr_s1,
            "ood_detection": ood_s1,
        },
        "stage2_physics_guided": {
            "ece": ece_s2,
            "coverage": coverage_s2,
            "uq_error_correlation": corr_s2,
            "ood_detection": ood_s2,
        },
        "materials_summary": {
            m: {
                "hardness": results_by_material[m]["hardness"],
                "n_samples": results_by_material[m]["n_samples"],
                "metrics": results_by_material[m]["metrics"],
                "std_mean_s1": float(np.mean(std_by_mat_s1[m])),
                "std_mean_s2": float(np.mean(std_by_mat_s2[m])),
            }
            for m in results_by_material
        },
    }

    # 保存 JSON
    json_path = output_dir / "physics_guided_uq_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_result, f, indent=2, ensure_ascii=False)
    print(f"\n[已保存] {json_path}")

    # 生成报告
    report_path = output_dir / "physics_guided_uq_report.md"
    _generate_report(full_result, report_path)
    print(f"[已保存] {report_path}")

    # 成功判据检查
    print("\n" + "=" * 70)
    print("阶段 2 成功判据检查")
    print("=" * 70)
    checks = [
        ("ECE < 0.10", ece_s2["ece"] < 0.10),
        ("95% 覆盖率 ∈ [0.90, 0.97]", 0.90 <= coverage_s2["coverage_95"] <= 0.97),
        ("OOD AUC > 0.7", ood_s2["auc_roc"] > 0.7),
        ("分离比 > 2", ood_s2["separation_ratio"] > 2.0),
        ("Spearman 相关 > 0.3", corr_s2["spearman_corr"] > 0.3),
        ("阶段2 ECE < 阶段1 ECE", ece_s2["ece"] < ece_s1["ece"]),
        ("阶段2 Spearman > 阶段1", corr_s2["spearman_corr"] > corr_s1["spearman_corr"]),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✓ 通过" if passed else "✗ 未通过"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print(">>> 阶段 2 全部判据通过！物理引导 UQ 显著优于纯 MC Dropout")
    else:
        print(">>> 部分判据未通过，需进一步分析")

    return full_result


def _generate_report(full_result: Dict, report_path: Path) -> None:
    """生成 Markdown 报告。"""
    s1 = full_result["stage1_mc_dropout"]
    s2 = full_result["stage2_physics_guided"]
    p = full_result["parameters"]

    lines = [
        "# 物理引导不确定性量化实验报告（阶段 2）\n",
        f"**实验时间**: {full_result['timestamp']}\n",
        f"**MC 采样次数**: {full_result['config']['n_samples']}\n",
        f"**MC Dropout 概率**: {full_result['config']['mc_dropout_prob']}\n",
        "\n## 1. 核心参数\n",
        f"- **混合系数 λ**: {p['lambda']:.4f}",
        f"- **阶段1温度 T_mc**: {p['T_mc']:.4f}",
        f"- **阶段2温度 T_pg**: {p['T_pg']:.4f}",
        f"- **公式**: uq = {p['T_pg']:.4f} × sqrt(ltc_std² + {p['lambda']:.4f}² × residual²)",
        f"- **ID 材料上纯 MC Dropout ECE**: {p['id_ece_mc_only']:.4f}",
        f"- **ID 材料上物理引导 UQ (无校准) ECE**: {p['id_ece_pg_raw']:.4f}",
        f"- **ID 材料上物理引导 UQ (校准后) ECE**: {p['id_ece_pg_calibrated']:.4f}",
        "\n## 2. 阶段 1 vs 阶段 2 全局对比\n",
        "### 2.1 校准误差",
        "| 指标 | 阶段1 (MC Dropout) | 阶段2 (物理引导) | 变化 |",
        "|------|-------------------|------------------|------|",
        f"| ECE | {s1['ece']['ece']:.4f} | {s2['ece']['ece']:.4f} | {s2['ece']['ece'] - s1['ece']['ece']:+.4f} |",
        f"| MCE | {s1['ece']['mce']:.4f} | {s2['ece']['mce']:.4f} | {s2['ece']['mce'] - s1['ece']['mce']:+.4f} |",
        "\n### 2.2 置信区间覆盖率",
        "| 名义水平 | 阶段1 | 阶段2 | 变化 |",
        "|---------|-------|-------|------|",
    ]

    for level in [80, 90, 95, 99]:
        b = s1["coverage"][f"coverage_{level}"]
        a = s2["coverage"][f"coverage_{level}"]
        lines.append(f"| {level}% | {b:.4f} | {a:.4f} | {a - b:+.4f} |")

    lines.extend(
        [
            "\n### 2.3 不确定性-误差相关性",
            "| 指标 | 阶段1 | 阶段2 | 变化 |",
            "|------|-------|-------|------|",
            f"| Spearman 相关 | {s1['uq_error_correlation']['spearman_corr']:.4f} | "
            f"{s2['uq_error_correlation']['spearman_corr']:.4f} | "
            f"{s2['uq_error_correlation']['spearman_corr'] - s1['uq_error_correlation']['spearman_corr']:+.4f} |",
            f"| Pearson 相关 | {s1['uq_error_correlation']['pearson_corr']:.4f} | "
            f"{s2['uq_error_correlation']['pearson_corr']:.4f} | "
            f"{s2['uq_error_correlation']['pearson_corr'] - s1['uq_error_correlation']['pearson_corr']:+.4f} |",
            f"| UQ-Error 比值 | {s1['uq_error_correlation']['uq_error_ratio']:.4f} | "
            f"{s2['uq_error_correlation']['uq_error_ratio']:.4f} | "
            f"{s2['uq_error_correlation']['uq_error_ratio'] - s1['uq_error_correlation']['uq_error_ratio']:+.4f} |",
            "\n### 2.4 OOD 检测能力",
            "| 指标 | 阶段1 | 阶段2 | 变化 |",
            "|------|-------|-------|------|",
            f"| ROC AUC | {s1['ood_detection']['auc_roc']:.4f} | "
            f"{s2['ood_detection']['auc_roc']:.4f} | "
            f"{s2['ood_detection']['auc_roc'] - s1['ood_detection']['auc_roc']:+.4f} |",
            f"| 分离比 (OOD/ID) | {s1['ood_detection']['separation_ratio']:.4f} | "
            f"{s2['ood_detection']['separation_ratio']:.4f} | "
            f"{s2['ood_detection']['separation_ratio'] - s1['ood_detection']['separation_ratio']:+.4f} |",
            "\n## 3. 各材料详细指标\n",
            "| 材料 | 硬度(HB) | MAE | 残差均值 | std_s1 | std_s2 | 变化 |",
            "|------|---------|-----|---------|--------|--------|------|",
        ]
    )

    for mat, data in full_result["materials_summary"].items():
        m = data["metrics"]
        s1_val = data["std_mean_s1"]
        s2_val = data["std_mean_s2"]
        lines.append(
            f"| {mat} | {data['hardness']:.0f} | {m['mae']:.4f} | "
            f"{m['residual_mean']:.4f} | {s1_val:.4f} | {s2_val:.4f} | {s2_val - s1_val:+.4f} |"
        )

    lines.extend(
        [
            "\n## 4. 可视化图表\n",
            "- 阶段对比校准曲线: `figures/stage_comparison_calibration.png`",
            "- 阶段对比 UQ-Error 散点: `figures/stage_comparison_uq_error.png`",
            "- 物理残差分布: `figures/physics_residual_distribution.png`",
            "- 阶段2 OOD 检测箱线图: `figures/ood_detection_stage2.png`",
            "\n## 5. 结论\n",
        ]
    )

    # 自动结论
    if s2["ece"]["ece"] < s1["ece"]["ece"]:
        lines.append(f"- ✓ 物理引导 UQ 改善了校准 (ECE: {s1['ece']['ece']:.4f} → {s2['ece']['ece']:.4f})")
    else:
        lines.append(f"- ✗ 物理引导 UQ 未改善校准 (ECE: {s1['ece']['ece']:.4f} → {s2['ece']['ece']:.4f})")

    if s2["uq_error_correlation"]["spearman_corr"] > s1["uq_error_correlation"]["spearman_corr"]:
        lines.append(
            f"- ✓ Spearman 相关性改善 ({s1['uq_error_correlation']['spearman_corr']:.4f} → "
            f"{s2['uq_error_correlation']['spearman_corr']:.4f})"
        )
    else:
        lines.append(
            f"- ✗ Spearman 相关性未改善 ({s1['uq_error_correlation']['spearman_corr']:.4f} → "
            f"{s2['uq_error_correlation']['spearman_corr']:.4f})"
        )

    if s2["ood_detection"]["auc_roc"] >= s1["ood_detection"]["auc_roc"]:
        lines.append(
            f"- ✓ OOD 检测能力保持/提升 (AUC: {s1['ood_detection']['auc_roc']:.4f} → "
            f"{s2['ood_detection']['auc_roc']:.4f})"
        )
    else:
        lines.append(
            f"- ✗ OOD 检测能力下降 (AUC: {s1['ood_detection']['auc_roc']:.4f} → {s2['ood_detection']['auc_roc']:.4f})"
        )

    lines.append(f"\n**物理意义**: 混合系数 λ = {p['lambda']:.4f} 平衡了 MC Dropout 不确定性 与物理残差的贡献。")
    lines.append(
        f"**核心创新**: 物理残差 |ltc_pred - physics_pred| 提供了独立于 Dropout 的 "
        f"不确定性信号，当 LTC 分支偏离物理规律时自动增大不确定性。"
    )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# 入口


def main():
    import argparse

    parser = argparse.ArgumentParser(description="物理引导 UQ 实验（阶段 2）")
    parser.add_argument(
        "--weights",
        type=str,
        default=str(Path(__file__).parent / "results" / "full_weights.pt"),
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(Path(__file__).parent / "results"),
    )
    parser.add_argument("--n_samples", type=int, default=100)
    parser.add_argument("--mc_dropout_prob", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    weights_path = Path(args.weights)
    if not weights_path.exists():
        print(f"[错误] 权重文件不存在: {weights_path}")
        sys.exit(1)

    run_physics_guided_uq(
        weights_path=weights_path,
        output_dir=Path(args.output_dir),
        n_samples=args.n_samples,
        mc_dropout_prob=args.mc_dropout_prob,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
