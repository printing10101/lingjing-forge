# -*- coding: utf-8 -*-
"""
fig01_sld.py — 论文 Fig.1：Tlusty 解析稳定性叶瓣图（SLD）。
参数取自论文 §2 物理基线默认（k=1e6 N/m, m=100 kg, zeta=0.05, Kt=2000 N/mm², 4 齿），
复用 data_generator.TlustyAnalyticalModel.compute_limiting_depth（与 exp51 同源）。
输出 300dpi PNG。
"""

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from data_generator import TlustyAnalyticalModel

# ---- 物理基线（论文 §2）----
tlusty = TlustyAnalyticalModel(
    stiffness=1e6,  # N/m
    modal_mass=100.0,  # kg
    damping_ratio=0.05,
    cutting_force_coeff=2000.0,  # N/mm²
    num_teeth=4,
)

# ---- 固定工艺参数（exp51 采样域内：H 80-200, D 6-16, f 0.05-0.5, ae 0.5-8）----
H = 200.0  # 材料硬度 HB
D = 8.0  # 刀具直径 mm
Z = 4  # 齿数
F = 0.3  # 每齿进给 mm/rev
AE = 6.0  # 径向切宽 mm

n = np.linspace(3000, 9000, 4000)
N = len(n)
# 固定工艺参数需广播为与转速等长数组（compute_limiting_depth 按 idx 索引）
a_lim = np.maximum(
    tlusty.compute_limiting_depth(
        n,
        num_lobes=8,
        hardness=np.full(N, H),
        tool_diameter=np.full(N, D),
        num_teeth=np.full(N, Z),
        feed_rate=np.full(N, F),
        radial_depth=np.full(N, AE),
    ),
    0.05,
)

fig, ax = plt.subplots(figsize=(8.4, 4.8))
ax.plot(n / 1000, a_lim, color="tab:red", linewidth=1.6, label=r"$a_{lim}$ (critical axial depth)")

# 失稳区域阴影：切深高于叶瓣包络 颤振
ax.fill_between(n / 1000, a_lim, a_lim.max() * 1.25, color="tab:red", alpha=0.12, label="chatter (unstable) region")
# 稳定区域（包络下方）
ax.fill_between(n / 1000, 0, a_lim, color="tab:green", alpha=0.08, label="stable region")

ax.axhline(0, color="k", linewidth=0.8)
ax.set_xlabel("Spindle speed n (krpm)")
ax.set_ylabel(r"Limiting axial depth of cut $a_{lim}$ (mm)")
ax.set_title(
    "Tlusty stability lobe diagram (SDOF, k=1 MN/m, m=100 kg, "
    r"$\zeta$=0.05, Kt=2000 N/mm², z=4)"
)
ax.set_xlim(3, 9)
ax.set_ylim(0, a_lim.max() * 1.25)
ax.legend(fontsize=9, loc="upper right")
ax.grid(alpha=0.3)

# 标注：峰值叶瓣（最稳定转速点示例）
i_max = int(np.argmax(a_lim))
ax.annotate(
    "peak lobe (sweet spot)",
    xy=(n[i_max] / 1000, a_lim[i_max]),
    xytext=(n[i_max] / 1000 + 0.35, a_lim[i_max] * 0.85),
    arrowprops=dict(arrowstyle="->", color="gray", lw=0.9),
    fontsize=8,
    color="gray",
)

fig.tight_layout()
out = BASE / "results" / "figures" / "fig01_sld_lobes.png"
fig.savefig(out, dpi=300)
plt.close(fig)
print(f"OK {out} ({out.stat().st_size // 1024} KB)")
print(f"a_lim range: {a_lim.min():.3f} .. {a_lim.max():.3f} mm | peak at n={n[i_max]:.0f} rpm ({a_lim[i_max]:.3f} mm)")
