# -*- coding: utf-8 -*-
"""
re_render_300dpi.py
论文投稿前图件 300dpi 重绘（免重训：全部从已存结果 JSON 重绘）。
- fig46a: tlusty_mismatch_results.json
- fig47b: exp46/47/47b 三 JSON + TLUSTY_MAE
- fig49a/b: spindle_extrapolation_results.json
- fig50a: uniwear.csv 原始数据重建 W1 时序 + Taylor 基线
- fig50b: uniwear_real_results.json (groups 每模型 R2 均值)
- fig52: multi_scene_transfer_results.json 四场景版（修复原 2 场景图与论文标题不符）
原 150dpi 图先备份到 figures/backup_150dpi/。
"""

import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

BASE = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE / "results"
FIG_DIR = OUTPUT_DIR / "figures"
DPI = 300

BACKUP = FIG_DIR / "backup_150dpi"
BACKUP.mkdir(exist_ok=True)

DELTAS = [0.00, 0.05, 0.10, 0.20, 0.40]


def backup(name: str):
    src = FIG_DIR / name
    if src.exists() and not (BACKUP / name).exists():
        shutil.copy2(src, BACKUP / name)


def _load(name: str) -> dict:
    return json.load(open(OUTPUT_DIR / name, encoding="utf-8"))


