"""
实验47b：物理感知门控 v2——门控输入加入显式冲突信号 |y_phys - y_ltc|

动机（exp47 全量结果）：门控输入 concat(x, y_phys) 与原版几乎无差别
（(0%,20%) smoke 的 -68% 是全量下消失的欠拟合假象）。机理分析：
门控只看 y_phys 单值无法判断"这个物理预测对不对"——它不知道 x 对应的
真值分布。改进：门控输入 = concat(x, y_phys, |y_phys - y_ltc|)。
|y_phys - y_ltc| 是模型内部"物理与数据打架"的直接信号：物理失配越大，
物理预测与 LTC 数据分支分歧越大 -> 门控应提高 alpha（信任数据）。

协议：与 exp46/exp47 完全一致（train-delta x test-delta 矩阵，5 seeds）。
H2 复检：train=0%, test>0 时是否防御（alpha 随 test_delta 上升）。
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
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

import exp46_tlusty_mismatch as base
from models import DLLNNModel
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

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"


def _load_tlusty_mae() -> dict:
    p = OUTPUT_DIR / "tlusty_mismatch_results.json"
    data = json.load(open(p, encoding="utf-8"))
    tb = data["tlusty_baseline"]["0.0"]
    return {float(k): float(v["MAE"]) for k, v in tb.items()}


TLUSTY_MAE = _load_tlusty_mae()


class PhysicsAwareDLLNNV2(nn.Module):
    """物理感知门控 v2：gate 输入 = concat(x, y_phys, |y_phys - y_ltc|)。"""

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
        self.gate = nn.Sequential(
            nn.Linear(input_dim + 2, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )
        self.physics_scale = nn.Parameter(torch.ones(1))
        self.physics_bias = nn.Parameter(torch.zeros(1))

    def forward(self, x: torch.Tensor, physics_pred: torch.Tensor):
        ltc_pred = self.ltc_branch(x)
        gate_in = torch.cat([x, physics_pred, (physics_pred - ltc_pred).abs()], dim=1)
        alpha = self.gate(gate_in)
        final_pred = alpha * ltc_pred + (1 - alpha) * (self.physics_scale * physics_pred + self.physics_bias)
        return final_pred, ltc_pred

    def reset_hidden(self):
        self.ltc_branch.reset_hidden()


@torch.no_grad()
def predict_all(model, test_loader, device, delta_idx: int):
    preds, y_true, gates = [], [], []
    for batch in test_loader:
        x, y, _, *phys_list = batch
        x = x.to(device)
        phys = phys_list[delta_idx].to(device)
        final, ltc = model(x, phys)
        preds.append(final.cpu().numpy())
        y_true.append(y.numpy())
        gate_in = torch.cat([x, phys, (phys - ltc).abs()], dim=1)
        gates.append(model.gate(gate_in).cpu().numpy())
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
        "experiment": "exp47b_physics_aware_gate_v2",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": _HAS_ODE,
        "deltas": DELTAS,
        "seeds": SEEDS,
        "num_samples": NUM_SAMPLES,
        "num_epochs": NUM_EPOCHS,
        "gate_input": "concat(x, y_phys, |y_phys-y_ltc|)",
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

            model = PhysicsAwareDLLNNV2(
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
    out_file = OUTPUT_DIR / "physics_aware_gate_v2_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_file}", flush=True)

    # 摘要
    print("\n=== SUMMARY: Physics-Aware Gate V2 MAE (train_d -> test_d) ===", flush=True)
    print("train_d | " + " | ".join(f"test={d:.0%}" for d in DELTAS) + " | gate_avg", flush=True)
    for train_delta in DELTAS:
        tds = str(train_delta)
        row = [f"{train_delta:.0%}"]
        for test_delta in DELTAS:
            c = results["matrix"][tds][str(test_delta)]
            row.append(f"{c['MAE_mean']:.3f}±{c['MAE_std']:.3f}")
        row.append(f"{np.mean([results['matrix'][tds][str(d)]['gate_mean'] for d in DELTAS]):.3f}")
        print(" | ".join(row), flush=True)

    # 与 exp46/exp47 对比
    print("\n=== vs exp46(orig) / exp47(v1): key cells ===", flush=True)
    try:
        exp46 = json.load(open(OUTPUT_DIR / "tlusty_mismatch_results.json", encoding="utf-8"))
        exp47 = json.load(open(OUTPUT_DIR / "physics_aware_gate_results.json", encoding="utf-8"))
        print("cell (train,test) | exp46 | exp47-v1 | exp47b-v2", flush=True)
        for td, td2 in [(0.0, 0.0), (0.0, 0.2), (0.0, 0.4), (0.2, 0.2), (0.4, 0.4)]:
            m46 = exp46["matrix"][str(td)][str(td2)]["MAE_mean"]
            m47 = exp47["matrix"][str(td)][str(td2)]["MAE_mean"]
            cell_b = results["matrix"].get(str(td), {}).get(str(td2))
            m47b = cell_b["MAE_mean"] if cell_b else float("nan")
            print(f"({td:.0%},{td2:.0%})        | {m46:.4f} | {m47:.4f}   | {m47b:.4f}", flush=True)
    except Exception as e:
        print(f"  compare failed: {e}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pcts = [d * 100 for d in DELTAS]

        # 图A：三版对角线
        fig, ax = plt.subplots(figsize=(7.5, 5))
        diag46 = [exp46["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        diag47 = [exp47["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        diag47b = [results["matrix"][str(d)][str(d)]["MAE_mean"] for d in DELTAS]
        ax.plot(
            pcts, [TLUSTY_MAE[d] for d in DELTAS], "s--", color="tab:red", label="Tlusty (mismatched)", linewidth=1.8
        )
        ax.plot(pcts, diag46, "o-", color="tab:orange", label="orig gate (exp46)", linewidth=1.6)
        ax.plot(pcts, diag47, "^-", color="tab:blue", label="aware v1 x+phys (exp47)", linewidth=1.6)
        ax.plot(pcts, diag47b, "D-", color="tab:green", label="aware v2 +|phys-ltc| (exp47b)", linewidth=1.8)
        ax.set_xlabel("Modal mismatch delta (%)")
        ax.set_ylabel("Test MAE (mm)")
        ax.set_title("Diagonal comparison: three gate designs")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig47b_diagonal_3gates.png", dpi=150)
        plt.close(fig)

        # 图B：train=0% 行（未见失配防御），三版对比
        fig, ax = plt.subplots(figsize=(7.5, 5))
        row46 = [exp46["matrix"]["0.0"][str(d)]["MAE_mean"] for d in DELTAS]
        row47 = [exp47["matrix"]["0.0"][str(d)]["MAE_mean"] for d in DELTAS]
        row47b = [results["matrix"]["0.0"][str(d)]["MAE_mean"] for d in DELTAS]
        ax.plot(
            pcts, [TLUSTY_MAE[d] for d in DELTAS], "s--", color="tab:red", label="Tlusty (mismatched)", linewidth=1.8
        )
        ax.plot(pcts, row46, "o-", color="tab:orange", label="orig (train=0%)", linewidth=1.6)
        ax.plot(pcts, row47, "^-", color="tab:blue", label="aware v1 (train=0%)", linewidth=1.6)
        ax.plot(pcts, row47b, "D-", color="tab:green", label="aware v2 (train=0%)", linewidth=1.8)
        ax.set_xlabel("Test mismatch delta (%)")
        ax.set_ylabel("Test MAE (mm)")
        ax.set_title("Unseen-mismatch defense (trained at 0%): three gate designs")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig47c_unseen_defense_3gates.png", dpi=150)
        plt.close(fig)

        # 图C：v2 门控 alpha vs test_delta
        fig, ax = plt.subplots(figsize=(7.5, 5))
        for train_delta in DELTAS:
            tds = str(train_delta)
            gates = [results["matrix"][tds][str(d)]["gate_mean"] for d in DELTAS]
            ax.plot(pcts, gates, "o-", linewidth=1.6, label=f"train d={train_delta:.0%}")
        ax.set_xlabel("Test mismatch delta (%)")
        ax.set_ylabel("Gate alpha (mean)")
        ax.set_title("Aware-gate v2: alpha vs test mismatch")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig47d_gate_v2_alpha.png", dpi=150)
        plt.close(fig)

        print(f"figures saved to {FIG_DIR}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
