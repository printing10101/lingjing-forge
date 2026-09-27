"""
实验51：SLD 叶瓣边界分布预测（任务表述升级，阶段 4）

科学问题：点回归任务上 DL 价值严格条件性（阶段 1-3）。任务表述升级为
"边界分布 ± 不确定性"后，解析基线（Tlusty，σ≡0 确定性输出）失去表达力，
DL 价值是否被重新激活？

任务构造：真实边界 a_lim(x) = Tlusty；模糊观测 y_obs = a_lim + ε，
ε ~ N(0, σ_fuzz(x))，σ_fuzz(x) = 模态参数失配（δ∈[0,0.4] 均匀）下边界漂移均值
（物理动机：阶段 1 已证模态参数 5-20% 偏差 → 边界误差 100%）。

模型（4 路）：Tlusty（确定性）/ 点回归 LTC（MSE）/ 分布预测 LTC（NLL）/
分布预测 LSTM（NLL）。评估：NLL / 80% 区间覆盖率（校准）/ 平均区间宽度（锐度）/
模糊区二分类 Brier score + 可靠性图。
"""

import sys, os, json, random
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_generator import TlustyAnalyticalModel, build_physics_features_7d
import models as _models
from models import DLLNNModel, BaselineLSTM

# 求解器选择（诚信记录，与 exp46 一致）：强制 Euler（dopri5 实测慢 90 倍）
_models._HAS_TORCHDIFFEQ = False
LTC_SOLVER = "euler"

# 配置
NUM_SAMPLES = 2000  # 基础样本数（每个 x 生成 K 个观测）
K_OBS = 5  # 每样本观测数（重复实验）
NUM_EPOCHS = 80
BATCH_SIZE = 64
SEEDS = [42, 43, 44]
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-4
FUZZ_DELTAS = [0.0, 0.05, 0.1, 0.2, 0.4]  # 模态参数失配档（σ_fuzz 源）
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"
FIG_DIR = OUTPUT_DIR / "figures"

torch.manual_seed(42)
np.random.seed(42)


def make_features(tlusty: TlustyAnalyticalModel, n_samp: int, seed: int) -> tuple:
    """采样 7 维工艺参数 + 真实边界 a_lim + 模糊度 σ_fuzz。"""
    rng = np.random.RandomState(seed)
    n = rng.uniform(3000, 9000, n_samp)
    f = rng.uniform(0.05, 0.5, n_samp)
    ap = rng.uniform(0.1, 10.0, n_samp)
    ae = rng.uniform(0.5, 8.0, n_samp)
    H = rng.uniform(80.0, 200.0, n_samp)
    D = rng.uniform(6.0, 16.0, n_samp)
    z = rng.randint(2, 7, n_samp).astype(float)

    a_lim = tlusty.compute_limiting_depth(
        n,
        num_lobes=10,
        hardness=H,
        tool_diameter=D,
        num_teeth=z,
        feed_rate=f,
        radial_depth=ae,
    )
    a_lim = np.maximum(a_lim, 0.05)

    # σ_fuzz：模态参数失配下边界漂移均值（物理动机化模糊度）
    drifts = []
    for delta in FUZZ_DELTAS:
        t2 = TlustyAnalyticalModel(
            stiffness=tlusty.stiffness * (1 - delta),
            modal_mass=tlusty.modal_mass,
            damping_ratio=tlusty.damping_ratio,
            cutting_force_coeff=tlusty.cutting_force_coeff * (1 + delta),
            num_teeth=tlusty.num_teeth,
        )
        a2 = np.maximum(
            t2.compute_limiting_depth(
                n,
                num_lobes=10,
                hardness=H,
                tool_diameter=D,
                num_teeth=z,
                feed_rate=f,
                radial_depth=ae,
            ),
            0.05,
        )
        drifts.append(np.abs(a2 - a_lim))
    sigma_fuzz = np.mean(drifts, axis=0) + 1e-3

    features = build_physics_features_7d(
        spindle_speed=n,
        feed_rate=f,
        axial_depth=ap,
        radial_depth=ae,
        hardness=H,
        tool_diameter=D,
        num_teeth=z,
    ).astype(np.float32)
    return features, a_lim.astype(np.float32), sigma_fuzz.astype(np.float32)


