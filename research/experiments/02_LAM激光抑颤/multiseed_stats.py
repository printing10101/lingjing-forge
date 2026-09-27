# -*- coding: utf-8 -*-
"""M5：时域闭环多种子统计（回应评审：§6 确定性单次运行与 §7 MC 口径不一致）。

5 个失稳转速 × 5 seeds（噪声/初始条件），报告无激光/闭环 RMS 的均值±区间。
输出：results/closed_loop/multiseed_summary.json
"""
import json, sys, os
from pathlib import Path
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closed_loop_chatter as clc
from closed_loop_chatter import chatter_response, ThermalSLDModel, K_STRUCT, M_MODAL, ZETA

model = ThermalSLDModel(stiffness=K_STRUCT, modal_mass=M_MODAL, damping_ratio=ZETA)
grid = np.linspace(400.0, 10000.0, 1200)
a_lims = model.compute_limiting_depth(grid, dT=0.0, clip=False)
i_valley = int(np.argmin(a_lims))
a_lim_fd = a_lims[i_valley] * 1e-3
a_p = a_lim_fd * 1.3
k_c_lin = 2.0 * ZETA * K_STRUCT / a_lim_fd

unstable_rpms = [2200, 2600, 2800, 3600, 3800]
seeds = [42, 43, 44, 45, 46]
rows = []
for n_rpm in unstable_rpms:
    tau_reg = 60.0 / n_rpm
    none_rms, cl_rms = [], []
    for seed in seeds:
        rn = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="none", t_end=2.0, seed=seed)
        rc = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="ff+pi", t_end=2.0, seed=seed, kp=6.5e6, ki=1.0e6)
        none_rms.append(rn["rms_ss"] * 1e6)
        cl_rms.append(rc["rms_ss"] * 1e6)
    rows.append({
        "rpm": n_rpm,
        "none_rms": {"mean": round(float(np.mean(none_rms)), 2), "std": round(float(np.std(none_rms)), 2),
                     "min": round(float(np.min(none_rms)), 2), "max": round(float(np.max(none_rms)), 2)},
        "cl_rms": {"mean": round(float(np.mean(cl_rms)), 2), "std": round(float(np.std(cl_rms)), 2),
                   "min": round(float(np.min(cl_rms)), 2), "max": round(float(np.max(cl_rms)), 2)},
    })
    print(f"{n_rpm} rpm: none={np.mean(none_rms):.1f}±{np.std(none_rms):.1f} um  cl={np.mean(cl_rms):.3f}±{np.std(cl_rms):.3f} um  (5 seeds)")

out = Path(__file__).resolve().parent.parent / "results" / "closed_loop"
out.mkdir(parents=True, exist_ok=True)
with open(out / "multiseed_summary.json", "w", encoding="utf-8") as f:
    json.dump({"a_p_mm": a_p * 1e3, "seeds": seeds, "points": rows,
               "note": "M5: 5 失稳点 × 5 seeds 时域统计（回应评审 §6 口径）"}, f, ensure_ascii=False, indent=1)
print(f"\n已保存 {out / 'multiseed_summary.json'}")
