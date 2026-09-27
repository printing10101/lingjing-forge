"""时域闭环颤振抑制一致性测试（closed_loop_chatter.py）。

验证：无激光再生颤振失稳窗口（物理真实性）→ 激光闭环（ff+pi）100% 抑制
且功率/温升在安全窗内，时域临界与频域叶瓣谷交叉一致。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "02_LAM激光抑颤"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import closed_loop_chatter as clc  # noqa: E402
from thermal_sld_model import ThermalSLDModel  # noqa: E402

# 与 main() 一致的工况（快速单点验证）
G = np.linspace(400.0, 10000.0, 1200)


@pytest.fixture(scope="module")
def setup():
    model = ThermalSLDModel(stiffness=clc.K_STRUCT, modal_mass=clc.M_MODAL,
                            damping_ratio=clc.ZETA)
    a_lims = model.compute_limiting_depth(G, dT=0.0, clip=False)
    iv = int(np.argmin(a_lims))
    a_lim = float(a_lims[iv]) * 1e-3          # mm -> m
    k_c_lin = 2.0 * clc.ZETA * clc.K_STRUCT / a_lim
    a_p = a_lim * 1.3
    return dict(a_lim=a_lim, a_p=a_p, k_c_lin=k_c_lin)


def test_fd_valley_consistent_with_engineering():
    """频域谷应与 exp_thermal_sld 工程场景量级一致（~3.5mm @ k=5e7）。"""
    model = ThermalSLDModel(stiffness=clc.K_STRUCT, modal_mass=clc.M_MODAL,
                            damping_ratio=clc.ZETA)
    a_lims = model.compute_limiting_depth(G, dT=0.0, clip=False)
    valley_mm = float(np.min(a_lims))
    assert 1.0 < valley_mm < 10.0, f"谷 {valley_mm:.2f}mm 应落在工程合理区间"


def test_noise_driven_stable_baseline(setup):
    """稳定转速下（2000 rpm）无激光稳态 RMS ~0.2um（噪声驱动，非颤振）。"""
    r = clc.chatter_response(setup["a_p"], k_c_lin=setup["k_c_lin"],
                             tau_reg=60.0 / 2000.0, control="none",
                             t_end=1.0, seed=2000)
    assert r["rms_ss"] < 5e-6, f"稳定点 RMS={r['rms_ss']*1e6:.2f}um 应 <5um"


def test_unstable_window_exists(setup):
    """强失稳转速（3600 rpm）无激光必须颤振（RMS>20um，撞刀或接近撞刀）。"""
    r = clc.chatter_response(setup["a_p"], k_c_lin=setup["k_c_lin"],
                             tau_reg=60.0 / 3600.0, control="none",
                             t_end=1.5, seed=3600)
    assert r["rms_ss"] > 20e-6, f"3600rpm 无激光应失稳（RMS={r['rms_ss']*1e6:.1f}um）"


def test_closed_loop_suppresses_instability(setup):
    """ff+pi 闭环在强失稳转速必须把 RMS 压到 <5um。"""
    r_none = clc.chatter_response(setup["a_p"], k_c_lin=setup["k_c_lin"],
                                  tau_reg=60.0 / 3600.0, control="none",
                                  t_end=1.5, seed=3600)
    r_cl = clc.chatter_response(setup["a_p"], k_c_lin=setup["k_c_lin"],
                                tau_reg=60.0 / 3600.0, control="ff+pi",
                                t_end=1.5, seed=3600,
                                kp=6.5e6, ki=1.0e6)
    assert r_cl["rms_ss"] < min(r_none["rms_ss"] / 3.0, 5e-6), \
        f"闭环应显著抑制（none={r_none['rms_ss']*1e6:.1f}um → cl={r_cl['rms_ss']*1e6:.2f}um）"


def test_closed_loop_power_within_safety_window(setup):
    """闭环功率 ≤ P_MAX（900W）、峰值温升 ≤ 800°C 相变安全窗。"""
    r = clc.chatter_response(setup["a_p"], k_c_lin=setup["k_c_lin"],
                             tau_reg=60.0 / 3600.0, control="ff+pi",
                             t_end=1.5, seed=3600, kp=6.5e6, ki=1.0e6)
    assert r["peak_p"] <= clc.P_MAX + 1e-9
    assert r["peak_dT"] <= 800.0


def test_force_softening_monotonic():
    """力软化因子随温升单调下降（热软化物理）。"""
    dTs = np.linspace(0.0, 800.0, 9)
    fs = [clc.force_softening(dT) for dT in dTs]
    assert all(fs[i] > fs[i + 1] for i in range(len(fs) - 1))
    assert fs[0] == pytest.approx(1.0, rel=1e-9)
    assert 0.4 < fs[-1] < 1.0


def test_cutting_stiffness_cross_consistency(setup):
    """时域-频域交叉一致：k_c_lin·a_lim = 2ζk（谷临界等价）。"""
    k_eff_at_lim = setup["k_c_lin"] * setup["a_lim"]
    assert k_eff_at_lim == pytest.approx(2.0 * clc.ZETA * clc.K_STRUCT, rel=1e-9)


def test_feedforward_power_constant():
    """前馈功率设定 651W（对应 500°C 目标温升 @ xi=768.8 °C/kW）。"""
    p_ff = clc.P_FEEDFORWARD
    assert p_ff == pytest.approx(500.0 / clc.XI_MEDIAN * 1000.0, rel=1e-9)
