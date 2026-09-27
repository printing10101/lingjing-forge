# -*- coding: utf-8 -*-
"""
r 敏感性分析：控制律对温差比 r 的鲁棒性（对冲"r 未实测"硬伤，§7.3）

科学问题
--------
控制器前馈按标称 κ_eff_nom=0.00046（r=0.3 假设）设计；真实温差比 r 未知，
真实净软化率 κ_eff_true = κ − δ·r 随 r 变化（r 大 → 结构整体也热 → 软化被
抵消）。若 r_true 偏离假设，原控制律（固定 500 °C 前馈 + PI）是否仍能抑制？
r 鲁棒律（保守前馈 + 反馈自适应）能否在所有 r 下抑制？

设计
----
- r 扫描：{0.1, 0.3, 0.5, 0.8, 1.0}（κ_eff_true = 0.000736 − 0.000517·r）
- 失稳工况：频域谷 a_p = 1.3×a_lim @ 谷转速（与 §6 一致）
- 控制律对比：
  A. 原律 ff+pi：前馈 500 °C 固定（按 κ_eff_nom 设计）+ PI 反馈
  B. r 鲁棒律 rff+pi：保守前馈（按 r=1 最坏情形 κ_eff_min 反推目标温升）+
     PI 反馈——前馈保守保证任何 r 下不欠补，反馈自适应吸收残余误差
- 指标：稳态 RMS、峰值功率、抑制成功（RMS < 50 μm）、调节时间
"""

import json
from pathlib import Path

import numpy as np

from closed_loop_chatter import K_STRUCT, M_MODAL, ZETA, XI_MEDIAN, P_MAX, chatter_response
from thermal_sld_model import ThermalSLDModel

import closed_loop_chatter as _clc

P_FF_BASE = _clc.P_FEEDFORWARD  # 原始标称前馈（500 °C @ κ_eff_nom），供还原

KAPPA_TI64 = 0.000736  # §4.1 J-C 标定
DELTA_TI64 = 0.000517  # §4.2 E(T) 标定
KAPPA_EFF_NOM = 0.00046  # 控制器标称（r=0.3 假设）

OUT_DIR = Path(__file__).resolve().parent.parent / "results" / "r_sensitivity"


