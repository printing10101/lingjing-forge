"""真实 LAM 实验数据交叉标定（Springer OA 全文，PDF 已下载存档）

把仿真超参数从"估计值"升级为"真实实验数据集标定值"：
  1. xi（功率→切削区温升）：Hybrid 2023 (IJAMT 125:1903, DOI 10.1007/s00170-022-10764-5)
     热像仪实测：750 W / 2mm 光斑 → 576~830 °C（随转速 20-70 m/min 变化）
     → xi = 0.77~1.11 °C/W ≈ 770~1110 °C/kW（转速越低越高，驻留时间长）
  2. κ_eff 实测（切削力下降/温升）：
     - Dominguez-Caballero et al. 2023（同篇）：LAM 车削 Ti-6Al-4V 力降 10%（ΔT≈550°C）
       → κ_eff ≈ 0.00018
     - Sun et al. 2015 LAML 铣削 Ti-6Al-4V：力降 40%（[27] 引文，ΔT≈500°C 假设）
       → κ_eff ≈ 0.0008
     - Rashid et al. 2015 综述实验（Lasers Manuf. Mater. Process. 2:164-185,
       DOI 10.1007/s40516-015-0013-4）：LAML Ti-6Al-4V ELI 铣削力降 20~23%
       → κ_eff ≈ 0.0004~0.00046（ΔT≈500°C）
  3. 温度安全窗：Ti-6Al-4V 相变开始 880°C（2023 篇）/ 800°C（2015 篇模型），
     表面氧化 1100°C；2015 篇取最大平均温度 500°C 避免微观组织改变
  4. 吸收率：光纤激光 ≤90W 时 0.35，>90W 氧化后 0.5（2015 篇实测）
  5. 功率预算修正：750W 级即达 576-830°C → 工程场景激光功率需求
     应从 1.1~5 kW 粗估下调至 0.5~1.5 kW 实测区间

输出：results/calibration/experimental_calibration.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thermal_sld_model import DELTA_TI64_CALIBRATED, KAPPA_TI64_CALIBRATED, net_softening

OUT = Path(__file__).resolve().parent.parent / "results" / "calibration"
OUT.mkdir(parents=True, exist_ok=True)

# ---- 真实实验数据点（来源：Springer OA PDF，本地存档 /tmp/sp_*.pdf）----
# (名称, 激光功率 W, 切削区温升 °C, 转速条件, 文献)
XI_POINTS = [
    ("low-speed 20 m/min", 750.0, 830.0, "车削 20 m/min", "Dominguez-Caballero 2023 IJAMT"),
    ("high-speed 70 m/min", 750.0, 576.0, "车削 70 m/min", "Dominguez-Caballero 2023 IJAMT"),
    ("cutting-edge 12mm lead", 750.0, 550.0, "激光-刀具距 12mm", "Dominguez-Caballero 2023 IJAMT"),
]

# κ_eff 实测点：(力下降 %, 温升 °C, 工况, 文献)
KAPPA_EFF_POINTS = [
    (0.10, 550.0, "车削 Ti-6Al-4V", "Dominguez-Caballero 2023 IJAMT"),
    (0.20, 500.0, "铣削 Ti-6Al-4V ELI", "Rashid 2015 LMMP"),
    (0.23, 500.0, "铣削 Ti-6Al-4V ELI (y向)", "Rashid 2015 LMMP"),
    (0.40, 500.0, "铣削 Ti-6Al-4V", "Sun et al. (via Rashid 2015)"),
]

T_CRITICAL = {
    "phase_change_2023": 880.0,
    "phase_change_2015_model": 800.0,
    "oxidation_2015": 1100.0,
    "max_avg_safe_2015": 500.0,
}
ABSORPTIVITY = {"below_90W": 0.35, "above_90W_oxidized": 0.5}


def calibrate_xi(points) -> dict:
    """xi = ΔT / P（°C/W），转 °C/kW。"""
    rows = []
    for name, p_w, dT_c, cond, src in points:
        xi = dT_c / p_w
        rows.append({"cond": name, "P_W": p_w, "dT_C": dT_c, "xi_C_per_kW": round(xi * 1000, 1), "src": src})
    xis = [r["xi_C_per_kW"] for r in rows]
    return {
        "points": rows,
        "xi_range_C_per_kW": [round(min(xis), 1), round(max(xis), 1)],
        "xi_median_C_per_kW": round(sorted(xis)[len(xis) // 2], 1),
    }


def calibrate_kappa_eff(points) -> dict:
    """κ_eff = 力下降 / ΔT（1/°C）。"""
    rows = []
    for frac, dT, cond, src in points:
        ke = frac / dT
        rows.append({"cond": cond, "force_drop_frac": frac, "dT_C": dT, "kappa_eff": round(ke, 7), "src": src})
    kes = [r["kappa_eff"] for r in rows]
    return {
        "points": rows,
        "kappa_eff_range": [round(min(kes), 7), round(max(kes), 7)],
        "kappa_eff_median": round(sorted(kes)[len(kes) // 2], 7),
    }


def main() -> None:
    xi = calibrate_xi(XI_POINTS)
    ke = calibrate_kappa_eff(KAPPA_EFF_POINTS)

    # 工程功率预算修正：谷 2.0× 需 ΔT=500°C P_kW = ΔT / xi_C_per_kW
    dT_needed = 500.0
    p_lo = dT_needed / xi["xi_range_C_per_kW"][1]  # 用最大 xi（最低功率）
    p_hi = dT_needed / xi["xi_range_C_per_kW"][0]
    print("=== 真实实验标定结果 ===")
    print(
        f"[xi] 功率→温升实测：{xi['xi_range_C_per_kW'][0]}~{xi['xi_range_C_per_kW'][1]} °C/kW "
        f"（中值 {xi['xi_median_C_per_kW']}）"
    )
    print(f"[κ_eff 实测] {ke['kappa_eff_range'][0]}~{ke['kappa_eff_range'][1]} /°C （中值 {ke['kappa_eff_median']}）")
    print(f"  对比：J-C 文献标定 κ={KAPPA_TI64_CALIBRATED}，δ={DELTA_TI64_CALIBRATED}（E(T)）")
    for r_, ke_ in ((0.0, None), (0.3, None), (1.0, None)):
        print(f"  r={r_}: κ_eff(J-C) = {net_softening(KAPPA_TI64_CALIBRATED, DELTA_TI64_CALIBRATED, r_):.6f}")
    print(f"[功率预算] 谷 2.0×（ΔT=500°C）实测需求：{p_lo:.2f}~{p_hi:.2f} kW （旧粗估 1.67~5.00 kW）")
    print(f"[安全窗] 相变 800-880°C / 氧化 1100°C / 平均安全上限 500°C")

    summary = {
        "xi": xi,
        "kappa_eff_measured": ke,
        "kappa_jc_calibrated": KAPPA_TI64_CALIBRATED,
        "delta_et_calibrated": DELTA_TI64_CALIBRATED,
        "power_for_2x_gain_kW": [round(p_lo, 2), round(p_hi, 2)],
        "power_old_estimate_kW": [1.67, 5.00],
        "temperature_limits": T_CRITICAL,
        "absorptivity": ABSORPTIVITY,
        "sources": {
            "hybrid2023": "Dominguez-Caballero et al. 2023, IJAMT 125:1903-1916, "
            "DOI 10.1007/s00170-022-10764-5 (OA, thermal camera)",
            "rashid2015": "Rashid et al. 2015, Lasers Manuf. Mater. Process. 2:164-185, "
            "DOI 10.1007/s40516-015-0013-4 (OA)",
        },
    }
    with open(OUT / "experimental_calibration.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\n已保存：{OUT / 'experimental_calibration.json'}")


if __name__ == "__main__":
    main()
