"""
exp_thermal_sld —— LAM 激光加热 -> 稳定性叶瓣 机制验证主实验
=============================================================
产出（research/experiments/results/thermal_sld/）：
    fig1_sld_curves.png     SLD 曲线族：刚性（delta=0）多温升 + 薄壁（delta=0.0006）对比
    fig2_reversal_phase.png delta-kappa 反转相图（谷值相对变化率 @500C）
    closed_form_check.csv   闭式解逐组合验证（理论 vs 实测）
    control_margin.csv      叶瓣谷抬升 vs 温升 + 激光功率粗估
    summary.json            全部关键数字（论文表格素材）

对应论文 5 机制部分 Figure 1-2 与 Table 1。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thermal_sld_model import (  # noqa: E402
    ThermalSLDModel,
    default_spindle_grid,
    KAPPA_TI64_CALIBRATED,
    KAPPA_TI64_RANGE,
    DELTA_TI64_CALIBRATED,
    net_softening,
)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent.parent / "results" / "thermal_sld"
OUT.mkdir(parents=True, exist_ok=True)

MODEL = ThermalSLDModel()
GRID = default_spindle_grid(400)
KAPPA_REF = 0.001  # 中等软化系数（1/℃），对应钛/钢高温软化量级
DELTA_REF = 0.0006  # 薄壁工件刚度退化系数（1/℃），500C 降 30%
DT_LIST = [0.0, 100.0, 200.0, 300.0, 400.0, 500.0]


def main() -> None:
    summary: dict = {}

    # 1. 闭式解验证（delta=0）：a_lim(T)/a_lim(0) == 1/(1-kappa*dT) 逐点
    kappa_set = [0.0005, 0.001, 0.0015]
    cf_rows: list[list] = []
    for kappa in kappa_set:
        for dT in DT_LIST[1:]:
            if kappa * dT >= 1.0:
                continue
            max_err, mean_err, n_unsat, n_total = MODEL.verify_closed_form(GRID, kappa, dT)
            cf_rows.append([kappa, dT, MODEL.closed_form_ratio(kappa, dT), max_err, mean_err, n_unsat, n_total])
    with open(OUT / "closed_form_check.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "kappa_1perC",
                "dT_C",
                "closed_form_ratio",
                "max_rel_err_unsat",
                "mean_rel_err_unsat",
                "n_unsat",
                "n_total",
            ]
        )
        w.writerows(cf_rows)
    worst = max(r[3] for r in cf_rows)
    summary["closed_form_max_rel_err_unsat"] = worst
    n_unsat_all = sum(r[5] for r in cf_rows)
    n_total_all = sum(r[6] for r in cf_rows)
    summary["closed_form_unsat_fraction"] = n_unsat_all / n_total_all
    print(
        f"[1] 闭式解验证完成：{len(cf_rows)} 组合，未饱和区最大逐点相对误差 = {worst:.2e} "
        f"（饱和点占比 {1 - n_unsat_all / n_total_all:.1%}，其增益被 20mm 上限截断）"
    )

    # 2. Figure 1：SLD 曲线族
    curves = {}
    for dT in DT_LIST:
        curves[f"rigid_dT{dT:.0f}"] = MODEL.compute_limiting_depth(GRID, dT=dT, kappa=KAPPA_REF, clip=False)
    curves["thin_dT400"] = MODEL.compute_limiting_depth(GRID, dT=400.0, kappa=KAPPA_REF, delta=DELTA_REF, clip=False)

    valley_base = MODEL.valley_level(curves["rigid_dT0"])
    valley_rigid_400 = MODEL.valley_level(curves["rigid_dT400"])
    valley_thin_400 = MODEL.valley_level(curves["thin_dT400"])
    valley_rigid_500 = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=500.0, kappa=KAPPA_REF, clip=False))
    summary["valley_mm"] = {
        "baseline": valley_base,
        "rigid_dT400": valley_rigid_400,
        "rigid_dT500": valley_rigid_500,
        "thin_dT400": valley_thin_400,
    }

    if True:  # matplotlib 顶层导入（本机已验证可用）
        fig, ax = plt.subplots(figsize=(9, 5.5))
        for dT in DT_LIST:
            ax.plot(GRID, curves[f"rigid_dT{dT:.0f}"], label=f"rigid, dT={dT:.0f} C (kappa={KAPPA_REF})")
        ax.plot(GRID, curves["thin_dT400"], "--", lw=2, label=f"thin-wall, dT=400 C (delta={DELTA_REF})")
        ax.set_xlabel("spindle speed (rpm)")
        ax.set_ylabel("a_lim (mm)")
        ax.set_title("Stability Lobe Diagram vs laser heating")
        ax.set_ylim(0, 8)
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(OUT / "fig1_sld_curves.png", dpi=150)
        plt.close(fig)
        print("[2] Figure 1 已保存：fig1_sld_curves.png")

    # Figure 1b：谷区放大（机制核心区：前 3 个叶瓣谷）
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for dT in DT_LIST:
        ax.plot(GRID, curves[f"rigid_dT{dT:.0f}"], lw=1.3, label=f"rigid, dT={dT:.0f} C")
    ax.plot(GRID, curves["thin_dT400"], lw=1.6, ls="--", c="#D62728", label="thin-wall, dT=400 C")
    ax.set_xlim(300, 1600)
    ax.set_ylim(0, 0.7)
    ax.set_xlabel("spindle speed (rpm)")
    ax.set_ylabel("a_lim (mm)")
    ax.set_title("Valley zone zoom: laser heating lifts the critical lobes")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig1b_valley_zoom.png", dpi=150)
    plt.close(fig)
    print("[2b] Figure 1b（谷区放大）已保存：fig1b_valley_zoom.png")

    # 3. Figure 2：delta-kappa 反转相图（谷值相对变化率 @500C）
    deltas = np.linspace(0.0, 0.0012, 7)  # 0 .. 0.0012 /C
    kappas = np.linspace(0.0002, 0.0018, 9)  # 0.0002 .. 0.0018 /C（500C 时 <=0.9<1）
    DT_PHASE = 500.0
    phase = np.zeros((len(deltas), len(kappas)))
    for i, dlt in enumerate(deltas):
        for j, kap in enumerate(kappas):
            aT = MODEL.compute_limiting_depth(GRID, dT=DT_PHASE, kappa=kap, delta=dlt, clip=False)
            vT = MODEL.valley_level(aT)
            phase[i, j] = (vT - valley_base) / valley_base  # 相对变化率

    if True:  # Figure 2
        fig, ax = plt.subplots(figsize=(8, 5.5))
        im = ax.imshow(
            phase,
            origin="lower",
            aspect="auto",
            cmap="RdYlGn",
            extent=(kappas[0], kappas[-1], deltas[0], deltas[-1]),
            vmin=-0.4,
            vmax=1.5,
        )
        # 反转判据线 delta = kappa
        line_x = np.linspace(kappas[0], kappas[-1], 50)
        ax.plot(line_x, line_x, "k--", lw=1.5, label="delta = kappa (reversal boundary)")
        ax.axhline(DELTA_REF, color="gray", ls=":", lw=1)
        ax.axvline(KAPPA_REF, color="gray", ls=":", lw=1)
        ax.set_xlabel("kappa (1/C) — softening rate")
        ax.set_ylabel("delta (1/C) — stiffness degradation rate")
        ax.set_title(f"Valley a_lim relative change at dT={DT_PHASE:.0f} C")
        ax.legend(fontsize=8, loc="upper left")
        fig.colorbar(im, label="(a_lim(T)-a_lim(0))/a_lim(0)")
        fig.tight_layout()
        fig.savefig(OUT / "fig2_reversal_phase.png", dpi=150)
        plt.close(fig)
        print("[3] Figure 2 已保存：fig2_reversal_phase.png")

    # 相图数值摘要：delta > kappa 区域是否有害（谷变化率 < 0）
    harmful_zone = phase < 0.0
    summary["phase_harmful_count"] = int(harmful_zone.sum())
    summary["phase_total"] = int(phase.size)
    above_line = deltas[:, None] > kappas[None, :]  # 显式值比较（网格起点不对称）
    above_harmful = int((harmful_zone & above_line).sum())
    summary["phase_harmful_above_delta_gt_kappa"] = above_harmful
    print(
        f"[3] 相图：{phase.size} 组合，加热有害（谷下降）{harmful_zone.sum()} 个，"
        f"其中 delta>kappa 侧 {above_harmful} 个"
    )

    # 4. 控制余量：叶瓣谷抬升 vs 温升 + 功率粗估
    # （论文基准参数谷仅 ~0.03mm，物理上无"抬到 2mm"意义；
    # 工程场景用真实机床刚度 k=5e7 N/m，谷 ~1.6mm，可谈 MRR 增益）
    # 4a. 论文基准场景（记录谷抬升曲线，表论文 Table 1）
    cm_rows: list[list] = []
    for dT in DT_LIST:
        aT = MODEL.compute_limiting_depth(GRID, dT=dT, kappa=KAPPA_REF, clip=False)
        v = MODEL.valley_level(aT)
        cm_rows.append([dT, v, (v - valley_base) / valley_base])
    summary["control_margin_paper_baseline"] = {f"dT{dT:.0f}": v for dT, v, _ in cm_rows}

    # 4b. 工程场景：真实机床（k=5e7 N/m, m=50 kg, zeta=0.05）
    ENG_MODEL = ThermalSLDModel(stiffness=5e7, modal_mass=50.0)
    eng_grid = default_spindle_grid(400)
    eng_rows: list[list] = []
    for dT in DT_LIST:
        aT = ENG_MODEL.compute_limiting_depth(eng_grid, dT=dT, kappa=KAPPA_REF, clip=False)
        v = ENG_MODEL.valley_level(aT)
        eng_rows.append([dT, v, (v - valley_base) / valley_base])
    eng_v0 = eng_rows[0][1]
    eng_rows = [[dT, v, (v - eng_v0) / eng_v0] for dT, v, _ in eng_rows]

    with open(OUT / "control_margin.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "dT_C", "valley_a_lim_mm", "rel_change_vs_baseline"])
        for dT, v, rel in eng_rows:
            w.writerow(["engineering_k5e7", dT, v, rel])
        for dT, v, rel in cm_rows:
            w.writerow(["paper_baseline_k1e6", dT, v, rel])
    summary["control_margin_engineering"] = {f"dT{dT:.0f}": v for dT, v, _ in eng_rows}

    # 工程场景：谷抬升至目标倍率所需温升（线性插值）+ 功率估算
    # 功率换算用真实实验标定 xi=733~1107 °C/kW（calibrate_from_literature_experiments.py，
    # 来源：Dominguez-Caballero 2023 IJAMT 热像仪实测 750W576~830°C）
    dT_arr = np.array([r[0] for r in eng_rows])
    v_arr = np.array([r[1] for r in eng_rows])
    eng_v0 = eng_rows[0][1]
    XI_LO, XI_HI = 733.3, 1106.7  # °C/kW，实测范围
    targets = {"1.5x": 1.5 * eng_v0, "2.0x": 2.0 * eng_v0}
    for name, target in targets.items():
        if v_arr[-1] >= target:
            dT_needed = float(np.interp(target, v_arr, dT_arr))
            power_lo = dT_needed / XI_HI
            power_hi = dT_needed / XI_LO
            summary[f"eng_dT_for_{name}"] = dT_needed
            summary[f"eng_power_kW_{name}"] = [round(power_lo, 2), round(power_hi, 2)]
            print(
                f"[4] 工程场景（k=5e7, 谷基线 {eng_v0:.2f}mm）：谷抬升至 {name} "
                f"（{target:.1f}mm）需 dT={dT_needed:.0f} C，"
                f"对应激光功率 {power_lo:.2f}~{power_hi:.2f} kW（实测 xi=733~1107 C/kW）"
            )
        else:
            print(f"[4] 工程场景（k=5e7）：500C 内谷（{v_arr[-1]:.2f}mm）不足以抬升至 {name}")

    # 4c. 文献标定场景：Ti-6Al-4V 净效应 κ_eff = κ - δ·r 与温差比判据
    # （标定值来自 calibrate_kappa_delta.py：κ=0.000736, δ=0.000517）
    KAPPA_CAL, DELTA_CAL = KAPPA_TI64_CALIBRATED, DELTA_TI64_CALIBRATED
    net_rows = []
    for r in (0.0, 0.1, 0.3, 0.5, 1.0):
        ke = net_softening(KAPPA_CAL, DELTA_CAL, r)
        gain = 1.0 / (1.0 - ke * 500.0) if ke * 500.0 < 1 else float("inf")
        net_rows.append((r, ke, gain))
        print(f"[4c] 标定场景: r={r:.1f} → κ_eff={ke:.6f} → 500°C 谷增益 {gain:.2f}×")
    summary["calibration"] = {
        "kappa_recommended": KAPPA_CAL,
        "kappa_range": list(KAPPA_TI64_RANGE),
        "delta": DELTA_CAL,
        "net_effect": {f"r{r:.1f}": {"kappa_eff": round(ke, 6), "gain_500C": round(g, 3)} for r, ke, g in net_rows},
        "sources": {
            "kappa": "J-C 9 sets: Karpat 2011 JMPT 10.1016/j.jmatprotec.2010.12.008 Table1 "
            "+ Zhang 2015 Procedia CIRP 10.1016/j.procir.2015.03.052 Table2",
            "delta": "E(T)=-57.7T+111672 MPa, Karpat 2009 (via Karpat 2011 p.743)",
        },
    }

    if True:  # matplotlib（本机已验证可用）
        fig, ax = plt.subplots(figsize=(9, 5.5))
        r_grid = np.linspace(0.0, 1.0, 200)
        for dT in (200, 300, 400, 500):
            ke = net_softening(KAPPA_CAL, DELTA_CAL, r_grid)
            gain = 1.0 / (1.0 - ke * dT)
            ax.plot(r_grid, gain, label=f"ΔT_cut={dT}°C")
        ax.axvline(0.3, color="gray", ls="--", lw=1, label="r=0.3（聚焦激光典型）")
        ax.set_xlabel("temperature ratio r = ΔT_struct / ΔT_cut")
        ax.set_ylabel("valley a_lim gain ×")
        ax.set_title("Ti-6Al-4V calibrated net gain vs temperature ratio (κ=0.000736, δ=0.000517 /°C)")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(OUT / "fig3_net_effect.png", dpi=150)
        plt.close(fig)
        print("[4c] Figure 3 已保存：fig3_net_effect.png")

    # 5. 交叉校验（torch 可用时）
    ok, reason, err = ThermalSLDModel.cross_check_original_model(GRID[:60])
    summary["cross_check"] = {"ok": ok, "reason": reason, "max_rel_err": err}
    print(f"[5] 与原类交叉校验：{'通过，最大相对误差 ' + f'{err:.2e}' if ok else f'跳过（{reason}）'}")

    # 6. summary.json
    with open(OUT / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("[6] summary.json 已保存")
    print("完成。输出目录：", OUT)


if __name__ == "__main__":
    main()
