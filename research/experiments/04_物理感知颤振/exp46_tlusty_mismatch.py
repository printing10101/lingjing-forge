"""
实验46：模态参数失配下残差补偿范式的价值（TLUSTY-MISMATCH train-delta x test-delta）

科学问题：Tlusty 解析模型在模态参数（k/m/zeta）失配时预测崩坏；
DL-LNN 的残差补偿（门控融合）能否在解析失效区间保持稳定？

协议（train-delta x test-delta 矩阵，一次训练测全部失配档）：
- 真实参数 Tlusty(k*=1e6, m*=100, zeta*=0.05) 生成数据（标签加 2% 噪声）
- 失配档 delta in {0%, 5%, 10%, 20%, 40%}：k'=k*(1+d), m'=m*(1+d), z'=z*(1+d)
- DL-LNN(train_delta)：训练时物理分支注入该档失配物理预测 -> LTC 学残差
  - 对角线 (train_delta == test_delta)：标定偏差补偿能力（变体 A）
  - 非对角线 (train_delta=0, test_delta=0.4)：未见漂移鲁棒性（变体 B）
- 三路基线：Tlusty(test_delta) 确定性解析 / LSTM 纯数据 / DL-LNN
- 5 seeds x 80 epochs；MAE/R2/门控alpha；DL-LNN vs Tlusty 单样本 t 检验 + Cohen's d
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
from torch.utils.data import Dataset, DataLoader
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))

from config import ModelConfig
from models import DLLNNWithPhysics, BaselineLSTM
from data_generator import build_physics_features_7d, TlustyAnalyticalModel
from metrics import ChatterMetrics

# 求解器选择（诚信记录）：本机装有 torchdiffeq，LTCCell 默认走 dopri5
# 自适应 ODE 积分（实测 0.899 s/step，全量实验约 16h）；强制走 Euler
# 一阶路径（0.010 s/step，全量约 10min）。求解器选择写入结果 JSON。
import models as _models

_HAS_ODE = _models._HAS_TORCHDIFFEQ
_models._HAS_TORCHDIFFEQ = False  # force Euler（fast, deterministic fixed-step）
LTC_SOLVER = "euler"

# 实验参数
DELTAS = [0.00, 0.05, 0.10, 0.20, 0.40]
SEEDS = [42, 43, 44, 45, 46]
NUM_SAMPLES = 1000
NOISE_LEVEL = 0.02
NUM_EPOCHS = 80
BATCH_SIZE = 32
TRAIN_RATIO = 0.70
VAL_RATIO = 0.15

# 真实模态参数（Tlusty 默认）
K_STAR = 1e6
M_STAR = 100.0
ZETA_STAR = 0.05

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"


def make_tlusty(delta: float) -> TlustyAnalyticalModel:
    """构造注入失配 delta 的 Tlusty 模型（k/m/zeta 同向失配）。"""
    return TlustyAnalyticalModel(
        stiffness=K_STAR * (1.0 + delta),
        modal_mass=M_STAR * (1.0 + delta),
        damping_ratio=ZETA_STAR * (1.0 + delta),
    )


class MismatchChatterDataset(Dataset):
    """带模态参数失配物理预测的合成颤振数据集。

    每个样本返回 (x, y, phys_true, phys_0, phys_5, ...)：
      x          7 维输入特征
      y          带噪声的极限切深标签（真实参数生成）
      phys_true  真实参数物理预测（a_lim_clean）
      phys_dx    各失配档位的物理预测（训练/测试时按需注入）
    """

    def __init__(self, num_samples: int = NUM_SAMPLES, noise_level: float = NOISE_LEVEL, seed: int = 42):
        super().__init__()
        rng = np.random.default_rng(seed)

        spindle_speed = rng.uniform(1000, 10000, num_samples)
        axial_depth = rng.uniform(0.1, 10.0, num_samples)
        feed_rate = rng.uniform(0.05, 0.5, num_samples)
        radial_depth = rng.uniform(0.5, 8.0, num_samples)
        hardness = rng.uniform(80.0, 200.0, num_samples)
        tool_diameter = rng.uniform(6.0, 16.0, num_samples)
        num_teeth = rng.integers(2, 7, num_samples).astype(float)

        tlusty_true = make_tlusty(0.0)
        a_lim = tlusty_true.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )
        a_lim_noisy = np.maximum(a_lim * (1 + rng.standard_normal(num_samples) * noise_level), 0.01)

        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
        )

        self.features = features.astype(np.float32)
        self.y = a_lim_noisy.astype(np.float32).reshape(-1, 1)
        self.phys_true = a_lim.astype(np.float32).reshape(-1, 1)

        # 各失配档位的物理预测
        self.phys_mismatch = {}
        for delta in DELTAS:
            tlusty_m = make_tlusty(delta)
            a_lim_m = tlusty_m.compute_limiting_depth(
                spindle_speed,
                hardness=hardness,
                tool_diameter=tool_diameter,
                num_teeth=num_teeth,
                feed_rate=feed_rate,
                radial_depth=radial_depth,
            )
            self.phys_mismatch[delta] = a_lim_m.astype(np.float32).reshape(-1, 1)

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, idx: int):
        x = torch.from_numpy(self.features[idx])
        y = torch.from_numpy(self.y[idx])
        phys_true = torch.from_numpy(self.phys_true[idx])
        phys_list = [torch.from_numpy(self.phys_mismatch[d][idx]) for d in DELTAS]
        return (x, y, phys_true, *phys_list)


def create_loaders(dataset: MismatchChatterDataset, batch_size: int, seed: int):
    total = len(dataset)
    train_n = int(total * TRAIN_RATIO)
    val_n = int(total * VAL_RATIO)
    torch.manual_seed(seed)
    train_ds, val_ds, test_ds = torch.utils.data.random_split(dataset, [train_n, val_n, total - train_n - val_n])
    kwargs = dict(batch_size=batch_size, num_workers=0, pin_memory=True)
    return (
        DataLoader(train_ds, shuffle=True, **kwargs),
        DataLoader(val_ds, shuffle=False, **kwargs),
        DataLoader(test_ds, shuffle=False, **kwargs),
    )


def train_model(
    model: nn.Module,
    train_loader,
    val_loader,
    config: ModelConfig,
    device: torch.device,
    train_delta: float,
    use_physics: bool,
    num_epochs: int = NUM_EPOCHS,
    verbose: bool = True,
) -> nn.Module:
    """训练模型；use_physics=True 时显式注入 train_delta 档失配物理预测（门控参与训练）。"""
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)

    delta_idx = DELTAS.index(train_delta)
    best_val_loss = float("inf")
    best_state = None

    for epoch in range(num_epochs):
        model.train()
        train_loss = 0.0
        n_batches = 0
        for batch in train_loader:
            x, y_true, _, *phys_list = batch
            x = x.to(device)
            y_true = y_true.to(device)
            optimizer.zero_grad()
            if use_physics:
                phys = phys_list[delta_idx].to(device)
                output = model(x, phys)
            else:
                output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output
            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)
            loss = criterion(y_pred, y_true)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
            n_batches += 1
        train_loss /= max(n_batches, 1)

        model.eval()
        val_loss = 0.0
        n_val = 0
        with torch.no_grad():
            for batch in val_loader:
                x, y_true, _, *phys_list = batch
                x = x.to(device)
                y_true = y_true.to(device)
                if use_physics:
                    phys = phys_list[delta_idx].to(device)
                    output = model(x, phys)
                else:
                    output = model(x)
                if isinstance(output, tuple):
                    y_pred = output[0]
                else:
                    y_pred = output
                if y_pred.shape != y_true.shape:
                    y_pred = y_pred.view_as(y_true)
                loss = criterion(y_pred, y_true)
                val_loss += loss.item()
                n_val += 1
        val_loss /= max(n_val, 1)
        scheduler.step()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if verbose and (epoch == 0 or (epoch + 1) % 20 == 0):
            print(f"    Epoch [{epoch + 1}/{num_epochs}] Train: {train_loss:.4f} Val: {val_loss:.4f}")

    if best_state is not None:
        model.load_state_dict(best_state)
    return model


@torch.no_grad()
def predict_all(model, test_loader, device, use_physics: bool, delta_idx: int):
    """对测试集推理，返回 dict: preds / y_true / gates（可选）。"""
    preds, y_true, gates = [], [], []
    for batch in test_loader:
        x, y, _, *phys_list = batch
        x = x.to(device)
        if use_physics:
            phys = phys_list[delta_idx].to(device)
            out = model(x, phys)
        else:
            out = model(x)
        if isinstance(out, tuple):
            pred = out[0]
        else:
            pred = out
        preds.append(pred.cpu().numpy())
        y_true.append(y.numpy())
        if use_physics:
            g = model.gate(x).cpu().numpy()
            gates.append(g)
    return {
        "preds": np.concatenate(preds, axis=0),
        "y_true": np.concatenate(y_true, axis=0),
        "gates": np.concatenate(gates, axis=0) if gates else None,
    }


def main():
    print("Device:", "cuda" if torch.cuda.is_available() else "cpu", flush=True)
    metrics_calc = ChatterMetrics()
    config = ModelConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    results = {
        "experiment": "exp46_tlusty_mismatch",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": LTC_SOLVER,
        "torchdiffeq_available": _HAS_ODE,
        "deltas": DELTAS,
        "seeds": SEEDS,
        "num_samples": NUM_SAMPLES,
        "num_epochs": NUM_EPOCHS,
        "noise_level": NOISE_LEVEL,
        "matrix": {},
        "tlusty_baseline": {},
        "lstm": {},
    }

    # LSTM（纯数据，与物理无关，每 seed 训练一次）
    lstm_by_seed = {}
    for seed in SEEDS:
        print(f"\n=== LSTM seed {seed} ===", flush=True)
        torch.manual_seed(seed)
        np.random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        dataset = MismatchChatterDataset(num_samples=NUM_SAMPLES, noise_level=NOISE_LEVEL, seed=seed)
        train_loader, val_loader, test_loader = create_loaders(dataset, BATCH_SIZE, seed)
        model = BaselineLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        ).to(device)
        model = train_model(
            model, train_loader, val_loader, config, device, train_delta=0.0, use_physics=False, verbose=False
        )
        r = predict_all(model, test_loader, device, use_physics=False, delta_idx=0)
        lstm_by_seed[seed] = {
            "MAE": metrics_calc.mae(r["preds"], r["y_true"]),
            "R2": metrics_calc.r2_score(r["preds"], r["y_true"]),
        }
        print(f"  LSTM seed {seed} MAE={lstm_by_seed[seed]['MAE']:.4f} R2={lstm_by_seed[seed]['R2']:.4f}", flush=True)

    lstm_maes = [lstm_by_seed[s]["MAE"] for s in SEEDS]
    results["lstm"] = {
        "MAE_mean": float(np.mean(lstm_maes)),
        "MAE_std": float(np.std(lstm_maes, ddof=1)) if len(lstm_maes) > 1 else 0.0,
        "per_seed": {str(s): lstm_by_seed[s] for s in SEEDS},
    }

    # DL-LNN：train_delta x test_delta 矩阵
    for train_delta in DELTAS:
        print(f"\n############ train_delta = {train_delta:.2f} ############", flush=True)
        tdelta_str = str(train_delta)
        results["matrix"][tdelta_str] = {}
        tlusty_baseline_by_test = {}

        for seed in SEEDS:
            print(f"\n  --- DL-LNN seed {seed} (train_d={train_delta:.2f}) ---", flush=True)
            torch.manual_seed(seed)
            np.random.seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            dataset = MismatchChatterDataset(num_samples=NUM_SAMPLES, noise_level=NOISE_LEVEL, seed=seed)
            train_loader, val_loader, test_loader = create_loaders(dataset, BATCH_SIZE, seed)

            model = DLLNNWithPhysics(
                input_dim=config.input_dim,
                hidden_dim=config.hidden_dim,
                num_layers=config.num_layers,
                output_dim=config.output_dim,
                dt=config.ltc_dt,
                dropout=config.dropout,
            ).to(device)
            model = train_model(
                model,
                train_loader,
                val_loader,
                config,
                device,
                train_delta=train_delta,
                use_physics=True,
                verbose=False,
            )

            # 测试时注入所有 test_delta
            for test_delta in DELTAS:
                t_idx = DELTAS.index(test_delta)
                r = predict_all(model, test_loader, device, use_physics=True, delta_idx=t_idx)
                tds = str(test_delta)
                if tds not in results["matrix"][tdelta_str]:
                    results["matrix"][tdelta_str][tds] = {"MAE_per_seed": [], "R2_per_seed": [], "gate_per_seed": []}
                results["matrix"][tdelta_str][tds]["MAE_per_seed"].append(metrics_calc.mae(r["preds"], r["y_true"]))
                results["matrix"][tdelta_str][tds]["R2_per_seed"].append(metrics_calc.r2_score(r["preds"], r["y_true"]))
                results["matrix"][tdelta_str][tds]["gate_per_seed"].append(
                    float(np.mean(r["gates"])) if r["gates"] is not None else None
                )

                # Tlusty 确定性基线（每 test_delta 只算一次）
                if seed == SEEDS[0]:
                    phys = dataset.phys_mismatch[test_delta][test_loader.dataset.indices]
                    tlusty_baseline_by_test[tds] = {
                        "MAE": metrics_calc.mae(phys, r["y_true"]),
                        "R2": metrics_calc.r2_score(phys, r["y_true"]),
                    }

        # 汇总矩阵单元 + 统计检验
        for test_delta in DELTAS:
            tds = str(test_delta)
            cell = results["matrix"][tdelta_str][tds]
            maes = np.array(cell["MAE_per_seed"])
            tlusty_mae = tlusty_baseline_by_test[tds]["MAE"]
            cell["MAE_mean"] = float(np.mean(maes))
            cell["MAE_std"] = float(np.std(maes, ddof=1)) if len(maes) > 1 else 0.0
            cell["R2_mean"] = float(np.mean(cell["R2_per_seed"]))
            cell["gate_mean"] = float(np.mean([g for g in cell["gate_per_seed"] if g is not None]))
            # 单样本 t 检验：DL-LNN 是否显著优于 Tlusty(test_delta)
            if len(maes) > 1 and np.std(maes, ddof=1) > 0:
                t_stat, p_val = stats.ttest_1samp(maes, tlusty_mae, alternative="less")
                cell["p_value"] = float(p_val)
                cell["cohens_d"] = float((tlusty_mae - np.mean(maes)) / np.std(maes, ddof=1))
                cell["gain_vs_tlusty_pct"] = float((tlusty_mae - np.mean(maes)) / tlusty_mae * 100.0)
            else:
                cell["p_value"] = None
                cell["cohens_d"] = None
                cell["gain_vs_tlusty_pct"] = float((tlusty_mae - np.mean(maes)) / tlusty_mae * 100.0)

        results["tlusty_baseline"][tdelta_str] = tlusty_baseline_by_test

    # 保存
    OUTPUT_DIR.mkdir(exist_ok=True)
    FIG_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "tlusty_mismatch_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_file}", flush=True)

    # 摘要表
    print("\n=== SUMMARY: MAE by (train_delta -> test_delta), Tlusty/LSTM reference ===", flush=True)
    header = "train_d | " + " | ".join(f"test={d:.0%}" for d in DELTAS) + " | gate_avg"
    print(header, flush=True)
    for train_delta in DELTAS:
        tds = str(train_delta)
        row = [f"{train_delta:.0%}"]
        for test_delta in DELTAS:
            cell = results["matrix"][tds][str(test_delta)]
            row.append(f"{cell['MAE_mean']:.3f}±{cell['MAE_std']:.3f}")
        row.append(f"{np.mean([results['matrix'][tds][str(d)]['gate_mean'] for d in DELTAS]):.3f}")
        print(" | ".join(row), flush=True)

    print(
        "\nTlusty(test_delta) baseline MAE:",
        " ".join(f"{d:.0%}={results['tlusty_baseline']['0.0'][str(d)]['MAE']:.3f}" for d in DELTAS),
        flush=True,
    )
    print("LSTM MAE:", f"{results['lstm']['MAE_mean']:.3f}±{results['lstm']['MAE_std']:.3f}", flush=True)

    # 图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pcts = [d * 100 for d in DELTAS]

        # 图A：对角线（train==test）+ Tlusty + LSTM
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
        fig.savefig(FIG_DIR / "fig46a_mismatch_diagonal.png", dpi=150)
        plt.close(fig)

        # 图B：热力图（train_delta x test_delta MAE）
        mat = np.array([[results["matrix"][str(td)][str(td2)]["MAE_mean"] for td2 in DELTAS] for td in DELTAS])
        fig, ax = plt.subplots(figsize=(6.5, 5.5))
        im = ax.imshow(mat, cmap="viridis_r")
        ax.set_xticks(range(len(DELTAS)), [f"{d:.0%}" for d in DELTAS])
        ax.set_yticks(range(len(DELTAS)), [f"{d:.0%}" for d in DELTAS])
        ax.set_xlabel("test mismatch delta")
        ax.set_ylabel("train mismatch delta")
        ax.set_title("DL-LNN MAE (train x test mismatch)")
        for i in range(len(DELTAS)):
            for j in range(len(DELTAS)):
                ax.text(
                    j,
                    i,
                    f"{mat[i, j]:.2f}",
                    ha="center",
                    va="center",
                    color="white" if mat[i, j] > mat.max() * 0.6 else "black",
                    fontsize=9,
                )
        fig.colorbar(im, ax=ax, label="MAE (mm)")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig46b_mismatch_heatmap.png", dpi=150)
        plt.close(fig)

        # 图C：门控 alpha 随 train_delta
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for train_delta in DELTAS:
            tds = str(train_delta)
            gates = [results["matrix"][tds][str(d)]["gate_mean"] for d in DELTAS]
            ax.plot(pcts, gates, "o-", linewidth=1.6, label=f"train d={train_delta:.0%}")
        ax.set_xlabel("test mismatch delta (%)")
        ax.set_ylabel("gate alpha (mean)")
        ax.set_title("Gate alpha: trust in physics vs data")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "fig46c_gate_alpha.png", dpi=150)
        plt.close(fig)

        print(f"figures saved to {FIG_DIR}", flush=True)
    except Exception as e:
        print(f"figure error: {e}", flush=True)

    return results


if __name__ == "__main__":
    main()
