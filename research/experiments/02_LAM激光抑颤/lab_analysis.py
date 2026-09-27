# -*- coding: utf-8 -*-
"""
E1/E2 实验数据分析脚本（实验室采集数据 → 论文参数）

用法：
    python lab_analysis.py e1 --csv lab_data/e1_run1.csv [--out lab_data/e1_results.json]
    python lab_analysis.py e2 --csv lab_data/e2_run1.csv [--out lab_data/e2_results.json]

CSV 必需列（E1）：timestamp_s, laser_power_w, T1_cut_zone_c, T2_structure_c, T3_spindle_c
CSV 必需列（E2）：timestamp_s, laser_power_w, T1_cut_zone_c, Fx_n, Fy_n, Fz_n

输出：
    E1：ξ (°C/kW)、r（ΔT_结构/ΔT_切削区）、各功率档稳态值 + 与论文假设对照
    E2：各功率档力降 (%)、κ_eff = (ΔF/F)/ΔT、与 §4.5 预测带 [0.00018, 0.0008] 对照

诚信约束：本脚本固定版本（可复现）；所有原始 CSV 须归档；负结果如实报告。
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

# 论文标定区间（§4.3 / §8，Ti-6Al-4V）
XI_RANGE = (733.0, 1107.0)          # °C/kW
KEFF_RANGE = (0.00018, 0.0008)      # °C⁻¹（§4.5 预测带）
KEFF_MEDIAN = 0.00046               # °C⁻¹
R_ASSUMPTION = 0.3                  # §4.4 假设值


def _load_csv(path: Path) -> dict:
    """读取 CSV（跳过非数值行），返回列名 -> np.ndarray。"""
    import csv
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise ValueError(f"空 CSV: {path}")
    cols = {k: np.array([float(r[k]) for r in rows if r[k].strip()])
            for k in rows[0].keys()}
    return cols


def _steady_window(arr: np.ndarray, frac: float = 0.5) -> np.ndarray:
    """取序列后 frac 部分作为稳态窗（丢弃启动瞬态）。"""
    n = len(arr)
    return arr[int(n * (1 - frac)):] if n > 1 else arr


def analyze_e1(path: Path) -> dict:
    cols = _load_csv(path)
    p, t1, t2, t3 = (cols["laser_power_w"], cols["T1_cut_zone_c"],
                     cols["T2_structure_c"], cols["T3_spindle_c"])
    base1, base2, base3 = _steady_window(t1[p == 0]), _steady_window(t2[p == 0]), _steady_window(t3[p == 0])
    ref1, ref2, ref3 = float(np.mean(base1)), float(np.mean(base2)), float(np.mean(base3))

    steps = {}
    for pw in sorted(set(np.round(p[p > 0]))):
        mask = np.isclose(p, pw)
        dT1 = float(np.mean(_steady_window(t1[mask])) - ref1)
        dT2 = float(np.mean(_steady_window(t2[mask])) - ref2)
        dT3 = float(np.mean(_steady_window(t3[mask])) - ref3)
        steps[int(pw)] = {"dT_cut_zone": dT1, "dT_structure": dT2,
                          "dT_spindle": dT3,
                          "r": (dT2 / dT1) if dT1 > 1.0 else None,
                          "xi": (dT1 / pw * 1000.0) if pw > 0 else None}

    # 汇总（功率加权线性拟合 ξ）
    xs = np.array([k for k in steps if steps[k]["xi"]])
    xis = np.array([steps[k]["xi"] for k in xs])
    rs = np.array([steps[k]["r"] for k in xs if steps[k]["r"] is not None])

    result = {
        "source_csv": str(path),
        "n_power_steps": len(steps),
        "xi_C_per_kW": {"median": float(np.median(xis)), "range": [float(xis.min()), float(xis.max())]} if len(xis) else None,
        "r": {"median": float(np.median(rs)), "range": [float(rs.min()), float(rs.max())]} if len(rs) else None,
        "steps": steps,
        "vs_paper": {
            "xi_in_range": (float(np.median(xis)) >= XI_RANGE[0] and float(np.median(xis)) <= XI_RANGE[1]) if len(xis) else None,
            "r_within_02_06": (0.2 <= float(np.median(rs)) <= 0.6) if len(rs) else None,
            "r_max_below_08": (float(rs.max()) < 0.8) if len(rs) else None,
        },
    }
    return result


def analyze_e2(path: Path) -> dict:
    cols = _load_csv(path)
    p, t1, fx, fy, fz = (cols["laser_power_w"], cols["T1_cut_zone_c"],
                         cols["Fx_n"], cols["Fy_n"], cols["Fz_n"])
    # 合力（主切削方向近似 Fz，按三向合力保守）
    F = np.sqrt(fx**2 + fy**2 + fz**2)

    F0 = float(np.mean(_steady_window(F[p == 0])))
    T_base = float(np.mean(_steady_window(t1[p == 0])))

    steps = {}
    for pw in sorted(set(np.round(p[p > 0]))):
        mask = np.isclose(p, pw)
        Fp = float(np.mean(_steady_window(F[mask])))
        dT = float(np.mean(_steady_window(t1[mask])) - T_base)
        drop = (F0 - Fp) / F0 * 100.0
        k_eff = (drop / 100.0) / dT if dT > 1.0 else None
        steps[int(pw)] = {"F0_N": F0, "Fp_N": Fp, "dT_C": dT,
                          "drop_pct": drop, "kappa_eff": k_eff}

    ks = np.array([steps[k]["kappa_eff"] for k in steps if steps[k]["kappa_eff"] is not None])
    drops = np.array([steps[k]["drop_pct"] for k in steps])
    dts = np.array([steps[k]["dT_C"] for k in steps])

    # Spearman 单调性（力降 vs ΔT）
    from scipy import stats
    rho = float(stats.spearmanr(drops, dts).statistic) if len(drops) > 2 else None

    result = {
        "source_csv": str(path),
        "n_groups": len(steps),
        "F0_N": F0,
        "kappa_eff_median": float(np.median(ks)) if len(ks) else None,
        "kappa_eff_range": [float(ks.min()), float(ks.max())] if len(ks) else None,
        "drop_monotonic_rho": rho,
        "steps": steps,
        "vs_paper": {
            "kappa_eff_in_range": (KEFF_RANGE[0] <= float(np.median(ks)) <= KEFF_RANGE[1]) if len(ks) else None,
            "kappa_eff_close_to_median": (abs(float(np.median(ks)) - KEFF_MEDIAN) / KEFF_MEDIAN < 0.5) if len(ks) else None,
            "drop_dT_monotonic": (rho is not None and rho > 0.7),
        },
    }
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="E1/E2 实验数据分析")
    ap.add_argument("mode", choices=["e1", "e2"])
    ap.add_argument("--csv", required=True, type=Path, help="采集 CSV 路径")
    ap.add_argument("--out", type=Path, default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    result = analyze_e1(args.csv) if args.mode == "e1" else analyze_e2(args.csv)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存: {args.out}")


if __name__ == "__main__":
    sys.exit(main())
