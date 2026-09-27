# -*- coding: utf-8 -*-
"""
双模态时域验证：激光主动抑制对多模态结构同样有效（§8 多模态扩展证据）

方法
----
在 closed_loop_chatter.chatter_response 的双模态扩展（mode2=True）上验证：
主模态（K=5e7 N/m, M=50 kg, ζ=0.05, ~159 Hz）+ 高频模态（k2=6.25k1 → ω2=2.5ω1，
同模态质量，阻尼比同），再生力按模态参与因子 p2 自洽分配。半隐式欧拉、
1 mm 撞刀冻结、20 N 过程噪声等机制与单模态完全一致（同一函数实现）。

验证逻辑
--------
1. 单模态 vs 双模态（p2=0.5 基准）：无激光失稳窗口一致，激光闭环均抑制
   → 结论对模态数稳健（局限③的仿真证据）
2. p2 敏感性（0.2/0.5/1.0）与 k2 比（4/6.25/9）：全组合激光闭环抑制
   → 结论对模态参数稳健
"""
import json
from pathlib import Path

import numpy as np

from closed_loop_chatter import (K_STRUCT, M_MODAL, ZETA, XI_MEDIAN,
                                 chatter_response, ThermalSLDModel)

OUT_DIR = Path(__file__).resolve().parent.parent / "results" / "multi_modal"
KP, KI = 6.5e6, 1.0e6


def _setup():
    model = ThermalSLDModel(stiffness=K_STRUCT, modal_mass=M_MODAL,
                            damping_ratio=ZETA)
    spindle_grid = np.linspace(400.0, 10000.0, 1200)
    a_lims = model.compute_limiting_depth(spindle_grid, dT=0.0, clip=False)
    i_valley = int(np.argmin(a_lims))
    a_lim_fd_mm = float(a_lims[i_valley])
    a_lim_fd = a_lim_fd_mm * 1e-3
    a_p = a_lim_fd * 1.3
    k_c_lin = 2.0 * ZETA * K_STRUCT / a_lim_fd
    return a_p, k_c_lin, i_valley


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    a_p, k_c_lin, i_valley = _setup()
    rpm_valley = 400.0 + i_valley * (9600.0 / 1199.0)
    print(f"谷：a_lim_fd={a_p/1.3*1e3:.3f} mm @ {rpm_valley:.0f} rpm；a_p={a_p*1e3:.3f} mm")

    # 失稳窗口（同 main() 扫法，2s 无激光）
    rpm_scan = np.arange(2000.0, 5200.0, 200.0)
    unstable = [n for n in rpm_scan
                if chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=60.0 / n,
                                    control="none", t_end=2.0, seed=int(n))["chattered"]]
    print(f"失稳转速窗口: {[int(r) for r in unstable]}")

    rows = []
    # ---- 1. 单 vs 双模态（p2=0.5, k2 比 6.25）----
    for n_rpm in unstable:
        tau = 60.0 / n_rpm
        r_single = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau,
                                    control="ff+pi", t_end=4.0, seed=int(n_rpm),
                                    kp=KP, ki=KI)
        r_dual = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau,
                                  control="ff+pi", t_end=4.0, seed=int(n_rpm),
                                  kp=KP, ki=KI, mode2=True,
                                  mode2_participation=0.5, mode2_stiffness_ratio=6.25)
        rows.append({"rpm": n_rpm,
                     "single_rms_um": r_single["rms_ss"] * 1e6,
                     "single_ok": r_single["rms_ss"] < 50e-6 and not r_single["chattered"],
                     "dual_rms_um": r_dual["rms_ss"] * 1e6,
                     "dual_mode2_um": (r_dual["rms2_ss"] or 0.0) * 1e6,
                     "dual_peak_W": r_dual["peak_p"],
                     "dual_ok": r_dual["rms_ss"] < 50e-6 and not r_dual["chattered"]})
        print(f"rpm={n_rpm:.0f} 单模态 RMS={rows[-1]['single_rms_um']:8.2f}μm "
              f"抑制={rows[-1]['single_ok']} | "
              f"双模态 RMS={rows[-1]['dual_rms_um']:8.2f}μm "
              f"(模态2={rows[-1]['dual_mode2_um']:.2f}μm) P={rows[-1]['dual_peak_W']:.0f}W "
              f"抑制={rows[-1]['dual_ok']}")

    # ---- 2. p2 × k2 比敏感性（在最强失稳点）----
    n_worst = max(unstable, key=lambda r: r)
    tau_w = 60.0 / n_worst
    sens = []
    for p2 in (0.2, 0.5, 1.0):
        for k2r in (4.0, 6.25, 9.0):
            rr = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_w,
                                  control="ff+pi", t_end=4.0, seed=7,
                                  kp=KP, ki=KI, mode2=True,
                                  mode2_participation=p2, mode2_stiffness_ratio=k2r)
            ok = rr["rms_ss"] < 50e-6 and not rr["chattered"]
            sens.append({"p2": p2, "k2_ratio": k2r,
                         "rms_um": rr["rms_ss"] * 1e6, "ok": ok})
            print(f"  p2={p2:.1f} k2比={k2r:.2f}: RMS={rr['rms_ss']*1e6:8.2f}μm "
                  f"峰值P={rr['peak_p']:.0f}W 抑制={ok}")

    n_fail = sum(1 for r in rows if not r["dual_ok"]) + sum(1 for s in sens if not s["ok"])
    n_tot = len(rows) + len(sens)
    summary = {"unstable_rpm": unstable, "rows": rows, "sens": sens,
               "suppress_all": n_fail == 0, "n_ok": n_tot - n_fail, "n_total": n_tot,
               "params": {"mode1": {"k": K_STRUCT, "m": M_MODAL, "zeta": ZETA,
                                    "freq_hz": float(np.sqrt(K_STRUCT / M_MODAL) / (2 * np.pi))},
                          "a_p_mm": a_p * 1e3, "rpm_valley": rpm_valley}}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果: {OUT_DIR / 'summary.json'}")
    print(f"多模态全组合抑制: {summary['suppress_all']} ({summary['n_ok']}/{summary['n_total']})")
    _plot_fig11(rows, OUT_DIR)


def _plot_fig11(rows, out_dir: Path) -> None:
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
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    x = [r["rpm"] for r in rows]
    ax.bar([v - 30 for v in x], [r["single_rms_um"] for r in rows],
           width=50, color="#1f77b4", alpha=0.8, label="单模态（原 §6）激光闭环 RMS")
    ax.bar([v + 30 for v in x], [r["dual_rms_um"] for r in rows],
           width=50, color="#d62728", alpha=0.8, label="双模态（k2=6.25k1, p2=0.5）激光闭环 RMS")
    ax.axhline(50.0, color="gray", ls=":", lw=1.2, label="抑制阈值 50 μm")
    ax.set_xlabel("主轴转速 (rpm)")
    ax.set_ylabel("稳态振动 RMS (μm，对数轴)")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    p = out_dir / "fig11_multi_modal.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"图件: {p}")


if __name__ == "__main__":
    main()
