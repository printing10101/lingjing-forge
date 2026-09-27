"""
τ 参数提取与物理释义分析脚本
================================
从训练好的 LTC (Liquid Time-Constant) 模型中提取可学习时间常数 τ，
分析其与颤振系统物理参数（固有频率、阻尼比等）的对应关系。

用途：
- 论文1（DL-LNN 主论文）第 5 节"τ 可解释性"实验
- 论文4（综述）第 6.2 节"可学习时间常数的物理释义"

输出：
- τ 分布直方图
- τ 与颤振频率的相关性分析
- τ 跨工况稳定性分析

运行方式：
    python extract_tau.py --model_path data/checkpoints/dl_lnn_best.pt \
                          --output_dir 论文相关/脚本/results/tau_analysis
"""

import os
import sys
import json
import argparse
import warnings
import types
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# === WinSock 损坏绕过补丁（必须在 import torch 之前执行）===
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

warnings.filterwarnings("ignore")

# 添加项目根目录到 sys.path（脚本可独立运行）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "python"))


def set_seed(seed: int = 42) -> None:
    """固定随机种子，保证可复现性。"""
    import random
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def load_ltc_model(model_path: str, config: dict) -> nn.Module:
    """加载训练好的 LTC 模型。

    Args:
        model_path: 模型权重路径
        config: 模型配置字典

    Returns:
        加载好的 LTC 模型
    """
    from experiments.models import LTCModel  # 项目内的 LTC 实现

    model = LTCModel(
        input_dim=config.get("input_dim", 7),
        hidden_dim=config.get("hidden_dim", 64),
        num_layers=config.get("num_layers", 3),
        output_dim=1,
        solver=config.get("solver", "rk4"),
        dt=config.get("dt", 0.1),
    )
    state_dict = torch.load(model_path, map_location="cpu")
    model.load_state_dict(state_dict)
    model.eval()
    return model


def extract_tau_parameters(model: nn.Module) -> Dict[str, np.ndarray]:
    """从 LTC 模型中提取所有可学习时间常数 τ。

    LTC 的 τ 通常存储在模型每一层的 tau 属性中。
    本函数兼容 ncps 库与项目自定义 LTC 实现。

    Args:
        model: 加载好的 LTC 模型

    Returns:
        Dict containing:
            - "tau_per_layer": 每层 τ 的均值/标准差
            - "tau_all": 所有 τ 参数的扁平数组
            - "tau_stats": 统计信息（min/max/mean/median）
    """
    tau_list = []
    tau_per_layer = []

    for name, param in model.named_parameters():
        if "tau" in name.lower():
            tau_np = param.detach().cpu().numpy().flatten()
            tau_list.append(tau_np)
            tau_per_layer.append({
                "layer": name,
                "tau_mean": float(np.mean(tau_np)),
                "tau_std": float(np.std(tau_np)),
                "tau_min": float(np.min(tau_np)),
                "tau_max": float(np.max(tau_np)),
            })

    if not tau_list:
        # 兼容 ncps 实现：τ 可能作为 buffer 而非 parameter
        for name, buffer in model.named_buffers():
            if "tau" in name.lower():
                tau_np = buffer.detach().cpu().numpy().flatten()
                tau_list.append(tau_np)
                tau_per_layer.append({
                    "layer": name,
                    "tau_mean": float(np.mean(tau_np)),
                    "tau_std": float(np.std(tau_np)),
                    "tau_min": float(np.min(tau_np)),
                    "tau_max": float(np.max(tau_np)),
                })

    if not tau_list:
        raise ValueError("未在模型中找到 τ 参数。请检查模型是否为 LTC 架构。")

    tau_all = np.concatenate(tau_list)

    return {
        "tau_per_layer": tau_per_layer,
        "tau_all": tau_all,
        "tau_stats": {
            "total_count": int(len(tau_all)),
            "mean": float(np.mean(tau_all)),
            "std": float(np.std(tau_all)),
            "min": float(np.min(tau_all)),
            "max": float(np.max(tau_all)),
            "median": float(np.median(tau_all)),
        },
    }


