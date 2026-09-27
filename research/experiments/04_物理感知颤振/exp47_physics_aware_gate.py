"""
实验47：物理感知门控（Physics-Aware Gate）——门控输入加入物理预测

动机（阶段1 exp46 诚实限制 #1）：原版门控仅输入 x（7 维特征），无法感知物理分支
是否失配——train=0% 时 α≈0.05 恒定，测试注入任何失配都不防御。

本实验：门控输入 = concat(x, y_phys)（8 维）。门控可直接看到物理预测的值，
训练时（train_delta>0 物理失配）学会"物理预测系统性漂移 -> 不信任物理"；
测试时注入新失配，门控看到物理预测异常 -> 自动防御（α 上升）。

协议：与 exp46 完全相同的 train-delta x test-delta 矩阵（可复现对比）。
核心假设：
  H1 对角线（train==test）：物理感知门控保持 exp46 的显著增益（不退化）
  H2 非对角线（train=0%, test>0）：修复"未见失配不防御"（MAE 低于原版）
  H3 门控 α 随 test_delta 上升（原版恒定）——机制证据
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
from models import DLLNNModel
from config import ModelConfig
from metrics import ChatterMetrics


# Tlusty 确定性基线（从 exp46 结果文件读取，避免重复计算）
def _load_tlusty_mae() -> dict:
    p = Path(__file__).resolve().parent.parent / "results" / "tlusty_mismatch_results.json"
    data = json.load(open(p, encoding="utf-8"))
    tb = data["tlusty_baseline"]["0.0"]
    return {float(k): float(v["MAE"]) for k, v in tb.items()}


TLUSTY_MAE = _load_tlusty_mae()

# 与 exp46 完全相同的求解器决策（诚信记录）
import models as _models

_HAS_ODE = _models._HAS_TORCHDIFFEQ
_models._HAS_TORCHDIFFEQ = False
LTC_SOLVER = "euler"

DELTAS = base.DELTAS
SEEDS = base.SEEDS
NUM_SAMPLES = base.NUM_SAMPLES
NUM_EPOCHS = base.NUM_EPOCHS

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"


class PhysicsAwareDLLNN(nn.Module):
    """物理感知门控版 DL-LNN：门控输入 = concat(x, physics_pred)。

    与 DLLNNWithPhysics 结构一致，唯一差异：gate 的输入维度 input_dim+1，
    且 forward 时 gate 接收 [x, physics_pred] 拼接。
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 64,
        num_layers: int = 3,
        output_dim: int = 1,
        dt: float = 0.1,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.ltc_branch = DLLNNModel(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dt=dt,
            dropout=dropout,
        )
        # 物理感知门控：输入 = x (7) + physics_pred (1) = 8 维
        self.gate = nn.Sequential(
            nn.Linear(input_dim + 1, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )
        self.physics_scale = nn.Parameter(torch.ones(1))
        self.physics_bias = nn.Parameter(torch.zeros(1))

    def forward(self, x: torch.Tensor, physics_pred: torch.Tensor):
        ltc_pred = self.ltc_branch(x)
        gate_in = torch.cat([x, physics_pred], dim=1)
        alpha = self.gate(gate_in)
        final_pred = alpha * ltc_pred + (1 - alpha) * (self.physics_scale * physics_pred + self.physics_bias)
        return final_pred, ltc_pred

    def reset_hidden(self):
        self.ltc_branch.reset_hidden()


@torch.no_grad()
def predict_all(model, test_loader, device, delta_idx: int):
    """推理：注入指定失配档的物理预测，返回 preds/y_true/gates。"""
    preds, y_true, gates = [], [], []
    for batch in test_loader:
        x, y, _, *phys_list = batch
        x = x.to(device)
        phys = phys_list[delta_idx].to(device)
        out = model(x, phys)
        preds.append(out[0].cpu().numpy())
        y_true.append(y.numpy())
        gates.append(model.gate(torch.cat([x, phys], dim=1)).cpu().numpy())
    return {
        "preds": np.concatenate(preds, axis=0),
        "y_true": np.concatenate(y_true, axis=0),
        "gates": np.concatenate(gates, axis=0),
    }


def main():
    print("Device:", "cuda" if torch.cuda.is_available() else "cpu", flush=True)
    metrics_calc = ChatterMetrics()
    config = ModelConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    results = {
        "experiment": "exp47_physics_aware_gate",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": _HAS_ODE,
        "deltas": DELTAS,
        "seeds": SEEDS,
        "num_samples": NUM_SAMPLES,
        "num_epochs": NUM_EPOCHS,
        "gate_input": "concat(x, physics_pred)",
        "matrix": {},
    }

    for train_delta in DELTAS:
        print(f"\n############ train_delta = {train_delta:.2f} ############", flush=True)
        tds = str(train_delta)
        results["matrix"][tds] = {}

        for seed in SEEDS:
            print(f"  --- seed {seed} (train_d={train_delta:.2f}) ---", flush=True)
            torch.manual_seed(seed)
            np.random.seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            dataset = base.MismatchChatterDataset(num_samples=NUM_SAMPLES, seed=seed)
            train_loader, val_loader, test_loader = base.create_loaders(dataset, base.BATCH_SIZE, seed)

            model = PhysicsAwareDLLNN(
                input_dim=config.input_dim,
                hidden_dim=config.hidden_dim,
                num_layers=config.num_layers,
                output_dim=config.output_dim,
                dt=config.ltc_dt,
                dropout=config.dropout,
            ).to(device)
            model = base.train_model(
                model,
                train_loader,
                val_loader,
                config,
                device,
                train_delta=train_delta,
                use_physics=True,
                verbose=False,
            )

            for test_delta in DELTAS:
                t_idx = DELTAS.index(test_delta)
                r = predict_all(model, test_loader, device, t_idx)
                key = str(test_delta)
                if key not in results["matrix"][tds]:
                    results["matrix"][tds][key] = {"MAE_per_seed": [], "R2_per_seed": [], "gate_per_seed": []}
                results["matrix"][tds][key]["MAE_per_seed"].append(metrics_calc.mae(r["preds"], r["y_true"]))
                results["matrix"][tds][key]["R2_per_seed"].append(metrics_calc.r2_score(r["preds"], r["y_true"]))
                results["matrix"][tds][key]["gate_per_seed"].append(float(np.mean(r["gates"])))

        # 汇总 + 统计（对比 Tlusty 基线，来自 exp46 结果文件）
        for test_delta in DELTAS:
            key = str(test_delta)
            cell = results["matrix"][tds][key]
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
    out_file = OUTPUT_DIR / "physics_aware_gate_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_file}", flush=True)

    # 摘要
    print("\n=== SUMMARY: Physics-Aware Gate MAE (train_d -> test_d) ===", flush=True)
    print("train_d | " + " | ".join(f"test={d:.0%}" for d in DELTAS) + " | gate_avg", flush=True)
    for train_delta in DELTAS:
        tds = str(train_delta)
        row = [f"{train_delta:.0%}"]
        for test_delta in DELTAS:
            c = results["matrix"][tds][str(test_delta)]
            row.append(f"{c['MAE_mean']:.3f}±{c['MAE_std']:.3f}")
        row.append(f"{np.mean([results['matrix'][tds][str(d)]['gate_mean'] for d in DELTAS]):.3f}")
        print(" | ".join(row), flush=True)

    # 与原版 exp46 对比
    print("\n=== vs exp46 (original gate): gain difference on key cells ===", flush=True)
    try:
        exp46 = json.load(open(OUTPUT_DIR / "tlusty_mismatch_results.json", encoding="utf-8"))
        print("cell (train,test)      | exp46 MAE | exp47 MAE | delta", flush=True)
        for td, td2 in [(0.0, 0.0), (0.0, 0.2), (0.0, 0.4), (0.2, 0.2), (0.4, 0.4)]:
            m46 = exp46["matrix"][str(td)][str(td2)]["MAE_mean"]
            cell47 = results["matrix"].get(str(td), {}).get(str(td2))
            if cell47 is None:
                m47 = float("nan")
            else:
                m47 = cell47["MAE_mean"]
            print(f"({td:.0%},{td2:.0%})           | {m46:.4f}   | {m47:.4f}   | {m47 - m46:+.4f}", flush=True)
    except Exception as e:
        print(f"  exp46 compare failed: {e}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pcts = [d * 100 for d in DELTAS]

        # 图A：对角线 vs exp46
        fig, ax = plt.subplots(figsize=(7.5, 5))
        diag47 = [results["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        diag47s = [results["matrix"][str(d)][str(d)]["MAE_std"] for d in DELTAS]
        diag46 = [exp46["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        ax.plot(
            pcts, [TLUSTY_MAE[d] for d in DELTAS], "s--", color="tab:red", label="Tlusty (mismatched)", linewidth=1.8
        )
        ax.errorbar(
            pcts, diag46, fmt="o-", color="tab:orange", label="DL-LNN orig gate (exp46)", linewidth=1.6, capsize=3
        )
        ax.errorbar(
            pcts,
            diag47,
            yerr=diag47s,
            fmt="^-",
            color="tab:green",
            label="DL-LNN physics-aware gate (exp47)",
            linewidth=1.8,
            capsize=3,
        )
        ax.set_xlabel("Modal mismatch delta (%)")
        ax.set_ylabel("Test MAE (mm)")
        ax.set_title("Diagonal: physics-aware vs original gate")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig47a_gate_compare_diagonal.png", dpi=150)
        plt.close(fig)

        # 图B：train=0% 行的防御能力（未见失配防御）
        fig, ax = plt.subplots(figsize=(7.5, 5))
        row46 = [exp46["matrix"]["0.0"][str(d)]["MAE_mean"] for d in DELTAS]
        row47 = [results["matrix"]["0.0"][str(d)]["MAE_mean"] for d in DELTAS]
        ax.plot(
            pcts, [TLUSTY_MAE[d] for d in DELTAS], "s--", color="tab:red", label="Tlusty (mismatched)", linewidth=1.8
        )
        ax.plot(pcts, row46, "o-", color="tab:orange", label="orig gate (train=0%)", linewidth=1.6)
        ax.plot(pcts, row47, "^-", color="tab:green", label="physics-aware gate (train=0%)", linewidth=1.8)
        ax.set_xlabel("Test mismatch delta (%)")
        ax.set_ylabel("Test MAE (mm)")
        ax.set_title("Unseen-mismatch defense (trained at 0% mismatch)")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig47b_unseen_defense.png", dpi=150)
        plt.close(fig)

        # 图C：门控 alpha vs test_delta（不同 train_delta 线）
        fig, ax = plt.subplots(figsize=(7.5, 5))
        for train_delta in DELTAS:
            tds = str(train_delta)
            gates = [results["matrix"][tds][str(d)]["gate_mean"] for d in DELTAS]
            ax.plot(pcts, gates, "o-", linewidth=1.6, label=f"train d={train_delta:.0%}")
        ax.set_xlabel("Test mismatch delta (%)")
        ax.set_ylabel("Gate alpha (mean)")
        ax.set_title("Physics-aware gate: alpha vs test mismatch")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig47c_gate_alpha_vs_test.png", dpi=150)
        plt.close(fig)

        print(f"figures saved to {FIG_DIR}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
