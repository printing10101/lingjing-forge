"""Tlusty 颤振稳定性解析计算模块。

实现经典的 Tlusty 稳定性叶图理论，用于预测颤振稳定性极限切削深度。

理论基础：
    Tlusty 公式基于再生颤振理论，考虑了机床动态特性与切削过程的耦合效应。

    稳定性极限切削深度：
        a_lim = -1 / (2 * K_s * Re[G(ω)])

    其中：
        - K_s: 切削力系数 (N/mm²)，与材料和刀具几何相关
        - G(ω): 机床频率响应函数 (mm/N)
        - Re[G(ω)]: 频率响应函数的实部

    主轴转速与颤振频率关系：
        n = 60 * ω / (2π * (j + 1))

    其中：
        - n: 主轴转速 (rpm)
        - ω: 颤振角频率 (rad/s)
        - j: 叶图扇叶序号 (0, 1, 2, ...)
"""

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from typing import Any
import numpy as np

logger = logging.getLogger(__name__)

# 默认机床动态参数（刚度、阻尼比、固有频率）
# 物理一致性：f_n = sqrt(k/m) / (2π)
DEFAULT_MACHINE_PARAMS: dict[str, dict[str, float]] = {
    "vmc_850": {
        "stiffness_x": 1.5e7,  # X向刚度 (N/m)
        "stiffness_y": 1.5e7,  # Y向刚度 (N/m)
        "stiffness_z": 2.0e8,  # Z向刚度 (N/m) - 修正：200 N/μm
        "damping_ratio": 0.05,  # 阻尼比
        "natural_freq": 100.0,  # 固有频率 (Hz) - 修正：与 k,m 物理一致
        "modal_mass": 50.0,  # 模态质量 (kg)
    },
    "cnc_lathe_ck6140": {
        "stiffness_x": 1.2e7,
        "stiffness_y": 1.2e7,
        "stiffness_z": 1.8e8,
        "damping_ratio": 0.04,
        "natural_freq": 95.0,
        "modal_mass": 60.0,
    },
    "small_vmc_640": {
        "stiffness_x": 1.0e7,
        "stiffness_y": 1.0e7,
        "stiffness_z": 1.5e8,
        "damping_ratio": 0.06,
        "natural_freq": 110.0,
        "modal_mass": 40.0,
    },
}

# 默认刀具参数
DEFAULT_TOOL_PARAMS: dict[str, dict[str, Any]] = {
    "endmill_d10": {
        "diameter": 10.0,  # 刀具直径 (mm)
        "num_flutes": 4,  # 齿数
        "helix_angle": 30.0,  # 螺旋角 (度)
        "cutting_force_coeff": 2000.0,  # 切削力系数 K_s (N/mm²)
    },
    "endmill_d16": {
        "diameter": 16.0,
        "num_flutes": 4,
        "helix_angle": 30.0,
        "cutting_force_coeff": 2200.0,
    },
    "endmill_d20": {
        "diameter": 20.0,
        "num_flutes": 5,
        "helix_angle": 35.0,
        "cutting_force_coeff": 2400.0,
    },
}


@dataclass
class MachineParams:
    """机床动态参数。"""

    machine_id: str = "vmc_850"
    stiffness_x: float = 1.5e7  # X向刚度 (N/m)
    stiffness_y: float = 1.5e7  # Y向刚度 (N/m)
    stiffness_z: float = 2.0e8  # Z向刚度 (N/m) - 修正：200 N/μm
    damping_ratio: float = 0.05  # 阻尼比
    natural_freq: float = 100.0  # 固有频率 (Hz) - 修正：与 k,m 物理一致
    modal_mass: float = 50.0  # 模态质量 (kg)

    def __post_init__(self) -> None:
        if self.stiffness_x <= 0 or self.stiffness_y <= 0 or self.stiffness_z <= 0:
            raise ValueError("刚度必须为正数")
        if self.damping_ratio <= 0 or self.damping_ratio >= 1:
            raise ValueError(f"阻尼比必须在 (0, 1) 范围内，当前值: {self.damping_ratio}")
        if self.natural_freq <= 0:
            raise ValueError(f"固有频率必须为正数，当前值: {self.natural_freq}")
        if self.modal_mass <= 0:
            raise ValueError(f"模态质量必须为正数，当前值: {self.modal_mass}")


@dataclass
class ToolParams:
    """刀具参数。"""

    tool_id: str = "endmill_d10"
    diameter: float = 10.0  # 刀具直径 (mm)
    num_flutes: int = 4  # 齿数
    helix_angle: float = 30.0  # 螺旋角 (度)
    cutting_force_coeff: float = 2000.0  # 切削力系数 K_s (N/mm²)

    def __post_init__(self) -> None:
        if self.diameter <= 0:
            raise ValueError(f"刀具直径必须为正数，当前值: {self.diameter}")
        if self.num_flutes <= 0:
            raise ValueError(f"齿数必须为正整数，当前值: {self.num_flutes}")
        if self.helix_angle < 0 or self.helix_angle > 90:
            raise ValueError(f"螺旋角必须在 [0, 90] 范围内，当前值: {self.helix_angle}")
        if self.cutting_force_coeff <= 0:
            raise ValueError(f"切削力系数必须为正数，当前值: {self.cutting_force_coeff}")


