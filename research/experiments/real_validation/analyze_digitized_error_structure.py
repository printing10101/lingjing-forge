# -*- coding: utf-8 -*-
"""
Step 3：切削稳定性物理模型的误差结构分析（152 点数字化数据集）。

模型配置：
  M0 项目占位参数（Step-1 基线，k=0.9e6/m=95/ζ=0.048 + D² 缩放，ae=1）
  M1 论文模态参数（表1/表2，按悬伸+方向，5 阶取最危险；Ktc=1288.5 N/mm²；不重乘 D²）
  M2 ae 敏感性（M1, ae ∈ {1, 2, 4} mm）

判定（Step-1 已修正方向）：ap < a_lim → 预测稳定。
误差分类：correct / aggressive(预测稳定实为颤振，危险) / conservative(预测颤振实为稳定)。

输出：step3_results.json + step3_per_point.csv + 控制台汇总
"""
import csv, io, json, sys
import numpy as np

sys.path.insert(0, r"D:\灵境制造\research\experiments")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from data_generator import TlustyAnalyticalModel

DATA = r"D:\灵境制造\research\datasets\measured_stability\ji2024_digitized_points.csv"

# ---- 论文表 1（x 向）/ 表 2（y 向）模态参数: L -> [(f, zeta, m), ...] ----
MODAL = {
    ("x", 35): [(565.1, .0322, .1966), (939.1, .0336, .0563), (1311.3, .0375, .0302), (1675.0, .0266, .0249), (2050.3, .0276, .0247)],
    ("x", 65): [(325.5, .0424, .1207), (551.5, .0197, .0941), (743.8, .0315, .0224), (977.6, .0298, .0268), (1184.3, .0268, .1122)],
    ("y", 35): [(538.5, .0730, .0843), (876.6, .0202, .2319), (1283.3, .0260, .0441), (1604.0, .0211, .0422), (1970.0, .0367, .0216)],
    ("y", 65): [(321.3, .0632, .1268), (540.6, .0174, .0919), (732.8, .0235, .0224), (919.3, .0416, .0332), (1164.3, .0273, .0440)],
}
KTC = 1288.5  # N/mm²，论文引用文献[50]
H_AL7075 = 150.0  # HB 典型值
Z = 3
D_TOOL = 6.0


def a_lim_modes(n_rpm, modes, ktc, ae, z=Z, hardness=H_AL7075):
    """给定模态组，逐阶算 a_lim 取最小。直接传 k=m(2πf)²，不重乘 D²（tool_diameter=10 触发 ratio=1）。"""
    best = np.inf
    for (f, zeta, m) in modes:
        k = m * (2 * np.pi * f) ** 2
        mdl = TlustyAnalyticalModel(stiffness=k, modal_mass=m, damping_ratio=zeta,
                                    cutting_force_coeff=ktc, num_teeth=z)
        a = mdl.compute_limiting_depth(
            np.array([n_rpm]), hardness=np.array([hardness]),
            tool_diameter=np.array([10.0]),  # ratio=1 → 不缩放
            num_teeth=np.array([float(z)]),
            feed_rate=None,                  # 未知 → 基准 0.25（切屑变薄因子=1）
            radial_depth=np.array([float(ae)]),
        )[0]
        best = min(best, a)
    return best


def load_points():
    rows = list(csv.DictReader(io.open(DATA, encoding="utf-8")))
    pts = []
    for r in rows:
        pts.append(dict(fig=r["fig"], panel=r["panel"], overhang=int(r["overhang"]),
                        feed=r["feed"], n=float(r["n_rpm"]), ap=float(r["ap_mm"]),
                        actual="stable" if r["result"] == "stable" else "chatter"))
    return pts


def classify(a_lim, ap):
    pred = "stable" if ap < a_lim else "chatter"
    return pred