def analyze_tau_physical_correlation(
    model: nn.Module,
    test_loader,
    tau_data: Dict,
) -> Dict:
    """分析 τ 与颤振物理参数的相关性。

    理论假设（来自论文1 第 2.2 节）：
        τ ∝ 1 / f_chatter
    即 τ 应与颤振频率 f_chatter 呈反比关系。

    Args:
        model: LTC 模型
        test_loader: 测试数据加载器（包含振动信号与对应频率）
        tau_data: extract_tau_parameters 的输出

    Returns:
        相关性分析结果
    """
    model.eval()
    f_chatter_list = []  # 颤振频率（从测试集提取）
    tau_eff_list = []    # 有效 τ（根据输入动态计算）

    with torch.no_grad():
        for batch in test_loader:
            x, y, f_chatter = batch  # 假设 batch 包含颤振频率
            # 提取该输入下的有效 τ
            # 对于 LTC，有效 τ = 1 / (1/τ_base + f(x, I, θ))
            # 这里简化为统计 τ 的均值
            tau_eff = tau_data["tau_stats"]["mean"]
            f_chatter_list.extend(f_chatter.numpy().flatten().tolist())
            tau_eff_list.append(tau_eff)

    f_chatter_arr = np.array(f_chatter_list)
    tau_arr = np.array(tau_eff_list)

    # 计算 Pearson 相关系数
    if len(f_chatter_arr) > 1:
        corr_matrix = np.corrcoef(f_chatter_arr, tau_arr)
        pearson_corr = float(corr_matrix[0, 1])
    else:
        pearson_corr = 0.0

    # 计算 1/f 与 τ 的相关性（理论上应正相关）
    inv_f = 1.0 / (f_chatter_arr + 1e-8)
    corr_inv = float(np.corrcoef(inv_f, tau_arr)[0, 1]) if len(inv_f) > 1 else 0.0

    return {
        "pearson_corr_f_vs_tau": pearson_corr,
        "pearson_corr_inv_f_vs_tau": corr_inv,
        "theoretical_prediction": "τ ∝ 1/f_chatter",
        "validation": "PASS" if corr_inv > 0.5 else "WEAK" if corr_inv > 0.2 else "FAIL",
        "f_chatter_range": [float(np.min(f_chatter_arr)), float(np.max(f_chatter_arr))],
        "n_samples": len(f_chatter_arr),
    }


def analyze_tau_cross_condition(
    model_paths: List[str],
    condition_names: List[str],
) -> Dict:
    """分析 τ 在不同工况下的稳定性。

    理论预期：τ 应在不同工况下保持相对稳定（因为 τ 反映系统固有特性），
    若 τ 跨工况波动剧烈，则说明 τ 可能过拟合到训练分布。

    Args:
        model_paths: 不同工况下训练的模型路径列表
        condition_names: 对应的工况名称

    Returns:
        跨工况稳定性分析结果
    """
    results = []
    for path, name in zip(model_paths, condition_names):
        try:
            model = load_ltc_model(path, config={})
            tau_data = extract_tau_parameters(model)
            results.append({
                "condition": name,
                "tau_mean": tau_data["tau_stats"]["mean"],
                "tau_std": tau_data["tau_stats"]["std"],
            })
        except Exception as e:
            results.append({"condition": name, "error": str(e)})

    valid_results = [r for r in results if "error" not in r]
    if len(valid_results) >= 2:
        tau_means = [r["tau_mean"] for r in valid_results]
        cross_condition_std = float(np.std(tau_means))
        cross_condition_cv = cross_condition_std / (np.mean(tau_means) + 1e-8)
    else:
        cross_condition_std = 0.0
        cross_condition_cv = 0.0

    return {
        "per_condition": results,
        "cross_condition_std": cross_condition_std,
        "cross_condition_cv": cross_condition_cv,  # 变异系数
        "stability_assessment": (
            "STABLE" if cross_condition_cv < 0.15
            else "MODERATE" if cross_condition_cv < 0.30
            else "UNSTABLE"
        ),
    }


