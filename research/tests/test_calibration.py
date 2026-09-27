"""Ti-6Al-4V 热-力参数标定一致性测试。

验证 calibrate_kappa_delta.py 与 thermal_sld_model.py 的标定常量：
  - δ 标定：E(T) = -57.7T + 111672 MPa 线性拟合斜率比（Karpat 2009）
  - κ 标定：9 组 J-C 参数的热软化窗口均值在文献范围内，推荐值为全体均值
  - 净效应判据：κ_eff = κ - δ·r 随温差比 r 单调递减，r=0 时增益最大
  - 标定参数下 500°C 谷增益与闭式解一致

运行（科研侧独立环境）：
  cd research && pytest tests/test_calibration.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "02_LAM激光抑颤"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))

import pytest

import calibrate_kappa_delta as ckd
from thermal_sld_model import (
    KAPPA_TI64_CALIBRATED, KAPPA_TI64_RANGE, DELTA_TI64_CALIBRATED,
    net_softening, ThermalSLDModel, default_spindle_grid,
)

# 文献锚点（经 Crossref/Unpaywall 验证）
E0_MPA, E_SLOPE = 111_672.0, 57.7   # Karpat 2009: E(T) = -57.7T + 111672 MPa
EXPECT_DELTA = E_SLOPE / E0_MPA     # 0.0005166...


def test_delta_equals_et_slope_ratio():
    """δ 标定 = E(T) 线性斜率 / E0（Karpat 2009 实测公式）。"""
    delta, e_vals = ckd.calibrate_delta()
    assert delta == pytest.approx(EXPECT_DELTA, rel=1e-9)
    assert DELTA_TI64_CALIBRATED == pytest.approx(EXPECT_DELTA, rel=1e-4)
    # 独立一致性：E(25°C) ≈ 110 GPa（与 Zhang 2015 Table 1 的 110 GPa 吻合）
    assert e_vals[25] / 1000 == pytest.approx(110.0, rel=0.02)


def test_kappa_recommended_is_range_mean():
    """κ 推荐值 = 全部参数组在标定窗口的 κ_avg 均值，且落在文献范围内。"""
    k_rows, k_summary = ckd.calibrate_kappa()
    assert k_summary["kappa_min"] <= k_summary["kappa_recommended"] <= k_summary["kappa_max"]
    assert k_summary["kappa_min"] > 0.0004      # 下限物理合理
    assert k_summary["kappa_max"] < 0.0015      # 上限物理合理
    # 与模型常量一致
    assert KAPPA_TI64_CALIBRATED == pytest.approx(k_summary["kappa_recommended"], rel=1e-3)
    assert KAPPA_TI64_RANGE[0] <= KAPPA_TI64_CALIBRATED <= KAPPA_TI64_RANGE[1]


def test_kappa_avg_exactly_reproduces_jc_residual():
    """κ_avg 定义精确性：1 - κ_avg·ΔT 必须等于 J-C 残余 1 - T*^m。"""
    TMELT, TROOM = 1630.0, 25.0
    TRANGE = TMELT - TROOM
    for _, _, _, _, m, _ in ckd.JC_SETS:
        for dT in (300.0, 500.0):
            k = ckd.kappa_avg(m, dT)
            residual = 1.0 - k * dT
            expect = 1.0 - (dT / TRANGE) ** m
            assert residual == pytest.approx(expect, rel=1e-12)


def test_kappa_range_covers_all_jc_sets():
    """κ 标定窗口覆盖全部 9 组 J-C 参数（含 Zhang 2015 两组）。"""
    k_rows, _ = ckd.calibrate_kappa()
    assert len(k_rows) == 9
    srcs = " ".join(r["src"] for r in k_rows)
    assert "Procedia" in srcs or "[2]" in srcs      # Zhang 2015 两组在列


def test_net_softening_monotonic_decreasing_in_r():
    """净软化率随温差比 r 单调递减（r 越大结构退化越显著）。"""
    for r_lo, r_hi in ((0.0, 1.0), (0.1, 0.5), (0.3, 1.0)):
        assert net_softening(KAPPA_TI64_CALIBRATED, DELTA_TI64_CALIBRATED, r_lo) > \
               net_softening(KAPPA_TI64_CALIBRATED, DELTA_TI64_CALIBRATED, r_hi)
    # 推荐参数下恒有益：κ_eff(r=1) > 0（κ > δ）
    assert net_softening(KAPPA_TI64_CALIBRATED, DELTA_TI64_CALIBRATED, 1.0) > 0


def test_calibrated_gain_matches_closed_form():
    """标定 κ 下 500°C 谷增益与闭式解 1/(1-κ_eff·ΔT) 一致（δ=0 理想聚焦）。"""
    k = KAPPA_TI64_CALIBRATED
    expect = 1.0 / (1.0 - k * 500.0)
    grid = default_spindle_grid(400)
    model = ThermalSLDModel()
    v0 = model.valley_level(model.compute_limiting_depth(grid, dT=0.0, clip=False))
    vT = model.valley_level(model.compute_limiting_depth(grid, dT=500.0, kappa=k, clip=False))
    assert vT / v0 == pytest.approx(expect, rel=1e-9)
    # 保守性：标定 κ 增益 < 原论文演示 κ=0.001 的增益
    assert vT / v0 < 2.0


def test_delta_above_kappa_reversal_still_holds():
    """标定 δ 下反转判据不变：δ > κ 时有害（谷下降）。"""
    model = ThermalSLDModel()
    grid = default_spindle_grid(400)
    v0 = model.valley_level(model.compute_limiting_depth(grid, dT=0.0, clip=False))
    # κ=δ 时中性（约等于 1），κ<δ 时下降
    v_neutral = model.valley_level(model.compute_limiting_depth(
        grid, dT=500.0, kappa=0.0004, delta=0.0004, clip=False))
    v_harm = model.valley_level(model.compute_limiting_depth(
        grid, dT=500.0, kappa=0.0004, delta=0.0005, clip=False))
    assert v_neutral / v0 == pytest.approx(1.0, abs=0.02)
    assert v_harm / v0 < 0.98