def make_observations(features, a_lim, sigma_fuzz, seed: int) -> tuple:
    """每个 x 生成 K 个模糊观测（重复实验）→ (X, y, sigma_true)。"""
    rng = np.random.RandomState(seed)
    X = np.repeat(features, K_OBS, axis=0)
    y = np.repeat(a_lim, K_OBS) + rng.randn(len(a_lim) * K_OBS) * np.repeat(sigma_fuzz, K_OBS)
    y = np.maximum(y, 0.01)
    sigma_true = np.repeat(sigma_fuzz, K_OBS)
    return (
        torch.from_numpy(X),
        torch.from_numpy(y.astype(np.float32)).unsqueeze(1),
        torch.from_numpy(sigma_true.astype(np.float32)),
    )


def gaussian_nll(pred, y_true):
    mu, logvar = pred[:, 0:1], pred[:, 1:2]
    logvar = torch.clamp(logvar, -6, 6)
    var = logvar.exp() + 1e-6
    return torch.mean(logvar + (y_true - mu) ** 2 / var)


def train_dist(model, loader, device, epochs=NUM_EPOCHS):
    opt = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    model.train()
    for _ in range(epochs):
        for xb, yb, _sb in loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            loss = gaussian_nll(model(xb), yb)
            loss.backward()
            opt.step()
        sched.step()
    return model


def train_point(model, loader, device, epochs=NUM_EPOCHS):
    opt = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    crit = nn.MSELoss()
    model.train()
    for _ in range(epochs):
        for xb, yb, _sb in loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            out = model(xb)
            yp = out[0] if isinstance(out, tuple) else out
            yp = yp.view_as(yb)
            loss = crit(yp, yb)
            loss.backward()
            opt.step()
        sched.step()
    return model


def predict_dist(model, X, device):
    model.eval()
    mus, sigmas = [], []
    with torch.no_grad():
        for i in range(0, len(X), 256):
            out = model(X[i : i + 256].to(device))
            p = out[0] if isinstance(out, tuple) else out
            mu, logvar = p[:, 0].cpu().numpy(), p[:, 1].cpu().numpy()
            mus.append(mu)
            sigmas.append(np.exp(np.clip(logvar / 2, -3, 3)))  # σ = exp(logvar/2)
    return np.concatenate(mus), np.concatenate(sigmas)


def predict_point(model, X, device):
    model.eval()
    preds = []
    with torch.no_grad():
        for i in range(0, len(X), 256):
            out = model(X[i : i + 256].to(device))
            p = out[0] if isinstance(out, tuple) else out
            preds.append(p[:, 0].cpu().numpy())
    return np.concatenate(preds)


def coverage(mu, sigma, a_true, level=0.80):
    """预测区间包含真实边界的比例（校准指标）。"""
    z = 1.2816  # 80% CI
    lo, hi = mu - z * sigma, mu + z * sigma
    return float(np.mean((a_true >= lo) & (a_true <= hi)))


