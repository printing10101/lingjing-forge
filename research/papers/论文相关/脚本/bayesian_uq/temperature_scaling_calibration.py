"""
温度缩放校准（Temperature Scaling for Regression UQ）
=====================================================

针对版本 A 实验结果（ECE=0.5446，95% 覆盖率=0.0008）进行校准。

核心问题：
    MC Dropout 产生的 std 绝对值过小（~1e-3），
    导致置信区间过窄，覆盖率远低于名义水平。
    模型"过度自信"。

校准方法：
    σ_calibrated = T · σ_original
    其中 T 是在 ID 材料上学习的温度参数。

    T 的学习目标：使 ID 材料的 95% 覆盖率接近 95%。
    解析解：T = quantile(|y_true - μ| / σ, 0.95) / z_{0.975}
    其中 z_{0.975} = 1.960（标准正态分布的 97.5% 分位数）

    为鲁棒性，同时用数值优化最小化 ECE，取两者中效果更好者。

关键原则：
    - 只在 ID 材料（45_Steel, 304_SS）上学习 T，避免 OOD 污染
    - 应用到所有材料评估校准后效果
    - OOD 检测能力应保持不变（T 是全局缩放，不改变 std 的相对排序）

运行方式：
    cd 项目根目录
    python -u research/papers/论文相关/脚本/bayesian_uq/temperature_scaling_calibration.py

输出：
    results/calibrated_uq_results.json   - 校准前后对比
    results/calibrated_uq_report.md      - 校准报告
    results/figures/calibration_before_after.png  - 校准前后对比曲线
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


# 温度缩放核心


def compute_temperature_analytic(
    mean_pred: np.ndarray,
    std_pred: np.ndarray,
    true_value: np.ndarray,
    target_confidence: float = 0.95,
) -> float:
    """解析法求温度参数 T。

    目标：使置信水平为 target_confidence 的区间覆盖率达到 target_confidence。

    设 r_i = |y_true_i - μ_i| / σ_i，则 |y_true - μ| ≤ z · T · σ 等价于
    r_i ≤ z · T，即 T = quantile(r, target_confidence) / z。

    Args:
        mean_pred: 预测均值 [N]
        std_pred: 预测标准差 [N]
        true_value: 真实值 [N]
        target_confidence: 目标置信水平（默认 0.95）

    Returns:
        温度参数 T
    """
    errors = np.abs(mean_pred - true_value).flatten()
    stds = std_pred.flatten()
    # 避免 std=0 导致除零
    ratios = errors / np.maximum(stds, 1e-10)
    z = _norm_ppf((1 + target_confidence) / 2)
    T = float(np.quantile(ratios, target_confidence)) / z
    return max(T, 1e-6)


def compute_temperature_numerical(
    mean_pred: np.ndarray,
    std_pred: np.ndarray,
    true_value: np.ndarray,
    n_bins: int = 10,
) -> Tuple[float, float]:
    """数值优化法求温度参数 T（最小化 ECE）。

    Args:
        mean_pred, std_pred, true_value: 同上
        n_bins: ECE 分桶数

    Returns:
        (T, 最小 ECE)
    """
    from scipy.optimize import minimize_scalar

    def ece_of_T(T: float) -> float:
        scaled_std = std_pred * T
        return compute_ece(mean_pred, scaled_std, true_value, n_bins=n_bins)["ece"]

    # 在 [0.1, 10000] 范围内搜索
    result = minimize_scalar(
        ece_of_T,
        bounds=(0.1, 10000.0),
        method="bounded",
        options={"xatol": 1e-3},
    )
    return float(result.x), float(result.fun)


def apply_temperature(
    std_pred: np.ndarray,
    T: float,
) -> np.ndarray:
    """应用温度缩放。"""
    return std_pred * T


# 校准前后对比可视化


def plot_calibration_comparison(
    mean_pred: np.ndarray,
    std_before: np.ndarray,
    std_after: np.ndarray,
    true_value: np.ndarray,
    save_path: Path,
    T: float,
) -> None:
    """绘制校准前后的校准曲线对比。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    confidence_levels = np.linspace(0.05, 0.99, 20)

    def compute_actuals(std):
        actuals = []
        for c in confidence_levels:
            k = _norm_ppf((1 + c) / 2)
            lower = mean_pred - k * std
            upper = mean_pred + k * std
            actual = np.mean((true_value >= lower) & (true_value <= upper))
            actuals.append(actual)
        return actuals

    actuals_before = compute_actuals(std_before)
    actuals_after = compute_actuals(std_after)

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration", linewidth=1.5)
    ax.plot(
        confidence_levels,
        actuals_before,
        "r-o",
        markersize=5,
        label=f"Before calibration (ECE={compute_ece(mean_pred, std_before, true_value)['ece']:.4f})",
        alpha=0.8,
    )
    ax.plot(
        confidence_levels,
        actuals_after,
        "g-s",
        markersize=5,
        label=f"After T={T:.2f} (ECE={compute_ece(mean_pred, std_after, true_value)['ece']:.4f})",
        alpha=0.8,
    )
    ax.set_xlabel("Nominal confidence level", fontsize=12)
    ax.set_ylabel("Actual coverage", fontsize=12)
    ax.set_title("Calibration Curve: Before vs After Temperature Scaling", fontsize=13)
    ax.legend(fontsize=10, loc="upper left")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图] {save_path}")


