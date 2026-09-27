"""几何特征分类判定规则（纯 Python 白盒逻辑）。

背景
====
本模块是「自主代码重构」路线图 Phase 1 P1-1 的产物：把 mesh 特征识别中
**离散的判定/分类规则**（凸台 vs 凹陷 vs 孔）从框架调用中解耦出来。

设计边界
========
- 这里**不包含**任何 RANSAC 拟合 / 矩阵运算内核（那部分保留对 numpy/sklearn/
  pyransac3d 的调用，属算法内核）。
- 这里只放**判定规则**（分类决策函数 + 输入合法性校验），是纯 Python、
  零框架依赖、可单测的「白盒业务逻辑」。

判定规则（与既有 hole_detector 行为逐字节一致，防回归）
======================================================
孔/凸台（HOLE vs BOSS）分类：
    offset  = 圆心区域相对参考面的沿法向量平均偏移
    threshold = 判定阈值（>0）

    offset < -threshold          → HOLE（凹陷）
    offset > +threshold          → BOSS（凸起）
    |offset| <= threshold        → 默认 HOLE（无法判定方向，工业上保守偏孔）
"""

from __future__ import annotations

from typing import Any

# 特征类型采用字符串常量（与 feature_store.FeatureType.value 保持一致，
# 避免本纯逻辑模块引入 dataclass/枚举框架依赖）。
FEATURE_PLANE = "plane"
FEATURE_CYLINDER = "cylinder"
FEATURE_HOLE = "hole"
FEATURE_BOSS = "boss"
FEATURE_UNKNOWN = "unknown"

# 合法审核动作（与 FeatureReviewStatus 对齐）
ACTION_CONFIRMED = "confirmed"
ACTION_REJECTED = "rejected"
ACTION_EDITED = "edited"


class FeatureClassificationError(ValueError):
    """特征分类判定输入非法。"""


def is_known_feature_type(feature_type: str) -> bool:
    """feature_type 是否在已知特征类型集合内。"""
    return feature_type in {
        FEATURE_PLANE,
        FEATURE_CYLINDER,
        FEATURE_HOLE,
        FEATURE_BOSS,
        FEATURE_UNKNOWN,
    }


def is_valid_review_action(action: str) -> bool:
    """action 是否为合法审核动作（confirmed / rejected / edited）。"""
    return action in {ACTION_CONFIRMED, ACTION_REJECTED, ACTION_EDITED}


def validate_offset(offset: float) -> float:
    """校验 offset 为有限数值，返回规整后的 float。

    Raises:
        FeatureClassificationError: offset 非有限数（NaN/inf）或无法转为数值。
    """
    try:
        value = float(offset)
    except (TypeError, ValueError) as e:
        raise FeatureClassificationError(f"offset 必须是有限数值，实际 {offset!r}") from e
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        raise FeatureClassificationError(f"offset 必须是有限数值，实际 {offset!r}")
    return value


def validate_threshold(threshold: float) -> float:
    """校验判定阈值 > 0 且为有限数值。

    Raises:
        FeatureClassificationError: threshold <= 0 或非有限数。
    """
    try:
        value = float(threshold)
    except (TypeError, ValueError) as e:
        raise FeatureClassificationError(f"threshold 必须是正有限数值，实际 {threshold!r}") from e
    if value != value or value in (float("inf"), float("-inf")):
        raise FeatureClassificationError(f"threshold 必须是正有限数值，实际 {threshold!r}")
    if value <= 0:
        raise FeatureClassificationError(f"threshold 必须 > 0，实际 {value!r}")
    return value


def classify_hole_or_boss(
    offset: float,
    threshold: float,
    default_type: str = FEATURE_HOLE,
) -> str:
    """根据圆心区域相对参考面的沿法向量偏移判定 HOLE 还是 BOSS。

    规则（与既有 hole_detector._classify_hole_or_boss 逐字节一致）：
        offset < -threshold  → HOLE
        offset > +threshold  → BOSS
        |offset| <= threshold → default_type（默认 HOLE）

    Args:
        offset: 圆心区域平均偏移（>0 凸起，<0 凹陷）。
        threshold: 判定阈值（必须 > 0）。
        default_type: 无法判定方向时的退化类型（默认 HOLE）。

    Returns:
        FEATURE_HOLE 或 FEATURE_BOSS。

    Raises:
        FeatureClassificationError: 输入非法，或 default_type 非法。
    """
    offset_v = validate_offset(offset)
    threshold_v = validate_threshold(threshold)
    if default_type not in {FEATURE_HOLE, FEATURE_BOSS}:
        raise FeatureClassificationError(f"default_type 必须为 hole 或 boss，实际 {default_type!r}")

    if offset_v < -threshold_v:
        return FEATURE_HOLE
    if offset_v > threshold_v:
        return FEATURE_BOSS
    return default_type


def classify_hole_or_boss_deep(
    offset: float,
    threshold: float,
    default_type: str = FEATURE_HOLE,
) -> tuple[str, float]:
    """判定类型并返回规整后的 offset（供参数落库使用）。

    与 :func:`classify_hole_or_boss` 判定规则一致，额外返回规整后的 offset，
    便于调用方把 abs(offset) 写入 depth_mm / height_mm。

    Returns:
        (feature_type, offset_value)
    """
    ftype = classify_hole_or_boss(offset, threshold, default_type)
    return ftype, validate_offset(offset)


def validate_feature_params(
    feature_type: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """校验并规整特征参数（S1 输入校验门禁）。

    对已知特征类型执行最小一致性校验：
    - 必要键存在（用 get 容错，不在此处强制所有字段，避免破坏既有宽容行为）。
    - 数值字段为有限数（radius_mm / depth_mm / height_mm / inlier_count）。

    Args:
        feature_type: 特征类型（plane/cylinder/hole/boss/unknown）。
        params: 特征参数字典。

    Returns:
        规整后的 params（拷贝，不就地修改入参）。

    Raises:
        FeatureClassificationError: feature_type 未知，或数值字段非有限数。
    """
    if not is_known_feature_type(feature_type):
        raise FeatureClassificationError(f"未知特征类型: {feature_type!r}")

    cleaned = dict(params)
    for key in ("radius_mm", "depth_mm", "height_mm"):
        value = cleaned.get(key)
        if value is None:
            continue
        try:
            cleaned[key] = float(value)
        except (TypeError, ValueError) as e:
            raise FeatureClassificationError(f"参数 {key} 必须为数值，实际 {value!r}") from e
        if cleaned[key] != cleaned[key] or cleaned[key] in (float("inf"), float("-inf")):
            raise FeatureClassificationError(f"参数 {key} 必须为有限数，实际 {value!r}")

    inliers = cleaned.get("inlier_count")
    if inliers is not None:
        try:
            cleaned["inlier_count"] = int(inliers)
        except (TypeError, ValueError) as e:
            raise FeatureClassificationError(f"参数 inlier_count 必须为整数，实际 {inliers!r}") from e

    return cleaned


__all__ = [
    "FEATURE_PLANE",
    "FEATURE_CYLINDER",
    "FEATURE_HOLE",
    "FEATURE_BOSS",
    "FEATURE_UNKNOWN",
    "ACTION_CONFIRMED",
    "ACTION_REJECTED",
    "ACTION_EDITED",
    "FeatureClassificationError",
    "is_known_feature_type",
    "is_valid_review_action",
    "validate_offset",
    "validate_threshold",
    "classify_hole_or_boss",
    "classify_hole_or_boss_deep",
    "validate_feature_params",
]
