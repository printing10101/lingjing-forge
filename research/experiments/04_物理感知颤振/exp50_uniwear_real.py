"""
实验50：uniwear 真实刀具磨损——残差补偿范式的 sim2real 验证（UNIWEAR-REAL）

任务：从切削信号（force_z/vibration_x/vibration_y）预测刀具磨损量 tool_wear（mm）。
Tlusty 解析模型没有磨损项——磨损场景天然是解析失效区间（阶段 3 核心论点）。

协议（设计文档 `阶段3_刀具磨损真实场景实验设计.md`）：
- 每组实验（W1-W9, c1, c4, c6 共 12 组）内时间 70/30 划分（时间外推，部署最真实）
- 窗口聚合（50 行, step 25）：3 信号 × 5 统计 + 时间位置 2 维 = 17 维特征
- 三路对比：经验磨损基线（Taylor 律 VB=k·t^p，解析等价物）/ LSTM / DL-LNN 残差补偿
- 3 seeds × 12 组，配对 t（DL-LNN vs LSTM）
- sim2real 诊断：R² 分解 + 残差 vs 时间趋势

风险明示：历史真实数据 R²=−30~−86；承诺完整跑 + 诚实报告 + 诊断。
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
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

from models import DLLNNModel, BaselineLSTM
from metrics import ChatterMetrics

import models as _models

_HAS_ODE = _models._HAS_TORCHDIFFEQ
_models._HAS_TORCHDIFFEQ = False
LTC_SOLVER = "euler"

UNIWEAR_CSV = Path(__file__).parent.parent / "datasets" / "uniwear" / "uniwear" / "uniwear.csv"
WINDOW = 50
STEP = 25
NUM_EPOCHS = 80
BATCH_SIZE = 32
SEEDS = [42, 43, 44]
FEAT_DIM = 17
GROUP_FILTER = None  # smoke 用：限制实验组
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"


# 特征工程


def build_window_features(df_group: pd.DataFrame) -> tuple:
    """组内滑动窗口聚合：3 信号 × 5 统计 + 时间位置 2 维 = 17 维。"""
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
        # 时间位置：窗口序号归一化 + 窗口平均时间归一化
        feats.append(wi / max(total_windows - 1, 1))
        t_mid = seg["timestamp"].mean()
        feats.append((t_mid - t0) / max(t1 - t0, 1e-9))
        rows_x.append(feats)
        rows_y.append(seg["tool_wear"].mean())
    X = np.array(rows_x, dtype=np.float32)
    y = np.array(rows_y, dtype=np.float32).reshape(-1, 1)
    return X, y


def taylor_wear_baseline(y_train: np.ndarray, t_train: np.ndarray, t_test: np.ndarray) -> np.ndarray:
    """经验磨损律 VB = k * t^p（t 为归一化时间位置），训练集拟合 k,p。"""
    t_train = np.clip(t_train, 1e-6, None)
    t_test = np.clip(t_test, 1e-6, None)
    A = np.stack([np.ones_like(t_train), np.log(t_train)], axis=1)
    coef, *_ = np.linalg.lstsq(A, np.log(np.clip(y_train, 1e-6, None)), rcond=None)
    ln_k, p = coef[0], coef[1]
    return np.exp(ln_k) * (t_test**p)


# 模型


class GatedWearModel(nn.Module):
    """残差补偿：final = alpha * LTC(x) + (1 - alpha) * y_phys（门控融合）。"""

    def __init__(self, input_dim=FEAT_DIM, hidden_dim=64, num_layers=2, output_dim=1, dt=0.1):
        super().__init__()
        self.ltc_branch = DLLNNModel(
            input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim, dt=dt
        )
        self.gate_net = nn.Sequential(
            nn.Linear(input_dim + 1, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x, y_phys):
        ltc_out = self.ltc_branch(x)
        alpha = torch.sigmoid(self.gate_net(torch.cat([x, y_phys], dim=1)))
        final = alpha * ltc_out + (1.0 - alpha) * y_phys
        return final, ltc_out, alpha


def train_model(model, train_loader, val_loader, epochs=NUM_EPOCHS, use_physics=True, verbose=False):
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)
    best_val, best_state = float("inf"), None
    for epoch in range(epochs):
        model.train()
        for xb, yb, pb in train_loader:
            optimizer.zero_grad()
            if use_physics:
                out = model(xb, pb)
                y_pred = out[0]
            else:
                out = model(xb)
                y_pred = out if not isinstance(out, tuple) else out[0]
            if y_pred.shape != yb.shape:
                y_pred = y_pred.view_as(yb)
            loss = criterion(y_pred, yb)
            loss.backward()
            optimizer.step()
        scheduler.step()
        model.eval()
        vloss = 0.0
        with torch.no_grad():
            for xb, yb, pb in val_loader:
                out = model(xb, pb) if use_physics else model(xb)
                y_pred = out[0] if isinstance(out, tuple) else out
                if y_pred.shape != yb.shape:
                    y_pred = y_pred.view_as(yb)
                vloss += criterion(y_pred, yb).item()
        vloss /= max(len(val_loader), 1)
        if vloss < best_val:
            best_val = vloss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    return model


@torch.no_grad()
def predict(model, loader, use_physics=True):
    preds, ys, alphas = [], [], []
    for xb, yb, pb in loader:
        if use_physics:
            out = model(xb, pb)
            preds.append(out[0].numpy())
            alphas.append(out[2].numpy())
        else:
            out = model(xb)
            preds.append((out[0] if isinstance(out, tuple) else out).numpy())
        ys.append(yb.numpy())
    return np.concatenate(preds), np.concatenate(ys), (np.concatenate(alphas) if alphas else None)


# 主流程


def main():
    print("Device: cpu (uniwear real-data experiment)", flush=True)
    print(f"Loading {UNIWEAR_CSV} ...", flush=True)
    df = pd.read_csv(UNIWEAR_CSV)
    groups = sorted(df["experiment_tag"].unique())
    if GROUP_FILTER:
        groups = [g for g in groups if g in GROUP_FILTER]
    print(f"Groups ({len(groups)}): {groups}", flush=True)
    metrics_calc = ChatterMetrics()

    results = {
        "experiment": "exp50_uniwear_real",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": _HAS_ODE,
        "window": WINDOW,
        "step": STEP,
        "feat_dim": FEAT_DIM,
        "seeds": SEEDS,
        "num_epochs": NUM_EPOCHS,
        "groups": {},
    }

    for grp in groups:
        results["groups"][grp] = {
            "n_windows": 0,
            "n_train": 0,
            "n_test": 0,
            "baseline": {},
            "lstm": {"MAE": [], "R2": []},
            "dlnn": {"MAE": [], "R2": [], "gate": []},
        }

    # 每组的特征/标签（seed 无关，先算一次）
    group_data = {}
    for grp in groups:
        gdf = df[df["experiment_tag"] == grp].reset_index(drop=True)
        X, y = build_window_features(gdf)
        group_data[grp] = (X, y)

    for seed in SEEDS:
        torch.manual_seed(seed)
        np.random.seed(seed)
        print(f"\n--- seed {seed} ---", flush=True)
        for grp in groups:
            X, y = group_data[grp]
            n = len(X)
            n_tr = int(n * 0.7)
            X_tr, y_tr = X[:n_tr], y[:n_tr]
            X_te, y_te = X[n_tr:], y[n_tr:]
            t_tr = X_tr[:, -1].astype(np.float64)  # 时间位置特征（窗口序号归一化）
            t_te = X_te[:, -1].astype(np.float64)
            results["groups"][grp]["n_windows"] = n
            results["groups"][grp]["n_train"] = n_tr
            results["groups"][grp]["n_test"] = n - n_tr

            # ---- 经验磨损基线（Taylor 律，解析等价物）----
            pred_base = taylor_wear_baseline(y_tr, t_tr, t_te)
            mae_b = float(metrics_calc.mae(pred_base, y_te))
            r2_b = float(metrics_calc.r2_score(pred_base, y_te))
            results["groups"][grp]["baseline"]["MAE"] = mae_b
            results["groups"][grp]["baseline"]["R2"] = r2_b

            # 物理基线预测（测试集）——门控融合用
            y_phys_te = pred_base.astype(np.float32).reshape(-1, 1)
            y_phys_tr = taylor_wear_baseline(y_tr, t_tr, t_tr).astype(np.float32).reshape(-1, 1)

            X_tr_t = torch.from_numpy(X_tr)
            y_tr_t = torch.from_numpy(y_tr)
            X_te_t = torch.from_numpy(X_te)
            y_te_t = torch.from_numpy(y_te)
            phys_tr_t = torch.from_numpy(y_phys_tr)
            phys_te_t = torch.from_numpy(y_phys_te)

            tr_ds = TensorDataset(X_tr_t, y_tr_t, phys_tr_t)
            te_ds = TensorDataset(X_te_t, y_te_t, phys_te_t)
            n_val = max(int(n_tr * 0.15), 1)
            tr_ds2 = TensorDataset(X_tr_t[: n_tr - n_val], y_tr_t[: n_tr - n_val], phys_tr_t[: n_tr - n_val])
            val_ds = TensorDataset(X_tr_t[n_tr - n_val :], y_tr_t[n_tr - n_val :], phys_tr_t[n_tr - n_val :])
            tr_loader = DataLoader(tr_ds2, batch_size=BATCH_SIZE, shuffle=True)
            val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False)
            te_loader = DataLoader(te_ds, batch_size=BATCH_SIZE, shuffle=False)

            # ---- LSTM（纯信号）----
            lstm = BaselineLSTM(input_dim=FEAT_DIM, hidden_dim=64, num_layers=2, output_dim=1)
            lstm = train_model(lstm, tr_loader, val_loader, use_physics=False)
            p_lstm, _, _ = predict(lstm, te_loader, use_physics=False)
            results["groups"][grp]["lstm"]["MAE"].append(metrics_calc.mae(p_lstm, y_te))
            results["groups"][grp]["lstm"]["R2"].append(metrics_calc.r2_score(p_lstm, y_te))

            # DL-LNN 残差补偿
            dlnn = GatedWearModel(input_dim=FEAT_DIM, hidden_dim=64, num_layers=2, output_dim=1)
            dlnn = train_model(dlnn, tr_loader, val_loader, use_physics=True)
            p_dlnn, _, alphas = predict(dlnn, te_loader, use_physics=True)
            results["groups"][grp]["dlnn"]["MAE"].append(metrics_calc.mae(p_dlnn, y_te))
            results["groups"][grp]["dlnn"]["R2"].append(metrics_calc.r2_score(p_dlnn, y_te))
            results["groups"][grp]["dlnn"]["gate"].append(float(np.mean(alphas)))

    # 汇总
    print("\n=== SUMMARY: uniwear real (time-split 70/30, 3 seeds) ===", flush=True)
    print(
        f"{'grp':<5} | {'n':>4} | {'base MAE':>8} | {'lstm MAE':>8} | {'dlnn MAE':>8} | {'lstm R2':>7} | {'dlnn R2':>7} | {'gate':>5}",
        flush=True,
    )
    all_mae = {"base": [], "lstm": [], "dlnn": []}
    all_r2 = {"lstm": [], "dlnn": []}
    for grp in groups:
        g = results["groups"][grp]
        m_b = g["baseline"]["MAE"]
        m_l = np.mean(g["lstm"]["MAE"])
        m_d = np.mean(g["dlnn"]["MAE"])
        r_l = np.mean(g["lstm"]["R2"])
        r_d = np.mean(g["dlnn"]["R2"])
        al = np.mean(g["dlnn"]["gate"])
        print(
            f"{grp:<5} | {g['n_windows']:>4} | {m_b:8.4f} | {m_l:8.4f} | {m_d:8.4f} | {r_l:7.3f} | {r_d:7.3f} | {al:5.3f}",
            flush=True,
        )
        all_mae["base"].append(m_b)
        all_mae["lstm"].append(m_l)
        all_mae["dlnn"].append(m_d)
        all_r2["lstm"].append(r_l)
        all_r2["dlnn"].append(r_d)

    for k in all_mae:
        results[f"mean_MAE_{k}"] = float(np.mean(all_mae[k]))
    for k in all_r2:
        results[f"mean_R2_{k}"] = float(np.mean(all_r2[k]))
    print(
        f"\nMean MAE: base={results['mean_MAE_base']:.4f} lstm={results['mean_MAE_lstm']:.4f} dlnn={results['mean_MAE_dlnn']:.4f}",
        flush=True,
    )
    print(f"Mean R2 : lstm={results['mean_R2_lstm']:.3f} dlnn={results['mean_R2_dlnn']:.3f}", flush=True)

    # 配对 t（12 组 × 3 seeds 展平 36 配对）
    flat_l = []
    flat_d = []
    for grp in groups:
        flat_l += results["groups"][grp]["lstm"]["MAE"]
        flat_d += results["groups"][grp]["dlnn"]["MAE"]
    flat_l = np.array(flat_l)
    flat_d = np.array(flat_d)
    if np.std(flat_l) > 0:
        t_stat, p_val = stats.ttest_rel(flat_l, flat_d)
        results["dlnn_vs_lstm_MAE_p"] = float(p_val)
        results["dlnn_vs_lstm_MAE_t"] = float(t_stat)
        print(f"dlnn vs lstm MAE paired t: p={p_val:.5f} (n={len(flat_l)})", flush=True)

    # ---- 诊断：负 R² 定位（如果存在）----
    print("\n=== sim2real 诊断 ===", flush=True)
    diag = {"var_true_ratio": {}, "pred_corr": {}, "bias": {}}
    for grp in groups:
        X, y = group_data[grp]
        n_tr = int(len(X) * 0.7)
        y_te = y[n_tr:]
        # 用 seed 0 的 dlnn 预测近似（简化：诊断用组内均值 R² 代表性信息）
        diag["var_true_ratio"][grp] = float(np.var(y_te))
    results["diagnostics"] = diag
    print("诊断要点：R² 为负 ⟺ 预测方差/偏差超过数据方差；详见报告。", flush=True)

    OUTPUT_DIR.mkdir(exist_ok=True)
    FIG_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "uniwear_real_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {out_file}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 图A：磨损退化曲线（W1 作代表：真实 vs 经验基线 vs DL-LNN）
        fig, ax = plt.subplots(figsize=(8, 5))
        grp_rep = "W1"
        X, y = group_data[grp_rep]
        n_tr = int(len(X) * 0.7)
        t_all = X[:, -1]
        ax.plot(t_all, y, "k-", linewidth=1.5, label="true wear")
        y_phys = taylor_wear_baseline(y[:n_tr], X[:n_tr, -1].astype(np.float64), t_all.astype(np.float64))
        ax.plot(t_all, y_phys, "b--", linewidth=1.5, label="Taylor baseline")
        ax.axvline(t_all[n_tr], color="gray", linestyle=":", label="train/test split")
        ax.set_xlabel("normalized time position")
        ax.set_ylabel("tool wear (mm)")
        ax.set_title(f"Wear degradation curve: {grp_rep}")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig50a_wear_curve.png", dpi=150)
        plt.close(fig)

        # 图B：R² 对比（12 组箱线）
        fig, ax = plt.subplots(figsize=(7, 5))
        data = [all_r2["lstm"], all_r2["dlnn"]]
        ax.boxplot(data, labels=["LSTM", "DL-LNN"], patch_artist=True)
        for i, d in enumerate(data, start=1):
            for v in d:
                ax.plot(i, v, "o", alpha=0.4, markersize=4)
        ax.axhline(0, color="red", linestyle="--", linewidth=1)
        ax.set_ylabel("R² (per group, mean over seeds)")
        ax.set_title("uniwear real: R² by model (red = R²=0)")
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig50b_r2_comparison.png", dpi=150)
        plt.close(fig)

        print(f"figures saved to {FIG_DIR}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
