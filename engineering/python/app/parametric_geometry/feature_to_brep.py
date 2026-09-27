"""特征 → B-rep 数据结构转换器（不依赖 OpenCASCADE）。

设计原则
========
灵境制造的参数化几何输出模块采用分层架构：
- feature_to_brep.py（本模块）：纯数据转换层，把阶段 2 的特征参数转换为 BrepShape
- assembly_builder.py：装配规则层，定义多 BrepShape 之间的布尔关系
- step_writer.py：STEP IO 层，把 BrepShape 列表转换为 STEP 文件（pythonOCC / FreeCAD / 模板）

为什么本模块不直接依赖 OpenCASCADE？
1. pythonOCC 在 Windows 上 wheel 安装可能失败，需要 conda 环境
2. FreeCAD Python API 需要 FreeCAD 安装包
3. 分层后 feature_to_brep.py 可独立测试（不依赖外部 CAD 库）
4. step_writer.py 可以根据可用引擎选择不同实现，但 BrepShape 数据结构不变

BrepShape 数据结构设计：
- shape_type: "plane" / "cylinder" / "box"（STEP 引擎可识别的基础类型）
- operation: "add" / "subtract"（布尔运算类型，add=fuse，subtract=cut）
- origin: [x, y, z] 位置（mm，世界坐标系）
- direction: [dx, dy, dz] 方向（单位向量，平面为法向，圆柱为轴线）
- params: 几何参数字典（radius_mm / height_mm / width_mm / depth_mm 等）

阶段 2 特征参数结构（输入）：
- plane:    {normal: [nx,ny,nz], offset: float, area_mm2: float}
- cylinder: {axis: [ax,ay,az], center: [cx,cy,cz], radius_mm: float, height_mm: float}
- hole:     {normal: [nx,ny,nz], center: [cx,cy,cz], radius_mm: float, depth_mm: float}
- boss:     {normal: [nx,ny,nz], center: [cx,cy,cz], radius_mm: float, height_mm: float}
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.parametric_geometry.step_store import ReviewedFeatureRef


# BrepShape 数据结构


@dataclass
class BrepShape:
    """不依赖 OpenCASCADE 的 B-rep 形状描述。

    step_writer.py 根据 shape_type + operation + origin + direction + params
    调用对应的 OpenCASCADE API 构造实际几何体。
    """

    shape_id: str
    shape_type: str  # "plane" / "cylinder" / "box"
    operation: str  # "add" / "subtract"
    origin: list[float]  # [x, y, z] 位置（mm）
    direction: list[float]  # [dx, dy, dz] 单位向量
    params: dict[str, Any]  # 几何参数
    source_feature_id: str  # 源特征 ID（用于追溯）
    conversion_notes: str = ""  # 转换备注（如降级原因）

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape_id": self.shape_id,
            "shape_type": self.shape_type,
            "operation": self.operation,
            "origin": list(self.origin),
            "direction": list(self.direction),
            "params": dict(self.params),
            "source_feature_id": self.source_feature_id,
            "conversion_notes": self.conversion_notes,
        }


# 转换异常


class FeatureToBrepError(Exception):
    """特征 → B-rep 转换异常。"""


# 工具函数


def _normalize_vector(v: list[float]) -> list[float]:
    """归一化向量，零向量返回 [0, 0, 1]。"""
    norm = math.sqrt(sum(x * x for x in v))
    if norm < 1e-9:
        return [0.0, 0.0, 1.0]
    return [x / norm for x in v]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为 float，失败返回默认值。"""
    try:
        f = float(value)
        if not math.isfinite(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_list(value: Any, length: int = 3, default: float = 0.0) -> list[float]:
    """安全转换为指定长度的 list[float]。"""
    if not isinstance(value, (list, tuple)):
        return [default] * length
    result = [_safe_float(x, default) for x in value[:length]]
    while len(result) < length:
        result.append(default)
    return result


# 各特征类型转换函数


def _convert_plane(feature: ReviewedFeatureRef) -> BrepShape:
    """平面特征 → BrepShape。

    阶段 2 plane 参数：
        {normal: [nx,ny,nz], offset: float, area_mm2: float}

    转换为 BrepShape：
        shape_type = "plane"
        operation = "add"
        origin = [0, 0, -offset]（沿 normal 方向偏移）
        direction = normal（归一化）
        params = {width_mm, height_mm}（根据 area_mm2 估算正方形边界）
    """
    params = feature.effective_params()

    normal = _safe_list(params.get("normal"), 3)
    normal = _normalize_vector(normal)
    offset = _safe_float(params.get("offset"), 0.0)
    area_mm2 = _safe_float(params.get("area_mm2"), 100.0)

    # 估算平面边界（假设正方形，边长 = sqrt(area)）
    side_mm = math.sqrt(max(area_mm2, 1.0))

    # 平面 origin = -offset * normal（normal·x = offset x = offset*normal）
    origin = [-offset * n for n in normal]

    return BrepShape(
        shape_id=f"brep_{feature.feature_id}",
        shape_type="plane",
        operation="add",
        origin=origin,
        direction=normal,
        params={
            "width_mm": side_mm,
            "height_mm": side_mm,
            "offset_mm": offset,
            "area_mm2": area_mm2,
        },
        source_feature_id=feature.feature_id,
        conversion_notes=(
            f"平面边界按 sqrt(area_mm2={area_mm2:.2f}) 估算为正方形 边长={side_mm:.2f}mm，实际边界需工程师审核"
        ),
    )


def _convert_cylinder(feature: ReviewedFeatureRef) -> BrepShape:
    """圆柱特征 → BrepShape。

    阶段 2 cylinder 参数：
        {axis: [ax,ay,az], center: [cx,cy,cz], radius_mm: float, height_mm: float}

    转换为 BrepShape：
        shape_type = "cylinder"
        operation = "add"
        origin = center
        direction = axis（归一化）
        params = {radius_mm, height_mm}
    """
    params = feature.effective_params()

    axis = _safe_list(params.get("axis"), 3)
    axis = _normalize_vector(axis)
    center = _safe_list(params.get("center"), 3)
    radius_mm = _safe_float(params.get("radius_mm"), 1.0)
    height_mm = _safe_float(params.get("height_mm"), 10.0)

    # radius / height 必须为正
    radius_mm = max(radius_mm, 0.1)
    height_mm = max(height_mm, 0.1)

    return BrepShape(
        shape_id=f"brep_{feature.feature_id}",
        shape_type="cylinder",
        operation="add",
        origin=center,
        direction=axis,
        params={
            "radius_mm": radius_mm,
            "height_mm": height_mm,
        },
        source_feature_id=feature.feature_id,
        conversion_notes=(f"圆柱 radius={radius_mm:.3f}mm height={height_mm:.3f}mm axis={axis} center={center}"),
    )


def _convert_hole(feature: ReviewedFeatureRef) -> BrepShape:
    """孔特征 → BrepShape（圆柱减运算）。

    阶段 2 hole 参数：
        {normal: [nx,ny,nz], center: [cx,cy,cz], radius_mm: float, depth_mm: float}

    转换为 BrepShape：
        shape_type = "cylinder"
        operation = "subtract"（从毛坯/零件中减去孔）
        origin = center
        direction = normal（归一化）
        params = {radius_mm, height_mm=depth_mm}
    """
    params = feature.effective_params()

    normal = _safe_list(params.get("normal"), 3)
    normal = _normalize_vector(normal)
    center = _safe_list(params.get("center"), 3)
    radius_mm = _safe_float(params.get("radius_mm"), 1.0)
    depth_mm = _safe_float(params.get("depth_mm"), 5.0)

    radius_mm = max(radius_mm, 0.1)
    depth_mm = max(depth_mm, 0.1)

    return BrepShape(
        shape_id=f"brep_{feature.feature_id}",
        shape_type="cylinder",
        operation="subtract",
        origin=center,
        direction=normal,
        params={
            "radius_mm": radius_mm,
            "height_mm": depth_mm,  # 孔的 depth 在 STEP 中作为 cylinder height
        },
        source_feature_id=feature.feature_id,
        conversion_notes=(
            f"孔 radius={radius_mm:.3f}mm depth={depth_mm:.3f}mm normal={normal} center={center}（布尔减运算）"
        ),
    )


def _convert_boss(feature: ReviewedFeatureRef) -> BrepShape:
    """凸台特征 → BrepShape（圆柱加运算）。

    阶段 2 boss 参数：
        {normal: [nx,ny,nz], center: [cx,cy,cz], radius_mm: float, height_mm: float}

    转换为 BrepShape：
        shape_type = "cylinder"
        operation = "add"（加到零件上）
        origin = center
        direction = normal（归一化）
        params = {radius_mm, height_mm}
    """
    params = feature.effective_params()

    normal = _safe_list(params.get("normal"), 3)
    normal = _normalize_vector(normal)
    center = _safe_list(params.get("center"), 3)
    radius_mm = _safe_float(params.get("radius_mm"), 1.0)
    height_mm = _safe_float(params.get("height_mm"), 5.0)

    radius_mm = max(radius_mm, 0.1)
    height_mm = max(height_mm, 0.1)

    return BrepShape(
        shape_id=f"brep_{feature.feature_id}",
        shape_type="cylinder",
        operation="add",
        origin=center,
        direction=normal,
        params={
            "radius_mm": radius_mm,
            "height_mm": height_mm,
        },
        source_feature_id=feature.feature_id,
        conversion_notes=(
            f"凸台 radius={radius_mm:.3f}mm height={height_mm:.3f}mm normal={normal} center={center}（布尔加运算）"
        ),
    )


# 主入口：批量转换


# 特征类型 转换函数映射
_FEATURE_CONVERTERS = {
    "plane": _convert_plane,
    "cylinder": _convert_cylinder,
    "hole": _convert_hole,
    "boss": _convert_boss,
}


@dataclass
class FeatureToBrepResult:
    """特征 → B-rep 转换结果。"""

    shapes: list[BrepShape] = field(default_factory=list)
    skipped_features: list[dict[str, Any]] = field(default_factory=list)
    conversion_errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.shapes)

    @property
    def has_errors(self) -> bool:
        return bool(self.conversion_errors)


