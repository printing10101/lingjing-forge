"""不确定性传播：κ/δ/r/xi 蒙特卡洛 → 增益与功率鲁棒区间

全仿真路线"鲁棒性"模块。审稿人必问"参数不确定怎么办"，主动给分布：

  增益 g(ΔT) = 1 / (1 - κ_eff·ΔT),   κ_eff = κ - δ·r,   ΔT = P·xi

参数不确定性来源（全部有实测/文献依据）：
  - κ: 9 组 J-C 参数均值 0.000736 /°C，范围 [0.000527, 0.001267]（calibrate_kappa_delta.py）
  - δ: Karpat 2009 弹性模量拟合 E(T)=-57.7T+111672 MPa → 0.000517 /°C
  - r: 温差比（结构区/切削区），**无实测**——文献合理区间 [0.3, 1.0]，
       1.0 = 结构区与切削区等温（最保守，κ_eff 最小）
  - xi: Springer OA 实测 733~1107 °C/kW

输出：指定功率下增益的 P5/P50/P95 分布；以及保证目标增益（P95 达标）所需功率。
诚实呈现：r 无实测 → 增益区间宽，这是论文最弱一环，如实报告。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ---- 参数分布（来源见 docstring）----
KAPPA_MU, KAPPA_LO, KAPPA_HI = 0.000736, 0.000527, 0.001267   # /°C（J-C 9 组标定）
DELTA_MU, DELTA_SD = 0.000517, 0.00005                        # /°C（Karpat 2009 拟合 ±10%）
R_LO, R_HI = 0.3, 1.0                                         # 温差比（无实测，宽区间）
XI_LO, XI_HI = 733.0, 1107.0                                  # °C/kW（Springer OA 实测）
XI_MU = 0.5 * (XI_LO + XI_HI)                                 # 733~1107 中位 920
N_SAMPLES = 5000
RNG = np.random.default_rng(20260811)


@dataclass
class UCDist:
    """从参数分布采样 κ_eff 与 ΔT 的蒙特卡洛实验。"""
    kappa: np.ndarray
    delta: np.ndarray
    r: np.ndarray
    xi: np.ndarray

    @property
    def kappa_eff(self) -> np.ndarray:
        return self.kappa - self.delta * self.r

    def sample_gain(self, p_W: float) -> np.ndarray:
        """指定激光功率下的叶瓣谷增益分布。"""
        dT = p_W / 1000.0 * self.xi
        return 1.0 / (1.0 - self.kappa_eff * dT)

    def power_for_gain(self, target_gain: float, quantile: float = 0.95) -> float:
        """P-quantile 保证目标增益所需功率（W）。二分搜索。"""
        lo, hi = 0.0, 5000.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            gain = self.sample_gain(mid)
            if np.quantile(gain, quantile) < target_gain:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def sample_dist(n: int = N_SAMPLES, seed: int = 20260811) -> UCDist:
    """独立采样各参数（三角分布：κ 用 J-C 区间均值峰；r 用均匀宽区间）。"""
    rng = np.random.default_rng(seed)
    kappa = rng.triangular(KAPPA_LO, KAPPA_MU, KAPPA_HI, n)
    delta = np.clip(rng.normal(DELTA_MU, DELTA_SD, n), 1e-5, None)
    r = rng.uniform(R_LO, R_HI, n)
    xi = rng.uniform(XI_LO, XI_HI, n)
    return UCDist(kappa, delta, r, xi)


def percentile(x: np.ndarray) -> dict:
    return {"p5": float(np.percentile(x, 5)), "p50": float(np.percentile(x, 50)),
            "p95": float(np.percentile(x, 95))}


def main() -> None:
    dist = sample_dist()
    out_dir = Path(__file__).resolve().parent.parent / "results" / "uncertainty"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 62)
    print("参数分布（每项来源见脚本 docstring）")
    print(f"  κ: 三角分布 {KAPPA_LO}~{KAPPA_HI}（峰 {KAPPA_MU}） J-C 9 组标定")
    print(f"  δ: 正态 {DELTA_MU}±{DELTA_SD}（Karpat 2009 拟合）")
    print(f"  r: 均匀 {R_LO}~{R_HI}（无实测，最宽假设）")
    print(f"  xi: 均匀 {XI_LO}~{XI_HI} °C/kW（Springer OA 实测）")
    print("=" * 62)

    # 1) 工程功率预算（651 W 前馈）下的增益分布
    p_eng = 651.0
    gain = dist.sample_gain(p_eng)
    g = percentile(gain)
    print(f"\n[1] 工程前馈功率 P={p_eng:.0f} W → 谷增益分布")
    print(f"    P5={g['p5']:.3f}×  P50={g['p50']:.3f}×  P95={g['p95']:.3f}×")
    print(f"    结论：P95 保守增益 {g['p95']:.2f}×；中位 {g['p50']:.2f}×")
    print(f"    最坏情形（r→1 等温 + κ 下限）：{float(np.min(gain)):.2f}×")

    # 2) 保证 P95 ≥ 1.2× / 1.3× / 1.5× 所需功率
    print("\n[2] 保证 P95 达标所需激光功率（W）")
    pw = {}
    for target in (1.2, 1.3, 1.5):
        p_req = dist.power_for_gain(target, 0.95)
        pw[str(target)] = round(p_req, 1)
        print(f"    P95 ≥ {target}× → {p_req:.0f} W")

    # 3) 安全窗交叉检查：P95 达标时峰值温升
    print("\n[3] 峰值温升（P=651W 前馈，xi 上限 1107）")
    dT_max = 651.0 / 1000.0 * XI_HI
    print(f"    ΔT_max = {dT_max:.0f}°C （相变安全窗 800°C：{'✓' if dT_max < 800 else '✗ 超窗'}）")

    # 4) r 敏感性：κ_eff 对 r 的退化（量化"无实测"环节的影响）
    print("\n[4] r 敏感性（κ_eff = κ−δ·r，κ=中值 0.000736）")
    for r in (0.0, 0.3, 0.5, 1.0):
        ke = KAPPA_MU - DELTA_MU * r
        print(f"    r={r:.1f} → κ_eff={ke:.6f} /°C → 651W 增益 {1/(1-ke*651/1000*XI_MU):.2f}×")

    # 5) 中位参数确定性复算（交叉验证蒙特卡洛）
    ke_mid = KAPPA_MU - DELTA_MU * 0.5
    dT_mid = p_eng / 1000.0 * XI_MU
    gain_mid = 1.0 / (1.0 - ke_mid * dT_mid)
    print(f"\n[5] 确定性复算（中位参数）：κ_eff={ke_mid:.6f}, ΔT={dT_mid:.0f}°C → 增益 {gain_mid:.3f}×")
    assert abs(gain_mid - g["p50"]) < 0.05, "蒙特卡洛中位数应接近中位参数确定性结果"

    # 保存
    summary = {
        "n_samples": N_SAMPLES,
        "params": {"kappa": {"mu": KAPPA_MU, "lo": KAPPA_LO, "hi": KAPPA_HI},
                   "delta": {"mu": DELTA_MU, "sd": DELTA_SD},
                   "r": {"lo": R_LO, "hi": R_HI}, "xi": {"lo": XI_LO, "hi": XI_HI}},
        "gain_at_651W": {k: round(v, 4) for k, v in g.items()},
        "power_for_p95": pw,
        "dT_max_at_651W": round(dT_max, 1),
        "safety_window_ok": dT_max < 800.0,
        "r_sensitivity": {str(r): round(KAPPA_MU - DELTA_MU * r, 6)
                          for r in (0.0, 0.3, 0.5, 1.0)},
        "deterministic_cross_check": {"kappa_eff": ke_mid, "gain": round(gain_mid, 4)},
    }
    out = out_dir / "uncertainty_summary.json"
    import json
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已保存 {out}")


if __name__ == "__main__":
    main()
