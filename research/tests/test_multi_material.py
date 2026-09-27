"""多材料扩展测试（multi_material.py）。

验证：材料表完整性、κ_eff 计算正确、增益窗口单调合理、
Inconel 718 δ 推导正确、安全窗不越界、证据级别标注存在。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "02_LAM激光抑颤"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import multi_material as mm  # noqa: E402


def test_materials_table_complete():
    names = [m.name for m in mm.MATERIALS]
    assert "Ti-6Al-4V" in names and "Inconel 718" in names
    assert any("Ti5553" in n for n in names)


def test_inconel_delta_derivation():
    """Inconel 718 δ 由公开 E(T) 推导：E0=200GPa, E(500°C)=170GPa → 0.0003。"""
    assert mm.IN718_DELTA == pytest.approx(0.0003, abs=2e-5)


def test_kappa_eff_formula():
    """κ_eff = κ − δ·r 区间计算（Ti-6Al-4V 锚点）。"""
    mat = mm.MATERIALS[0]
    ke = mat.kappa_eff(r=1.0)
    kappa_lo = mat.kappa[0] - mat.delta * 1.0
    kappa_hi = mat.kappa[1] - mat.delta * 1.0
    assert ke == pytest.approx((kappa_lo, kappa_hi))


def test_gain_increasing_with_kappa():
    """同一材料：κ 高端（乐观）增益 > 低端（保守）增益。"""
    for mat in mm.MATERIALS:
        g = mat.gain_at(651.0, 500.0, r=0.5)
        assert g[1] > g[0], f"{mat.name} 增益窗口应单调"


def test_gain_conservative_when_r_high():
    """r 高（结构等温）→ 增益低（保守方向一致）。"""
    mat = mm.MATERIALS[0]
    g_r03 = mat.gain_at(651.0, 599.0, r=0.3)
    g_r10 = mat.gain_at(651.0, 599.0, r=1.0)
    assert g_r10[0] < g_r03[0]
    assert g_r10[1] < g_r03[1]


def test_ti64_anchor_window():
    """Ti-6Al-4V 锚点：651W/599°C 下 r=0.3 增益应覆盖 1.2~1.6×（论文核心数字）。"""
    mat = mm.MATERIALS[0]
    g = mat.gain_at(651.0, 599.0, r=0.3)
    assert g[1] >= 1.2, f"Ti64 乐观增益 {g[1]:.2f}× 应 ≥1.2"
    assert g[0] <= 1.6, f"Ti64 保守增益 {g[0]:.2f}× 应 ≤1.6（保守不吹）"


def test_safety_window_cap():
    """温升必须被钳制在 800°C 相变安全窗内。"""
    for mat in mm.MATERIALS:
        g = mat.gain_at(651.0, 1200.0)   # 请求 1200°C 也应被钳制
        ke_max = mat.kappa[1] - mat.delta * 0.3
        assert 1.0 / (1.0 - ke_max * 800.0) > 0, "钳制后不应产生负增益"
        # 800°C 时增益有限（不越界）
        assert g[1] < 20.0


def test_evidence_level_annotated():
    """每种材料必须带证据级别标注（），防止未核实数据冒充锚点。"""
    for mat in mm.MATERIALS:
        assert any(tag in mat.evidence for tag in ("✅", "⚠️", "🔶")), \
            f"{mat.name} 缺证据级别标注"


def test_ti5553_conservative_assumption():
    """Ti5553 同族假设：保守取等 Ti-6Al-4V，不得声称更高增益。"""
    ti64, ti5553 = mm.MATERIALS[0], mm.MATERIALS[1]
    # 同族保守：Ti5553 xi 缩放 = 1.0（不放大）
    assert ti5553.xi_scale <= 1.0
    assert ti64.xi_scale == 1.0
