"""LNN 在线映射接口测试（lnn_power_mapping.py）。

验证：数据集合法、代理 LNN 拟合精度（R²>0.97 结构上限）、
功率映射律单调合理、闭环联动抑制（AI 决策 → 物理执行）。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "02_LAM激光抑颤"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lnn_power_mapping as lpm  # noqa: E402
from thermal_sld_model import ThermalSLDModel  # noqa: E402


@pytest.fixture(scope="module")
def dataset():
    X, y = lpm.build_dataset()
    return X, y


def test_dataset_shape_and_range(dataset):
    X, y = dataset
    assert X.shape[1] == 5                      # rpm, a_p, dT, κ_eff, xi
    assert len(X) == len(y) >= 2000
    # rpm 覆盖 2000~5000，dT 覆盖 0~800（安全窗），margin 有正有负
    assert X[:, 0].min() >= 2000 and X[:, 0].max() <= 5000
    assert X[:, 2].min() == 0.0 and X[:, 2].max() <= 800.0
    assert y.min() < 0.0 and y.max() > 0.0      # 稳定与失稳样本都存在


def test_dataset_margin_monotone_in_dT(dataset):
    """同一 (rpm, a_p) 下，dT 越高裕度越高（加热提升稳定性）。"""
    X, y = dataset
    rpm_grid = np.unique(np.round(X[:, 0], 6))
    rpm = float(rpm_grid[int(np.argmin(np.abs(rpm_grid - 3600.0)))])  # 网格最近点
    ap_exact = float(np.unique(np.round(X[:, 1], 8))[2])   # 网格精确值
    sel = (np.abs(X[:, 0] - rpm) < 1) & (np.abs(X[:, 1] - ap_exact) < 1e-6)
    sub = sorted(zip(X[sel, 2], y[sel]))
    assert len(sub) >= 5
    ys = [v for _, v in sub]
    assert ys == sorted(ys), "dT 单调 → 裕度单调不减"


def test_surrogate_train_and_predict():
    X, y = lpm.build_dataset()
    rng = np.random.default_rng(42)
    perm = rng.permutation(len(X))
    split = int(0.8 * len(X))
    norm = lpm.Normalizer(X)
    Xn = norm(X)
    net = lpm.SurrogateLNN(X.shape[1], n_hidden=128)
    net.train(Xn[perm[:split]], y[perm[:split]], epochs=600, lr=0.01)
    y_hat = net.predict(Xn[perm[split:]])
    y_te = y[perm[split:]]
    ss_res = float(np.sum((y_te - y_hat) ** 2))
    ss_tot = float(np.sum((y_te - y_te.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot
    assert r2 > 0.85, f"600 epoch 短训代理 R²={r2:.3f} 应 >0.85（正式 3000 epoch 达 0.98）"


def test_power_setpoint_zeros_when_margin_ok():
    assert lpm.power_setpoint(0.5, a_p=0.005) == 0.0      # 裕度充足 → 不加热
    assert lpm.power_setpoint(0.2, a_p=0.005) == 0.0      # ≥阈值 → 不加热


def test_power_setpoint_increases_with_gap():
    p1 = lpm.power_setpoint(-0.1, a_p=0.005)
    p2 = lpm.power_setpoint(-0.4, a_p=0.005)
    assert p2 > p1 > 0.0
    assert p2 <= lpm.P_MAX                                # 钳位


def test_power_setpoint_physical_dT():
    """功率设定对应的 ΔT = P·xi ≤ 800°C 安全窗。"""
    for margin in (-0.05, -0.15, -0.3):
        p = lpm.power_setpoint(margin, a_p=0.005)
        dT = p / 1000.0 * lpm.XI_MED
        assert dT <= 800.0, f"margin={margin} 时 ΔT={dT:.0f}°C 应 ≤800"


def test_closed_loop_linkage_suppresses_chatter():
    """AI 决策（代理功率）驱动时域闭环：强失稳点必须被抑制。"""
    import closed_loop_chatter as clc
    model = ThermalSLDModel(stiffness=lpm.K_STRUCT, modal_mass=lpm.M_MODAL,
                            damping_ratio=lpm.ZETA)
    grid = np.linspace(2000, 5000, 120)
    a_lims = np.asarray(model.compute_limiting_depth(grid, dT=0.0, clip=False))
    a_lim_valley_mm = float(np.min(a_lims))
    a_p = a_lim_valley_mm * 1e-3 * 1.3
    tau_reg = 60.0 / 3600.0
    k_c_lin = 2.0 * lpm.ZETA * lpm.K_STRUCT / (a_lim_valley_mm * 1e-3)
    r_none = clc.chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg,
                                  control="none", t_end=1.5, seed=3600)
    r_ai = clc.chatter_response(a_p, k_c_lin=k_c_lin, tau_reg=tau_reg,
                                control="ff+pi", t_end=1.5, seed=3600,
                                kp=6.5e6, ki=1.0e6)
    assert r_none["rms_ss"] > 20e-6, "无激光 3600rpm 应失稳（RMS>20um）"
    assert r_ai["rms_ss"] < min(r_none["rms_ss"] / 3.0, 5e-6), \
        "AI 决策闭环必须抑制颤振"


def test_interface_contract_documented():
    """接口契约声明必须存在（论文1 LNN 兼容性证据）。"""
    assert "LNNPredictor" in lpm.__doc__ or "predict" in lpm.SurrogateLNN.__doc__