@dataclass
class ChatterParams:
    """颤振稳定性计算参数。"""

    spindle_rpm: float = 8000.0  # 主轴转速 (rpm)
    machine: MachineParams = field(default_factory=MachineParams)
    tool: ToolParams = field(default_factory=ToolParams)
    axial_depth: float | None = None  # 轴向切深 (mm)，None 时计算极限切深

    def __post_init__(self) -> None:
        if self.spindle_rpm <= 0:
            raise ValueError(f"主轴转速必须为正数，当前值: {self.spindle_rpm}")
        if self.axial_depth is not None and self.axial_depth <= 0:
            raise ValueError(f"轴向切深必须为正数，当前值: {self.axial_depth}")


def get_machine_params(machine_id: str) -> MachineParams:
    """获取机床动态参数。

    优先从 machines.json 读取，若不存在则使用硬编码默认值。

    Args:
        machine_id: 机床标识 (如 'vmc_850', 'cnc_lathe_ck6140')

    Returns:
        MachineParams 对象
    """
    # 尝试从 machines.json 读取
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "database", "data", "machines.json")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            machines = json.load(f)

        for machine in machines:
            if machine.get("id") == machine_id:
                # 从配置中提取参数，缺失时使用默认值
                default = DEFAULT_MACHINE_PARAMS.get(machine_id, DEFAULT_MACHINE_PARAMS["vmc_850"])
                return MachineParams(
                    machine_id=machine_id,
                    stiffness_x=machine.get("stiffness_x", default["stiffness_x"]),
                    stiffness_y=machine.get("stiffness_y", default["stiffness_y"]),
                    stiffness_z=machine.get("stiffness_z", default["stiffness_z"]),
                    damping_ratio=machine.get("damping_ratio", default["damping_ratio"]),
                    natural_freq=machine.get("natural_freq", default["natural_freq"]),
                    modal_mass=machine.get("modal_mass", default["modal_mass"]),
                )
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.warning("读取 machines.json 失败: %s，使用默认参数", e)

    # 使用硬编码默认值
    if machine_id in DEFAULT_MACHINE_PARAMS:
        params = DEFAULT_MACHINE_PARAMS[machine_id]
        return MachineParams(machine_id=machine_id, **params)

    # 未知机床，使用 vmc_850 默认值
    logger.warning("未找到机床 '%s' 的参数，使用 vmc_850 默认值", machine_id)
    params = DEFAULT_MACHINE_PARAMS["vmc_850"]
    return MachineParams(machine_id=machine_id, **params)


def get_default_machine_params() -> dict[str, dict[str, float]]:
    """获取所有默认机床参数。"""
    return DEFAULT_MACHINE_PARAMS.copy()


def _compute_frf(
    machine: MachineParams,
    freq_hz: float,
) -> complex:
    """计算机床频率响应函数 (FRF)。

    单自由度系统频率响应：
        G(ω) = 1 / (k - m*ω² + i*c*ω)

    其中：
        - k: 刚度 (N/m)，使用 stiffness_z（主切削方向）
        - m: 模态质量 (kg)
        - c: 阻尼系数 (N·s/m) = 2 * ζ * √(k*m)
        - ω: 角频率 (rad/s) = 2π*f

    Args:
        machine: 机床参数
        freq_hz: 频率 (Hz)

    Returns:
        频率响应函数值 (复数，单位 m/N)
    """
    omega = 2 * np.pi * freq_hz
    k = machine.stiffness_z  # 使用 Z 向刚度（主切削方向）
    m = machine.modal_mass
    c = 2 * machine.damping_ratio * np.sqrt(k * m)

    # G(ω) = 1 / (k - m*ω² + i*c*ω)
    denominator = k - m * omega**2 + 1j * c * omega
    return 1.0 / denominator


