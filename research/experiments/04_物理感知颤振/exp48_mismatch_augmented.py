"""
实验48：失配数据增强训练（Mismatch-Augmented Training）——修复"未见失配不防御"

动机（exp47/exp47b 双重证伪的深层机理）：门控无论输入什么特征，都无法防御
训练中从未出现的失配——train=0% 时门控从未见过"物理与数据大冲突"样本。
修复路径（报告 §4.3 提出）：训练时显式注入失配样本（失配数据增强）。

方案：训练时每个 batch 随机从 DELTAS 抽一个失配档作为该 batch 的物理预测
（含 0%）。门控在训练中见过全部冲突强度 -> 测试时遇到任意新失配 -> 防御。

协议：5 seeds，其余与 exp46/47b 完全一致。模型 = exp47b v2（冲突信号门控，
增强效果应最佳）。对比：
  H1 对角线（与 47b 一致性）：增强不应退化（训练分布变宽，期望保持或更好）
  H2 通用防御（本次核心）：aug 训练后，test=20%/40% 的 MAE 应接近对角线水平
     （0.36/0.52 级别），而非未防御的 0.88/1.44
  H3 门控 alpha 随 test_delta 上升（见过失配 -> 自动防御的机制证据）
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime

os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import torch
import torch.nn.functional as F
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

import exp46_tlusty_mismatch as base
import exp47b_physics_aware_gate_v2 as v2
from config import ModelConfig
from metrics import ChatterMetrics

import models as _models

_HAS_ODE = _models._HAS_TORCHDIFFEQ
_models._HAS_TORCHDIFFEQ = False
LTC_SOLVER = "euler"

DELTAS = base.DELTAS
SEEDS = base.SEEDS
NUM_SAMPLES = base.NUM_SAMPLES
NUM_EPOCHS = base.NUM_EPOCHS
BATCH_SIZE = base.BATCH_SIZE
TLUSTY_MAE = v2.TLUSTY_MAE

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"


def train_model_augmented(model, train_loader, val_loader, config, device, num_epochs=NUM_EPOCHS, verbose=False):
    """训练时每个 batch 随机注入失配档（失配数据增强）。

    与 exp46 train_model 的唯一差异：physics 不固定为 train_delta，
    而是每个 batch 从 DELTAS 均匀随机抽取。
    """
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs)
    model.train()
    for epoch in range(num_epochs):
        total_loss = 0.0
        for batch in train_loader:
            x, y, _, *phys_list = batch
            x, y = x.to(device), y.to(device)
            delta_idx = np.random.randint(0, len(phys_list))
            phys = phys_list[delta_idx].to(device)
            out = model(x, phys)
            loss = F.mse_loss(out[0], y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        scheduler.step()
        if verbose and (epoch + 1) % 20 == 0:
            print(f"    epoch {epoch + 1}/{num_epochs} loss={total_loss / len(train_loader):.4f}", flush=True)
    return model


def main():
    print("Device:", "cuda" if torch.cuda.is_available() else "cpu", flush=True)
    metrics_calc = ChatterMetrics()
    config = ModelConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    results = {
        "experiment": "exp48_mismatch_augmented_training",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": _HAS_ODE,
        "deltas": DELTAS,
        "seeds": SEEDS,
        "num_samples": NUM_SAMPLES,
        "num_epochs": NUM_EPOCHS,
        "gate_input": "concat(x, y_phys, |y_phys-y_ltc|) [exp47b v2]",
        "training": "mismatch-augmented (per-batch random delta)",
        "matrix": {},
    }

    row_key = "aug"
    results["matrix"][row_key] = {}

    for seed in SEEDS:
        print(f"  --- seed {seed} (augmented) ---", flush=True)
        torch.manual_seed(seed)
        np.random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        dataset = base.MismatchChatterDataset(num_samples=NUM_SAMPLES, seed=seed)
        train_loader, val_loader, test_loader = base.create_loaders(dataset, BATCH_SIZE, seed)

        model = v2.PhysicsAwareDLLNNV2(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        ).to(device)
        model = train_model_augmented(model, train_loader, val_loader, config, device, verbose=False)

        for test_delta in DELTAS:
            t_idx = DELTAS.index(test_delta)
            r = v2.predict_all(model, test_loader, device, t_idx)
            key = str(test_delta)
            if key not in results["matrix"][row_key]:
                results["matrix"][row_key][key] = {"MAE_per_seed": [], "R2_per_seed": [], "gate_per_seed": []}
            results["matrix"][row_key][key]["MAE_per_seed"].append(metrics_calc.mae(r["preds"], r["y_true"]))
            results["matrix"][row_key][key]["R2_per_seed"].append(metrics_calc.r2_score(r["preds"], r["y_true"]))
            results["matrix"][row_key][key]["gate_per_seed"].append(float(np.mean(r["gates"])))

    # 汇总 + 统计
    for test_delta in DELTAS:
        key = str(test_delta)
        cell = results["matrix"][row_key][key]
        maes = np.array(cell["MAE_per_seed"])
        cell["MAE_mean"] = float(np.mean(maes))
        cell["MAE_std"] = float(np.std(maes, ddof=1)) if len(maes) > 1 else 0.0
        cell["R2_mean"] = float(np.mean(cell["R2_per_seed"]))
        cell["gate_mean"] = float(np.mean(cell["gate_per_seed"]))
        cell["gate_std"] = float(np.std(cell["gate_per_seed"], ddof=1)) if len(cell["gate_per_seed"]) > 1 else 0.0
        if len(maes) > 1 and np.std(maes, ddof=1) > 0:
            t_stat, p_val = stats.ttest_1samp(maes, TLUSTY_MAE[test_delta], alternative="less")
            cell["p_vs_tlusty"] = float(p_val)
            cell["cohens_d"] = float((TLUSTY_MAE[test_delta] - np.mean(maes)) / np.std(maes, ddof=1))
            cell["gain_vs_tlusty_pct"] = float(
                (TLUSTY_MAE[test_delta] - np.mean(maes)) / TLUSTY_MAE[test_delta] * 100.0
            )
        else:
            cell["p_vs_tlusty"] = None
            cell["cohens_d"] = None
            cell["gain_vs_tlusty_pct"] = float(
                (TLUSTY_MAE[test_delta] - np.mean(maes)) / TLUSTY_MAE[test_delta] * 100.0
            )

    OUTPUT_DIR.mkdir(exist_ok=True)
    FIG_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "mismatch_augmented_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_file}", flush=True)

    # 摘要
    print("\n=== SUMMARY: Augmented training, MAE by test_delta ===", flush=True)
    print("train=aug | " + " | ".join(f"test={d:.0%}" for d in DELTAS) + " | gate_avg", flush=True)
    row = ["aug"]
    for test_delta in DELTAS:
        c = results["matrix"][row_key][str(test_delta)]
        row.append(f"{c['MAE_mean']:.3f}±{c['MAE_std']:.3f}")
    row.append(f"{np.mean([results['matrix'][row_key][str(d)]['gate_mean'] for d in DELTAS]):.3f}")
    print(" | ".join(row), flush=True)

    # 对比 exp46 对角线 / 47b 对角线 / Tlusty
    print("\n=== vs exp46-diag / exp47b-diag / Tlusty ===", flush=True)
    try:
        exp46 = json.load(open(OUTPUT_DIR / "tlusty_mismatch_results.json", encoding="utf-8"))
        exp47b = json.load(open(OUTPUT_DIR / "physics_aware_gate_v2_results.json", encoding="utf-8"))
        print("test_delta | Tlusty | exp46-diag | exp47b-diag | exp48-aug", flush=True)
        for d in DELTAS:
            t46 = exp46["matrix"][str(d)][str(d)]["MAE_mean"]
            t47b = exp47b["matrix"][str(d)][str(d)]["MAE_mean"]
            t48 = results["matrix"][row_key][str(d)]["MAE_mean"]
            print(f"{d:.0%}      | {TLUSTY_MAE[d]:.3f}  | {t46:.3f}     | {t47b:.3f}      | {t48:.3f}", flush=True)
    except Exception as e:
        print(f"  compare failed: {e}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pcts = [d * 100 for d in DELTAS]
        exp46 = json.load(open(OUTPUT_DIR / "tlusty_mismatch_results.json", encoding="utf-8"))
        exp47b = json.load(open(OUTPUT_DIR / "physics_aware_gate_v2_results.json", encoding="utf-8"))

        # 图A：aug 行 vs 各版本对角线（通用防御目标线 = 对角线水平）
        fig, ax = plt.subplots(figsize=(8, 5))
        diag46 = [exp46["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        diag47b = [exp47b["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        aug = [results["matrix"][row_key][str(d)]["MAE_mean"] for d in DELTAS]
        augs = [results["matrix"][row_key][str(d)]["MAE_std"] for d in DELTAS]
        ax.plot(
            pcts, [TLUSTY_MAE[d] for d in DELTAS], "s--", color="tab:red", label="Tlusty (mismatched)", linewidth=1.8
        )
        ax.plot(pcts, diag46, "o-", color="tab:orange", label="exp46 diag (train=test)", linewidth=1.5)
        ax.plot(pcts, diag47b, "^-", color="tab:blue", label="exp47b diag (train=test)", linewidth=1.5)
        ax.errorbar(
            pcts,
            aug,
            yerr=augs,
            fmt="D-",
            color="tab:green",
            label="exp48 augmented (one model, all test d)",
            linewidth=2,
            capsize=3,
        )
        ax.set_xlabel("Test mismatch delta (%)")
        ax.set_ylabel("Test MAE (mm)")
        ax.set_title("Mismatch-augmented training: universal defense?")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig48_aug_universal_defense.png", dpi=150)
        plt.close(fig)

        # 图B：alpha vs test_delta（机制）
        fig, ax = plt.subplots(figsize=(7.5, 5))
        gates = [results["matrix"][row_key][str(d)]["gate_mean"] for d in DELTAS]
        gates_s = [results["matrix"][row_key][str(d)]["gate_std"] for d in DELTAS]
        ax.errorbar(pcts, gates, yerr=gates_s, fmt="D-", color="tab:green", capsize=3, linewidth=1.8)
        ax.set_xlabel("Test mismatch delta (%)")
        ax.set_ylabel("Gate alpha (mean)")
        ax.set_title("Augmented training: alpha vs test mismatch")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig48b_aug_gate_alpha.png", dpi=150)
        plt.close(fig)

        print(f"figures saved to {FIG_DIR}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
