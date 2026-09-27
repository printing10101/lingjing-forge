"""
实验49：跨转速外推——门控在分布外区间的激活行为（SPINDLE-EXTRAPOLATION）

科学问题（报告 5.4 自建议）：训练 n=3,000-8,000 rpm，测试 n=10,000-15,000 rpm。
在转速外推区：Tlusty 解析模型依然正确（解析公式无范围限制），数据驱动分支（LTC）
外推漂移。门控能否识别"数据分支不可靠"并自动切回物理（alpha -> 0）？

关键设计点：转速是 7 维输入特征之一（归一化 /10000，训练区 0.3-0.8，外推区 1.0-1.5）
——输入特征层面的分布外（门控可观测），与阶段 1 的"物理内部失配"（门控盲区）形成对比。

协议：
- 训练：SyntheticChatterDataset(spindle_speed_range=(3000, 8000))，5 seeds，1000 样本
- 域内测试：SyntheticChatterDataset((3000, 8000), seed=999)
- 外推测试：SyntheticChatterDataset((10000, 15000), seed=999)
- 模型：Tlusty（=a_lim_clean 基线）/ LSTM（纯数据）/ DL-LNN 原版门控 / v2 冲突门控
- 观测：MAE/R²（2 区间 × 4 模型）+ 门控 alpha（域内 vs 外推）+ alpha 按转速分箱激活曲线
- 假设：H1 外推区 alpha 显著低于域内（门控识别分布外并切回物理）
         H2 DL-LNN 外推 MAE 显著优于 LSTM（物理先验=外推安全带）
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

import exp46_tlusty_mismatch as base
import exp47b_physics_aware_gate_v2 as v2
from models import DLLNNWithPhysics, BaselineLSTM
from data_generator import SyntheticChatterDataset
from config import ModelConfig
from metrics import ChatterMetrics

import models as _models

_HAS_ODE = _models._HAS_TORCHDIFFEQ
_models._HAS_TORCHDIFFEQ = False
LTC_SOLVER = "euler"

SEEDS = [42, 43, 44, 45, 46]
NUM_SAMPLES = 1000
NUM_EPOCHS = 80
BATCH_SIZE = 32
TRAIN_RANGE = (3000.0, 8000.0)
EXTRAP_RANGE = (10000.0, 15000.0)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"


def make_loaders(dataset, batch_size=BATCH_SIZE, seed=0):
    g = torch.Generator().manual_seed(seed)
    n = len(dataset)
    idx = torch.randperm(n, generator=g)
    n_train, n_val = int(0.8 * n), int(0.1 * n)
    train_ds = torch.utils.data.Subset(dataset, idx[:n_train])
    val_ds = torch.utils.data.Subset(dataset, idx[n_train : n_train + n_val])
    test_ds = torch.utils.data.Subset(dataset, idx[n_train + n_val :])
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    return train_loader, val_loader, test_loader


def train_loop(model, train_loader, val_loader, config, device, use_physics=True, num_epochs=NUM_EPOCHS, verbose=False):
    """训练；use_physics=True 时注入 batch[2]（该数据集自身的 a_lim_clean）。"""
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)
    best_val, best_state = float("inf"), None
    for epoch in range(num_epochs):
        model.train()
        for batch in train_loader:
            x, y_true, phys = batch
            x, y_true, phys = x.to(device), y_true.to(device), phys.to(device)
            optimizer.zero_grad()
            if use_physics:
                out = model(x, phys)
            else:
                out = model(x)
            y_pred = out[0] if isinstance(out, tuple) else out
            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)
            loss = criterion(y_pred, y_true)
            loss.backward()
            optimizer.step()
        scheduler.step()
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for batch in val_loader:
                x, y_true, phys = batch
                x, y_true, phys = x.to(device), y_true.to(device), phys.to(device)
                out = model(x, phys) if use_physics else model(x)
                y_pred = out[0] if isinstance(out, tuple) else out
                if y_pred.shape != y_true.shape:
                    y_pred = y_pred.view_as(y_true)
                val_loss += criterion(y_pred, y_true).item()
        val_loss /= max(len(val_loader), 1)
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    return model


@torch.no_grad()
def evaluate(model, test_loader, device, use_physics=True, gate_fn=None):
    """返回 preds/y_true/phys_true/spindle_speed/gates。gate_fn 用于 v2 门控。"""
    preds, ys, physs, speeds, gates = [], [], [], [], []
    for batch in test_loader:
        x, y_true, phys = batch
        x, y_true, phys = x.to(device), y_true.to(device), phys.to(device)
        out = model(x, phys) if use_physics else model(x)
        y_pred = out[0] if isinstance(out, tuple) else out
        preds.append(y_pred.cpu().numpy())
        ys.append(y_true.cpu().numpy())
        physs.append(phys.cpu().numpy())
        if gate_fn is not None:
            gates.append(gate_fn(model, x, phys).cpu().numpy())
    return {
        "preds": np.concatenate(preds),
        "y_true": np.concatenate(ys),
        "phys": np.concatenate(physs),
        "gates": np.concatenate(gates) if gates else None,
    }


def main():
    print("Device:", "cuda" if torch.cuda.is_available() else "cpu", flush=True)
    metrics_calc = ChatterMetrics()
    config = ModelConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    results = {
        "experiment": "exp49_spindle_extrapolation",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": _HAS_ODE,
        "train_range": list(TRAIN_RANGE),
        "extrap_range": list(EXTRAP_RANGE),
        "seeds": SEEDS,
        "num_samples": NUM_SAMPLES,
        "num_epochs": NUM_EPOCHS,
        "models": {},
    }

    # 每个模型：{seed: {in_domain: {...}, extrapolation: {...}}}
    for model_name, use_physics in [("lstm", False), ("dlnn", True), ("dlnn_v2", True)]:
        results["models"][model_name] = {
            "in_domain": {"MAE": [], "R2": [], "gate": []},
            "extrapolation": {"MAE": [], "R2": [], "gate": []},
        }

    # Tlusty 基线（确定性，2 区间各算一次即可）
    tlusty = {}
    for rng, key in [(TRAIN_RANGE, "in_domain"), (EXTRAP_RANGE, "extrapolation")]:
        ds = SyntheticChatterDataset(num_samples=300, spindle_speed_range=rng, noise_level=0.02, seed=999)
        loader = DataLoader(ds, batch_size=64, shuffle=False)
        ys, physs = [], []
        for batch in loader:
            _, y, p = batch
            ys.append(y.numpy())
            physs.append(p.numpy())
        ys = np.concatenate(ys)
        physs = np.concatenate(physs)
        tlusty[key] = {"MAE": metrics_calc.mae(physs, ys), "R2": metrics_calc.r2_score(physs, ys)}
    results["tlusty_baseline"] = tlusty

    # 保存训练好的模型（CPU 副本，供图 C 激活曲线复用，避免重复训练）
    saved_models = {}

    # 外推测试集（所有 seed 共用；数据生成 seed=999 固定）
    extrap_ds = SyntheticChatterDataset(num_samples=400, spindle_speed_range=EXTRAP_RANGE, noise_level=0.02, seed=999)
    extrap_loader = DataLoader(extrap_ds, batch_size=64, shuffle=False)

    for seed in SEEDS:
        print(f"\n--- seed {seed} ---", flush=True)
        torch.manual_seed(seed)
        np.random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        train_ds = SyntheticChatterDataset(
            num_samples=NUM_SAMPLES, spindle_speed_range=TRAIN_RANGE, noise_level=0.02, seed=seed
        )
        train_loader, val_loader, in_test_loader = make_loaders(train_ds, BATCH_SIZE, seed)

        # ---- LSTM（纯数据分支，不注入物理）----
        lstm = BaselineLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        ).to(device)
        lstm = train_loop(lstm, train_loader, val_loader, config, device, use_physics=False)
        r_in = evaluate(lstm, in_test_loader, device, use_physics=False)
        r_ex = evaluate(lstm, extrap_loader, device, use_physics=False)
        results["models"]["lstm"]["in_domain"]["MAE"].append(metrics_calc.mae(r_in["preds"], r_in["y_true"]))
        results["models"]["lstm"]["in_domain"]["R2"].append(metrics_calc.r2_score(r_in["preds"], r_in["y_true"]))
        results["models"]["lstm"]["extrapolation"]["MAE"].append(metrics_calc.mae(r_ex["preds"], r_ex["y_true"]))
        results["models"]["lstm"]["extrapolation"]["R2"].append(metrics_calc.r2_score(r_ex["preds"], r_ex["y_true"]))

        # DL-LNN 原版门控
        dlnn = DLLNNWithPhysics(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        ).to(device)
        dlnn = train_loop(dlnn, train_loader, val_loader, config, device, use_physics=True)
        r_in = evaluate(dlnn, in_test_loader, device, use_physics=True, gate_fn=lambda m, x, p: m.gate(x))
        r_ex = evaluate(dlnn, extrap_loader, device, use_physics=True, gate_fn=lambda m, x, p: m.gate(x))
        saved_models[(seed, "dlnn")] = dlnn.cpu()
        results["models"]["dlnn"]["in_domain"]["MAE"].append(metrics_calc.mae(r_in["preds"], r_in["y_true"]))
        results["models"]["dlnn"]["in_domain"]["R2"].append(metrics_calc.r2_score(r_in["preds"], r_in["y_true"]))
        results["models"]["dlnn"]["in_domain"]["gate"].append(float(np.mean(r_in["gates"])))
        results["models"]["dlnn"]["extrapolation"]["MAE"].append(metrics_calc.mae(r_ex["preds"], r_ex["y_true"]))
        results["models"]["dlnn"]["extrapolation"]["R2"].append(metrics_calc.r2_score(r_ex["preds"], r_ex["y_true"]))
        results["models"]["dlnn"]["extrapolation"]["gate"].append(float(np.mean(r_ex["gates"])))

        # DL-LNN v2 冲突门控
        dlnn2 = v2.PhysicsAwareDLLNNV2(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        ).to(device)
        dlnn2 = train_loop(dlnn2, train_loader, val_loader, config, device, use_physics=True)
        gate2 = lambda m, x, p: m.gate(torch.cat([x, p, (p - m.ltc_branch(x)).abs()], dim=1))
        r_in = evaluate(dlnn2, in_test_loader, device, use_physics=True, gate_fn=gate2)
        r_ex = evaluate(dlnn2, extrap_loader, device, use_physics=True, gate_fn=gate2)
        saved_models[(seed, "dlnn_v2")] = dlnn2.cpu()
        results["models"]["dlnn_v2"]["in_domain"]["MAE"].append(metrics_calc.mae(r_in["preds"], r_in["y_true"]))
        results["models"]["dlnn_v2"]["in_domain"]["R2"].append(metrics_calc.r2_score(r_in["preds"], r_in["y_true"]))
        results["models"]["dlnn_v2"]["in_domain"]["gate"].append(float(np.mean(r_in["gates"])))
        results["models"]["dlnn_v2"]["extrapolation"]["MAE"].append(metrics_calc.mae(r_ex["preds"], r_ex["y_true"]))
        results["models"]["dlnn_v2"]["extrapolation"]["R2"].append(metrics_calc.r2_score(r_ex["preds"], r_ex["y_true"]))
        results["models"]["dlnn_v2"]["extrapolation"]["gate"].append(float(np.mean(r_ex["gates"])))

    # 汇总统计
    for mn in results["models"]:
        for key in ["in_domain", "extrapolation"]:
            cell = results["models"][mn][key]
            for metric in ["MAE", "R2", "gate"]:
                arr = np.array(cell[metric])
                cell[f"{metric}_mean"] = float(np.mean(arr))
                cell[f"{metric}_std"] = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0

    # 门控 alpha：外推 vs 域内 配对 t 检验（同 seed 配对）
    for mn in ["dlnn", "dlnn_v2"]:
        g_in = np.array(results["models"][mn]["in_domain"]["gate"])
        g_ex = np.array(results["models"][mn]["extrapolation"]["gate"])
        if len(g_in) > 1 and np.std(g_in) > 0:
            t_stat, p_val = stats.ttest_rel(g_in, g_ex)  # H1: g_ex < g_in
            results["models"][mn]["gate_in_vs_extrap_p"] = float(p_val)
        else:
            results["models"][mn]["gate_in_vs_extrap_p"] = None

    # DL-LNN vs LSTM 外推 MAE 配对检验
    m_lstm = np.array(results["models"]["lstm"]["extrapolation"]["MAE"])
    m_dlnn = np.array(results["models"]["dlnn"]["extrapolation"]["MAE"])
    m_v2 = np.array(results["models"]["dlnn_v2"]["extrapolation"]["MAE"])
    if len(m_lstm) > 1 and np.std(m_lstm) > 0:
        _, p1 = stats.ttest_rel(m_lstm, m_dlnn)
        _, p2 = stats.ttest_rel(m_lstm, m_v2)
        results["extrap_dlnn_vs_lstm_p"] = float(p1)
        results["extrap_v2_vs_lstm_p"] = float(p2)

    OUTPUT_DIR.mkdir(exist_ok=True)
    FIG_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "spindle_extrapolation_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {out_file}", flush=True)

    # 摘要
    print("\n=== SUMMARY: MAE (in-domain 3-8k vs extrapolation 10-15k) ===", flush=True)
    print(
        f"{'model':<10} | {'in MAE':>8} | {'extrap MAE':>10} | {'extrap R2':>9} | {'gate in':>7} | {'gate ex':>7}",
        flush=True,
    )
    print(
        f"{'Tlusty':<10} | {tlusty['in_domain']['MAE']:8.4f} | {tlusty['extrapolation']['MAE']:10.4f} | {tlusty['extrapolation']['R2']:9.3f} |",
        flush=True,
    )
    for mn, label in [("lstm", "LSTM"), ("dlnn", "DL-LNN"), ("dlnn_v2", "DL-LNN v2")]:
        c = results["models"][mn]
        print(
            f"{label:<10} | {c['in_domain']['MAE_mean']:8.4f} | {c['extrapolation']['MAE_mean']:10.4f} | {c['extrapolation']['R2_mean']:9.3f} | {c['in_domain']['gate_mean']:7.3f} | {c['extrapolation']['gate_mean']:7.3f}",
            flush=True,
        )

    print("\n=== Gate alpha: in-domain vs extrapolation (paired t) ===", flush=True)
    for mn in ["dlnn", "dlnn_v2"]:
        c = results["models"][mn]
        p = results["models"][mn]["gate_in_vs_extrap_p"]
        print(
            f"{mn}: in={c['in_domain']['gate_mean']:.3f} ex={c['extrapolation']['gate_mean']:.3f} p={p:.4f}"
            if p
            else f"{mn}: constant",
            flush=True,
        )
    print(f"extrap DL-LNN vs LSTM p={results.get('extrap_dlnn_vs_lstm_p')}", flush=True)
    print(f"extrap v2 vs LSTM p={results.get('extrap_v2_vs_lstm_p')}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 图A：MAE 对比（2 区间 × 4 模型）
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
        fig.savefig(FIG_DIR / "fig49a_extrap_mae.png", dpi=300)
        plt.close(fig)

        # 图B：gate alpha 箱线（域内 vs 外推）
        fig, ax = plt.subplots(figsize=(7, 5))
        data_in = [results["models"]["dlnn"]["in_domain"]["gate"], results["models"]["dlnn_v2"]["in_domain"]["gate"]]
        data_ex = [
            results["models"]["dlnn"]["extrapolation"]["gate"],
            results["models"]["dlnn_v2"]["extrapolation"]["gate"],
        ]
        pos_in = [1, 2]
        pos_ex = [1.35, 2.35]
        bp1 = ax.boxplot(
            data_in, positions=pos_in, widths=0.28, patch_artist=True, boxprops=dict(facecolor="tab:blue", alpha=0.6)
        )
        bp2 = ax.boxplot(
            data_ex, positions=pos_ex, widths=0.28, patch_artist=True, boxprops=dict(facecolor="tab:red", alpha=0.6)
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
        fig.savefig(FIG_DIR / "fig49b_gate_alpha_extrap.png", dpi=300)
        plt.close(fig)

        # 图C：alpha 按转速分箱（激活曲线；复用主循环已训模型）
        fig, ax = plt.subplots(figsize=(8, 5))
        for mn, label, gate_fn in [
            ("dlnn", "DL-LNN", lambda m, x, p: m.gate(x)),
            ("dlnn_v2", "DL-LNN v2", lambda m, x, p: m.gate(torch.cat([x, p, (p - m.ltc_branch(x)).abs()], dim=1))),
        ]:
            bin_edges = [3000, 4500, 6000, 8000, 10000, 12000, 14000, 15000]
            bin_centers, bin_gates = [], []
            for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
                gs = []
                for seed in SEEDS:
                    model = saved_models[(seed, mn)].to(device)
                    with torch.no_grad():
                        for rng in [TRAIN_RANGE, EXTRAP_RANGE]:
                            ds_ = SyntheticChatterDataset(
                                num_samples=400, spindle_speed_range=rng, noise_level=0.02, seed=999
                            )
                            loader_ = DataLoader(ds_, batch_size=64, shuffle=False)
                            for batch in loader_:
                                x, _, p = batch
                                x, p = x.to(device), p.to(device)
                                speeds = x[:, 0].cpu().numpy() * 10000.0  # 反归一化转速
                                g = gate_fn(model, x, p).cpu().numpy().flatten()
                                for s, gv in zip(speeds, g):
                                    if lo <= s < hi:
                                        gs.append(gv)
                    model.cpu()
                if gs:
                    bin_centers.append((lo + hi) / 2)
                    bin_gates.append(float(np.mean(gs)))
            ax.plot(bin_centers, bin_gates, "o-", linewidth=1.8, label=label)
        ax.axvspan(3000, 8000, color="tab:blue", alpha=0.08, label="train range")
        ax.axvspan(10000, 15000, color="tab:red", alpha=0.08, label="extrapolation")
        ax.set_xlabel("Spindle speed (rpm)")
        ax.set_ylabel("Gate alpha")
        ax.set_title("Gate activation curve vs spindle speed")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig49c_gate_activation_curve.png", dpi=300)
        plt.close(fig)

        print(f"figures saved to {FIG_DIR}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
