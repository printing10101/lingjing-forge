"""增益放大策略量化探索（在已有热扩展 Tlusty 模型上直接验证）

目标：在文献标定参数（κ=0.000736, δ=0.000517）下，探索提高谷增益的手段：
  A. ΔT 上限推高（局部快速加热，瞬态超过氧化窗口）：400~800°C
  B. DRX 动态再结晶增强 κ（JMPT 2011: 350-500°C 以上流动应力额外下降，
     等效 κ 增大 30~100%）：κ_eff 放大系数 1.3 / 1.5 / 2.0
  C. 转速联动（叶瓣等比放大 → 形状不变 → 谷→肩部平移增益与加热增益
     近似乘法叠加）：验证等比放大下的乘法分解 + 复合增益

输出：控制台表格 + results/gain_strategies/gain_strategies.json
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thermal_sld_model import (
    ThermalSLDModel,
    default_spindle_grid,
    KAPPA_TI64_CALIBRATED,
    DELTA_TI64_CALIBRATED,
    net_softening,
)

OUT = Path(__file__).resolve().parent.parent / "results" / "gain_strategies"
OUT.mkdir(parents=True, exist_ok=True)


def gain(kappa_eff: float, dT: float) -> float:
    """谷增益 1/(1-κ_eff·ΔT)；饱和（ΔT 超过 1/κ_eff）返回 inf。"""
    if kappa_eff * dT >= 1.0:
        return float("inf")
    return 1.0 / (1.0 - kappa_eff * dT)


def main() -> None:
    summary = {}
    kappa_c, delta_c = KAPPA_TI64_CALIBRATED, DELTA_TI64_CALIBRATED

    # ---- A. ΔT 上限扫描（r=0.3 聚焦典型）----
    ke03 = net_softening(kappa_c, delta_c, 0.3)
    print("=== A. ΔT 上限推高（r=0.3, κ_eff=%.6f）===" % ke03)
    a_rows = []
    for dT in (400, 500, 600, 700, 800):
        g = gain(ke03, dT)
        a_rows.append((dT, round(g, 3)))
        print(f"  ΔT={dT:>4}°C → 谷增益 {g:.3f}×")
    summary["A_dT_scan"] = {f"dT{dT}": g for dT, g in a_rows}

    # ---- B. DRX 增强 κ（350-500°C 以上流动应力额外软化）----
    print("\n=== B. DRX 增强 κ（ΔT=500°C, r=0.3）===")
    b_rows = []
    for boost in (1.0, 1.3, 1.5, 2.0):
        ke = ke03 * boost
        g = gain(ke, 500.0)
        b_rows.append((boost, round(ke, 6), round(g, 3)))
        print(f"  κ_eff×{boost:<4} → {ke:.6f} → 谷增益 {g:.3f}×")
    summary["B_DRX_boost_500C"] = {f"x{boost}": g for boost, _, g in b_rows}

    # C. 转速联动：等比放大 谷肩部平移与加热乘法叠加
    # δ=0 时叶瓣等比放大（形状不变），乘法分解应精确成立；
    # δ>0 时叶瓣变形平移，固定工作点增益可能被破坏 需要转速重新寻优。
    print("\n=== C. 转速联动（等比放大保持叶瓣形状 → 乘法分解）===")
    grid = default_spindle_grid(4000)
    model = ThermalSLDModel()
    a0 = model.compute_limiting_depth(grid, dT=0.0, clip=False)
    aT = model.compute_limiting_depth(grid, dT=500.0, kappa=kappa_c, delta=delta_c, clip=False)
    aT0 = model.compute_limiting_depth(grid, dT=500.0, kappa=kappa_c, delta=0.0, clip=False)
    v0 = model.valley_level(a0)
    vT = model.valley_level(aT)

    # 谷位置 + 谷后第一个局部峰（肩）
    i_v = int(np.argmin(a0))
    seg = a0[i_v:]
    d = np.diff(seg)
    peaks = np.where((d[:-1] > 0) & (d[1:] < 0))[0]
    i_shoulder = i_v + int(peaks[0]) + 1 if len(peaks) else i_v + int(np.argmax(seg[:300]))
    s0, sT0, sT = a0[i_shoulder], aT0[i_shoulder], aT[i_shoulder]

    # C1: δ=0 乘法分解验证
    heat0 = vT0 = model.valley_level(aT0)
    pos0 = s0 / v0
    total0 = sT0 / v0
    mult0 = pos0 * (heat0 / v0)
    print(f"  [C1 δ=0] 谷 {v0:.4f}→{heat0:.4f}（{heat0 / v0:.3f}×），肩 {s0:.4f}→{sT0:.4f}（{sT0 / s0:.3f}×）")
    print(
        f"          纯转速平移 {pos0:.3f}×，复合 {total0:.3f}×，乘法分解预测 {mult0:.3f}×，"
        f"误差 {abs(total0 - mult0) / total0 * 100:.2f}%"
    )

    # C2: δ>0 固定工作点 vs 谷邻域（±5% 转速）重新寻优
    heat_d = vT / v0
    n_lo = max(0, i_v - int(0.05 * len(grid)))
    n_hi = min(len(grid), i_v + int(0.05 * len(grid)))
    i_best0 = n_lo + int(np.argmax(a0[n_lo:n_hi]))
    i_bestT = n_lo + int(np.argmax(aT[n_lo:n_hi]))
    opt0, optT = a0[i_best0], aT[i_bestT]
    print(
        f"  [C2 δ>0] 固定谷增益 {heat_d:.3f}×；谷邻域±5%rpm 重新寻优："
        f"{opt0:.4f}→{optT:.4f}（{optT / opt0:.3f}× vs 未加热邻域最优，{optT / v0:.3f}× vs 原始谷）"
    )
    print(f"  [负面发现] δ 使叶瓣变形：固定工作点（肩 {s0:.4f}→{sT:.4f}）增益可能被破坏")
    summary["C_spindle_link"] = {
        "valley_rpm": float(grid[i_v]),
        "shoulder_rpm": float(grid[i_shoulder]),
        "c1_delta0_mult_error_pct": round(abs(total0 - mult0) / total0 * 100, 2),
        "c2_fixed_gain": round(heat_d, 3),
        "c2_neighborhood_opt_gain_vs_orig_valley": round(optT / v0, 3),
        "c2_neighborhood_opt_gain_vs_opt0": round(optT / opt0, 3),
        "shoulder_fixed_drop": round(float(sT / s0), 3),
    }

    # ---- D. 组合包络（最坏/典型/激进）----
    print("\n=== D. 组合包络（最坏/典型/激进）===")
    combos = {
        "典型_聚焦500C固定点": heat_d,
        "聚焦500C+邻域寻优": optT / v0,
        "激进_700C+DRX1.5+邻域寻优": gain(ke03 * 1.5, 700.0) * (optT / v0) / heat_d,
        "极限_800C+DRX2+邻域寻优": gain(ke03 * 2.0, 800.0) * (optT / v0) / heat_d,
    }
    for name, g in combos.items():
        print(f"  {name:<26}: {g:.2f}×" if np.isfinite(g) else f"  {name:<26}: 饱和(∞)")
    summary["D_combos"] = {k: (v if np.isfinite(v) else None) for k, v in combos.items()}

    with open(OUT / "gain_strategies.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\n已保存：{OUT / 'gain_strategies.json'}")


if __name__ == "__main__":
    main()