def main():
    pts = load_points()
    print("加载 %d 点" % len(pts))

    # M0：项目占位参数（复现 Step-1 口径：stiffness=0.9e6, m=95, ζ=0.048, D² 缩放生效, ae=1）
    m0 = TlustyAnalyticalModel(stiffness=0.9e6, modal_mass=95.0, damping_ratio=0.048,
                               cutting_force_coeff=2000.0, num_teeth=Z)
    # M1/M2：论文模态参数，ae 敏感性
    AE_LIST = [1.0, 2.0, 4.0]

    out_rows = []
    for i, p in enumerate(pts):
        rec = dict(p)
        # M0
        a0 = m0.compute_limiting_depth(
            np.array([p["n"]]), hardness=np.array([H_AL7075]),
            tool_diameter=np.array([D_TOOL]), num_teeth=np.array([float(Z)]),
            feed_rate=None, radial_depth=np.array([1.0]))[0]
        rec["a_lim_M0"] = a0
        rec["pred_M0"] = classify(a0, p["ap"])
        # M1..M2（3 个 ae）
        for ae in AE_LIST:
            a = a_lim_modes(p["n"], MODAL[(p["feed"], p["overhang"])], KTC, ae)
            key = "ae%d" % int(ae)
            rec["a_lim_" + key] = a
            rec["pred_" + key] = classify(a, p["ap"])
            if p["ap"] < a:
                rec["margin_" + key] = (a - p["ap"]) / a
            else:
                rec["margin_" + key] = (p["ap"] - a) / a  # 超出量的比例（负 margin 语义：越负越深入颤振区）
        out_rows.append(rec)

    # ---- 汇总 ----
    def err_type(rec, key):
        pred = rec["pred_" + key] if key else rec["pred_M0"]
        if pred == rec["actual"]:
            return "correct"
        return "aggressive" if pred == "stable" else "conservative"

    def summarize(key):
        cnt = {"correct": 0, "aggressive": 0, "conservative": 0}
        for r in out_rows:
            cnt[err_type(r, key)] += 1
        n = sum(cnt.values())
        return cnt, n

    print()
    print("=" * 84)
    print("模型 × 误差方向（152 点）")
    print("%-22s %8s %10s %12s %10s" % ("配置", "正确", "激进(危)", "保守", "激进占比"))
    for key, label in [(None, "M0 项目占位参数"),
                       ("ae1", "M1 论文模态 ae=1"),
                       ("ae2", "M2 论文模态 ae=2"),
                       ("ae4", "M2 论文模态 ae=4")]:
        c, n = summarize(key)
        print("%-22s %8d %10d %12d %9.1f%%" % (label, c["correct"], c["aggressive"],
                                               c["conservative"], 100 * c["aggressive"] / n))

    # 分组：65mm 时 x vs y（图15 vs 图18 同悬伸）
    print()
    print("65mm 悬伸：进给方向 x vs y（M1 ae=1）")
    for feed in ("x", "y"):
        sub = [r for r in out_rows if r["overhang"] == 65 and r["feed"] == feed]
        c = {"correct": 0, "aggressive": 0, "conservative": 0}
        for r in sub:
            c[err_type(r, "ae1")] += 1
        print("  feed=%s: n=%d  correct=%d  aggressive=%d  conservative=%d"
              % (feed, len(sub), c["correct"], c["aggressive"], c["conservative"]))

    # 分组：ap 水平（低切深 vs 高切深，M1 ae=1）
    print()
    print("按 ap 水平（M1 ae=1）")
    for lo, hi, tag in [(0.0, 0.25, "ap≤0.25(低)"), (0.25, 0.6, "0.25<ap<0.6"), (0.6, 99, "ap≥0.6(高)")]:
        sub = [r for r in out_rows if lo <= r["ap"] < hi]
        if not sub:
            continue
        c = {"correct": 0, "aggressive": 0, "conservative": 0}
        for r in sub:
            c[err_type(r, "ae1")] += 1
        print("  %-14s n=%3d  correct=%3d  aggressive=%3d  conservative=%3d"
              % (tag, len(sub), c["correct"], c["aggressive"], c["conservative"]))

    # Fisher 精确检验：65mm x vs y 的 aggressive 率
    try:
        from scipy.stats import fisher_exact
        xag = sum(1 for r in out_rows if r["overhang"] == 65 and r["feed"] == "x" and err_type(r, "ae1") == "aggressive")
        xn = sum(1 for r in out_rows if r["overhang"] == 65 and r["feed"] == "x")
        yag = sum(1 for r in out_rows if r["overhang"] == 65 and r["feed"] == "y" and err_type(r, "ae1") == "aggressive")
        yn = sum(1 for r in out_rows if r["overhang"] == 65 and r["feed"] == "y")
        odds, p = fisher_exact([[xag, xn - xag], [yag, yn - yag]])
        print()
        print("Fisher 精确检验（65mm，aggressive 率 x=%.1f%% vs y=%.1f%%）：OR=%.3f, p=%.4f"
              % (100 * xag / xn, 100 * yag / yn, odds, p))
    except Exception as e:
        print("Fisher 检验失败:", e)

    # 实测稳定点在哪（模型把 27 个真稳定点判成什么）
    print()
    print("27 个实测稳定点的预测分布（M1 ae=1）：")
    from collections import Counter
    cnt = Counter(err_type(r, "ae1") for r in out_rows if r["actual"] == "stable")
    print("  ", dict(cnt))
    print("125 个实测颤振点的预测分布（M1 ae=1）：")
    cnt = Counter(err_type(r, "ae1") for r in out_rows if r["actual"] == "chatter")
    print("  ", dict(cnt))

    # 落盘
    with io.open(r"D:\灵境制造\research\experiments\results\step3_error_structure_per_point.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print()
    print("逐点结果 -> step3_per_point.csv")


if __name__ == "__main__":
    main()
