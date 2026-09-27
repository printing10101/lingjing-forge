"""真实 LAM 实验数据标定一致性测试（calibrate_from_literature_experiments.py）。

数据源（Springer OA 全文，已下载存档）：
  - Dominguez-Caballero 2023, IJAMT 125:1903, DOI 10.1007/s00170-022-10764-5
  - Rashid et al. 2015, LMMP 2:164-185, DOI 10.1007/s40516-015-0013-4
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "02_LAM激光抑颤"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calibrate_from_literature_experiments as cal  # noqa: E402


def test_xi_point_values_match_paper():
    """750 W / 2mm 光斑 → 550~830°C（热像仪实测），xi = ΔT/P（脚本 round 至 1 位）。"""
    xi = cal.calibrate_xi(cal.XI_POINTS)
    xis = [r["xi_C_per_kW"] for r in xi["points"]]
    assert min(xis) == pytest.approx(round(550.0 / 750.0 * 1000, 1), rel=1e-9)   # 733.3
    assert max(xis) == pytest.approx(round(830.0 / 750.0 * 1000, 1), rel=1e-9)   # 1106.7


def test_xi_range_reasonable():
    """实测 xi 应在 500~1500 °C/kW（工业 LAM 温升效率量级）。"""
    xi = cal.calibrate_xi(cal.XI_POINTS)
    lo, hi = xi["xi_range_C_per_kW"]
    assert 500.0 < lo < hi < 1500.0


def test_kappa_eff_measured_values():
    """力下降/温升 → κ_eff（脚本 round 至 7 位）：10%/550°C≈1.818e-4；40%/500°C=8e-4。"""
    ke = cal.calibrate_kappa_eff(cal.KAPPA_EFF_POINTS)
    pts = {p["cond"]: p["kappa_eff"] for p in ke["points"]}
    assert pts["车削 Ti-6Al-4V"] == pytest.approx(0.10 / 550.0, rel=2e-3)
    assert pts["铣削 Ti-6Al-4V"] == pytest.approx(0.40 / 500.0, rel=2e-3)


def test_kappa_eff_measured_consistent_with_jc():
    """实测 κ_eff 中值应与 J-C 标定同量级（比值 0.3~3），验证文献标定可复现。"""
    ke = cal.calibrate_kappa_eff(cal.KAPPA_EFF_POINTS)
    ratio = ke["kappa_eff_median"] / cal.KAPPA_TI64_CALIBRATED
    assert 0.3 <= ratio <= 3.0


def test_power_budget_using_measured_xi():
    """谷 2.0×（ΔT=500°C）实测功率需求 0.3~1.0 kW，远低于旧粗估 1.67~5 kW。"""
    xi = cal.calibrate_xi(cal.XI_POINTS)
    lo, hi = xi["xi_range_C_per_kW"]
    p_lo = 500.0 / hi
    p_hi = 500.0 / lo
    assert p_lo == pytest.approx(500.0 / 1106.7, rel=1e-6)
    assert 0.3 <= p_lo < p_hi <= 1.0


def test_temperature_safety_window():
    """安全窗：相变 800~880°C < 氧化 1100°C；平均安全上限 500°C 低于两者。"""
    t = cal.T_CRITICAL
    assert t["phase_change_2023"] < t["oxidation_2015"]
    assert t["max_avg_safe_2015"] < t["phase_change_2015_model"]


def test_net_softening_with_measured_params():
    """实测 κ_eff 中值代入净效应判据：r=1 时仍须为正（聚焦是必要条件）。"""
    ke = cal.calibrate_kappa_eff(cal.KAPPA_EFF_POINTS)
    ke_med = ke["kappa_eff_median"]
    r1 = cal.net_softening(ke_med, 0.0, 1.0)   # 保守：仅实测软化、无结构退化
    assert r1 > 0
    # 与 J-C 标定 + E(T) δ 组合：κ_eff(J-C) - δ·r 在 r≤1 时为正（推荐参数下）
    assert cal.net_softening(cal.KAPPA_TI64_CALIBRATED, cal.DELTA_TI64_CALIBRATED, 1.0) > 0
