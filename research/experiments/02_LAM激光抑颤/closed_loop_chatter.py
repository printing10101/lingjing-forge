"""时域闭环仿真：2-DOF 再生颤振 + 热软化 + 激光功率控制律

全仿真路线"灵魂"模块。把论文从静态频域 SLD 分析升级为动态闭环验证：

  1. 2-DOF 再生颤振时域模型（Altintas 类型，单模态主导近似）：
       m x'' + c x' + k x = -k_c·a_p·f(T_cut) · [ x(t) - x(t-T) ]
     f(T_cut) = 1 - κ_eff·ΔT_cut 为激光热软化因子（实测标定）
  2. 激光功率执行器：一阶滞后 tau_laser，饱和限幅（0~P_max）
  3. 控制律（双自由度，可切换）：
       - "ff" 前馈开环（功率由工况设定）
       - "pi" 反馈 PI（颤振 RMS 代理指标）
       - "ff+pi" 联合（推荐，论文双自由度控制律）
  4. 颤振指标（在线可测代理）：位移 RMS 滑动窗
  5. 输出：时域响应、抑制效果、稳定时间、稳态 RMS

无激光临界切深直接取自频域 ThermalSLDModel（k=5e7 N/m 工程刚度），
保证时域-频域交叉一致（benchmark 复现一并完成）。
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thermal_sld_model import ThermalSLDModel, net_softening  # noqa: E402

# ---- 真实实验标定（calibrate_from_literature_experiments.py，DOI 见该文件）----
KAPPA_EFF_MEDIAN = 0.00046  # 实测中值（0.00018~0.0008 范围）
XI_MEDIAN = 768.0  # °C/kW 实测中值（733~1107）
XI_LO, XI_HI = 733.3, 1106.7

# ---- 动力学基准（与 exp_thermal_sld.py 工程场景一致：k=5e7, m=50kg, zeta=0.05）----
K_STRUCT = 5.0e7  # N/m
M_MODAL = 50.0  # kg
ZETA = 0.05
WN_HZ = np.sqrt(K_STRUCT / M_MODAL) / (2 * np.pi)  # ~159 Hz
KC_TI64 = 2.0e9  # 比切力 N/m^2（Ti-6Al-4V 典型值）
SPINDLE_RPM = 3000.0  # 主轴转速
TAU = 60.0 / SPINDLE_RPM  # 再生周期 s
DT_CUT_TARGET = 500.0  # 目标温升 °C（安全窗内）
TAU_LASER = 0.02  # 激光执行器一阶滞后 s
P_MAX = 1000.0  # 执行器饱和 W
P_FEEDFORWARD = DT_CUT_TARGET / XI_MEDIAN * 1000.0  # 前馈功率 W


def dT_from_power(p_w: float, xi_C_per_kW: float = XI_MEDIAN) -> float:
    """功率→温升（实测 xi 标定）。"""
    return p_w / 1000.0 * xi_C_per_kW


def force_softening(dT_cut: float, kappa_eff: float = KAPPA_EFF_MEDIAN) -> float:
    """热软化因子 f = 1 - κ_eff·ΔT_cut（物理下限钳位）。"""
    return max(1.0 - kappa_eff * dT_cut, 1e-3)


def chatter_response(
    a_p: float,
    k_c_lin: float,
    tau_reg: float,
    control: str = "none",
    t_end: float = 6.0,
    kp: float = 0.0,
    ki: float = 0.0,
    chatter_target: float = 5.0e-5,
    seed: int = 0,
    dt_out: float = 5e-5,
    kappa_eff_true: float | None = None,
    mode2: bool = False,
    mode2_participation: float = 0.5,
    mode2_stiffness_ratio: float = 6.25,
):
    """单自由度再生颤振时域响应（延迟用完整历史缓冲）。

    k_c_lin / tau_reg 由调用方给定（与频域 a_lim 及谷转速匹配，
    保证时域-频域交叉一致）。无激光时 a_p > a_lim 应指数发散；
    激光（前馈/反馈）使 f_soft 降低有效切削刚度，重新落入稳定区 → 抑制。

    kappa_eff_true: 真实世界的净软化率（=κ−δ·r，r 为真实温差比）。
    控制器按标称 KAPPA_EFF_MEDIAN 设计前馈；若 r 与假设偏离，
    实际软化与设计值不同——本参数用于 r 敏感性分析（§7.3）。
    """
    rng = np.random.default_rng(seed)
    m = M_MODAL
    wn = 2 * np.pi * WN_HZ
    c_damp = 2 * ZETA * wn * m
    k = K_STRUCT

    n_steps = int(t_end / dt_out)
    n_tau = max(1, int(round(tau_reg / dt_out)))
    # 初始扰动：随机微冲量（材料不均匀/切入冲击）
    x0 = float(rng.normal(0, 2e-6))
    v0 = float(rng.normal(0, 1e-3))
    X_CLAMP = 1.0e-3  # 撞刀/破坏阈值 m：振幅达 1mm 视为颤振破坏，冻结积分
    F_NOISE = 20.0  # 切削力过程噪声 N（材料硬度波动/切屑断裂，持续激励源）
    chattered = False

    xs = np.zeros(n_steps + 1)
    ps = np.zeros(n_steps + 1)
    dTs = np.zeros(n_steps + 1)
    ind = np.zeros(n_steps + 1)
    xs[0] = x0
    x, v = x0, v0
    # 模态 2（可选：多模态扩展，§8）：k2=ratio·k，同模态质量，阻尼比同
    if mode2:
        k2 = mode2_stiffness_ratio * K_STRUCT
        m2 = M_MODAL
        wn2 = np.sqrt(k2 / m2)
        c2 = 2 * ZETA * wn2 * m2
        x2, v2 = float(rng.normal(0, 2e-6)), float(rng.normal(0, 1e-3))
        xs2 = np.zeros(n_steps + 1)
        xs2[0] = x2
        chattered2 = False
    else:
        k2 = c2 = x2 = v2 = 0.0
        xs2 = None
        chattered2 = False
    p_laser = 0.0
    int_err = 0.0
    win = max(1, int(round(0.05 / dt_out)))  # 50ms RMS 窗

    for i in range(n_steps):
        t = i * dt_out
        # ---- 控制律（RMS 用总位移 x + p2·x2）----
        x_total_now = x + mode2_participation * x2 if mode2 else x
        if control == "ff":
            p_laser = min(P_FEEDFORWARD, P_MAX)
        elif control in ("pi", "ff+pi"):
            if i >= win and not chattered:
                w = xs[i - win : i + 1] + (mode2_participation * xs2[i - win : i + 1] if mode2 else 0.0)
                E = float(np.sqrt(np.mean(w**2)))
                e = max(E - chatter_target, 0.0)
                int_err += e * dt_out
                p_fb = kp * e + ki * int_err
            else:
                p_fb = 0.0
            p_ff = P_FEEDFORWARD if control == "ff+pi" else 0.0
            p_cmd = min(p_ff + p_fb, P_MAX)
            p_laser += (p_cmd - p_laser) * dt_out / TAU_LASER
        # ---- 动力学（半隐式欧拉，步长 5e-5 << 周期）----
        if chattered:
            pass  # 已破坏：冻结状态
        else:
            x_prev = xs[i - n_tau] if i >= n_tau else xs[0]
            keff_use = kappa_eff_true if kappa_eff_true is not None else KAPPA_EFF_MEDIAN
            f_soft = max(1.0 - keff_use * dT_from_power(p_laser), 1e-3)
            k_eff = k_c_lin * a_p * f_soft  # k_c_lin=N/m²（单位切深系数）· a_p → N/m
            if mode2:
                x2_prev = xs2[i - n_tau] if i >= n_tau else xs2[0]
                xt = x + mode2_participation * x2
                xtp = x_prev + mode2_participation * x2_prev
                f_regen = -k_eff * (xt - xtp)
                x_dd = (-c_damp * v - k * x + f_regen + float(rng.normal(0.0, F_NOISE))) / m
                x2_dd = (-c2 * v2 - k2 * x2 + mode2_participation * f_regen + float(rng.normal(0.0, F_NOISE))) / m2
                v_new = v + dt_out * x_dd
                x_new = x + dt_out * v_new
                v2_new = v2 + dt_out * x2_dd
                x2_new = x2 + dt_out * v2_new
                if abs(x_new) > X_CLAMP:
                    x_new = X_CLAMP if x_new > 0 else -X_CLAMP
                    v_new = 0.0
                    chattered = True
                if abs(x2_new) > X_CLAMP:
                    x2_new = X_CLAMP if x2_new > 0 else -X_CLAMP
                    v2_new = 0.0
                    chattered2 = True
                x, v, x2, v2 = x_new, v_new, x2_new, v2_new
            else:
                x_dd = (-c_damp * v - k * x - k_eff * (x - x_prev) + float(rng.normal(0.0, F_NOISE))) / m
                v_new = v + dt_out * x_dd
                x_new = x + dt_out * v_new
                if abs(x_new) > X_CLAMP:
                    x_new = X_CLAMP if x_new > 0 else -X_CLAMP
                    v_new = 0.0
                    chattered = True
                x, v = x_new, v_new
        xs[i + 1] = x
        if xs2 is not None:
            xs2[i + 1] = x2
        ps[i + 1] = p_laser
        dTs[i + 1] = dT_from_power(p_laser)
        if i >= win:
            if mode2:
                w = xs[i + 1 - win : i + 2] + mode2_participation * xs2[i + 1 - win : i + 2]
            else:
                w = xs[i + 1 - win : i + 2]
            ind[i + 1] = float(np.sqrt(np.mean(w**2)))

    t = np.arange(n_steps + 1) * dt_out
    n_ss = max(1, int(n_steps * 0.25))
    rms_ss = float(np.sqrt(np.mean(xs[-n_ss:] ** 2)))
    peak_p = float(np.max(ps))
    peak_dT = float(np.max(dTs))
    # 稳定时间：RMS 首次衰减到 2× 目标以下并保持（从后往前找）
    t_settle = None
    for j in range(n_steps, win, -1):
        if ind[j] <= max(chatter_target * 2.0, 2e-6):
            t_settle = t[j]
        else:
            break
    if mode2:
        x2_ss = xs2[-n_ss:]
        rms2_ss = float(np.sqrt(np.mean(x2_ss**2)))
    else:
        rms2_ss = None
    return dict(
        t=t,
        x=xs,
        p=ps,
        dT=dTs,
        ind=ind,
        t_settle=t_settle,
        rms_ss=rms_ss,
        peak_p=peak_p,
        peak_dT=peak_dT,
        chattered=chattered,
        rms2_ss=rms2_ss,
        x2=xs2,
    )


def main() -> None:
    import json

    # 频域模型定位叶瓣谷（最危险工况） 时域验证切深 = 1.3× 谷临界
    # 时域失稳另需再生相位 sin(ωT)>0（叶瓣频率），故扫转速找失稳窗口
    model = ThermalSLDModel(stiffness=K_STRUCT, modal_mass=M_MODAL, damping_ratio=ZETA)
    spindle_grid = np.linspace(400.0, 10000.0, 1200)
    a_lims = model.compute_limiting_depth(spindle_grid, dT=0.0, clip=False)
    i_valley = int(np.argmin(a_lims))
    a_lim_fd_mm = float(a_lims[i_valley])  # compute_limiting_depth 返回 mm
    a_lim_fd = a_lim_fd_mm * 1e-3  # mm -> m（时域动力学单位）
    a_p = a_lim_fd * 1.3
    # 单位切深切削刚度系数（N/m²，与转速无关）：频域谷处 k_eff=2ζk 的等价
    k_c_lin = 2.0 * ZETA * K_STRUCT / a_lim_fd
    print(f"频域叶瓣谷：a_lim = {a_lim_fd_mm:.3f} mm @ {spindle_grid[i_valley]:.0f} rpm")
    print(f"时域验证切深 a_p = {a_p * 1e3:.3f} mm（1.3× 谷临界）")

    # 扫转速：无激光 vs 双自由度激光，统计失稳/抑制
    rpm_scan = np.arange(2000.0, 5200.0, 200.0)
    rows = []
    for n_rpm in rpm_scan:
        tau_reg = 60.0 / n_rpm
        r_none = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="none", t_end=2.0, seed=int(n_rpm))
        r_cl = chatter_response(
            a_p, k_c_lin=k_c_lin, tau_reg=tau_reg, control="ff+pi", t_end=2.0, seed=int(n_rpm), kp=6.5e6, ki=1.0e6
        )
        rows.append(
            {
                "rpm": n_rpm,
                "none_chattered": r_none["chattered"],
                "cl_chattered": r_cl["chattered"],
                "none_rms_um": r_none["rms_ss"] * 1e6,
                "cl_rms_um": r_cl["rms_ss"] * 1e6,
                "cl_peak_p_W": r_cl["peak_p"],
                "cl_peak_dT_C": r_cl["peak_dT"],
            }
        )

    n_inst = 0
    n_saved = 0
    for r in rows:
        # 失稳判据：稳态 RMS > 20 um（稳定基线 ~0.2 um，噪声驱动）
        r["none_instable"] = r["none_rms_um"] > 20.0
        r["cl_suppressed"] = (not r["cl_chattered"]) and r["cl_rms_um"] < min(r["none_rms_um"] / 3.0, 5.0)
        if r["none_instable"]:
            n_inst += 1
            if r["cl_suppressed"]:
                n_saved += 1
    print(
        f"[时域扫描] {len(rows)} 转速点：无激光失稳 {n_inst} 点，"
        f"激光闭环抑制 {n_saved}/{n_inst} 点（{100.0 * n_saved / n_inst:.0f}%）"
    )
    for r in rows:
        mark = "!" if r["none_instable"] else "."
        print(
            f"  {mark} {r['rpm']:5.0f} rpm  无激光 RMS={r['none_rms_um']:8.1f}um  "
            f"激光 RMS={r['cl_rms_um']:6.2f}um  P={r['cl_peak_p_W']:.0f}W "
            f"dT={r['cl_peak_dT_C']:.0f}C"
        )

    # 验证断言
    assert n_inst >= 3, "时域应存在无激光失稳窗口（颤振物理）"
    assert n_inst > 0 and n_saved == n_inst, "激光闭环必须抑制全部失稳点"
    for r in rows:
        if r["none_instable"]:
            assert r["cl_rms_um"] < min(r["none_rms_um"] / 3.0, 5.0), "闭环抑制必须显著"

    out = Path(__file__).resolve().parent.parent / "results" / "closed_loop"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "closed_loop_summary.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "a_lim_fd_mm": a_lim_fd_mm,
                "valley_rpm": float(spindle_grid[i_valley]),
                "a_p_mm": a_p * 1e3,
                "rpm_scan": rows,
                "stats": {"n_points": len(rows), "n_instability_none": n_inst, "n_suppressed_cl": n_saved},
                "params": {
                    "xi_C_per_kW": XI_MEDIAN,
                    "kappa_eff": KAPPA_EFF_MEDIAN,
                    "tau_laser_s": TAU_LASER,
                    "p_max_W": P_MAX,
                    "p_feedforward_W": P_FEEDFORWARD,
                    "wn_hz": WN_HZ,
                    "zeta": ZETA,
                    "k_struct": K_STRUCT,
                },
            },
            f,
            ensure_ascii=False,
            indent=1,
        )
    # 论文图件：强失稳点（3600 rpm）时域对比——无激光 vs 激光闭环
    _plot_timeseries(a_p, k_c_lin, 3600.0, out)
    print("已保存 results/closed_loop/closed_loop_summary.json + fig_closed_loop.png")


def _plot_timeseries(a_p: float, k_c_lin: float, n_rpm: float, out: Path) -> None:
    """绘制失稳转速时域响应对比（无激光 vs 激光闭环）+ 激光功率轨迹。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    r_none = chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=60.0 / n_rpm, control="none", t_end=1.5, seed=int(n_rpm))
    r_cl = chatter_response(
        a_p, k_c_lin=k_c_lin, tau_reg=60.0 / n_rpm, control="ff+pi", t_end=1.5, seed=int(n_rpm), kp=6.5e6, ki=1.0e6
    )
    fig, axes = plt.subplots(3, 1, figsize=(8.5, 8.5), sharex=True)
    for ax in axes:
        ax.grid(alpha=0.3)
    axes[0].plot(r_none["t"], r_none["x"] * 1e6, color="crimson", lw=0.7)
    axes[0].set_ylabel(r"无激光 $x$ ($\mu$m)")
    axes[0].set_title(f"{n_rpm:.0f} rpm，a_p = {a_p * 1e3:.2f} mm（1.3× 频域谷临界）——无激光颤振 vs 激光闭环抑制")
    axes[1].plot(r_cl["t"], r_cl["x"] * 1e6, color="navy", lw=0.7)
    axes[1].set_ylabel(r"激光闭环 $x$ ($\mu$m)")
    axes[2].plot(r_cl["t"], r_cl["p"], color="darkorange", lw=1.0)
    axes[2].set_ylabel("激光功率 P (W)")
    axes[2].set_xlabel("时间 (s)")
    fig.tight_layout()
    fig.savefig(out / "fig_closed_loop.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
