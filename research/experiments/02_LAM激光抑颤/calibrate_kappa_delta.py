"""Ti-6Al-4V 热-力参数标定（LAM 主动抑颤机制实验）

标定两个核心参数：
  kappa (κ): 比切削力（流动应力）软化率, Ks(T) = Ks(1 - κΔT)
             来源: Johnson-Cook 热软化项 1-T*^m 在 300-500°C 区间的平均/微分软化率,
             7 组 J-C 参数 (JMPT 2011) + 2 组 (Procedia CIRP 2015) 交叉验证
  delta (δ): 结构等效刚度退化率, k(T) = k(1 - δΔT)
             来源: Karpat 2009 实测 E(T) = -57.7T + 111672 MPa (JMPT 2011 引用)

文献（全部经 Crossref/Unpaywall 验证）:
  [1] Y. Karpat, J. Mater. Process. Technol. 211 (2011) 737-749, DOI 10.1016/j.jmatprotec.2010.12.008
      Table 1: 7 组 J-C 参数; p.743: E(T) = -57.7T + 111672 MPa (Karpat 2009)
  [2] Y. Zhang et al., Procedia CIRP 22 (2015) 107-114, DOI 10.1016/j.procir.2015.03.052
      Table 2: 2 组 J-C 参数 (Set1/Set2); Table 1: E=110 GPa, Tm=1630°C, Troom=25°C
"""
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "results" / "calibration"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 输入数据
# Johnson-Cook 参数 (A MPa, B MPa, n, C, m) — 文献表
JC_SETS = [
    # (A, B, n, C, m, 文献)
    (782.7, 498.4, 0.28, 0.028, 1.0,   "Umbrello 2008 [1]"),
    (724.0, 683.1, 0.47, 0.035, 1.0,   "Lee & Lin 1998 [1]"),
    (968.0, 380.0, 0.421, 0.0197, 0.577, "Li & He 2006 [1]"),
    (859.0, 640.0, 0.22, 2.2e-5, 1.1,   "Ozel & Zeren 2004 [1]"),
    (862.0, 331.0, 0.34, 0.012, 0.8,   "Meyer & Kleponis 2001 [1]"),
    (1098.0, 1092.0, 0.93, 0.014, 1.1, "Chen et al. 2004 [1]"),
    (997.9, 653.1, 0.45, 0.0198, 0.7,  "Seo et al. 2005 [1]"),
    (862.0, 331.0, 0.34, 0.012, 0.8,   "Meyer & Kleponis 2001 [2] Set1"),
    (1098.0, 1092.0, 0.93, 0.014, 1.1, "Chen et al. 2004 [2] Set2"),
]

TMELT, TROOM = 1630.0, 25.0   # [2] Table 1
TRANGE = TMELT - TROOM        # 1605 °C

# E(T) 线性拟合 (Karpat 2009 via [1] p.743): E(T) = E0 - s*T, T 单位 °C
E0_MPA, E_SLOPE = 111_672.0, 57.7


# ---------------------------------------------------------------- κ 标定
def kappa_avg(m: float, dT: float) -> float:
    """平均软化率: Ks(dT)/Ks(0) = 1 - T*^m = 1 - kappa_avg*dT"""
    tstar = dT / TRANGE
    return tstar ** m / dT


def kappa_diff(m: float, dT: float) -> float:
    """微分软化率: d(T*^m)/dT = m*T*^(m-1)/TRANGE"""
    tstar = dT / TRANGE
    return m * tstar ** (m - 1) / TRANGE


KAPPA_WINDOW = (300.0, 500.0)   # 标定窗口: 流动软化起始前 (DRX 在 350-500°C [1])

