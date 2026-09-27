"""论文级图件生成：Fig.5 蒙特卡洛 / Fig.6 多材料 / Fig.7 代理 LNN。

数据来源：各模块 summary.json + 重算（保证图件可复现）。
输出：research/experiments/results/paper_figs/
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent.parent / "results"
OUT = RESULTS / "paper_figs"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 200})


# Fig.5 蒙特卡洛增益分布
def fig5_mc() -> str:
    import uncertainty_propagation as up

    dist = up.sample_dist(n=8000, seed=11)
    g = dist.sample_gain(p_W=651.0)  # 651W ≈ 闭环演示功率
    q05, q50, q95 = np.percentile(g, [5, 50, 95])
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.hist(g, bins=60, color="#4C72B0", alpha=0.85, density=True)
    for q, ls in [(q05, "--"), (q50, "-"), (q95, "--")]:
        ax.axvline(q, ls=ls, color="#C44E52", lw=1.4)
    ax.text(q05, ax.get_ylim()[1] * 0.92, f"P5={q05:.2f}×", ha="center", fontsize=9, color="#C44E52")
    ax.text(q50, ax.get_ylim()[1] * 0.82, f"中位={q50:.2f}×", ha="center", fontsize=9, color="#C44E52")
    ax.text(q95, ax.get_ylim()[1] * 0.92, f"P95={q95:.2f}×", ha="center", fontsize=9, color="#C44E52")
    ax.set_xlabel("叶瓣谷增益 g (651 W, ΔT≈500°C)")
    ax.set_ylabel("概率密度")
    ax.set_title("Fig.5 蒙特卡洛增益分布（κ/δ/r/xi 不确定性传播，N=8000）")
    path = OUT / "fig5_mc_gain.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return str(path)


# Fig.6 多材料增益窗口
def fig6_materials() -> str:
    data = json.loads((RESULTS / "multi_material" / "multi_material_summary.json").read_text())
    mats, g_lo_r03, g_hi_r03, g_lo_r10, g_hi_r10, ev = [], [], [], [], [], []
    for m in data["materials"]:
        mats.append(m["material"])
        g_lo_r03.append(m["gain_r0.3"][0])
        g_hi_r03.append(m["gain_r0.3"][1])
        g_lo_r10.append(m["gain_r1.0"][0])
        g_hi_r10.append(m["gain_r1.0"][1])
        ev.append("锚点" if m["evidence"].startswith("✅") else ("推导" if m["evidence"].startswith("⚠️") else "区间"))
    x = np.arange(len(mats))
    w = 0.36
    fig, ax = plt.subplots(figsize=(6.8, 3.8))
    for lo, hi, off, lab, c in [
        (g_lo_r03, g_hi_r03, -w / 2, "r=0.3（集中加热）", "#4C72B0"),
        (g_lo_r10, g_hi_r10, w / 2, "r=1.0（等温极限）", "#DD8452"),
    ]:
        ax.bar(
            x + off,
            np.array(hi) - np.array(lo),
            w,
            bottom=lo,
            label=lab,
            color=c,
            alpha=0.8,
            yerr=[[0] * len(mats), [0] * len(mats)],
        )
    ax.axhline(1.0, color="k", lw=0.8, ls=":")
    ax.axhspan(1.2, 1.6, color="#55A868", alpha=0.12)
    ax.text(2.4, 1.55, "目标窗口 1.2~1.6×", fontsize=9, color="#2E7D32")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{m}\n{ev}" for m, ev in zip(mats, ev)])
    ax.set_ylabel("增益窗口 g")
    ax.set_title("Fig.6 多材料增益窗口（证据级别：锚点/推导/区间）")
    ax.legend(fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig6_materials.png")
    plt.close(fig)
    return str(OUT / "fig6_materials.png")


# Fig.7 代理 LNN 拟合
def fig7_lnn() -> str:
    import lnn_power_mapping as lpm

    X, y = lpm.build_dataset()
    rng = np.random.default_rng(42)
    perm = rng.permutation(len(X))
    split = int(0.8 * len(X))
    norm = lpm.Normalizer(X)
    Xn = norm(X)
    net = lpm.SurrogateLNN(X.shape[1], n_hidden=128)
    losses = net.train(Xn[perm[:split]], y[perm[:split]], epochs=1200, lr=0.01)
    y_te, y_hat = y[perm[split:]], net.predict(Xn[perm[split:]])
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.6))
    # 左：预测 vs 真值
    axes[0].scatter(y_te, y_hat, s=6, alpha=0.5, color="#4C72B0")
    lim = [min(y_te.min(), y_hat.min()), max(y_te.max(), y_hat.max())]
    axes[0].plot(lim, lim, "r--", lw=1.2)
    ss_res = float(np.sum((y_te - y_hat) ** 2))
    ss_tot = float(np.sum((y_te - y_te.mean()) ** 2))
    axes[0].set_xlabel("真值裕度 margin")
    axes[0].set_ylabel("代理预测")
    axes[0].set_title(f"左：预测 vs 真值（R²={1 - ss_res / ss_tot:.3f}）")
    # 右：loss 曲线
    axes[1].plot(losses, color="#C44E52", lw=1.3)
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("MSE")
    axes[1].set_title("右：训练损失曲线")
    fig.suptitle("Fig.7 代理 LNN（接口契约对齐论文1 LNNPredictor.predict）", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "fig7_lnn.png")
    plt.close(fig)
    return str(OUT / "fig7_lnn.png")


if __name__ == "__main__":
    p5 = fig5_mc()
    print("Fig.5:", p5)
    p6 = fig6_materials()
    print("Fig.6:", p6)
    p7 = fig7_lnn()
    print("Fig.7:", p7)