def compute_stability_limit(
    params: ChatterParams,
) -> float:
    """计算给定主轴转速下的稳定性极限切削深度。

    基于 Tlusty 公式（单自由度系统修正形式）：
        a_lim = |1 + 2ζir·G(ω)|² / (2·K_s·Re[G(ω)])

    其中：
        - ζ: 阻尼比
        - ir: 虚数单位
        - G(ω): 频率响应函数
        - K_s: 切削力系数

    Args:
        params: 颤振计算参数

    Returns:
        极限切削深度 (mm)
    """
    f_n = params.machine.natural_freq
    zeta = params.machine.damping_ratio

    # 在固有频率附近搜索极限切深的最小值
    # 频率范围：0.5*f_n 到 2*f_n
    freqs = np.linspace(f_n * 0.5, f_n * 2.0, 500)

    min_a_lim = float("inf")

    for freq in freqs:
        # 计算 FRF
        frf = _compute_frf(params.machine, freq)
        re_frf = frf.real

        # 跳过 Re[G] <= 0 或接近零的点（避免 a_lim 数值奇异）
        # Re[G] 0 时 a_lim = numerator / (2·K_s·Re[G]) 会发散到无穷大
        if re_frf <= 1e-9:
            continue

        # Tlusty 公式（单自由度修正形式）
        # a_lim = |1 + 2ζi·G(ω)|² / (2·K_s·Re[G(ω)])
        numerator = abs(1.0 + 2.0 * zeta * 1j * frf) ** 2

        # 单位转换：K_s (N/mm²), FRF (m/N mm/N)
        k_s = params.tool.cutting_force_coeff  # N/mm²
        re_frf_mm = re_frf * 1000.0  # m/N → mm/N

        a_lim_mm = numerator / (2.0 * k_s * re_frf_mm)

        # 记录最小值
        if a_lim_mm > 0 and a_lim_mm < min_a_lim:
            min_a_lim = a_lim_mm

    # 如果未找到有效值，返回默认值
    if min_a_lim == float("inf") or min_a_lim <= 0:
        logger.warning("未找到有效的极限切深，使用默认值")
        return 1.0

    # 限制在合理范围内
    if min_a_lim > 100.0:
        logger.warning("极限切深过大: %s mm，限制为 100 mm", min_a_lim)
        return 100.0

    return float(min_a_lim)


def compute_stability_lobe(
    machine: MachineParams,
    tool: ToolParams,
    speed_range: tuple[float, float] = (1000, 10000),
    num_points: int = 100,
    num_lobes: int = 5,
) -> dict[str, Any]:
    """计算稳定性叶图。

    生成主轴转速与极限切削深度的关系曲线（稳定性叶图）。

    基于 Tlusty 稳定性理论，对于每个叶图扇叶 j，主轴转速与颤振频率关系：
        n = 60 * f / (j + 1)

    Args:
        machine: 机床参数
        tool: 刀具参数
        speed_range: 主轴转速范围 (rpm)，如 (1000, 10000)
        num_points: 每个叶图的点数
        num_lobes: 叶图扇叶数量

    Returns:
        包含以下键的字典：
        - speeds: 主轴转速列表 (rpm)
        - limit_depths: 极限切削深度列表 (mm)
        - lobes: 各扇叶的 (speeds, limit_depths) 元组列表
    """
    all_speeds = []
    all_depths = []
    lobes = []

    f_n = machine.natural_freq
    k_s = tool.cutting_force_coeff  # N/mm²
    zeta = machine.damping_ratio

    # 频率搜索范围：0.5*f_n 到 2*f_n
    freq_min = f_n * 0.5
    freq_max = f_n * 2.0

    for j in range(num_lobes):
        lobe_speeds = []
        lobe_depths = []

        # 对于叶图 j，计算对应的频率范围
        # n = 60 * f / (j + 1) => f = n * (j + 1) / 60
        f_for_speed_min = speed_range[0] * (j + 1) / 60.0
        f_for_speed_max = speed_range[1] * (j + 1) / 60.0

        # 取交集：频率既要满足转速范围，也要在有效范围内
        f_start = max(freq_min, f_for_speed_min)
        f_end = min(freq_max, f_for_speed_max)

        if f_start >= f_end:
            continue  # 该叶图在指定转速范围内无有效点

        freqs = np.linspace(f_start, f_end, num_points)

        for freq in freqs:
            speed_rpm = 60.0 * freq / (j + 1)

            # 计算 FRF
            frf = _compute_frf(machine, freq)
            re_frf = frf.real

            # 只取 Re[G] > 0 或接近零的点（避免 a_lim 发散）
            if re_frf <= 1e-9:
                continue

            # Tlusty 公式（单自由度修正形式）
            # a_lim = |1 + 2ζi·G(ω)|² / (2·K_s·Re[G(ω)])
            numerator = abs(1.0 + 2.0 * zeta * 1j * frf) ** 2
            re_frf_mm = re_frf * 1000.0  # m/N → mm/N
            a_lim_mm = numerator / (2.0 * k_s * re_frf_mm)

            # 统一截断阈值：与 compute_stability_limit 保持一致（100mm）
            if a_lim_mm <= 0 or a_lim_mm > 100.0:
                continue

            lobe_speeds.append(float(speed_rpm))
            lobe_depths.append(float(a_lim_mm))

        if lobe_speeds:
            lobes.append((lobe_speeds, lobe_depths))
            all_speeds.extend(lobe_speeds)
            all_depths.extend(lobe_depths)

    return {
        "speeds": all_speeds,
        "limit_depths": all_depths,
        "lobes": lobes,
    }