def kappa_eff_of_r(r: float) -> float:
    """κ_eff = κ − δ·r（定理 2）。"""
    return KAPPA_TI64 - DELTA_TI64 * r


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 频域定位谷（与 §6 一致）----
    model = ThermalSLDModel(stiffness=K_STRUCT, modal_mass=M_MODAL, damping_ratio=ZETA)
    spindle_grid = np.linspace(400.0, 10000.0, 1200)
    a_lims = model.compute_limiting_depth(spindle_grid, dT=0.0, clip=False)
    i_valley = int(np.argmin(a_lims))
    a_lim_fd_mm = float(a_lims[i_valley])
    a_lim_fd = a_lim_fd_mm * 1e-3
    a_p = a_lim_fd * 1.3
    k_c_lin = 2.0 * ZETA * K_STRUCT / a_lim_fd
    rpm_valley = float(spindle_grid[i_valley])
    tau_reg = 60.0 / rpm_valley
    print(f"谷：a_lim={a_lim_fd_mm:.3f} mm @ {rpm_valley:.0f} rpm；a_p={a_p * 1e3:.3f} mm")

    # 无激光基准：扫转速找失稳窗口（再生相位条件，§6 同法）
    rpm_scan = np.arange(2000.0, 5200.0, 200.0)
    unstable = []
    for n_rpm in rpm_scan:
        tau_r = 60.0 / n_rpm
        r0 = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_r, control="none", t_end=2.0, seed=int(n_rpm))
        if r0["chattered"] or r0["rms_ss"] > 50e-6:
            unstable.append(n_rpm)
    print(f"失稳转速窗口: {[int(r) for r in unstable]}")
    if not unstable:
        raise RuntimeError("未找到失稳窗口，敏感性分析无意义")

    # ---- r 扫描（在失稳点集合上）----
    r_grid = [0.1, 0.3, 0.5, 0.8, 1.0]
    rows = []
    for n_rpm in unstable:
        tau_reg = 60.0 / n_rpm
        for r in r_grid:
            keff_true = kappa_eff_of_r(r)
            # A. 原律：前馈按标称 500 °C（P_FEEDFORWARD 全局默认）
            ra = chatter_response(
                a_p,
                k_c_lin=k_c_lin,
                tau_reg=tau_reg,
                control="ff+pi",
                t_end=4.0,
                seed=int(n_rpm),
                kp=6.5e6,
                ki=1.0e6,
                kappa_eff_true=keff_true,
            )
            # B. r 鲁棒律：保守前馈（按 r=1 最坏情形 κ_eff_min 反推温升目标）
            import closed_loop_chatter as clc

            keff_min = kappa_eff_of_r(1.0)  # 0.000219
            dT_req = 500.0 * (KAPPA_EFF_NOM / keff_min)  # 达到同等软化的保守温升
            p_ff_robust = min(dT_req / XI_MEDIAN * 1000.0, P_MAX)
            clc.P_FEEDFORWARD = p_ff_robust
            rb = chatter_response(
                a_p,
                k_c_lin=k_c_lin,
                tau_reg=tau_reg,
                control="ff+pi",
                t_end=4.0,
                seed=int(n_rpm),
                kp=6.5e6,
                ki=1.0e6,
                kappa_eff_true=keff_true,
            )
            clc.P_FEEDFORWARD = P_FF_BASE  # 还原全局前馈

            rows.append(
                {
                    "rpm": n_rpm,
                    "r": r,
                    "kappa_eff_true": keff_true,
                    "orig_rms_um": ra["rms_ss"] * 1e6,
                    "orig_peak_p_W": ra["peak_p"],
                    "orig_suppressed": ra["rms_ss"] < 50e-6 and not ra["chattered"],
                    "robust_rms_um": rb["rms_ss"] * 1e6,
                    "robust_peak_p_W": rb["peak_p"],
                    "robust_ff_W": p_ff_robust,
                    "robust_suppressed": rb["rms_ss"] < 50e-6 and not rb["chattered"],
                }
            )
            if n_rpm == unstable[0] and r in (0.3, 1.0):
                print(
                    f"rpm={n_rpm:.0f} r={r:.1f} κ_eff_true={keff_true:.6f} | "
                    f"原律 RMS={ra['rms_ss'] * 1e6:8.1f}μm P={ra['peak_p']:5.0f}W "
                    f"抑制={rows[-1]['orig_suppressed']} | "
                    f"鲁棒 RMS={rb['rms_ss'] * 1e6:8.1f}μm P={rb['peak_p']:5.0f}W "
                    f"抑制={rows[-1]['robust_suppressed']}"
                )

    n_fail_orig = sum(1 for x in rows if not x["orig_suppressed"])
    n_fail_robust = sum(1 for x in rows if not x["robust_suppressed"])
    print(f"\n原律失败组合: {n_fail_orig}/{len(rows)} | 鲁棒律失败组合: {n_fail_robust}/{len(rows)}")

    # 汇总
    summary = {
        "r_grid": r_grid,
        "unstable_rpm": unstable,
        "rows": rows,
        "orig_suppress_all": all(x["orig_suppressed"] for x in rows),
        "robust_suppress_all": all(x["robust_suppressed"] for x in rows),
        "n_fail_orig": n_fail_orig,
        "n_fail_robust": n_fail_robust,
        "params": {
            "kappa_TI64": KAPPA_TI64,
            "delta_TI64": DELTA_TI64,
            "kappa_eff_nom": KAPPA_EFF_NOM,
            "a_p_mm": a_p * 1e3,
            "rpm_valley": rpm_valley,
        },
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果: {OUT_DIR / 'summary.json'}")
    print(f"原律全 r 抑制: {summary['orig_suppress_all']} | 鲁棒律全 r 抑制: {summary['robust_suppress_all']}")

    # ---- Fig.10：r 敏感性（最差失稳点 3600 rpm 处 RMS vs r）----
    _plot_fig10(rows, unstable, OUT_DIR)


def _plot_fig10(rows, unstable, out_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for _f in ("Microsoft YaHei", "SimHei"):
        try:
            plt.rcParams["font.sans-serif"] = [_f, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue

    fig, ax1 = plt.subplots(figsize=(7.2, 4.6))
    r_vals = sorted({x["r"] for x in rows})
    rpm_worst = max(unstable, key=lambda r: 60.0 / r)  # 3600rpm 附近最坏
    # 用所有失稳点的平均 RMS（稳健）；另画最坏点（3600rpm）曲线
    ax1.axhline(50.0, color="gray", ls=":", lw=1.2, label="抑制阈值 50 μm")
    for label, key_rms, key_p, color in [
        ("原律（前馈按标称 κ_eff）", "orig_rms_um", "orig_peak_p_W", "#1f77b4"),
        ("r 鲁棒律（保守前馈+反馈）", "robust_rms_um", "robust_peak_p_W", "#d62728"),
    ]:
        worst = [next(x for x in rows if x["rpm"] == 3600 and x["r"] == r) for r in r_vals]
        rms_w = [x[key_rms] for x in worst]
        rms_all = [np.mean([x[key_rms] for x in rows if x["r"] == r]) for r in r_vals]
        ax1.plot(r_vals, rms_all, "--", color=color, alpha=0.45, lw=1.5, label=f"{label}（失稳点均值）")
        ax1.plot(r_vals, rms_w, "-o", color=color, lw=2.0, ms=5, label=f"{label}（3600 rpm 最坏点）")
    ax1.set_xlabel("温差比 r（结构/切削区温升比，未实测参数）")
    ax1.set_ylabel("稳态振动 RMS (μm)")
    ax1.set_yscale("log")
    ax1.set_ylim(0.05, 500)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    p = out_dir / "fig10_r_robustness.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"图件: {p}")


if __name__ == "__main__":
    main()
