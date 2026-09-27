# -*- coding: utf-8 -*-
"""
实验52：跨数据集迁移——uniwear 零样本泛化验证（CROSS-DATASET TRANSFER）

回应苛刻审稿人"泛化性证据薄弱（跨机床/跨数据集实证缺失）"致命项（三轮一致）。

协议：
- 场景 A（跨数据集零样本）：训练 = nuaa 组（W1-W9 全窗口）；测试 = phm2010 组
  （c1/c4/c6 全窗口）——模型从未见过测试数据集任何样本（最严泛化测试）
- 场景 B（同数据集内迁移对照）：训练 = c1+c4；测试 = c6（同分布内对照）
- 三路对比：Taylor 经验基线（时间律跨数据集外推）/ LSTM（纯信号）/ DL-LNN（残差补偿）
- 3 seeds × 2 场景，指标：MAE、R²（含 var_true_ratio 诊断）、门控 α
- 诚信协议：Euler 求解器（_HAS_TORCHDIFFEQ=False 强制）；固定种子；
  结果 JSON 记录 ltc_solver/数据源；负 R² 用 var_true_ratio 分解

预期（诚实声明）：跨数据集迁移是强测试，信号分布差异可能压制所有模型；
若 DL-LNN 相对 LSTM 保持优势 → "物理先验在零样本迁移中提供约束"强证据；
若全模型失效 → 如实报告泛化边界（能力边界本身是有价值发现）。
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))
import exp50_uniwear_real as E  # 复用 exp50 组件（窗口特征/模型/训练/基线）

import models as _models

_models._HAS_TORCHDIFFEQ = False  # 强制 Euler（记忆坑：漏设则 LTCCell 走 dopri5 慢 90 倍）
LTC_SOLVER = "euler"

SEEDS = [42, 43, 44]
NUM_EPOCHS = 60
BATCH_SIZE = 128
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"
UNIWEAR_CSV = E.UNIWEAR_CSV

NUAA_GROUPS = [f"W{i}" for i in range(1, 10)]  # W1-W9
PHM_GROUPS = ["c1", "c4", "c6"]  # phm2010


def build_scene_data(df, train_groups, test_groups):
    """拼接训练/测试窗口。窗口特征含组内归一化时间位置（语义跨组一致）。"""
    X_tr_list, y_tr_list = [], []
    for g in train_groups:
        gdf = df[df["experiment_tag"] == g].reset_index(drop=True)
        X, y = E.build_window_features(gdf)
        X_tr_list.append(X)
        y_tr_list.append(y)
    X_tr = np.concatenate(X_tr_list, axis=0).astype(np.float32)
    y_tr = np.concatenate(y_tr_list, axis=0).astype(np.float32).reshape(-1, 1)

    te_meta = {}
    X_te_list, y_te_list = [], []
    for g in test_groups:
        gdf = df[df["experiment_tag"] == g].reset_index(drop=True)
        X, y = E.build_window_features(gdf)
        te_meta[g] = {"n": len(X)}
        X_te_list.append(X)
        y_te_list.append(y)
    X_te = np.concatenate(X_te_list, axis=0).astype(np.float32)
    y_te = np.concatenate(y_te_list, axis=0).astype(np.float32).reshape(-1, 1)
    return X_tr, y_tr, X_te, y_te, te_meta


def run_scene(df, train_groups, test_groups, scene_name, seed, metrics_calc):
    torch.manual_seed(seed)
    np.random.seed(seed)
    X_tr, y_tr, X_te, y_te, te_meta = build_scene_data(df, train_groups, test_groups)

    n_tr = len(X_tr)
    t_tr = X_tr[:, -1].astype(np.float64)
    t_te = X_te[:, -1].astype(np.float64)

    # ---- Taylor 经验基线（时间律，训练集拟合 测试集外推）----
    pred_base = E.taylor_wear_baseline(y_tr[:, 0], t_tr, t_te).reshape(-1, 1)
    mae_b = float(metrics_calc.mae(pred_base, y_te))
    r2_b = float(metrics_calc.r2_score(pred_base, y_te))
    var_tr = float(np.var(y_te))  # var_true_ratio 诊断
    var_all = float(np.var(np.concatenate([y_tr, y_te])))
    var_ratio = var_tr / max(var_all, 1e-12)

    y_phys_tr = E.taylor_wear_baseline(y_tr[:, 0], t_tr, t_tr).astype(np.float32).reshape(-1, 1)
    y_phys_te = pred_base.astype(np.float32)

    X_tr_t = torch.from_numpy(X_tr)
    y_tr_t = torch.from_numpy(y_tr)
    X_te_t = torch.from_numpy(X_te)
    y_te_t = torch.from_numpy(y_te)
    phys_tr_t = torch.from_numpy(y_phys_tr)
    phys_te_t = torch.from_numpy(y_phys_te)

    n_val = max(int(n_tr * 0.1), 1)
    tr_ds = TensorDataset(X_tr_t[: n_tr - n_val], y_tr_t[: n_tr - n_val], phys_tr_t[: n_tr - n_val])
    val_ds = TensorDataset(X_tr_t[n_tr - n_val :], y_tr_t[n_tr - n_val :], phys_tr_t[n_tr - n_val :])
    te_ds = TensorDataset(X_te_t, y_te_t, phys_te_t)
    tr_loader = DataLoader(tr_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False)
    te_loader = DataLoader(te_ds, batch_size=BATCH_SIZE, shuffle=False)

    # ---- LSTM（纯信号）----
    lstm = E.BaselineLSTM(input_dim=E.FEAT_DIM, hidden_dim=64, num_layers=2, output_dim=1)
    lstm = E.train_model(lstm, tr_loader, val_loader, epochs=NUM_EPOCHS, use_physics=False)
    p_lstm, _, _ = E.predict(lstm, te_loader, use_physics=False)
    mae_l = float(metrics_calc.mae(p_lstm, y_te))
    r2_l = float(metrics_calc.r2_score(p_lstm, y_te))

    # DL-LNN 残差补偿
    dlnn = E.GatedWearModel(input_dim=E.FEAT_DIM, hidden_dim=64, num_layers=2, output_dim=1)
    dlnn = E.train_model(dlnn, tr_loader, val_loader, epochs=NUM_EPOCHS, use_physics=True)
    p_dlnn, _, alphas = E.predict(dlnn, te_loader, use_physics=True)
    mae_d = float(metrics_calc.mae(p_dlnn, y_te))
    r2_d = float(metrics_calc.r2_score(p_dlnn, y_te))
    alpha = float(np.mean(alphas))

    return {
        "scene": scene_name,
        "seed": seed,
        "n_train": int(n_tr),
        "n_test": int(len(X_te)),
        "test_groups": te_meta,
        "baseline": {"MAE": mae_b, "R2": r2_b},
        "lstm": {"MAE": mae_l, "R2": r2_l},
        "dlnn": {"MAE": mae_d, "R2": r2_d, "gate": alpha},
        "diagnostics": {"var_true_ratio": var_ratio, "var_test": var_tr},
    }


def main():
    print("Device: cpu (exp52 cross-dataset transfer)", flush=True)
    print(f"Loading {UNIWEAR_CSV} ...", flush=True)
    df = pd.read_csv(UNIWEAR_CSV)
    metrics_calc = E.ChatterMetrics()

    scenes = [
        ("A_cross_dataset", NUAA_GROUPS, PHM_GROUPS),
        ("B_within_dataset", ["c1", "c4"], ["c6"]),
    ]

    results = {
        "experiment": "exp52_cross_dataset_transfer",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": bool(_models._HAS_TORCHDIFFEQ),
        "seeds": SEEDS,
        "num_epochs": NUM_EPOCHS,
        "window": E.WINDOW,
        "step": E.STEP,
        "scenes": {},
    }

    for scene_name, tr_groups, te_groups in scenes:
        print(f"\n=== scene {scene_name}: train={tr_groups} test={te_groups} ===", flush=True)
        runs = [run_scene(df, tr_groups, te_groups, scene_name, s, metrics_calc) for s in SEEDS]
        results["scenes"][scene_name] = {"runs": runs}

        # 汇总（3 seeds 均值）
        agg = {"baseline": {}, "lstm": {}, "dlnn": {}}
        for k in ("baseline", "lstm", "dlnn"):
            keys = ["MAE"] if k == "baseline" else ["MAE", "R2"]
            for mk in keys:
                agg[k][mk] = float(np.mean([r[k][mk] for r in runs]))
        agg["dlnn"]["gate"] = float(np.mean([r["dlnn"]["gate"] for r in runs]))
        agg["var_true_ratio"] = float(np.mean([r["diagnostics"]["var_true_ratio"] for r in runs]))
        results["scenes"][scene_name]["agg"] = agg

        m_b = agg["baseline"]["MAE"]
        m_l = agg["lstm"]["MAE"]
        m_d = agg["dlnn"]["MAE"]
        r_l = agg["lstm"]["R2"]
        r_d = agg["dlnn"]["R2"]
        print(
            f"  baseline MAE={m_b:.4f} | lstm MAE={m_l:.4f} R2={r_l:.3f} | "
            f"dlnn MAE={m_d:.4f} R2={r_d:.3f} gate={agg['dlnn']['gate']:.3f} | "
            f"var_true_ratio={agg['var_true_ratio']:.4f}",
            flush=True,
        )

        # 配对 t（DL-LNN vs LSTM，3 seeds）
        flat_l = np.array([r["lstm"]["MAE"] for r in runs])
        flat_d = np.array([r["dlnn"]["MAE"] for r in runs])
        if np.std(flat_l - flat_d) > 0:
            t_stat, p_val = stats.ttest_rel(flat_l, flat_d)
            results["scenes"][scene_name]["paired_t"] = {"t": float(t_stat), "p": float(p_val), "n": int(len(flat_l))}
            print(f"  dlnn vs lstm paired t: p={p_val:.4f} (n={len(flat_l)})", flush=True)

    OUTPUT_DIR.mkdir(exist_ok=True)
    FIG_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "cross_dataset_transfer_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {out_file}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
        for ax, (sn, sdata) in zip(axes, results["scenes"].items()):
            agg = sdata["agg"]
            labels = ["Taylor", "LSTM", "DL-LNN"]
            maes = [agg["baseline"]["MAE"], agg["lstm"]["MAE"], agg["dlnn"]["MAE"]]
            bars = ax.bar(labels, maes, color=["#888888", "#d98c8c", "#8c9ad9"])
            for b, v in zip(bars, maes):
                ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.4f}", ha="center", va="bottom", fontsize=9)
            ax.set_title(f"Scene {sn}\n(var_true_ratio={agg['var_true_ratio']:.4f})")
            ax.set_ylabel("MAE (mm)")
            ax.grid(axis="y", alpha=0.3)
        fig.suptitle("exp52 cross-dataset transfer: zero-shot generalization (3 seeds mean)")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig52_cross_dataset_transfer.png", dpi=150)
        plt.close(fig)
        print(f"figure saved to {FIG_DIR / 'fig52_cross_dataset_transfer.png'}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