def brier_score(prob_unstable, label):
    return float(np.mean((prob_unstable - label) ** 2))


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device={device}", flush=True)

    # 数据（seed=0 固定一次生成，训练/测试划分）
    tlusty = TlustyAnalyticalModel()
    feats, a_lim, sig_fuzz = make_features(tlusty, NUM_SAMPLES, seed=0)
    n_tr = int(0.8 * NUM_SAMPLES)
    X_tr, y_tr, s_tr = make_observations(feats[:n_tr], a_lim[:n_tr], sig_fuzz[:n_tr], seed=0)
    X_te_raw, a_te, s_te = feats[n_tr:], a_lim[n_tr:], sig_fuzz[n_tr:]
    # 测试集：真实边界 a_te（评估校准/决策）——不注入观测噪声
    X_te = torch.from_numpy(X_te_raw)
    tr_loader = DataLoader(TensorDataset(X_tr, y_tr, s_tr), batch_size=BATCH_SIZE, shuffle=True)

    n_test = len(X_te)
    print(f"train={len(X_tr)} test={n_test} (K={K_OBS})", flush=True)

    results = {
        "protocol": "SLD distribution prediction (phase 4)",
        "ltc_solver": LTC_SOLVER,
        "NLL": {},
        "coverage80": {},
        "width": {},
        "brier": {},
        "acc_fuzzy": {},
        "nll_per_seed": {},
        "coverage_per_seed": {},
        "brier_per_seed": {},
    }

    for seed in SEEDS:
        torch.manual_seed(seed)
        np.random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        # 模糊区决策任务（ap 采样在边界附近）
        rng = np.random.RandomState(seed)
        ap = a_te * (1 + rng.uniform(-0.15, 0.15, n_test))
        label = (ap > a_te).astype(float)

        # ---- Tlusty 确定性基线（注入模态参数失配 δ=10%：真实机床参数未知，
        # 解析模型有系统误差——避免"用 Tlusty 预测 Tlusty"的作弊基线）----
        t_mis = TlustyAnalyticalModel(
            stiffness=tlusty.stiffness * 0.9,
            modal_mass=tlusty.modal_mass,
            damping_ratio=tlusty.damping_ratio,
            cutting_force_coeff=tlusty.cutting_force_coeff * 1.1,
            num_teeth=tlusty.num_teeth,
        )
        mu_t = np.maximum(
            t_mis.compute_limiting_depth(
                X_te_raw[:, 0] * 10000.0,
                num_lobes=10,
                hardness=X_te_raw[:, 4] * 200.0,
                tool_diameter=X_te_raw[:, 5] * 20.0,
                num_teeth=np.clip(np.round(X_te_raw[:, 6] * 6.0), 2, 6),
                feed_rate=X_te_raw[:, 1] * 0.5,
                radial_depth=X_te_raw[:, 3] * 8.0,
            ),
            0.05,
        )
        sigma_t = np.zeros(n_test)
        cov_t = coverage(mu_t, sigma_t, a_te)
        p_t = (ap > mu_t).astype(float)
        brier_t = brier_score(p_t, label)
        acc_t = float(np.mean((p_t > 0.5) == label))

        # 点回归 LTC
        m_pt = DLLNNModel(input_dim=7, hidden_dim=128, num_layers=3, output_dim=1, dt=0.1, dropout=0.2).to(device)
        train_point(m_pt, tr_loader, device)
        mu_pt = predict_point(m_pt, X_te, device)

        # 分布预测 LTC
        m_dl = DLLNNModel(input_dim=7, hidden_dim=128, num_layers=3, output_dim=2, dt=0.1, dropout=0.2).to(device)
        train_dist(m_dl, tr_loader, device)
        mu_dl, sig_dl = predict_dist(m_dl, X_te, device)

        # 分布预测 LSTM
        m_ls = BaselineLSTM(input_dim=7, hidden_dim=128, num_layers=2, output_dim=2).to(device)
        train_dist(m_ls, tr_loader, device)
        mu_ls, sig_ls = predict_dist(m_ls, X_te, device)

        # ---- NLL（对真实边界的密度：以测试模糊度为尺度）----
        def nll_vs_true(mu, sigma):
            var = sigma**2 + s_te**2 + 1e-6
            return float(np.mean(0.5 * np.log(2 * np.pi * var) + (a_te - mu) ** 2 / (2 * var)))

        for name, mu, sigma in [
            ("tlusty", mu_t, sigma_t),
            ("point_ltc", mu_pt, np.zeros(n_test)),
            ("dist_ltc", mu_dl, sig_dl),
            ("dist_lstm", mu_ls, sig_ls),
        ]:
            results["nll_per_seed"].setdefault(name, []).append(nll_vs_true(mu, sigma))
            results["coverage_per_seed"].setdefault(name, []).append(coverage(mu, sigma, a_te))

        # 模糊区决策
        # 分布预测：P(unstable) = Φ((ap−μ)/σ)
        p_dl = 0.5 * (1 + np.tanh((ap - mu_dl) / (np.maximum(sig_dl, 1e-3)) * 0.5))
        p_ls = 0.5 * (1 + np.tanh((ap - mu_ls) / (np.maximum(sig_ls, 1e-3)) * 0.5))
        # 点回归：硬阈值
        p_pt = (ap > mu_pt).astype(float)
        for name, p in [("tlusty", p_t), ("point_ltc", p_pt), ("dist_ltc", p_dl), ("dist_lstm", p_ls)]:
            results["brier_per_seed"].setdefault(name, []).append(brier_score(p, label))
            results["acc_fuzzy"].setdefault(name, []).append(float(np.mean((p > 0.5) == label)))

    # 汇总
    print("\n=== SUMMARY: SLD distribution prediction (3 seeds) ===", flush=True)
    print(f"{'model':<12} | {'NLL':>8} | {'cov80%':>8} | {'Brier':>7} | {'acc_fuzzy':>10}", flush=True)
    names = ["tlusty", "point_ltc", "dist_ltc", "dist_lstm"]
    for name in names:
        nll = np.mean(results["nll_per_seed"][name])
        cov = np.mean(results["coverage_per_seed"][name])
        br = np.mean(results["brier_per_seed"][name])
        ac = np.mean(results["acc_fuzzy"][name])
        results["NLL"][name] = nll
        results["coverage80"][name] = cov
        results["brier"][name] = br
        results["acc_fuzzy"][name] = ac
        print(f"{name:<12} | {nll:8.4f} | {cov:8.3f} | {br:7.4f} | {ac:10.4f}", flush=True)

    # 配对检验：dist_ltc vs point_ltc / dist_ltc vs dist_lstm（Brier）
    from scipy import stats

    for a, b in [("dist_ltc", "point_ltc"), ("dist_ltc", "dist_lstm"), ("dist_ltc", "tlusty")]:
        t, p = stats.ttest_rel(results["brier_per_seed"][a], results["brier_per_seed"][b])
        results[f"brier_paired_{a}_vs_{b}"] = {"t": float(t), "p": float(p)}
        print(f"paired brier {a} vs {b}: t={t:.3f} p={p:.4f}", flush=True)

    # ---- 可靠性图（用最后一个 seed 的 dist_ltc）----
    fig, ax = plt.subplots(figsize=(6, 5))
    # 简化：用累积分布对比（校准曲线略）
    ax.plot([0, 1], [0, 1], "k--", alpha=0.6, label="perfect")
    for name, p in [("dist_ltc", p_dl), ("dist_lstm", p_ls), ("point_ltc", p_pt), ("tlusty", p_t)]:
        bins = np.linspace(0, 1, 11)
        idx = np.digitize(p, bins) - 1
        idx = np.clip(idx, 0, 9)
        freq = np.array([np.mean(label[idx == i]) if np.any(idx == i) else np.nan for i in range(10)])
        ax.plot(bins[:-1] + 0.05, freq, marker="o", ms=4, label=name)
    ax.set_xlabel("predicted P(unstable)")
    ax.set_ylabel("empirical frequency")
    ax.set_title("Reliability (fuzzy boundary zone)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig51_reliability.png", dpi=300)
    plt.close(fig)

    with open(OUTPUT_DIR / "sld_distribution_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"Results saved to {OUTPUT_DIR / 'sld_distribution_results.json'}", flush=True)


if __name__ == "__main__":
    main()
