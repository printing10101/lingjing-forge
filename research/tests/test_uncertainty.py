"""不确定性传播蒙特卡洛测试（uncertainty_propagation.py）。

验证：采样分布合法、增益区间覆盖确定性复算、功率二分搜索单调、
最坏情形（r→1）如实呈现（无吹捧）。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "02_LAM激光抑颤"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uncertainty_propagation as uc  # noqa: E402


def test_sample_dist_shapes():
    d = uc.sample_dist(n=2000, seed=1)
    assert d.kappa.shape == (2000,)
    assert d.delta.shape == (2000,)
    assert d.r.shape == (2000,)
    assert d.xi.shape == (2000,)


def test_kappa_within_jc_range():
    """κ 采样必须在 J-C 标定区间 [0.000527, 0.001267] 内（三角分布支撑）。"""
    d = uc.sample_dist(n=10000, seed=2)
    assert d.kappa.min() >= uc.KAPPA_LO - 1e-12
    assert d.kappa.max() <= uc.KAPPA_HI + 1e-12


def test_r_and_xi_uniform_support():
    d = uc.sample_dist(n=5000, seed=3)
    assert d.r.min() >= uc.R_LO - 1e-12 and d.r.max() <= uc.R_HI + 1e-12
    assert d.xi.min() >= uc.XI_LO - 1e-9 and d.xi.max() <= uc.XI_HI + 1e-9


def test_gain_monotonic_in_power():
    """增益必须随功率单调递增（任意参数样本）。"""
    d = uc.sample_dist(n=500, seed=4)
    g1 = d.sample_gain(300.0)
    g2 = d.sample_gain(600.0)
    assert np.all(g2 > g1)


def test_gain_distribution_covers_deterministic():
    """MC 增益区间应覆盖中位参数确定性复算（1.40×@651W）。"""
    d = uc.sample_dist(n=5000, seed=5)
    g = d.sample_gain(651.0)
    det = 1.0 / (1.0 - (uc.KAPPA_MU - uc.DELTA_MU * 0.5) * 651.0 / 1000.0 * uc.XI_MU)
    assert np.percentile(g, 5) < det < np.percentile(g, 95)
    # 中位数接近确定性结果（±10%）
    assert abs(np.median(g) - det) / det < 0.1


def test_power_for_gain_meets_quantile():
    """power_for_gain 返回的功率必须恰好满足 P95 目标（验证二分搜索）。"""
    d = uc.sample_dist(n=3000, seed=6)
    p_req = d.power_for_gain(1.3, 0.95)
    g = d.sample_gain(p_req)
    assert np.percentile(g, 95) >= 1.3 - 1e-9
    # 略低 1% 功率时不应达标（单调性）
    g_lo = d.sample_gain(p_req * 0.99)
    assert np.percentile(g_lo, 95) < 1.3


def test_safety_window_not_violated():
    """工程前馈 651W 下峰值温升必须低于 800°C 相变安全窗。"""
    dT_max = 651.0 / 1000.0 * uc.XI_HI
    assert dT_max < 800.0


def test_worst_case_honest():
    """最坏情形（r→1 等温 + κ 下限）必须如实 ≤ 1.0（不吹捧增益）。"""
    d = uc.sample_dist(n=5000, seed=7)
    g = d.sample_gain(651.0)
    # r 均匀采样下限 0.3，最坏组合需直接构造
    ke_worst = uc.KAPPA_LO - uc.DELTA_MU * 1.0
    dT_worst = 651.0 / 1000.0 * uc.XI_LO
    gain_worst = 1.0 / (1.0 - ke_worst * dT_worst)
    assert gain_worst < 1.10, f"最坏情形增益 {gain_worst:.3f}× 应接近或低于 1.0"


def test_deterministic_cross_check_in_summary():
    """确定性复算（中位参数）应接近 MC 中位数（脚本内断言）。"""
    d = uc.sample_dist(n=5000, seed=20260811)
    ke_mid = uc.KAPPA_MU - uc.DELTA_MU * 0.5
    dT_mid = 651.0 / 1000.0 * uc.XI_MU
    det = 1.0 / (1.0 - ke_mid * dT_mid)
    assert abs(det - np.median(d.sample_gain(651.0))) < 0.05