def plot_coverage_comparison(
    mean_pred: np.ndarray,
    std_before: np.ndarray,
    std_after: np.ndarray,
    true_value: np.ndarray,
    save_path: Path,
    T: float,
) -> None:
    """绘制校准前后覆盖率对比柱状图。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    levels = [0.80, 0.90, 0.95, 0.99]
    ks = [1.282, 1.645, 1.960, 2.576]

    def get_actuals(std):
        return [float(np.mean((true_value >= mean_pred - k * std) & (true_value <= mean_pred + k * std))) for k in ks]

    actuals_before = get_actuals(std_before)
    actuals_after = get_actuals(std_after)

    x = np.arange(len(levels))
    width = 0.27

    fig, ax = plt.subplots(figsize=(9, 6))
    bars_nominal = ax.bar(x - width, levels, width, label="Nominal", color="#888888", alpha=0.7)
    bars_before = ax.bar(x, actuals_before, width, label="Before", color="#F44336", alpha=0.8)
    bars_after = ax.bar(x + width, actuals_after, width, label=f"After (T={T:.2f})", color="#4CAF50", alpha=0.8)

    ax.set_xlabel("Confidence level", fontsize=12)
    ax.set_ylabel("Actual coverage", fontsize=12)
    ax.set_title("Coverage Comparison: Before vs After Temperature Scaling", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(l * 100)}%" for l in levels])
    ax.legend(fontsize=10)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(0, 1.05)

    # 在柱子上方添加数值标签
    for bars in [bars_before, bars_after]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h + 0.01, f"{h:.3f}", ha="center", va="bottom", fontsize=9)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图] {save_path}")


# 主校准流程


def run_calibration(
    weights_path: Path,
    output_dir: Path,
    n_samples: int = 100,
    mc_dropout_prob: float = 0.1,
    seed: int = 42,
) -> Dict[str, Any]:
    """运行温度缩放校准实验。

    流程：
        1. 重新跑 MC Dropout 获取 per-sample 预测
        2. 在 ID 材料（45_Steel, 304_SS）上学习温度 T
        3. 应用 T 到所有材料
        4. 对比校准前后的 ECE、覆盖率、OOD 检测能力
        5. 生成报告与可视化
    """
    set_global_seed(seed)

    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("=" * 70)
    print("温度缩放校准实验（Temperature Scaling for Bayesian DL-LNN UQ）")
    print("=" * 70)
    print(f"设备: {device}")
    print(f"MC 采样次数: {n_samples}")
    print(f"MC Dropout 概率: {mc_dropout_prob}")
    print()

    # 1. 加载贝叶斯模型
    print("[1/6] 加载贝叶斯 DL-LNN 权重...")
    bayesian_model = load_bayesian_dllnn(
        weights_path=weights_path,
        device=device,
        mc_dropout_prob=mc_dropout_prob,
    )

    # 2. 生成 LOMO 测试集
    print("\n[2/6] 生成 LOMO 测试集...")
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

    # 3. 对每种材料跑 MC Dropout
    print(f"\n[3/6] 对 5 种材料跑 MC Dropout (n_samples={n_samples})...")
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

        mean_denorm = uq_result["mean_denorm"].flatten()
        std_denorm = uq_result["std_denorm"].flatten()

        # 反物理引导缩放
        mean_orig = mean_denorm / ks
        std_orig = std_denorm / ks

        mae = float(np.mean(np.abs(mean_orig - y_true)))
        rmse = float(np.sqrt(np.mean((mean_orig - y_true) ** 2)))
        std_mean = float(np.mean(std_orig))

        print(f"    MAE={mae:.4f}, RMSE={rmse:.4f}, std_mean={std_mean:.4f}")

        results_by_material[mat_name] = {
            "n_samples": int(mask.sum()),
            "hardness": MATERIALS_CONFIG[mat_name]["hardness"],
            "y_true": y_true,
            "mean_pred": mean_orig,
            "std_pred": std_orig,
            "y_phys": y_phys,
            "metrics": {
                "mae": mae,
                "rmse": rmse,
                "std_mean": std_mean,
            },
        }

    # 4. 学习温度参数 T
    print("\n[4/6] 学习温度参数 T (在 ID 材料上)...")

    id_mats = ["45_Steel", "304_SS"]
    ood_mats = ["6061-T6"]  # 仅保留低硬度 OOD 材料

    id_mean = np.concatenate([results_by_material[m]["mean_pred"] for m in id_mats])
    id_std = np.concatenate([results_by_material[m]["std_pred"] for m in id_mats])
    id_true = np.concatenate([results_by_material[m]["y_true"] for m in id_mats])

    # 方法1：解析法（基于 95% 覆盖率）
    T_analytic = compute_temperature_analytic(id_mean, id_std, id_true, target_confidence=0.95)
    print(f"  解析法 T (95% 覆盖率匹配): {T_analytic:.4f}")

    # 方法2：数值优化（最小化 ECE）
    T_numerical, min_ece = compute_temperature_numerical(id_mean, id_std, id_true, n_bins=10)
    print(f"  数值法 T (最小化 ECE={min_ece:.4f}): {T_numerical:.4f}")

    # 选择 ECE 更小的 T
    ece_analytic = compute_ece(id_mean, id_std * T_analytic, id_true)["ece"]
    print(f"  解析法在 ID 上的 ECE: {ece_analytic:.4f}")
    print(f"  数值法在 ID 上的 ECE: {min_ece:.4f}")

    if min_ece <= ece_analytic:
        T = T_numerical
        T_method = "numerical (min ECE)"
        print(f"  → 选择数值法 T = {T:.4f}")
    else:
        T = T_analytic
        T_method = "analytic (95% coverage match)"
        print(f"  → 选择解析法 T = {T:.4f}")

    print(f"\n  最终温度参数 T = {T:.4f} (方法: {T_method})")
    print(f"  含义: σ_calibrated = {T:.4f} × σ_original")

    # 5. 应用校准并对比
    print("\n[5/6] 应用校准并对比前后指标...")

    # 合并所有材料
    all_mean_before = np.concatenate([results_by_material[m]["mean_pred"] for m in results_by_material])
    all_std_before = np.concatenate([results_by_material[m]["std_pred"] for m in results_by_material])
    all_true = np.concatenate([results_by_material[m]["y_true"] for m in results_by_material])

    all_std_after = apply_temperature(all_std_before, T)

    # 校准前指标
    ece_before = compute_ece(all_mean_before, all_std_before, all_true)
    coverage_before = compute_coverage(all_mean_before, all_std_before, all_true)
    uq_corr_before = compute_uq_error_correlation(all_mean_before, all_std_before, all_true)

    std_by_material_before = {m: results_by_material[m]["std_pred"] for m in results_by_material}
    ood_before = compute_ood_detection_auc(std_by_material_before, id_mats, ood_mats)

    # 校准后指标
    ece_after = compute_ece(all_mean_before, all_std_after, all_true)
    coverage_after = compute_coverage(all_mean_before, all_std_after, all_true)
    uq_corr_after = compute_uq_error_correlation(all_mean_before, all_std_after, all_true)

    std_by_material_after = {m: results_by_material[m]["std_pred"] * T for m in results_by_material}
    ood_after = compute_ood_detection_auc(std_by_material_after, id_mats, ood_mats)

    # 打印对比
    print(f"\n  {'指标':<25} {'校准前':>12} {'校准后':>12} {'变化':>12}")
    print("  " + "-" * 65)
    print(
        f"  {'ECE':<25} {ece_before['ece']:>12.4f} {ece_after['ece']:>12.4f} "
        f"{(ece_after['ece'] - ece_before['ece']):>+12.4f}"
    )
    print(
        f"  {'MCE':<25} {ece_before['mce']:>12.4f} {ece_after['mce']:>12.4f} "
        f"{(ece_after['mce'] - ece_before['mce']):>+12.4f}"
    )
    for level in [80, 90, 95, 99]:
        b = coverage_before[f"coverage_{level}"]
        a = coverage_after[f"coverage_{level}"]
        print(f"  {'Coverage ' + str(level) + '%':<25} {b:>12.4f} {a:>12.4f} {(a - b):>+12.4f}")
    print(
        f"  {'Spearman 相关':<25} {uq_corr_before['spearman_corr']:>12.4f} "
        f"{uq_corr_after['spearman_corr']:>12.4f} "
        f"{(uq_corr_after['spearman_corr'] - uq_corr_before['spearman_corr']):>+12.4f}"
    )
    print(
        f"  {'Pearson 相关':<25} {uq_corr_before['pearson_corr']:>12.4f} "
        f"{uq_corr_after['pearson_corr']:>12.4f} "
        f"{(uq_corr_after['pearson_corr'] - uq_corr_before['pearson_corr']):>+12.4f}"
    )
    print(
        f"  {'OOD AUC':<25} {ood_before['auc_roc']:>12.4f} "
        f"{ood_after['auc_roc']:>12.4f} "
        f"{(ood_after['auc_roc'] - ood_before['auc_roc']):>+12.4f}"
    )
    print(
        f"  {'分离比 (OOD/ID std)':<25} {ood_before['separation_ratio']:>12.4f} "
        f"{ood_after['separation_ratio']:>12.4f} "
        f"{(ood_after['separation_ratio'] - ood_before['separation_ratio']):>+12.4f}"
    )

    # 6. 可视化
    print("\n[6/6] 生成可视化图表...")
    plot_calibration_comparison(
        all_mean_before,
        all_std_before,
        all_std_after,
        all_true,
        figures_dir / "calibration_before_after.png",
        T,
    )
    plot_coverage_comparison(
        all_mean_before,
        all_std_before,
        all_std_after,
        all_true,
        figures_dir / "coverage_comparison.png",
        T,
    )
    # 校准后的 OOD 检测箱线图
    plot_ood_detection(
        std_by_material_after,
        id_mats,
        ood_mats,
        figures_dir / "ood_detection_calibrated.png",
    )
    # 校准后的 UQ-误差散点图
    plot_uq_error_scatter(
        all_mean_before,
        all_std_after,
        all_true,
        figures_dir / "uq_error_corr_calibrated.png",
    )

    # 汇总结果
    full_result = {
        "experiment": "Temperature Scaling Calibration for Bayesian DL-LNN UQ",
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
        "temperature": {
            "T_analytic": T_analytic,
            "T_numerical": T_numerical,
            "T_final": T,
            "method": T_method,
            "id_ece_analytic": ece_analytic,
            "id_ece_numerical": min_ece,
        },
        "before_calibration": {
            "ece": ece_before,
            "coverage": coverage_before,
            "uq_error_correlation": uq_corr_before,
            "ood_detection": ood_before,
        },
        "after_calibration": {
            "ece": ece_after,
            "coverage": coverage_after,
            "uq_error_correlation": uq_corr_after,
            "ood_detection": ood_after,
        },
        "materials_summary": {
            m: {
                "hardness": results_by_material[m]["hardness"],
                "n_samples": results_by_material[m]["n_samples"],
                "metrics": results_by_material[m]["metrics"],
                "std_mean_calibrated": float(np.mean(results_by_material[m]["std_pred"] * T)),
            }
            for m in results_by_material
        },
    }

    # 保存 JSON
    json_path = output_dir / "calibrated_uq_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_result, f, indent=2, ensure_ascii=False)
    print(f"\n[已保存] {json_path}")

    # 生成 Markdown 报告
    report_path = output_dir / "calibrated_uq_report.md"
    _generate_calibration_report(full_result, report_path)
    print(f"[已保存] {report_path}")

    # 成功判据检查
    print("\n" + "=" * 70)
    print("校准成功判据检查")
    print("=" * 70)
    checks = [
        ("ECE < 0.10", ece_after["ece"] < 0.10),
        ("95% 覆盖率 ∈ [0.90, 0.97]", 0.90 <= coverage_after["coverage_95"] <= 0.97),
        ("OOD AUC 保持 > 0.7", ood_after["auc_roc"] > 0.7),
        ("分离比保持 > 2", ood_after["separation_ratio"] > 2.0),
        ("Spearman 相关改善", uq_corr_after["spearman_corr"] > uq_corr_before["spearman_corr"]),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✓ 通过" if passed else "✗ 未通过"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print(">>> 校准成功！所有判据通过，可进入阶段 2（物理引导不确定性）")
    else:
        print(">>> 部分判据未通过，需进一步分析")

    return full_result


def _generate_calibration_report(full_result: Dict, report_path: Path) -> None:
    """生成 Markdown 校准报告。"""
    b = full_result["before_calibration"]
    a = full_result["after_calibration"]
    t = full_result["temperature"]

    lines = [
        "# 温度缩放校准报告（Temperature Scaling Calibration）\n",
        f"**实验时间**: {full_result['timestamp']}\n",
        f"**MC 采样次数**: {full_result['config']['n_samples']}\n",
        f"**MC Dropout 概率**: {full_result['config']['mc_dropout_prob']}\n",
        "\n## 1. 温度参数 T\n",
        f"- **解析法 T (95% 覆盖率匹配)**: {t['T_analytic']:.4f}",
        f"- **数值法 T (最小化 ECE)**: {t['T_numerical']:.4f}",
        f"- **最终 T**: {t['T_final']:.4f}",
        f"- **选择方法**: {t['method']}",
        f"- **校准公式**: σ_calibrated = {t['T_final']:.4f} × σ_original",
        f"- **ID 材料上的 ECE（解析法）**: {t['id_ece_analytic']:.4f}",
        f"- **ID 材料上的 ECE（数值法）**: {t['id_ece_numerical']:.4f}",
        "\n## 2. 校准前后对比\n",
        "### 2.1 校准误差",
        "| 指标 | 校准前 | 校准后 | 变化 |",
        "|------|--------|--------|------|",
        f"| ECE | {b['ece']['ece']:.4f} | {a['ece']['ece']:.4f} | {a['ece']['ece'] - b['ece']['ece']:+.4f} |",
        f"| MCE | {b['ece']['mce']:.4f} | {a['ece']['mce']:.4f} | {a['ece']['mce'] - b['ece']['mce']:+.4f} |",
        "\n### 2.2 置信区间覆盖率",
        "| 名义水平 | 校准前 | 校准后 | 变化 |",
        "|---------|--------|--------|------|",
    ]

    for level in [80, 90, 95, 99]:
        before = b["coverage"][f"coverage_{level}"]
        after = a["coverage"][f"coverage_{level}"]
        lines.append(f"| {level}% | {before:.4f} | {after:.4f} | {after - before:+.4f} |")

    lines.extend(
        [
            "\n### 2.3 不确定性-误差相关性",
            "| 指标 | 校准前 | 校准后 | 变化 |",
            "|------|--------|--------|------|",
            f"| Spearman 相关 | {b['uq_error_correlation']['spearman_corr']:.4f} | "
            f"{a['uq_error_correlation']['spearman_corr']:.4f} | "
            f"{a['uq_error_correlation']['spearman_corr'] - b['uq_error_correlation']['spearman_corr']:+.4f} |",
            f"| Pearson 相关 | {b['uq_error_correlation']['pearson_corr']:.4f} | "
            f"{a['uq_error_correlation']['pearson_corr']:.4f} | "
            f"{a['uq_error_correlation']['pearson_corr'] - b['uq_error_correlation']['pearson_corr']:+.4f} |",
            f"| 高UQ组 MAE | {b['uq_error_correlation']['high_uq_mae']:.4f} | "
            f"{a['uq_error_correlation']['high_uq_mae']:.4f} | "
            f"{a['uq_error_correlation']['high_uq_mae'] - b['uq_error_correlation']['high_uq_mae']:+.4f} |",
            f"| 低UQ组 MAE | {b['uq_error_correlation']['low_uq_mae']:.4f} | "
            f"{a['uq_error_correlation']['low_uq_mae']:.4f} | "
            f"{a['uq_error_correlation']['low_uq_mae'] - b['uq_error_correlation']['low_uq_mae']:+.4f} |",
            f"| UQ-Error 比值 | {b['uq_error_correlation']['uq_error_ratio']:.4f} | "
            f"{a['uq_error_correlation']['uq_error_ratio']:.4f} | "
            f"{a['uq_error_correlation']['uq_error_ratio'] - b['uq_error_correlation']['uq_error_ratio']:+.4f} |",
            "\n### 2.4 OOD 检测能力",
            "| 指标 | 校准前 | 校准后 | 变化 |",
            "|------|--------|--------|------|",
            f"| ROC AUC | {b['ood_detection']['auc_roc']:.4f} | "
            f"{a['ood_detection']['auc_roc']:.4f} | "
            f"{a['ood_detection']['auc_roc'] - b['ood_detection']['auc_roc']:+.4f} |",
            f"| ID std 均值 | {b['ood_detection']['id_std_mean']:.4f} | "
            f"{a['ood_detection']['id_std_mean']:.4f} | "
            f"{a['ood_detection']['id_std_mean'] - b['ood_detection']['id_std_mean']:+.4f} |",
            f"| OOD std 均值 | {b['ood_detection']['ood_std_mean']:.4f} | "
            f"{a['ood_detection']['ood_std_mean']:.4f} | "
            f"{a['ood_detection']['ood_std_mean'] - b['ood_detection']['ood_std_mean']:+.4f} |",
            f"| 分离比 (OOD/ID) | {b['ood_detection']['separation_ratio']:.4f} | "
            f"{a['ood_detection']['separation_ratio']:.4f} | "
            f"{a['ood_detection']['separation_ratio'] - b['ood_detection']['separation_ratio']:+.4f} |",
            "\n## 3. 各材料校准后指标\n",
            "| 材料 | 硬度(HB) | 样本数 | MAE | std_mean (校准前) | std_mean (校准后) |",
            "|------|---------|--------|-----|------------------|------------------|",
        ]
    )

    for mat, data in full_result["materials_summary"].items():
        m = data["metrics"]
        std_before = m["std_mean"]
        std_after = data["std_mean_calibrated"]
        lines.append(
            f"| {mat} | {data['hardness']:.0f} | {data['n_samples']} | "
            f"{m['mae']:.4f} | {std_before:.4f} | {std_after:.4f} |"
        )

    lines.extend(
        [
            "\n## 4. 可视化图表\n",
            "- 校准前后对比曲线: `figures/calibration_before_after.png`",
            "- 覆盖率对比柱状图: `figures/coverage_comparison.png`",
            "- 校准后 OOD 检测箱线图: `figures/ood_detection_calibrated.png`",
            "- 校准后 UQ-误差散点图: `figures/uq_error_corr_calibrated.png`",
            "\n## 5. 结论\n",
        ]
    )

    # 自动结论
    if a["ece"]["ece"] < 0.10:
        lines.append("- ✓ 校准成功，ECE 已降至 0.10 以下")
    else:
        lines.append(f"- ✗ ECE = {a['ece']['ece']:.4f} 仍高于 0.10，可能需要更复杂的校准方法")

    if 0.90 <= a["coverage"]["coverage_95"] <= 0.97:
        lines.append("- ✓ 95% 覆盖率已达目标区间 [0.90, 0.97]")
    else:
        lines.append(f"- ✗ 95% 覆盖率 = {a['coverage']['coverage_95']:.4f} 未达目标")

    if a["ood_detection"]["auc_roc"] > 0.7:
        lines.append(f"- ✓ OOD 检测能力保持 (AUC = {a['ood_detection']['auc_roc']:.4f} > 0.7)")
    else:
        lines.append(f"- ✗ OOD 检测能力下降 (AUC = {a['ood_detection']['auc_roc']:.4f})")

    if a["uq_error_correlation"]["spearman_corr"] > b["uq_error_correlation"]["spearman_corr"]:
        lines.append("- ✓ Spearman 相关性改善")
    else:
        lines.append("- ✗ Spearman 相关性未改善")

    lines.append(
        f"\n**物理意义**: 温度参数 T = {t['T_final']:.4f} 表明原始 MC Dropout "
        f"不确定性需要放大 {t['T_final']:.1f} 倍才能匹配实际误差分布。"
    )
    lines.append(f"**OOD 检测不变性**: T 是全局缩放，不改变 std 的相对排序，因此 OOD AUC 和分离比保持不变。")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# 入口


def main():
    import argparse

    parser = argparse.ArgumentParser(description="温度缩放校准实验")
    parser.add_argument(
        "--weights",
        type=str,
        default=str(Path(__file__).parent / "results" / "full_weights.pt"),
        help="Full 配置权重文件路径",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(Path(__file__).parent / "results"),
        help="结果输出目录",
    )
    parser.add_argument("--n_samples", type=int, default=100, help="MC 采样次数")
    parser.add_argument("--mc_dropout_prob", type=float, default=0.1, help="MC Dropout 概率")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    weights_path = Path(args.weights)
    if not weights_path.exists():
        print(f"[错误] 权重文件不存在: {weights_path}")
        print("请先运行: python rerun_full_save_weights.py")
        sys.exit(1)

    run_calibration(
        weights_path=weights_path,
        output_dir=Path(args.output_dir),
        n_samples=args.n_samples,
        mc_dropout_prob=args.mc_dropout_prob,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
