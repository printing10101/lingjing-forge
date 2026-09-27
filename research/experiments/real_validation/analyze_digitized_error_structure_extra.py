# -*- coding: utf-8 -*-
"""Step 3 补充：D-F 悖论检验 + 保守误差速度分布 + 误差结构图"""
import csv, io, sys
import numpy as np

sys.path.insert(0, r"D:\灵境制造\research\experiments")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

rows = list(csv.DictReader(io.open("step3_per_point.csv", encoding="utf-8")))
for r in rows:
    r["n"] = float(r["n"]); r["ap"] = float(r["ap"]); r["overhang"] = int(r["overhang"])

from scipy.stats import fisher_exact

# 1) D-F 悖论：65mm 低切深（bottom 面板，ap≤0.25）实测稳定率 x vs y
xsub = [r for r in rows if r["overhang"] == 65 and r["feed"] == "x" and r["ap"] <= 0.25]
ysub = [r for r in rows if r["overhang"] == 65 and r["feed"] == "y" and r["ap"] <= 0.25]
xs = sum(1 for r in xsub if r["actual"] == "stable")
ys = sum(1 for r in ysub if r["actual"] == "stable")
odds, p = fisher_exact([[xs, len(xsub) - xs], [ys, len(ysub) - ys]])
print("D-F 悖论检验（65mm, ap≤0.25）：")
print("  x 向进给: %d/%d 稳定" % (xs, len(xsub)))
print("  y 向进给: %d/%d 稳定" % (ys, len(ysub)))
print("  Fisher 精确: OR=%.3f, p=%.5f" % (odds, p))

# 模型对这批点是否区分了方向（M1 ae=1 预测稳定数对比）
xp = sum(1 for r in xsub if r["pred_ae1"] == "stable")
yp = sum(1 for r in ysub if r["pred_ae1"] == "stable")
print("  模型预测稳定: x %d/%d vs y %d/%d" % (xp, len(xsub), yp, len(ysub)))

# 2) 保守误差的速度分布
cons = [r for r in rows if r["pred_ae1"] != r["actual"] and r["pred_ae1"] == "chatter"]
aggr = [r for r in rows if r["pred_ae1"] != r["actual"] and r["pred_ae1"] == "stable"]
print()
print("保守误差 %d 个的速度范围: %.0f - %.0f rpm，中位 %.0f" % (
    len(cons), min(r["n"] for r in cons), max(r["n"] for r in cons),
    float(np.median([r["n"] for r in cons]))))
print("  ap 范围: %.1f - %.1f mm" % (min(r["ap"] for r in cons), max(r["ap"] for r in cons)))
print("激进误差 %d 个: ap≤0.25 占 %d/%d；速度中位 %.0f rpm" % (
    len(aggr), sum(1 for r in aggr if r["ap"] <= 0.25), len(aggr),
    float(np.median([r["n"] for r in aggr]))))

# 3) 误差结构图（2×3 面板）
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.size": 8, "axes.titlesize": 9,
                     "font.family": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                     "axes.unicode_minus": False})

panels = [("Fig10", "top"), ("Fig10", "bot"), ("Fig15", "top"), ("Fig15", "bot"), ("Fig18", "top"), ("Fig18", "bot")]
titles = {"Fig10": "35mm / x-feed", "Fig15": "65mm / x-feed", "Fig18": "65mm / y-feed"}
fig, axes = plt.subplots(2, 3, figsize=(9.5, 5.4), sharex=True)
COLOR = {"correct": "#7f7f7f", "aggressive": "#d62728", "conservative": "#1f77b4"}
MARK = {"stable": "o", "chatter": "x"}
for ax, (fg, pn) in zip(axes.flat, panels):
    sub = [r for r in rows if r["fig"] == fg and r["panel"] == pn]
    for r in sub:
        et = "correct" if r["pred_ae1"] == r["actual"] else ("aggressive" if r["pred_ae1"] == "stable" else "conservative")
        ax.scatter(r["n"] / 1000, r["ap"] * 1000 if False else r["ap"],
                   c=COLOR[et], marker=MARK[r["actual"]], s=26,
                   edgecolors="k" if et != "correct" else "none", linewidths=0.4)
    ax.set_title("%s (%s)" % (titles[fg], pn))
    ax.set_ylim(bottom=0)
for ax in axes[-1]:
    ax.set_xlabel("spindle speed (krpm)")
for ax in axes[:, 0]:
    ax.set_ylabel("ap (mm)")
handles = [plt.Line2D([], [], color=COLOR[k], marker="o", ls="", label=k) for k in COLOR]
handles.append(plt.Line2D([], [], color="k", marker="o", ls="", label="actual=stable", mfc="w"))
handles.append(plt.Line2D([], [], color="k", marker="x", ls="", label="actual=chatter"))
fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8)
fig.suptitle("误差结构：M1（论文模态参数，ae=1mm）逐点判定", fontsize=11)
fig.tight_layout(rect=[0, 0.07, 1, 0.96])
fig.savefig("step3_error_map.png", dpi=200)
print()
print("图 -> step3_error_map.png")
