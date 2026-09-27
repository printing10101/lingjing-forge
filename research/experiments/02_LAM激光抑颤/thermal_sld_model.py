"""
热扩展 Tlusty 稳定性模型（numpy-only，零 torch 依赖）
=====================================================
复刻 research/experiments/data_generator.py 的 TlustyAnalyticalModel.compute_limiting_depth
公式（逐行一致，见下方 _tlusty_a_lim_base），并加入温度依赖双通道：

    通道1 Ks 软化（正向）：Ks(T) = Ks_0 * (1 - kappa * dT)
        —— 激光加热软化材料 -> 切削力系数下降 -> a_lim 上升（稳定性边界抬升）
    通道2 刚度退化（负向，薄壁工件）：k(T) = k_0 * (1 - delta * dT)
        —— 弹性模量随温度下降 -> 工件/结构模态刚度下降
    通道3 阻尼：zeta 固定（一阶近似），预留 zeta_temperature_coeff 接口

论文核心结果（由本模块数值验证）：
    - 闭式解（delta=0，固定转速）：a_lim(T) = a_lim(0) / (1 - kappa*dT)
      —— 加热使稳定性叶瓣全谱等比放大，与转速无关
    - 反转判据：delta > kappa 时加热由益转害（刚度退化快于软化）

Ti-6Al-4V 文献标定（详见 experiments/calibrate_kappa_delta.py）：
    - KAPPA_TI64_CALIBRATED = 0.000736 /°C：9 组 Johnson-Cook 参数（JMPT 2011
      Table 1 + Procedia CIRP 2015 Table 2）热软化项 1-T*^m 在 300-500°C 窗口的
      平均软化率均值；文献范围 0.000527 ~ 0.001267
    - DELTA_TI64_CALIBRATED = 0.000517 /°C：Karpat 2009 实测
      E(T) = -57.7T + 111672 MPa（JMPT 2011 p.743 引用）线性拟合斜率比
    - 净效应判据：kappa_eff = kappa - delta*r，r = 结构温升/切削区温升（温差比）
      —— 聚焦激光（r→0）保证净有益；均匀加热（r→1）时净增益大幅衰减，
        部分保守 J-C 参数组（m>=1.0）下接近中性甚至转负

设计约束：
    - 不 import torch：本机无 torch 也能运行全部机制验证
    - 与原类一致性由 tests/test_thermal_sld_model.py 交叉校验保证
      （torch 可用时自动启用，否则 skip）
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

# 基准参数（与 data_generator.TlustyAnalyticalModel 默认值一致）
DEFAULT_STIFFNESS = 1e6  # N/m
DEFAULT_MODAL_MASS = 100.0  # kg
DEFAULT_DAMPING_RATIO = 0.05
DEFAULT_CUTTING_FORCE_COEFF = 2000.0  # N/mm^2
DEFAULT_NUM_TEETH = 4

# ---- Ti-6Al-4V 文献标定值（calibrate_kappa_delta.py 产出）----
KAPPA_TI64_CALIBRATED = 0.000736  # 1/°C，9 组 J-C 参数均值（300-500°C 窗口）
KAPPA_TI64_RANGE = (0.000527, 0.001267)  # 文献范围
DELTA_TI64_CALIBRATED = round(57.7 / 111_672.0, 9)  # ≈0.000517 /°C，Karpat 2009 E(T) 线性拟合


def net_softening(kappa: float, delta: float, r):
    """净软化率 κ_eff = κ - δ·r（r=结构温升/切削区温升，0~1）。"""
    return kappa - delta * r


CLIP_LO, CLIP_HI = 0.1, 20.0  # a_lim 合理范围（mm，与 data_generator 一致）


def _tlusty_a_lim_base(
    spindle_speed: np.ndarray,
    k_base: float,
    m_base: float,
    zeta: float,
    Ks_base: float,
    num_teeth: float,
    hardness: np.ndarray,
    tool_diameter: np.ndarray,
    feed_rate: np.ndarray,
    radial_depth: np.ndarray,
    num_lobes: int = 10,
) -> np.ndarray:
    """逐行复刻 data_generator.TlustyAnalyticalModel.compute_limiting_depth（无热效应）。

    多物理耦合：
        - 硬度 H -> Ks_eff = Ks_base*(H/200)^0.8
        - 齿数 z -> Ks_eff *= z/4
        - 进给 f -> Ks_eff *= (1 + 0.15*(f-0.25)/0.25)（切屑变薄非线性）
        - 刀具直径 D -> k,m,c 均乘 (D/10)^2（悬臂梁）
        - 径向切宽 ae -> 方向因子 mu = 0.5*(1+ae/8)
    Tlusty: a_lim = -1 / (2*Ks*mu*Re(G))，仅 Re(G)<0 时为正，取最危险（最小正）叶瓣。
    """
    c_base = 2.0 * zeta * np.sqrt(k_base * m_base)
    Ks_base_nm2 = Ks_base * 1e6  # N/mm^2 -> N/m^2

    N = len(spindle_speed)
    Ks_eff = Ks_base_nm2 * (hardness / 200.0) ** 0.8
    Ks_eff = Ks_eff * (num_teeth / 4.0)
    Ks_eff = Ks_eff * (1.0 + 0.15 * (feed_rate - 0.25) / 0.25)

    D_ratio = (tool_diameter / 10.0) ** 2
    k_eff = k_base * D_ratio
    m_eff = m_base * D_ratio
    c_eff = c_base * D_ratio

    mu_dir = 0.5 * (1.0 + radial_depth / 8.0)

    a_lim = np.empty(N)
    for idx in range(N):
        n_rpm = spindle_speed[idx]
        if n_rpm <= 0:
            a_lim[idx] = 20.0
            continue
        k_i, m_i, c_i = k_eff[idx], m_eff[idx], c_eff[idx]
        Ks_i, mu_i = Ks_eff[idx], mu_dir[idx]
        best_a = None
        for j in range(1, num_lobes + 1):
            f_c = j * n_rpm / 60.0
            omega_c = 2 * np.pi * f_c
            denom_real = k_i - m_i * omega_c**2
            denom_imag = c_i * omega_c
            real_G = denom_real / (denom_real**2 + denom_imag**2)
            if abs(real_G) < 1e-12:
                continue
            a_val = -1.0 / (2.0 * Ks_i * mu_i * real_G)
            if a_val <= 0:
                continue
            if best_a is None or a_val < best_a:
                best_a = a_val
        a_lim[idx] = best_a * 1000.0 if best_a is not None else 20.0
    return np.clip(a_lim, CLIP_LO, CLIP_HI)


class ThermalSLDModel:
    """热扩展稳定性模型：Tlusty + 温度依赖双通道。

    Parameters
    ----------
    stiffness, modal_mass, damping_ratio, cutting_force_coeff, num_teeth:
        与 data_generator.TlustyAnalyticalModel 同语义的基准参数。
    """

    def __init__(
        self,
        stiffness: float = DEFAULT_STIFFNESS,
        modal_mass: float = DEFAULT_MODAL_MASS,
        damping_ratio: float = DEFAULT_DAMPING_RATIO,
        cutting_force_coeff: float = DEFAULT_CUTTING_FORCE_COEFF,
        num_teeth: int = DEFAULT_NUM_TEETH,
    ) -> None:
        self.stiffness = stiffness
        self.modal_mass = modal_mass
        self.damping_ratio = damping_ratio
        self.cutting_force_coeff = cutting_force_coeff
        self.num_teeth = num_teeth

    def compute_limiting_depth(
        self,
        spindle_speed: np.ndarray,
        dT: float = 0.0,
        kappa: float = 0.0,
        delta: float = 0.0,
        hardness: Optional[np.ndarray] = None,
        tool_diameter: Optional[np.ndarray] = None,
        num_teeth: Optional[np.ndarray] = None,
        feed_rate: Optional[np.ndarray] = None,
        radial_depth: Optional[np.ndarray] = None,
        num_lobes: int = 10,
        clip: bool = True,
    ) -> np.ndarray:
        """带温度效应的极限切深。

        dT:      切削区温升（℃），>= 0
        kappa:   软化系数（1/℃），Ks(T)=Ks*(1-kappa*dT)
        delta:   刚度退化系数（1/℃），k(T)=k*(1-delta*dT)
        clip:    True（默认）时按 data_generator 约定截断至 [0.1, 20] mm
                （论文 1 数据生成器行为）；False 返回未截断真实值。
                机制分析（叶瓣谷/闭式解/相图）必须用 clip=False，
                否则真实谷值（本基准下 ~0.033mm）被下限吞没。
        """
        spindle_speed = np.atleast_1d(np.asarray(spindle_speed, dtype=float))
        N = len(spindle_speed)
        if hardness is None:
            hardness = np.full(N, 200.0)
        else:
            hardness = np.atleast_1d(np.asarray(hardness, dtype=float))
        if tool_diameter is None:
            tool_diameter = np.full(N, 10.0)
        else:
            tool_diameter = np.atleast_1d(np.asarray(tool_diameter, dtype=float))
        if num_teeth is None:
            num_teeth = np.full(N, float(self.num_teeth))
        else:
            num_teeth = np.atleast_1d(np.asarray(num_teeth, dtype=float))
        if feed_rate is None:
            feed_rate = np.full(N, 0.25)
        else:
            feed_rate = np.atleast_1d(np.asarray(feed_rate, dtype=float))
        if radial_depth is None:
            radial_depth = np.full(N, 4.0)
        else:
            radial_depth = np.atleast_1d(np.asarray(radial_depth, dtype=float))

        assert dT >= 0.0, "dT 必须 >= 0"
        assert 0.0 <= kappa * dT < 1.0, "软化因子必须满足 0 <= kappa*dT < 1"
        assert 0.0 <= delta * dT < 1.0, "刚度退化因子必须满足 0 <= delta*dT < 1"

        c_base = 2.0 * self.damping_ratio * np.sqrt(self.stiffness * self.modal_mass)
        Ks_base_nm2 = self.cutting_force_coeff * 1e6

        Ks_eff = Ks_base_nm2 * (hardness / 200.0) ** 0.8
        Ks_eff = Ks_eff * (num_teeth / 4.0)
        Ks_eff = Ks_eff * (1.0 + 0.15 * (feed_rate - 0.25) / 0.25)
        # —— 通道1：热软化 ——
        Ks_eff = Ks_eff * (1.0 - kappa * dT)

        D_ratio = (tool_diameter / 10.0) ** 2
        k_eff = self.stiffness * D_ratio
        m_eff = self.modal_mass * D_ratio
        # —— 通道2：刚度热退化（薄壁工件模态）——
        k_eff = k_eff * (1.0 - delta * dT)
        # 阻尼按 zeta 不变同步更新（c = 2*zeta*sqrt(k*m)）
        c_eff = 2.0 * self.damping_ratio * np.sqrt(k_eff * m_eff)

        mu_dir = 0.5 * (1.0 + radial_depth / 8.0)

        a_lim = np.empty(N)
        for idx in range(N):
            n_rpm = spindle_speed[idx]
            if n_rpm <= 0:
                a_lim[idx] = 20.0
                continue
            k_i, m_i, c_i = k_eff[idx], m_eff[idx], c_eff[idx]
            Ks_i, mu_i = Ks_eff[idx], mu_dir[idx]
            best_a = None
            for j in range(1, num_lobes + 1):
                f_c = j * n_rpm / 60.0
                omega_c = 2 * np.pi * f_c
                denom_real = k_i - m_i * omega_c**2
                denom_imag = c_i * omega_c
                real_G = denom_real / (denom_real**2 + denom_imag**2)
                if abs(real_G) < 1e-12:
                    continue
                a_val = -1.0 / (2.0 * Ks_i * mu_i * real_G)
                if a_val <= 0:
                    continue
                if best_a is None or a_val < best_a:
                    best_a = a_val
            a_lim[idx] = best_a * 1000.0 if best_a is not None else 20.0
        if clip:
            return np.clip(a_lim, CLIP_LO, CLIP_HI)
        return a_lim

    def closed_form_ratio(self, kappa: float, dT: float) -> float:
        """闭式解：加热后 a_lim 的等比放大因子 1/(1-kappa*dT)（delta=0 时）。"""
        return 1.0 / (1.0 - kappa * dT)

    def verify_closed_form(
        self,
        spindle_speed: np.ndarray,
        kappa: float,
        dT: float,
        rtol: float = 1e-9,
    ) -> Tuple[float, float, int, int]:
        """验证闭式解：未截断值逐点相对误差（clip=False）。

        未截断真实值下，闭式解 a_lim(T)/a_lim(0) = 1/(1-kappa*dT) 与转速
        无关地逐点成立（Ks 等比缩放不改变叶瓣结构）。返回
        (最大相对误差, 均值, 有效点数, 总点数)。
        """
        a0 = self.compute_limiting_depth(spindle_speed, dT=0.0, clip=False)
        aT = self.compute_limiting_depth(spindle_speed, dT=dT, kappa=kappa, clip=False)
        n_total = int(len(a0))
        ratio = aT / a0
        expect = self.closed_form_ratio(kappa, dT)
        rel_err = np.abs(ratio - expect) / expect
        return float(rel_err.max()), float(rel_err.mean()), n_total, n_total

    @staticmethod
    def detect_valleys(a_lim: np.ndarray, n_valleys: int = 3) -> np.ndarray:
        """检测 SLD 局部极小（叶瓣谷）索引，按深度排序取最深的 n_valleys 个。"""
        a = np.asarray(a_lim, dtype=float)
        if len(a) < 3:
            return np.array([], dtype=int)
        local_min = (a[1:-1] < a[:-2]) & (a[1:-1] < a[2:])
        idx = np.flatnonzero(local_min) + 1
        if len(idx) == 0:
            return np.array([], dtype=int)
        order = np.argsort(a[idx])
        return idx[order[:n_valleys]]

    def valley_level(self, a_lim: np.ndarray, n_valleys: int = 1) -> float:
        """最危险叶瓣谷水平（最低 n_valleys 个局部极小的均值，mm）。

        默认 n_valleys=1 取最低谷（最危险操作点）。闭式解证明所有叶瓣谷
        等比缩放，谷选择不影响结论。
        """
        idx = self.detect_valleys(a_lim, n_valleys)
        if len(idx) == 0:
            return float(np.min(a_lim))
        return float(np.mean(np.asarray(a_lim)[idx]))

    @staticmethod
    def cross_check_original_model(
        spindle_speed: np.ndarray,
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """与原类 data_generator.TlustyAnalyticalModel 交叉校验（需 torch 可用）。

        Returns: (ok, reason_or_None, max_rel_err_or_None)
        """
        try:
            import sys
            from pathlib import Path

            exp_dir = str(Path(__file__).resolve().parent)
            if exp_dir not in sys.path:
                sys.path.insert(0, exp_dir)
            from data_generator import TlustyAnalyticalModel  # noqa: E402
        except Exception as exc:  # torch 缺失等
            return False, f"原类不可用（{exc.__class__.__name__}: {exc}）", None

        ours = ThermalSLDModel()
        orig = TlustyAnalyticalModel()
        a_ours = ours.compute_limiting_depth(spindle_speed, dT=0.0)
        a_orig = orig.compute_limiting_depth(spindle_speed)
        rel_err = np.abs(a_ours - a_orig) / np.maximum(np.abs(a_orig), 1e-12)
        return True, None, float(rel_err.max())


def default_spindle_grid(n_pts: int = 400, lo: float = 300.0, hi: float = 10000.0) -> np.ndarray:
    """默认转速网格。

    覆盖前 3 个叶瓣谷（j 叶瓣谷 ~ 60*f_n/j = 955/477/318 rpm，论文基准
    参数 f_n=15.9 Hz）。注意：叶瓣仅存在于后共振区，谷全部位于
    n < 60*f_n 以下，旧网格下限 500 只能罩住 j=1 一个谷。
    """
    return np.linspace(lo, hi, n_pts)
