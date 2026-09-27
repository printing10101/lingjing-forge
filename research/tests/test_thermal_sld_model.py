"""
exp_thermal_sld 配套测试：热扩展 Tlusty 稳定性模型
===================================================
覆盖：
- 闭式解一致性：delta=0 时 a_lim(T)/a_lim(0) == 1/(1-kappa*dT) 逐点成立（rtol 1e-9）
- 单调性：kappa>0 时叶瓣谷随 dT 单调不减
- 反转判据：delta 显著大于 kappa 时加热由益转害（谷相对变化率下降）
- 谷检测：网格覆盖第一叶瓣谷（~955 rpm，论文基准参数）
- clip 开关：默认行为与 data_generator 一致；clip=False 返回未截断真实值
- 零 torch 依赖：import 本模块不触发 torch
- 与原类交叉校验：torch 可用时自动启用（否则 skip）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_EXP_DIR = str(Path(__file__).resolve().parent.parent / "experiments")
_EXP_DIR_LAM = str(Path(__file__).resolve().parent.parent / "experiments" / "02_LAM激光抑颤")
if _EXP_DIR_LAM not in sys.path:
    sys.path.insert(0, _EXP_DIR_LAM)
if _EXP_DIR not in sys.path:
    sys.path.insert(0, _EXP_DIR)

from thermal_sld_model import (  # noqa: E402
    ThermalSLDModel,
    default_spindle_grid,
)

MODEL = ThermalSLDModel()
GRID = default_spindle_grid(400)


def test_module_import_does_not_require_torch():
    """零 torch 依赖：import 不触发 torch。"""
    import ast
    import importlib

    mod = importlib.import_module("thermal_sld_model")
    # 只检查顶层 import（cross_check_original_model 内部的条件导入不算）
    src = Path(str(mod.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names: set[str] = set()
    for n in top_imports:
        names.update(a.name.split(".")[0] for a in n.names)
    assert "torch" not in names, f"顶层不应 import torch：{names}"


def test_closed_form_exact():
    """闭式解：delta=0 时未截断值逐点 a_lim(T)/a_lim(0) == 1/(1-kappa*dT)。

    clip=False 揭示真实叶瓣结构（论文基准下第一叶瓣谷 ~0.033mm，
    被数据生成器 [0.1,20] 约定截断）；闭式解与转速无关逐点成立。
    """
    for kappa in (0.0005, 0.001, 0.0015):
        for dT in (100.0, 200.0, 300.0, 400.0):
            if kappa * dT >= 1.0:
                continue
            a0 = MODEL.compute_limiting_depth(GRID, dT=0.0, clip=False)
            aT = MODEL.compute_limiting_depth(GRID, dT=dT, kappa=kappa, clip=False)
            ratio = aT / a0
            expect = MODEL.closed_form_ratio(kappa, dT)
            np.testing.assert_allclose(ratio, expect, rtol=1e-9)


def test_closed_form_kappa_boundary():
    """软化因子边界：kappa*dT 接近 1 时被拒绝（断言抛出）。"""
    with pytest.raises(AssertionError):
        MODEL.compute_limiting_depth(GRID, dT=600.0, kappa=0.002)
    with pytest.raises(AssertionError):
        MODEL.compute_limiting_depth(GRID, dT=-10.0)


def test_valley_monotonic_increasing():
    """加热（kappa>0, delta=0）时叶瓣谷单调不减（clip=False）。"""
    vals = [
        MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=dT, kappa=0.001, clip=False))
        for dT in (0.0, 100.0, 200.0, 300.0, 400.0, 500.0)
    ]
    diffs = np.diff(vals)
    assert np.all(diffs >= -1e-12), f"谷值不单调：{vals}"


def test_valley_rises_by_closed_form_amount():
    """谷抬升量符合闭式解：500C/kappa=0.001 时谷应精确翻倍（clip=False）。"""
    v0 = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=0.0, clip=False))
    v500 = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=500.0, kappa=0.001, clip=False))
    expect_ratio = MODEL.closed_form_ratio(0.001, 500.0)  # 2.0
    assert abs((v500 / v0) - expect_ratio) < 1e-9
    # 谷真实值远低于数据生成器下限（论文基准参数），证明 clip 会吞没机制
    assert v0 < 0.1, f"论文基准下第一叶瓣谷应 <0.1mm，实测 {v0}"


def test_reversal_when_delta_gt_kappa():
    """反转判据：delta 显著大于 kappa 时，加热的谷收益显著收窄甚至转负。"""
    kappa = 0.0005
    dT = 500.0
    v0 = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=0.0, clip=False))
    v_rigid = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=dT, kappa=kappa, clip=False))
    v_thin = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=dT, kappa=kappa, delta=0.0016, clip=False))
    gain_rigid = (v_rigid - v0) / v0
    gain_thin = (v_thin - v0) / v0
    # 薄壁场景收益必须显著低于刚性场景（刚度退化吃掉软化收益）
    assert gain_thin < gain_rigid - 0.15, f"反转未发生：rigid={gain_rigid:.3f} thin={gain_thin:.3f}"
    # 在强刚度退化下净收益可转负
    v_strong = MODEL.valley_level(MODEL.compute_limiting_depth(GRID, dT=dT, kappa=kappa, delta=0.0019, clip=False))
    gain_strong = (v_strong - v0) / v0
    assert gain_strong < 0.0, f"delta>kappa 时应出现有害区，实际 gain={gain_strong:.3f}"


def test_valley_detection_covers_first_lobe():
    """谷检测：clip=False 下第一叶瓣谷在 ~955 rpm（固有频率 15.9 Hz）。"""
    idx = ThermalSLDModel.detect_valleys(MODEL.compute_limiting_depth(GRID, dT=0.0, clip=False), n_valleys=3)
    assert len(idx) >= 3
    valley_rpms = GRID[idx]
    # 第一个叶瓣谷应在 900-1050 rpm 附近（f_n=15.9 Hz -> n=60*f_n/j, j=1）
    assert 800.0 < valley_rpms[0] < 1100.0, f"第一叶瓣谷不在预期位置：{valley_rpms}"


def test_clip_default_matches_data_generator():
    """默认行为（clip=True）与 data_generator 约定一致：[0.1, 20] mm。"""
    a = MODEL.compute_limiting_depth(GRID, dT=0.0)
    assert a.min() >= 0.1 - 1e-12
    assert a.max() <= 20.0 + 1e-12


def test_clip_false_no_truncation():
    """clip=False 返回未截断真实值（允许低于 0.1 的真实谷）。"""
    a = MODEL.compute_limiting_depth(GRID, dT=0.0, clip=False)
    assert a.min() < 0.1 - 1e-6, "clip=False 时应暴露 <0.1mm 的真实谷"


def test_thermal_effect_direction():
    """加热方向性：dT=0 时 kappa/delta 无效应（退化回基准）。"""
    a0 = MODEL.compute_limiting_depth(GRID, dT=0.0, clip=False)
    a_noop = MODEL.compute_limiting_depth(GRID, dT=0.0, kappa=0.003, delta=0.003, clip=False)
    np.testing.assert_allclose(a_noop, a0, rtol=0.0, atol=0.0)


def test_cross_check_original_model():
    """与原类 TlustyAnalyticalModel 交叉校验（需 torch；无 torch 时 skip）。"""
    ok, reason, max_err = ThermalSLDModel.cross_check_original_model(GRID[:60])
    if not ok:
        pytest.skip(f"原类不可用：{reason}")
    assert max_err is not None and max_err < 1e-6, f"与原类偏差过大：{max_err}"