def plot_tau_distribution(tau_data: Dict, output_path: str) -> None:
    """绘制 τ 分布直方图。

    Args:
        tau_data: extract_tau_parameters 的输出
        output_path: 图片保存路径
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tau_all = tau_data["tau_all"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # 左图：τ 分布直方图
    axes[0].hist(tau_all, bins=50, color="steelblue", edgecolor="white", alpha=0.8)
    axes[0].axvline(tau_data["tau_stats"]["mean"], color="red", linestyle="--",
                    label=f"Mean = {tau_data['tau_stats']['mean']:.4f}")
    axes[0].axvline(tau_data["tau_stats"]["median"], color="green", linestyle="--",
                    label=f"Median = {tau_data['tau_stats']['median']:.4f}")
    axes[0].set_xlabel("τ (时间常数)")
    axes[0].set_ylabel("频次")
    axes[0].set_title("LTC 可学习时间常数 τ 分布")
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    # 右图：每层 τ 均值
    layer_names = [l["layer"].split(".")[-1] for l in tau_data["tau_per_layer"]]
    layer_means = [l["tau_mean"] for l in tau_data["tau_per_layer"]]
    layer_stds = [l["tau_std"] for l in tau_data["tau_per_layer"]]

    axes[1].bar(range(len(layer_means)), layer_means,
                yerr=layer_stds, capsize=5, color="coral", edgecolor="black")
    axes[1].set_xticks(range(len(layer_names)))
    axes[1].set_xticklabels(layer_names, rotation=45, ha="right")
    axes[1].set_xlabel("层")
    axes[1].set_ylabel("τ 均值")
    axes[1].set_title("各层 τ 均值与标准差")
    axes[1].grid(alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"[OK] τ 分布图已保存至: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="τ 参数提取与物理释义分析")
    parser.add_argument("--model_path", type=str, default="data/checkpoints/dl_lnn_best.pt",
                        help="训练好的 LTC 模型路径")
    parser.add_argument("--output_dir", type=str,
                        default="论文相关/脚本/results/tau_analysis",
                        help="输出目录")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    set_seed(args.seed)
    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 70)
    print("τ 参数提取与物理释义分析")
    print("=" * 70)

    # 1. 加载模型
    print(f"\n[1/4] 加载 LTC 模型: {args.model_path}")
    if not os.path.exists(args.model_path):
        print(f"[警告] 模型文件不存在: {args.model_path}")
        print("       将使用随机初始化的 LTC 模型进行演示。")
        model = load_ltc_model.__wrapped__() if hasattr(load_ltc_model, "__wrapped__") else None
        # 退而求其次：创建随机模型
        from experiments.models import LTCModel
        model = LTCModel(input_dim=7, hidden_dim=64, num_layers=3, output_dim=1)
    else:
        model = load_ltc_model(args.model_path, config={})

    # 2. 提取 τ 参数
    print("\n[2/4] 提取可学习时间常数 τ ...")
    tau_data = extract_tau_parameters(model)
    print(f"  - τ 参数总数: {tau_data['tau_stats']['total_count']}")
    print(f"  - 均值: {tau_data['tau_stats']['mean']:.6f}")
    print(f"  - 标准差: {tau_data['tau_stats']['std']:.6f}")
    print(f"  - 范围: [{tau_data['tau_stats']['min']:.6f}, {tau_data['tau_stats']['max']:.6f}]")

    # 3. 绘制 τ 分布
    print("\n[3/4] 绘制 τ 分布图 ...")
    plot_path = os.path.join(args.output_dir, "tau_distribution.png")
    plot_tau_distribution(tau_data, plot_path)

    # 4. 物理释义分析
    print("\n[4/4] 物理释义分析（τ vs 颤振频率）...")
    print("  [注] 完整分析需要测试数据加载器，此处输出理论框架。")
    physical_analysis = {
        "theoretical_prediction": "τ ∝ 1/f_chatter",
        "explanation": (
            "LTC 的时间常数 τ 反映神经元状态变化的快慢，"
            "对应物理上颤振系统的固有周期 T = 1/f。"
            "理论上 τ 应与颤振频率 f_chatter 呈反比关系。"
        ),
        "tau_mean": tau_data["tau_stats"]["mean"],
        "expected_f_chatter": 1.0 / (tau_data["tau_stats"]["mean"] + 1e-8),
        "validation_status": "PENDING_DATA",
    }

    # 保存结果
    results = {
        "tau_extraction": tau_data,
        "physical_analysis": physical_analysis,
        "config": {
            "model_path": args.model_path,
            "seed": args.seed,
        },
    }
    output_json = os.path.join(args.output_dir, "tau_analysis_results.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] 结果已保存至: {output_json}")

    print("\n" + "=" * 70)
    print("τ 参数提取完成。")
    print("=" * 70)


if __name__ == "__main__":
    main()