def calibrate_kappa():
    rows = []
    for A, B, n, C, m, src in JC_SETS:
        k_avg_300, k_avg_500 = kappa_avg(m, 300.0), kappa_avg(m, 500.0)
        k_dif_300, k_dif_500 = kappa_diff(m, 300.0), kappa_diff(m, 500.0)
        rows.append({
            "src": src, "m": m,
            "kappa_avg_300C": round(k_avg_300, 6), "kappa_avg_500C": round(k_avg_500, 6),
            "kappa_diff_300C": round(k_dif_300, 6), "kappa_diff_500C": round(k_dif_500, 6),
        })
    avgs = [r["kappa_avg_300C"] for r in rows] + [r["kappa_avg_500C"] for r in rows]
    kmin, kmax = min(avgs), max(avgs)
    kappa_reco = round(sum(avgs) / len(avgs), 6)   # 全体均值作为推荐
    return rows, {"kappa_min": round(kmin, 6), "kappa_max": round(kmax, 6),
                  "kappa_recommended": kappa_reco,
                  "window_degC": list(KAPPA_WINDOW)}


# ---------------------------------------------------------------- δ 标定
def calibrate_delta():
    """E(T) 线性拟合 → δ = slope / E0（结构等效刚度 k ∝ E）"""
    delta = E_SLOPE / E0_MPA
    e_vals = {T: E0_MPA - E_SLOPE * T for T in (25, 100, 200, 300, 400, 500, 600, 800)}
    return delta, e_vals


# ---------------------------------------------------------------- 净效应判据
def net_softening(kappa: float, delta: float, r: float) -> float:
    """净软化率: κ_eff = κ - δ*r, r = 结构温升/切削区温升 (温差比 0~1)"""
    return kappa - delta * r


# ---------------------------------------------------------------- 主流程
def main():
    k_rows, k_summary = calibrate_kappa()
    delta, e_vals = calibrate_delta()

    print("=== κ 标定 (J-C 热软化, 300-500°C 窗口) ===")
    for r in k_rows:
        print(f"  {r['src'][:28]:30s} m={r['m']:<5} κ_avg=[{r['kappa_avg_300C']}, {r['kappa_avg_500C']}]  κ_diff=[{r['kappa_diff_300C']}, {r['kappa_diff_500C']}]")
    print(f"  范围: {k_summary['kappa_min']} ~ {k_summary['kappa_max']} /°C   推荐: {k_summary['kappa_recommended']} /°C")

    print("\n=== δ 标定 (E(T) = -57.7T + 111672 MPa, Karpat 2009) ===")
    print(f"  δ = {E_SLOPE}/{E0_MPA} = {delta:.6f} /°C")
    print("  E(T): " + ", ".join(f"{T}°C→{e_vals[T]/1000:.1f} GPa" for T in (25, 200, 400, 600, 800)))

    print("\n=== 净效应判据 (κ_eff = κ - δ·r) ===")
    k_reco = k_summary["kappa_recommended"]
    for r in (0.0, 0.1, 0.3, 0.5, 1.0):
        ke = net_softening(k_reco, delta, r)
        print(f"  r={r:<4} κ_eff={ke:.6f}   (500°C 增益 {1/(1-ke*500) if ke*500<1 else float('inf'):.2f}×)")

    result = {
        "kappa": {**k_summary, "per_set": k_rows, "sources": [
            "Karpat 2011 JMPT 10.1016/j.jmatprotec.2010.12.008 Table 1 (7 JC sets)",
            "Zhang 2015 Procedia CIRP 10.1016/j.procir.2015.03.052 Table 2 (2 JC sets)"]},
        "delta": {"value": round(delta, 6), "formula": "E(T) = -57.7T + 111672 MPa (Karpat 2009)",
                  "E_GPa": {str(T): round(e_vals[T]/1000, 2) for T in e_vals},
                  "source": "Karpat 2011 JMPT 10.1016/j.jmatprotec.2010.12.008 p.743"},
        "net_effect": {"formula": "kappa_eff = kappa - delta*r",
                       "kappa_recommended": k_reco,
                       "delta": round(delta, 6),
                       "examples": {str(r): round(net_softening(k_reco, delta, r), 6) for r in (0.0, 0.1, 0.3, 0.5, 1.0)}},
        "note": "DRX 流动软化起始于 350-500°C (Karpat 2011), 该窗口以上 κ 额外增大",
    }
    (OUT / "calibration.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已保存: {OUT / 'calibration.json'}")


if __name__ == "__main__":
    main()