def convert_features_to_brep(
    features: list[ReviewedFeatureRef],
) -> FeatureToBrepResult:
    """把阶段 2 已确认特征列表转换为 BrepShape 列表。

    跳过 review_status=rejected 的特征（工程师已拒绝）。
    对未识别的特征类型记录到 skipped_features。
    对转换异常记录到 conversion_errors（不中断整体流程）。

    Args:
        features: 阶段 2 导出的 ReviewedFeatureRef 列表

    Returns:
        FeatureToBrepResult
    """
    result = FeatureToBrepResult()

    for feature in features:
        # 跳过工程师已拒绝的特征
        if feature.review_status == "rejected":
            result.skipped_features.append(
                {
                    "feature_id": feature.feature_id,
                    "feature_type": feature.feature_type,
                    "reason": "rejected_by_engineer",
                }
            )
            continue

        converter = _FEATURE_CONVERTERS.get(feature.feature_type)
        if converter is None:
            result.skipped_features.append(
                {
                    "feature_id": feature.feature_id,
                    "feature_type": feature.feature_type,
                    "reason": f"unsupported_feature_type: {feature.feature_type}",
                }
            )
            continue

        try:
            shape = converter(feature)
            result.shapes.append(shape)
        except Exception as e:
            result.conversion_errors.append(
                {
                    "feature_id": feature.feature_id,
                    "feature_type": feature.feature_type,
                    "error": str(e),
                }
            )

    return result
