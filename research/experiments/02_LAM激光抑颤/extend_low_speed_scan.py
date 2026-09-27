# -*- coding: utf-8 -*-
"""扩展时域验证：低速区扫描（1000–2000 rpm）+ τ_laser 敏感性。

回应 Hermes 评审 F3：
  - 全文卖点"补位 SSV 低速盲区"，原扫描最低 2200 rpm → 扩展至 1000 rpm；
  - τ_laser=0.02 s 为假设却支撑"响应带宽数十 ms" → 0.02–2 s 敏感性扫描。
输出：results/closed_loop/extended_low_speed_summary.json
"""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import closed_loop_chatter as clc
from closed_loop_chatter import chatter_response, ThermalSLDModel, K_STRUCT, M_MODAL, ZETA, KAPPA_EFF_MEDIAN, XI_MEDIAN, P_MAX, P_FEEDFORWARD

# 频域谷定位（与 main 一致）
model = ThermalSLDModel(stiffness=K_STRUCT, modal_mass=M_MODAL, damping_ratio=ZETA)
spindle_grid = np.linspace(400.0, 10000.0, 1200)
a_lims = model.compute_limiting_depth(spindle_grid, dT=0.0, clip=False)
i_valley = int(np.argmin(a_lims))
a_lim_fd_mm = float(a_lims[i_valley])
a_lim_fd = a_lim_fd_mm * 1e-3
a_p = a_lim_fd * 1.3
k_c_lin = 2.0 * ZETA * K_STRUCT / a_lim_fd
print(f"谷: a_lim={a_lim_fd_mm:.3f}mm @{spindle_grid[i_valley]:.0f}rpm | a_p={a_p*1e3:.3f}mm")

# ==== 1) 低速区扩展扫描（1000–5200 rpm）====
rpm_scan = np.arange(1000.0, 5200.0, 200.0)
rows = []
for n_rpm in rpm_scan:
    tau_reg = 60.0 / n_rpm
    r_none = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="none", t_end=2.0, seed=int(n_rpm))
    r_cl = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="ff+pi", t_end=2.0, seed=int(n_rpm), kp=6.5e6, ki=1.0e6)
    rows.append({"rpm": float(n_rpm), "none_rms_um": r_none["rms_ss"] * 1e6, "cl_rms_um": r_cl["rms_ss"] * 1e6,
                 "none_chattered": r_none["chattered"], "cl_chattered": r_cl["chattered"]})
for r in rows:
    r["none_instable"] = r["none_rms_um"] > 20.0
    r["cl_suppressed"] = (not r["cl_chattered"]) and r["cl_rms_um"] < min(r["none_rms_um"] / 3.0, 5.0)

n_inst = sum(1 for r in rows if r["none_instable"])
n_saved = sum(1 for r in rows if r["none_instable"] and r["cl_suppressed"])
print(f"[低速扩展] {len(rows)} 点：失稳 {n_inst}，抑制 {n_saved}/{n_inst}")
for r in rows:
    mark = "!" if r["none_instable"] else "."
    print(f"  {mark} {r['rpm']:5.0f} rpm  none={r['none_rms_um']:8.1f}um  cl={r['cl_rms_um']:6.2f}um")
# 低速区单独统计（1000–2000 rpm）
low = [r for r in rows if r["rpm"] <= 2000]
low_inst = sum(1 for r in low if r["none_instable"])
low_saved = sum(1 for r in low if r["none_instable"] and r["cl_suppressed"])
print(f"[低速区 1000-2000] {len(low)} 点：失稳 {low_inst}，抑制 {low_saved}/{low_inst}")

# ==== 2) τ_laser 敏感性（0.02 / 0.2 / 2.0 s）====
tau_sens = []
for tau in [0.02, 0.2, 2.0]:
    clc.TAU_LASER = tau  # monkeypatch 模块常量
    for n_rpm in [1400.0, 2000.0, 3600.0]:
        tau_reg = 60.0 / n_rpm
        r_cl = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="ff+pi", t_end=2.0, seed=int(n_rpm), kp=6.5e6, ki=1.0e6)
        tau_sens.append({"tau_laser_s": tau, "rpm": float(n_rpm), "cl_rms_um": r_cl["rms_ss"] * 1e6, "chattered": r_cl["chattered"]})
        print(f"  tau={tau}s @{n_rpm:.0f}rpm  cl_rms={r_cl['rms_ss']*1e6:.2f}um  chattered={r_cl['chattered']}")
clc.TAU_LASER = 0.02  # 还原

out = Path(__file__).resolve().parent.parent / "results" / "closed_loop"
out.mkdir(parents=True, exist_ok=True)
with open(out / "extended_low_speed_summary.json", "w", encoding="utf-8") as f:
    json.dump({
        "a_p_mm": a_p * 1e3, "valley_rpm": float(spindle_grid[i_valley]),
        "rpm_scan": rows,
        "stats": {"n_points": len(rows), "n_instability_none": n_inst, "n_suppressed_cl": n_saved,
                  "low_speed_1000_2000": {"n_points": len(low), "n_instability": low_inst, "n_suppressed": low_saved}},
        "tau_laser_sensitivity": tau_sens,
        "note": "低速区扩展 + τ_laser 敏感性（回应评审 F3：低速补位无验证、τ_laser 假设化）",
    }, f, ensure_ascii=False, indent=1)
print(f"\n已保存 {out / 'extended_low_speed_summary.json'}")
