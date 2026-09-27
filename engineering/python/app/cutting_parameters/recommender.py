"""切削参数推荐引擎：基于材料 + 几何特征 + 精度档位推荐切削参数。

设计原则
========
「材料优先 + 特征驱动」策略：
- 材料的 specific_cutting_force (K_s) 直接决定阶段 5 颤振预测的稳定性极限
- cutting_speed_range / feed_range / depth_of_cut_range 决定推荐参数的上下界
- 几何特征类型 (plane/cylinder/hole/boss) 决定径向切深 (radial_depth) 的默认值
- 精度档位 (coarse/standard/high) 决定 operation（roughing/finishing）

推荐算法（保守取值，便于工程师审核）：
- cutting_speed: 取材料范围 [min, max] 的 1/3 分位（偏低，保证安全）
- feed_per_tooth: 取材料范围 [min, max] 的中位数
- axial_depth: 取材料范围 [min, max] 的中位数
- radial_depth: 按特征类型给默认比例（plane: 0.5*D, cylinder: 0.3*D, hole: D, boss: 0.5*D）
- spindle_rpm = V_c * 1000 / (π * D)
- feed_rate = spindle_rpm * num_flutes * feed_per_tooth

阶段 5 对接（ChatterParams 契约）：
- 输出的 RecommendedCuttingParams 通过 to_chatter_params_dict() 转换
- cutting_force_coeff (K_s) 直接取自 MaterialParams.specific_cutting_force
- spindle_rpm / axial_depth 直接传入 ChatterParams

工业硬约束（项目记忆）：
- 推荐参数为「算法建议」，非「最优解」，工程师必须审核
- HRC52 材料的 K_s 标注 pending_calibration，需自采数据校准
- 所有参数必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.cutting_parameters.cutting_store import (
    CuttingParametersError,
    MaterialNotFoundError,
    RecommendedCuttingParams,
)
from app.cutting_parameters.material_resolver import (
    MaterialParams,
    MaterialResolver,
    MaterialResolverError,
    get_material_resolver,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CuttingParamRecommender",
    "RecommendationError",
    "to_chatter_params_dict",
    "FEATURE_TYPE_RADIAL_DEPTH_RATIO",
]


# 异常类


class RecommendationError(CuttingParametersError):
    """推荐引擎异常。"""


class FeatureNotSupportedError(RecommendationError):
    """特征类型不支持。"""


# 常量：特征类型 径向切深占刀具直径比例


# 径向切深 (ae) 占刀具直径 D 的比例（保守值，便于工程师审核）
FEATURE_TYPE_RADIAL_DEPTH_RATIO: dict[str, float] = {
    "plane": 0.5,  # 平面铣削：ae = 0.5*D（行距）
    "cylinder": 0.3,  # 圆柱面铣削：ae = 0.3*D（侧铣，避免过切）
    "hole": 1.0,  # 孔加工：ae = D（满刀，孔特征专用）
    "boss": 0.5,  # 凸台铣削：ae = 0.5*D（外轮廓铣削）
}

SUPPORTED_FEATURE_TYPES = frozenset(FEATURE_TYPE_RADIAL_DEPTH_RATIO.keys())


# 推荐引擎


class CuttingParamRecommender:
    """切削参数推荐引擎。

    使用方式：
        recommender = CuttingParamRecommender()
        params = recommender.recommend(
            feature_id="feat_plane_001",
            feature_type="plane",
            material_id="al_6061",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
    """

    def __init__(self, resolver: MaterialResolver | None = None) -> None:
        """初始化推荐引擎。

        Args:
            resolver: 材料解析器（默认使用全局单例，便于测试注入）
        """
        self._resolver = resolver if resolver is not None else get_material_resolver()

    def recommend(
        self,
        feature_id: str,
        feature_type: str,
        material_id: str,
        precision_tier: str,
        tool_diameter_mm: float,
        num_flutes: int,
        machine_type: str = "default",
    ) -> RecommendedCuttingParams:
        """为单个特征推荐切削参数。

        Args:
            feature_id: 特征 ID（追溯用）
            feature_type: 特征类型 (plane / cylinder / hole / boss)
            material_id: 材料 ID (如 al_6061 / ti_tc4 / steel_hrc52)
            precision_tier: 精度档位 (coarse / standard / high)
            tool_diameter_mm: 刀具直径 (mm)
            num_flutes: 齿数
            machine_type: 机床类型标识（仅供追溯，本方法不查询机床参数）

        Returns:
            RecommendedCuttingParams 推荐参数（review_status=PENDING）

        Raises:
            FeatureNotSupportedError: 特征类型不支持
            MaterialNotFoundError: 材料 ID 未找到
            RecommendationError: 推荐失败（参数越界等）
        """
        # 1. 校验特征类型
        if feature_type not in SUPPORTED_FEATURE_TYPES:
            raise FeatureNotSupportedError(
                f"特征类型 {feature_type} 不支持，当前支持：{sorted(SUPPORTED_FEATURE_TYPES)}"
            )

        # 2. 校验刀具参数
        if tool_diameter_mm <= 0:
            raise RecommendationError(f"刀具直径必须为正数，当前值: {tool_diameter_mm}")
        if num_flutes <= 0:
            raise RecommendationError(f"齿数必须为正整数，当前值: {num_flutes}")

        # 3. 查询材料参数
        try:
            material = self._resolver.get_material(material_id)
        except MaterialResolverError as e:
            raise MaterialNotFoundError(str(e)) from e

        # 4. 确定 operation（粗加工 / 精加工）
        operation = self._determine_operation(precision_tier, feature_type)

        # 5. 从材料范围中取值
        cutting_speed = self._pick_cutting_speed(material, operation)
        feed_per_tooth = self._pick_feed_per_tooth(material, operation)
        axial_depth = self._pick_axial_depth(material, operation, feature_type)
        radial_depth = self._pick_radial_depth(feature_type, tool_diameter_mm)

        # 6. 计算衍生参数
        spindle_rpm = self._compute_spindle_rpm(cutting_speed, tool_diameter_mm)
        feed_rate = self._compute_feed_rate(spindle_rpm, num_flutes, feed_per_tooth)
        tool_life_min = self._estimate_tool_life(
            cutting_speed,
            material.taylor_exponent_n,
            material.taylor_constant_c,
        )
        cutting_time_s = self._estimate_cutting_time(feature_type, axial_depth, radial_depth, feed_rate)

        # 7. 构造 warnings
        warnings = self._build_warnings(
            material=material,
            precision_tier=precision_tier,
            feature_type=feature_type,
            cutting_speed=cutting_speed,
            material_speed_range=material.cutting_speed_range[operation],
        )

        return RecommendedCuttingParams(
            feature_id=feature_id,
            feature_type=feature_type,
            operation=operation,
            spindle_speed_rpm=round(spindle_rpm, 1),
            feed_rate_mm_per_min=round(feed_rate, 2),
            feed_per_tooth_mm=round(feed_per_tooth, 4),
            cutting_speed_m_per_min=round(cutting_speed, 2),
            axial_depth_mm=round(axial_depth, 3),
            radial_depth_mm=round(radial_depth, 3),
            estimated_cutting_time_s=round(cutting_time_s, 1),
            tool_life_estimate_min=round(tool_life_min, 1),
            warnings=warnings,
            review_status="pending",
            edited_params={},
            reviewed_by="",
            reviewed_at=0.0,
            engineer_notes="",
            material_id=material.id,
            tool_diameter_mm=tool_diameter_mm,
            num_flutes=num_flutes,
        )

    # 内部方法

    @staticmethod
    def _determine_operation(precision_tier: str, feature_type: str) -> str:
        """根据精度档位 + 特征类型确定粗/精加工。

        - high 档位 → finishing（追求表面质量）
        - coarse 档位 → roughing（追求材料去除率）
        - standard 档位：
            - plane / cylinder → roughing（大面先粗加工）
            - hole / boss → finishing（孔/凸台精度敏感）
        """
        tier = precision_tier.lower().strip()
        if tier == "high":
            return "finishing"
        if tier == "coarse":
            return "roughing"
        # standard
        if feature_type in ("hole", "boss"):
            return "finishing"
        return "roughing"

    @staticmethod
    def _pick_cutting_speed(material: MaterialParams, operation: str) -> float:
        """切削速度：取材料范围 [min, max] 的 1/3 分位（保守偏低，安全）。"""
        speed_range = material.cutting_speed_range.get(operation, [0.0, 0.0])
        if len(speed_range) != 2 or speed_range[1] <= speed_range[0]:
            return 50.0  # 兜底
        return speed_range[0] + (speed_range[1] - speed_range[0]) / 3.0

    @staticmethod
    def _pick_feed_per_tooth(material: MaterialParams, operation: str) -> float:
        """每齿进给量：取材料范围 [min, max] 的中位数。"""
        feed_range = material.feed_range.get(operation, [0.0, 0.0])
        if len(feed_range) != 2:
            return 0.05
        return (feed_range[0] + feed_range[1]) / 2.0

    @staticmethod
    def _pick_axial_depth(material: MaterialParams, operation: str, feature_type: str) -> float:
        """轴向切深：取材料范围 [min, max] 的中位数。

        hole 特征采用 finishing 的 depth_range（孔深方向，避免大切深断刀）。
        """
        op = operation
        if feature_type == "hole" and op == "roughing":
            # 孔粗加工仍用 roughing 范围，但取下限附近值（更保守）
            depth_range = material.depth_of_cut_range.get("roughing", [0.5, 2.0])
            return depth_range[0]
        depth_range = material.depth_of_cut_range.get(op, [0.5, 2.0])
        if len(depth_range) != 2:
            return 1.0
        return (depth_range[0] + depth_range[1]) / 2.0

    @staticmethod
    def _pick_radial_depth(feature_type: str, tool_diameter_mm: float) -> float:
        """径向切深：按特征类型占刀具直径比例。"""
        ratio = FEATURE_TYPE_RADIAL_DEPTH_RATIO.get(feature_type, 0.5)
        return ratio * tool_diameter_mm

    @staticmethod
    def _compute_spindle_rpm(cutting_speed_m_per_min: float, tool_diameter_mm: float) -> float:
        """主轴转速：n = V_c * 1000 / (π * D)。"""
        if tool_diameter_mm <= 0:
            return 0.0
        return cutting_speed_m_per_min * 1000.0 / (math.pi * tool_diameter_mm)

    @staticmethod
    def _compute_feed_rate(spindle_rpm: float, num_flutes: int, feed_per_tooth_mm: float) -> float:
        """进给速度：F = n * z * f_z。"""
        return spindle_rpm * num_flutes * feed_per_tooth_mm

    @staticmethod
    def _estimate_tool_life(
        cutting_speed_m_per_min: float,
        taylor_n: float,
        taylor_c: float,
    ) -> float:
        """Taylor 刀具寿命估算：T = (C / V_c)^(1/n)。

        Args:
            cutting_speed_m_per_min: 切削速度 V_c
            taylor_n: Taylor 指数（材料相关）
            taylor_c: Taylor 常数 C

        Returns:
            估算刀具寿命（分钟），失败返回 0.0
        """
        if taylor_n <= 0 or taylor_c <= 0 or cutting_speed_m_per_min <= 0:
            return 0.0
        try:
            return (taylor_c / cutting_speed_m_per_min) ** (1.0 / taylor_n)
        except (ValueError, OverflowError):
            return 0.0

    @staticmethod
    def _estimate_cutting_time(
        feature_type: str,
        axial_depth_mm: float,
        radial_depth_mm: float,
        feed_rate_mm_per_min: float,
    ) -> float:
        """估算切削时间（秒）。

        简化模型：假设切削路径长度 100mm（典型小特征），时间 = 路径 / 进给。
        实际切削时间需 CAM 软件精确计算，本值为参考。
        """
        if feed_rate_mm_per_min <= 0:
            return 0.0
        path_length_mm = 100.0  # 兜底参考路径长度
        # 孔特征时间按轴向进给计算
        if feature_type == "hole":
            path_length_mm = axial_depth_mm if axial_depth_mm > 0 else 10.0
        # 平面/圆柱/凸台按径向进给 + 多刀
        elif radial_depth_mm > 0:
            path_length_mm = 100.0 + radial_depth_mm * 5.0
        return path_length_mm / feed_rate_mm_per_min * 60.0

    @staticmethod
    def _build_warnings(
        material: MaterialParams,
        precision_tier: str,
        feature_type: str,
        cutting_speed: float,
        material_speed_range: list[float],
    ) -> list[str]:
        """构造警告列表。"""
        warnings: list[str] = []

        # 材料校准状态
        if material.calibration_status == "pending_calibration":
            warnings.append(
                f"材料 {material.id} 数据待自采校准（pending_calibration），"
                f"K_s={material.specific_cutting_force} N/mm² 为工程估算值"
            )

        # 切削速度越界检查
        if (
            len(material_speed_range) == 2
            and material_speed_range[1] > 0
            and (cutting_speed < material_speed_range[0] * 0.9 or cutting_speed > material_speed_range[1] * 1.1)
        ):
            warnings.append(
                f"切削速度 {cutting_speed:.1f} m/min 接近材料范围边界 {material_speed_range}，工程师需重点审核"
            )

        # 精度档位提示
        if precision_tier == "coarse":
            warnings.append("coarse 档位仅用于粗加工参考，不可用于配合面")
        elif precision_tier == "high":
            warnings.append("high 档位采用 finishing 参数，需精加工工序保证表面质量")

        # HRC52 特殊警告
        if material.hardness_hrc is not None and material.hardness_hrc >= 50:
            warnings.append(f"高硬度材料（HRC{material.hardness_hrc:.0f}）需硬质合金或陶瓷刀具，普通 HSS 刀具不可用")

        # 孔特征警告
        if feature_type == "hole":
            warnings.append("孔加工建议二次复核：钻孔后铰孔或镗孔保证精度")

        return warnings


# 阶段 5 对接：转换为 ChatterParams dict


def to_chatter_params_dict(
    params: RecommendedCuttingParams,
    resolver: MaterialResolver | None = None,
    machine_id: str = "vmc_850",
) -> dict[str, Any]:
    """将 RecommendedCuttingParams 转换为阶段 5 ChatterParams 兼容的 dict。

    阶段 5 simulation.chatter.ChatterParams 结构：
        - spindle_rpm: float
        - machine: MachineParams (machine_id, stiffness_*, damping_ratio, natural_freq, modal_mass)
        - tool: ToolParams (tool_id, diameter, num_flutes, helix_angle, cutting_force_coeff)
        - axial_depth: Optional[float]

    本函数输出 dict，阶段 5 可直接 ChatterParams(**dict) 构造。
    cutting_force_coeff (K_s) 取自 MaterialParams.specific_cutting_force。

    Args:
        params: 推荐的切削参数
        resolver: 材料解析器（默认全局单例）
        machine_id: 机床 ID（阶段 5 内部查询机床动态参数）

    Returns:
        dict，可直接传给 ChatterParams 构造函数

    Raises:
        MaterialNotFoundError: 材料未找到
        RecommendationError: 转换失败
    """
    if resolver is None:
        resolver = get_material_resolver()

    try:
        material = resolver.get_material(params.material_id)
    except MaterialResolverError as e:
        raise MaterialNotFoundError(str(e)) from e

    # 取生效参数（工程师编辑过的值优先）
    effective = params.effective_params()

    spindle_rpm = float(effective["spindle_speed_rpm"])
    axial_depth = float(effective["axial_depth_mm"])
    tool_diameter = params.tool_diameter_mm if params.tool_diameter_mm > 0 else 10.0
    num_flutes = params.num_flutes if params.num_flutes > 0 else 4

    # 构造 MachineParams dict（使用阶段 5 默认机床参数）
    machine_dict = {
        "machine_id": machine_id,
        "stiffness_x": 1.5e7,
        "stiffness_y": 1.5e7,
        "stiffness_z": 2.0e8,
        "damping_ratio": 0.05,
        "natural_freq": 100.0,
        "modal_mass": 50.0,
    }

    # 构造 ToolParams dict，cutting_force_coeff = K_s
    tool_dict = {
        "tool_id": f"endmill_d{int(tool_diameter)}",
        "diameter": tool_diameter,
        "num_flutes": num_flutes,
        "helix_angle": 30.0,
        "cutting_force_coeff": float(material.specific_cutting_force),  # K_s
    }

    return {
        "spindle_rpm": spindle_rpm,
        "machine": machine_dict,
        "tool": tool_dict,
        "axial_depth": axial_depth,
    }