# fig46a：对角线（train==test）+ Tlusty + LSTM
def fig46a():
    name = "fig46a_mismatch_diagonal.png"
    backup(name)
    results = _load("tlusty_mismatch_results.json")
    pcts = [d * 100 for d in DELTAS]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    diag_means = [results["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
    diag_stds = [results["matrix"][str(d)][str(d)]["MAE_std"] for d in DELTAS]
    ax.plot(
        pcts,
        [results["tlusty_baseline"]["0.0"][str(d)]["MAE"] for d in DELTAS],
        "s--",
        color="tab:red",
        label="Tlusty (mismatched params)",
        linewidth=1.8,
    )
    ax.errorbar(
        pcts,
        diag_means,
        yerr=diag_stds,
        fmt="^-",
        color="tab:green",
        label="DL-LNN (trained on same mismatch)",
        linewidth=1.8,
        capsize=3,
    )
    ax.errorbar(
        pcts,
        [results["lstm"]["MAE_mean"]] * len(pcts),
        yerr=[results["lstm"]["MAE_std"]] * len(pcts),
        fmt="o-",
        color="tab:blue",
        label="LSTM (data-only)",
        linewidth=1.8,
        capsize=3,
    )
    ax.set_xlabel("Modal parameter mismatch delta (%)")
    ax.set_ylabel("Test MAE (mm)")
    ax.set_title("Residual compensation under modal parameter mismatch")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi")


# fig47b：三版门控对角线对比
def _tlusty_mae() -> dict:
    data = _load("tlusty_mismatch_results.json")
    tb = data["tlusty_baseline"]["0.0"]
    return {float(k): float(v["MAE"]) for k, v in tb.items()}


def fig47b():
    name = "fig47b_diagonal_3gates.png"
    backup(name)
    exp46 = _load("tlusty_mismatch_results.json")
    exp47 = _load("physics_aware_gate_results.json")
    exp47b = _load("physics_aware_gate_v2_results.json")
    tlusty_mae = _tlusty_mae()
    pcts = [d * 100 for d in DELTAS]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    diag46 = [exp46["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
    diag47 = [exp47["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
    diag47b = [exp47b["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
    ax.plot(pcts, [tlusty_mae[d] for d in DELTAS], "s--", color="tab:red", label="Tlusty (mismatched)", linewidth=1.8)
    ax.plot(pcts, diag46, "o-", color="tab:orange", label="orig gate (exp46)", linewidth=1.6)
    ax.plot(pcts, diag47, "^-", color="tab:blue", label="aware v1 x+phys (exp47)", linewidth=1.6)
    ax.plot(pcts, diag47b, "D-", color="tab:green", label="aware v2 +|phys-ltc| (exp47b)", linewidth=1.8)
    ax.set_xlabel("Modal mismatch delta (%)")
    ax.set_ylabel("Test MAE (mm)")
    ax.set_title("Diagonal comparison: three gate designs")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi")


# fig49a / fig49b：转速外推 MAE + gate alpha 箱线
def fig49():
    results = _load("spindle_extrapolation_results.json")
    tlusty = results["tlusty_baseline"]

    name = "fig49a_extrap_mae.png"
    backup(name)
    fig, ax = plt.subplots(figsize=(8, 5))
    models_list = ["Tlusty", "LSTM", "DL-LNN", "DL-LNN v2"]
    in_mae = [tlusty["in_domain"]["MAE"]]
    ex_mae = [tlusty["extrapolation"]["MAE"]]
    for mn in ["lstm", "dlnn", "dlnn_v2"]:
        in_mae.append(results["models"][mn]["in_domain"]["MAE_mean"])
        ex_mae.append(results["models"][mn]["extrapolation"]["MAE_mean"])
    x = np.arange(len(models_list))
    w = 0.35
    b1 = ax.bar(x - w / 2, in_mae, w, label="In-domain (3-8k rpm)", color="tab:blue", alpha=0.85)
    b2 = ax.bar(x + w / 2, ex_mae, w, label="Extrapolation (10-15k rpm)", color="tab:red", alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(models_list)
    ax.set_ylabel("Test MAE (mm)")
    ax.set_title("Spindle-speed extrapolation: MAE by model")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    for bars in [b1, b2]:
        for bar in bars:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.01,
                f"{bar.get_height():.2f}",
                ha="center",
                fontsize=8,
            )
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi")

    name = "fig49b_gate_alpha_extrap.png"
    backup(name)
    fig, ax = plt.subplots(figsize=(7, 5))
    data_in = [results["models"]["dlnn"]["in_domain"]["gate"], results["models"]["dlnn_v2"]["in_domain"]["gate"]]
    data_ex = [
        results["models"]["dlnn"]["extrapolation"]["gate"],
        results["models"]["dlnn_v2"]["extrapolation"]["gate"],
    ]
    bp1 = ax.boxplot(
        data_in, positions=[1, 2], widths=0.28, patch_artist=True, boxprops=dict(facecolor="tab:blue", alpha=0.6)
    )
    bp2 = ax.boxplot(
        data_ex, positions=[1.35, 2.35], widths=0.28, patch_artist=True, boxprops=dict(facecolor="tab:red", alpha=0.6)
    )
    ax.set_xticks([1.175, 2.175])
    ax.set_xticklabels(["DL-LNN", "DL-LNN v2"])
    ax.set_ylabel("Gate alpha (mean)")
    ax.set_title("Gate alpha: in-domain vs extrapolation")
    from matplotlib.patches import Patch

    ax.legend(
        [Patch(color="tab:blue", alpha=0.6), Patch(color="tab:red", alpha=0.6)],
        ["In-domain", "Extrapolation"],
        fontsize=9,
    )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi")


# fig50a / fig50b：uniwear 真实磨损
def _build_window_features(df_group: pd.DataFrame) -> tuple:
    """exp50 同款窗口特征（3 信号 × 5 统计 + 时间位置 2 维 = 17 维）。"""
    WINDOW, STEP = 50, 25
    sig_cols = ["force_z", "vibration_x", "vibration_y"]
    n = len(df_group)
    rows_x, rows_y = [], []
    starts = list(range(0, n - WINDOW + 1, STEP))
    total_windows = max(len(starts), 1)
    t0 = df_group["timestamp"].iloc[0]
    t1 = df_group["timestamp"].iloc[-1]
    for wi, s in enumerate(starts):
        seg = df_group.iloc[s : s + WINDOW]
        feats = []
        for c in sig_cols:
            v = seg[c].values.astype(np.float64)
            feats += [v.mean(), np.sqrt(np.mean(v**2)), v.std(), stats.kurtosis(v), np.ptp(v)]
        feats.append(wi / max(total_windows - 1, 1))
        t_mid = seg["timestamp"].mean()
        feats.append((t_mid - t0) / max(t1 - t0, 1e-9))
        rows_x.append(feats)
        rows_y.append(seg["tool_wear"].mean())
    X = np.array(rows_x, dtype=np.float32)
    y = np.array(rows_y, dtype=np.float32).reshape(-1, 1)
    return X, y


def _taylor_wear_baseline(y_train, t_train, t_test):
    t_train = np.clip(t_train, 1e-6, None)
    t_test = np.clip(t_test, 1e-6, None)
    A = np.stack([np.ones_like(t_train), np.log(t_train)], axis=1)
    coef, *_ = np.linalg.lstsq(A, np.log(np.clip(y_train, 1e-6, None)), rcond=None)
    ln_k, p = coef[0], coef[1]
    return np.exp(ln_k) * (t_test**p)


def fig50():
    results = _load("uniwear_real_results.json")

    name = "fig50a_wear_curve.png"
    backup(name)
    csv_path = BASE.parent / "datasets" / "uniwear" / "uniwear" / "uniwear.csv"
    df = pd.read_csv(csv_path)
    grp_col = "experiment_tag"
    grp_rep = "W1"
    dfg = df[df[grp_col] == grp_rep]
    X, y = _build_window_features(dfg)
    n_tr = int(len(X) * 0.7)
    t_all = X[:, -1]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(t_all, y, "k-", linewidth=1.5, label="true wear")
    y_phys = _taylor_wear_baseline(y[:n_tr], X[:n_tr, -1].astype(np.float64), t_all.astype(np.float64))
    ax.plot(t_all, y_phys, "b--", linewidth=1.5, label="Taylor baseline")
    ax.axvline(t_all[n_tr], color="gray", linestyle=":", label="train/test split")
    ax.set_xlabel("normalized time position")
    ax.set_ylabel("tool wear (mm)")
    ax.set_title(f"Wear degradation curve: {grp_rep}")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi")

    name = "fig50b_r2_comparison.png"
    backup(name)
    g = results["groups"]
    data = [[float(np.mean(g[grp]["lstm"]["R2"])) for grp in g], [float(np.mean(g[grp]["dlnn"]["R2"])) for grp in g]]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.boxplot(data, patch_artist=True)
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["LSTM", "DL-LNN"])
    for i, d in enumerate(data, start=1):
        for v in d:
            ax.plot(i, v, "o", alpha=0.4, markersize=4)
    ax.axhline(0, color="red", linestyle="--", linewidth=1)
    ax.set_ylabel("R² (per group, mean over seeds)")
    ax.set_title("uniwear real: R² by model (red = R²=0)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi")


# fig52：跨数据集零样本迁移四场景（修复 2 场景 4 场景）
def fig52():
    name = "fig52_cross_dataset_transfer.png"
    backup(name)
    m = _load("multi_scene_transfer_results.json")
    scenes = m["scenes"]
    order = ["A_cross_dataset", "A2_reverse_cross", "B_within_phm2010", "B2_within_nuaa"]
    titles = {
        "A_cross_dataset": "A: cross-dataset (PHM2010→NUAA)",
        "A2_reverse_cross": "A2: reverse cross (NUAA→PHM2010)",
        "B_within_phm2010": "B: within PHM2010",
        "B2_within_nuaa": "B2: within NUAA",
    }
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.2))
    colors = ["#888888", "#d98c8c", "#8c9ad9"]
    for ax, sn in zip(axes, order):
        s = scenes[sn]
        labels = ["Taylor", "LSTM", "DL-LNN"]
        maes = [s["base_MAE_mean"], s["lstm_MAE_mean"], s["dlnn_MAE_mean"]]
        bars = ax.bar(labels, maes, color=colors)
        for b, v in zip(bars, maes):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.4f}", ha="center", va="bottom", fontsize=9)
        sub = titles[sn]
        extra = f"gate={s.get('gate_mean', float('nan')):.3f}"
        ax.set_title(f"{sub}\n({extra})", fontsize=9)
        ax.set_ylabel("MAE (mm)")
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("exp52b zero-shot cross-dataset transfer: 4 scenes (3 seeds mean)", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIG_DIR / name, dpi=DPI)
    plt.close(fig)
    print(f"OK {name} @{DPI}dpi (4 scenes)")


if __name__ == "__main__":
    fig46a()
    fig47b()
    fig49()
    fig50()
    fig52()
    print("\nALL REDRAWN @300dpi; backups in", BACKUP)
