"""Kienzle 切削力解析计算模块。

实现经典的 Kienzle 切削力公式，为 PINN 提供物理约束。

公式：
    Fz = kc1.1 * b * h^(1 - mc)
其中：
    - kc1.1: 比切削力 (N/mm^2)，h=1mm 时的基准值
    - mc: 切削力指数 (通常 0.2~0.3)
    - b: 切削宽度 (mm)
    - h: 未变形切屑厚度 (mm)，通常等于每齿进给量 fz

三个方向的切削力经验关系：
    - Fx (进给力) ≈ 0.3 * Fz
    - Fy (径向力) ≈ 0.4 * Fz
    - Fz (主切削力) = Kienzle 公式计算值
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass


logger = logging.getLogger(__name__)


# 常用材料 Kienzle 系数 (kc1.1, mc)
DEFAULT_MATERIAL_COEFFICIENTS: dict[str, dict[str, float]] = {
    "45steel": {"kc1_1": 2000.0, "mc": 0.25},
    "aluminum_6061": {"kc1_1": 800.0, "mc": 0.20},
    "stainless_304": {"kc1_1": 2500.0, "mc": 0.28},
    "cast_iron_ht200": {"kc1_1": 1100.0, "mc": 0.22},
    "titanium_tc4": {"kc1_1": 2200.0, "mc": 0.26},
    "copper": {"kc1_1": 700.0, "mc": 0.18},
}

# 力方向比例系数
FORCE_DIRECTION_RATIOS: dict[str, float] = {
    "Fx_ratio": 0.3,  # 进给力 / 主切削力
    "Fy_ratio": 0.4,  # 径向力 / 主切削力
}

# 材料名规范化：中文名 / materials.json ID / 常见别名 标准系数表 ID
# （真实输入常为中文材料名或数据库 ID，如 "45#钢"、"steel_45"、"TC4"）
MATERIAL_ALIASES: dict[str, str] = {
    # 碳钢
    "45#钢": "45steel",
    "45钢": "45steel",
    "45": "45steel",
    "steel_45": "45steel",
    "steel_q235": "45steel",
    "steel_40cr": "45steel",
    # 铝合金
    "6061": "aluminum_6061",
    "6061铝": "aluminum_6061",
    "6061-t6": "aluminum_6061",
    "al_6061": "aluminum_6061",
    "al_7075": "aluminum_6061",
    # 不锈钢
    "304不锈钢": "stainless_304",
    "不锈钢": "stainless_304",
    "ss_304": "stainless_304",
    # 灰铸铁
    "灰铸铁": "cast_iron_ht200",
    "ht200": "cast_iron_ht200",
    "cast_iron_ht250": "cast_iron_ht200",
    # 钛合金
    "钛合金": "titanium_tc4",
    "tc4": "titanium_tc4",
    "ti_tc4": "titanium_tc4",
    # 铜及铜合金
    "铜": "copper",
    "紫铜": "copper",
    "brass_c36000": "copper",
    "copper_c11000": "copper",
}


def _normalize_material(material: str) -> str:
    """规范化材料名：去空白、转小写后查别名表，未命中返回原串。"""
    candidate = material.strip().lower()
    if candidate in DEFAULT_MATERIAL_COEFFICIENTS:
        return candidate
    alias = MATERIAL_ALIASES.get(material.strip()) or MATERIAL_ALIASES.get(candidate)
    return alias or material.strip()


@dataclass
class KienzleParams:
    """Kienzle 公式计算参数。"""

    material: str = "45steel"
    width: float = 10.0  # 切削宽度 b (mm)
    chip_thickness: float = 0.1  # 未变形切屑厚度 h (mm)
    kc1_1: float | None = None  # 比切削力，None 时从配置读取
    mc: float | None = None  # 切削力指数，None 时从配置读取

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise ValueError(f"切削宽度必须为正数，当前值: {self.width}")
        if self.chip_thickness <= 0:
            raise ValueError(f"切屑厚度必须为正数，当前值: {self.chip_thickness}")
        coeffs = get_kienzle_coefficients(self.material)
        if self.kc1_1 is None:
            self.kc1_1 = coeffs["kc1_1"]
        if self.mc is None:
            self.mc = coeffs["mc"]


def get_kienzle_coefficients(material: str) -> dict[str, float]:
    """获取材料的 Kienzle 系数。

    优先从 process_rules.json 读取，若不存在则使用硬编码默认值。
    材料名支持中文名 / 材料库 ID / 别名（见 MATERIAL_ALIASES）。
    """
    material = _normalize_material(material)
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "process_rules.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            rules = json.load(f)
        for rule in rules:
            if rule.get("category") == "kienzle_coefficients":
                mat_coeffs = rule.get("details", {}).get("materials", {})
                if material in mat_coeffs:
                    return mat_coeffs[material]
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.debug("加载材料系数失败，使用默认值: %s", e)

    if material in DEFAULT_MATERIAL_COEFFICIENTS:
        return DEFAULT_MATERIAL_COEFFICIENTS[material]

    raise ValueError(f"未找到材料 '{material}' 的 Kienzle 系数。可用材料: {list(DEFAULT_MATERIAL_COEFFICIENTS.keys())}")


def compute_cutting_force_fz(
    kc1_1: float,
    mc: float,
    width: float,
    chip_thickness: float,
) -> float:
    """计算主切削力 Fz (N)。

    Fz = kc1.1 * b * h^(1 - mc)

    Args:
        kc1_1: 比切削力 (N/mm^2)
        mc: 切削力指数
        width: 切削宽度 (mm)
        chip_thickness: 未变形切屑厚度 (mm)

    Returns:
        主切削力 Fz (N)
    """
    # [N-H4] 零底负幂防御：当 chip_thickness == 0 且 (1-mc) < 0 时，0 ** 负数 = inf
    # 典型 mc=0.2~0.3，1-mc=0.7~0.8 为正，但仍需防御 mc > 1 的异常材料和 0 输入
    if chip_thickness <= 0:
        logger.debug(
            "chip_thickness=%.6f <= 0，切削力返回 0（避免 0 ** 负数 = inf）",
            chip_thickness,
        )
        return 0.0
    return kc1_1 * width * (chip_thickness ** (1.0 - mc))


def compute_cutting_forces(
    material: str = "45steel",
    width: float = 10.0,
    chip_thickness: float = 0.1,
    kc1_1: float | None = None,
    mc: float | None = None,
) -> dict[str, float]:
    """计算三个方向的切削力。

    Args:
        material: 材料名称
        width: 切削宽度 b (mm)
        chip_thickness: 未变形切屑厚度 h (mm)
        kc1_1: 可选，覆盖默认比切削力
        mc: 可选，覆盖默认切削力指数

    Returns:
        包含 Fx, Fy, Fz 的字典 (单位: N)
    """
    coeffs = get_kienzle_coefficients(material)
    _kc1_1 = kc1_1 if kc1_1 is not None else coeffs["kc1_1"]
    _mc = mc if mc is not None else coeffs["mc"]

    fz = compute_cutting_force_fz(_kc1_1, _mc, width, chip_thickness)
    fx = FORCE_DIRECTION_RATIOS["Fx_ratio"] * fz
    fy = FORCE_DIRECTION_RATIOS["Fy_ratio"] * fz

    return {"Fx": fx, "Fy": fy, "Fz": fz}


def compute_specific_cutting_force(
    kc1_1: float,
    mc: float,
    chip_thickness: float,
) -> float:
    """计算特定切屑厚度下的比切削力 kc。

    kc = kc1.1 * h^(-mc)

    Args:
        kc1_1: 比切削力基准值 (N/mm^2)
        mc: 切削力指数
        chip_thickness: 未变形切屑厚度 (mm)

    Returns:
        比切削力 kc (N/mm^2)
    """
    # [N-H4] 零底负幂防御：-mc 通常为负数，chip_thickness=0 时 0 ** 负数 = inf
    if chip_thickness <= 0:
        logger.debug(
            "chip_thickness=%.6f <= 0，比切削力返回 0（避免 0 ** 负数 = inf）",
            chip_thickness,
        )
        return 0.0
    return kc1_1 * (chip_thickness ** (-mc))
