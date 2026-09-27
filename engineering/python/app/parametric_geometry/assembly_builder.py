"""CAD 装配器：把 BrepShape 列表组织为带装配顺序的零件。

设计原则
========
灵境制造的参数化几何输出模块采用分层架构：
- feature_to_brep.py：纯数据转换层，特征参数 → BrepShape
- assembly_builder.py（本模块）：装配规则层，定义多 BrepShape 之间的布尔关系与装配顺序
- step_writer.py：STEP IO 层，把装配后的 BrepShape 列表写入 STEP 文件

本模块的职责：
1. 选择基准形状（base shape）：通常为最大的 add 形状（plane 优先，作为零件底面）
2. 排序 add 形状：按体积从大到小（先大后小，避免小特征被覆盖）
3. 排序 subtract 形状：按 shape_id 保持稳定顺序（钻孔顺序不影响最终几何）
4. 估算毛坯尺寸：所有 add 形状的 bbox 并集 + 配置的 blank_margin_mm
5. 输出装配计划：AssemblyPlan，包含基准、add 列表、subtract 列表、毛坯 bbox、装配顺序

不参与布尔运算的形状（auxiliary）：
- 多 plane 同时存在时，第一个 plane 作为底座基准；其余 plane 作为辅助参考（仅记录坐标，不参与布尔）
- 这样可避免两个 plane fuse 后产生非流形几何

毛坯尺寸估算策略：
- 计算 add 形状（base + boss）的 axis-aligned bbox 并集
- 加上配置的 blank_margin_mm（默认 2.0mm，可由 ParametricGeometryConfig 调整）
- 输出 blank_bbox 供 CAM 软件参考（仅作为下料参考，不强制约束）

工业硬约束（项目记忆）：
- mesh → 参数化 CAD 自动转换未解决，本模块输出的是「建议装配方案」，工程师必须审核
- 装配顺序的合理性影响 CAM 加工策略，最终由 CAM 软件（NX/PowerMill/PyCAM）二次校验
- 毛坯 bbox 估算仅供参考，实际下料尺寸由工艺工程师根据材料规格与加工余量决定
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.parametric_geometry.feature_to_brep import BrepShape

logger = logging.getLogger(__name__)


# 异常


class AssemblyBuilderError(Exception):
    """装配器异常。"""


# Bounding Box 工具


@dataclass
class BoundingBox:
    """Axis-aligned bounding box（轴对齐包围盒）。

    所有坐标单位为 mm。blank_bbox 用于毛坯尺寸估算，
    实际下料尺寸由工艺工程师决定。
    """

    min_x: float = math.inf
    min_y: float = math.inf
    min_z: float = math.inf
    max_x: float = -math.inf
    max_y: float = -math.inf
    max_z: float = -math.inf

    @property
    def is_empty(self) -> bool:
        """bbox 是否为空（无任何形状加入）。"""
        return self.min_x > self.max_x

    @property
    def size_x(self) -> float:
        return self.max_x - self.min_x if not self.is_empty else 0.0

    @property
    def size_y(self) -> float:
        return self.max_y - self.min_y if not self.is_empty else 0.0

    @property
    def size_z(self) -> float:
        return self.max_z - self.min_z if not self.is_empty else 0.0

    @property
    def center(self) -> list[float]:
        if self.is_empty:
            return [0.0, 0.0, 0.0]
        return [
            (self.min_x + self.max_x) / 2,
            (self.min_y + self.max_y) / 2,
            (self.min_z + self.max_z) / 2,
        ]

    def union_point(self, p: list[float]) -> None:
        """把一个点并入 bbox。"""
        x, y, z = p[0], p[1], p[2]
        self.min_x = min(self.min_x, x)
        self.min_y = min(self.min_y, y)
        self.min_z = min(self.min_z, z)
        self.max_x = max(self.max_x, x)
        self.max_y = max(self.max_y, y)
        self.max_z = max(self.max_z, z)

    def union_bbox(self, other: BoundingBox) -> None:
        """把另一个 bbox 并入。"""
        if other.is_empty:
            return
        self.min_x = min(self.min_x, other.min_x)
        self.min_y = min(self.min_y, other.min_y)
        self.min_z = min(self.min_z, other.min_z)
        self.max_x = max(self.max_x, other.max_x)
        self.max_y = max(self.max_y, other.max_y)
        self.max_z = max(self.max_z, other.max_z)

    def expand(self, margin_mm: float) -> BoundingBox:
        """返回扩大 margin 的新 bbox。"""
        if self.is_empty or margin_mm <= 0:
            return BoundingBox(
                min_x=self.min_x,
                min_y=self.min_y,
                min_z=self.min_z,
                max_x=self.max_x,
                max_y=self.max_y,
                max_z=self.max_z,
            )
        return BoundingBox(
            min_x=self.min_x - margin_mm,
            min_y=self.min_y - margin_mm,
            min_z=self.min_z - margin_mm,
            max_x=self.max_x + margin_mm,
            max_y=self.max_y + margin_mm,
            max_z=self.max_z + margin_mm,
        )

    def to_dict(self) -> dict[str, Any]:
        if self.is_empty:
            return {
                "min": None,
                "max": None,
                "size": [0.0, 0.0, 0.0],
                "center": [0.0, 0.0, 0.0],
                "is_empty": True,
            }
        return {
            "min": [self.min_x, self.min_y, self.min_z],
            "max": [self.max_x, self.max_y, self.max_z],
            "size": [self.size_x, self.size_y, self.size_z],
            "center": self.center,
            "is_empty": False,
        }


# 形状几何估算


def _shape_volume_mm3(shape: BrepShape) -> float:
    """估算 BrepShape 的体积（mm³）。

    用于装配排序（大形状优先作为 base）：
    - plane:    width × height × 0.1（按 0.1mm 板厚估算）
    - cylinder: π × r² × h
    - box:      width × height × depth
    """
    p = shape.params
    try:
        if shape.shape_type == "plane":
            w = float(p.get("width_mm", 0.0))
            h = float(p.get("height_mm", 0.0))
            return max(w * h * 0.1, 1.0)
        if shape.shape_type == "cylinder":
            r = float(p.get("radius_mm", 0.0))
            h = float(p.get("height_mm", 0.0))
            return math.pi * r * r * h
        if shape.shape_type == "box":
            w = float(p.get("width_mm", 0.0))
            h = float(p.get("height_mm", 0.0))
            d = float(p.get("depth_mm", 0.0))
            return w * h * d
    except (TypeError, ValueError) as e:
        logger.warning(
            "估算形状体积失败 shape_id=%s type=%s: %s",
            shape.shape_id,
            shape.shape_type,
            e,
        )
    return 0.0


def _shape_bbox(shape: BrepShape) -> BoundingBox:
    """估算 BrepShape 的 axis-aligned bbox。

    简化处理：cylinder 沿任意方向的 bbox 用 origin ± height/2 作为近似（不旋转）。
    实际几何 bbox 应考虑方向旋转，但本模块仅用于装配顺序判断与毛坯估算，
    简化处理已足够（最终 bbox 由 CAM 软件精确计算）。
    """
    p = shape.params
    bbox = BoundingBox()
    try:
        ox = float(shape.origin[0])
        oy = float(shape.origin[1])
        oz = float(shape.origin[2])
    except (IndexError, TypeError, ValueError):
        logger.warning("形状 origin 非法 shape_id=%s: %r", shape.shape_id, shape.origin)
        return bbox

    if shape.shape_type == "plane":
        try:
            w = float(p.get("width_mm", 0.0))
            h = float(p.get("height_mm", 0.0))
        except (TypeError, ValueError):
            w, h = 0.0, 0.0
        # plane 是薄板，厚度近似 0.1mm
        bbox.union_point([ox - w / 2, oy - h / 2, oz - 0.05])
        bbox.union_point([ox + w / 2, oy + h / 2, oz + 0.05])
    elif shape.shape_type == "cylinder":
        try:
            r = float(p.get("radius_mm", 0.0))
            h = float(p.get("height_mm", 0.0))
        except (TypeError, ValueError):
            r, h = 0.0, 0.0
        # 简化：用 direction 的分量作为延伸方向的近似（保守包围盒）
        try:
            dx = abs(float(shape.direction[0]))
            dy = abs(float(shape.direction[1]))
            dz = abs(float(shape.direction[2]))
        except (IndexError, TypeError, ValueError):
            dx, dy, dz = 0.0, 0.0, 1.0
        half = h / 2
        bbox.union_point(
            [
                ox - r - dx * half,
                oy - r - dy * half,
                oz - r - dz * half,
            ]
        )
        bbox.union_point(
            [
                ox + r + dx * half,
                oy + r + dy * half,
                oz + r + dz * half,
            ]
        )
    elif shape.shape_type == "box":
        try:
            w = float(p.get("width_mm", 0.0))
            h = float(p.get("height_mm", 0.0))
            d = float(p.get("depth_mm", 0.0))
        except (TypeError, ValueError):
            w, h, d = 0.0, 0.0, 0.0
        bbox.union_point([ox - w / 2, oy - h / 2, oz - d / 2])
        bbox.union_point([ox + w / 2, oy + h / 2, oz + d / 2])
    else:
        logger.warning(
            "未知 shape_type=%s shape_id=%s，bbox 计算跳过",
            shape.shape_type,
            shape.shape_id,
        )

    return bbox


# 装配计划


@dataclass
class AssemblyPlan:
    """装配计划：把多个 BrepShape 组织成有顺序的零件加工流程。

    step_writer.py 根据 AssemblyPlan：
    1. 用 base_shape 作为初始毛坯（或第一个 add 形状）
    2. 依次 fuse add_shapes 中其余形状
    3. 依次 cut subtract_shapes 中所有孔
    4. auxiliary_shapes 仅记录到 STEP 作为参考几何（construction geometry），不参与布尔
    """

    base_shape: BrepShape | None = None
    add_shapes: list[BrepShape] = field(default_factory=list)
    subtract_shapes: list[BrepShape] = field(default_factory=list)
    auxiliary_shapes: list[BrepShape] = field(default_factory=list)
    blank_bbox: BoundingBox = field(default_factory=BoundingBox)
    assembly_order: list[str] = field(default_factory=list)
    assembly_notes: list[str] = field(default_factory=list)

    @property
    def total_shape_count(self) -> int:
        return (
            (1 if self.base_shape is not None else 0)
            + len(self.add_shapes)
            + len(self.subtract_shapes)
            + len(self.auxiliary_shapes)
        )

    @property
    def has_solid(self) -> bool:
        """是否有可写为实体的形状（base 或 add 非空）。"""
        return self.base_shape is not None or bool(self.add_shapes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_shape": self.base_shape.to_dict() if self.base_shape else None,
            "add_shapes": [s.to_dict() for s in self.add_shapes],
            "subtract_shapes": [s.to_dict() for s in self.subtract_shapes],
            "auxiliary_shapes": [s.to_dict() for s in self.auxiliary_shapes],
            "blank_bbox": self.blank_bbox.to_dict(),
            "assembly_order": list(self.assembly_order),
            "assembly_notes": list(self.assembly_notes),
            "total_shape_count": self.total_shape_count,
            "has_solid": self.has_solid,
        }


# 装配器


def build_assembly_plan(
    shapes: list[BrepShape],
    blank_margin_mm: float = 2.0,
) -> AssemblyPlan:
    """把 BrepShape 列表组织为装配计划。

    装配规则：
    1. 按 operation 分组：add / subtract
    2. add 形状中：plane 优先作为 base（如有），其余 plane 作为 auxiliary
    3. 非平面 add 形状（cylinder/boss）：按体积从大到小排序，加入 add_shapes
    4. subtract 形状（hole）：按 shape_id 保持稳定顺序，加入 subtract_shapes
    5. 计算 blank_bbox：add 形状的 bbox 并集 + blank_margin_mm
    6. 装配顺序：base → add → subtract（auxiliary 仅作参考）

    Args:
        shapes: feature_to_brep.py 输出的 BrepShape 列表
        blank_margin_mm: 毛坯余量（mm），加到 bbox 各方向

    Returns:
        AssemblyPlan
    """
    plan = AssemblyPlan()

    # 1. 按 operation 分组
    add_shapes: list[BrepShape] = []
    subtract_shapes: list[BrepShape] = []
    for s in shapes:
        if s.operation == "add":
            add_shapes.append(s)
        elif s.operation == "subtract":
            subtract_shapes.append(s)
        else:
            plan.assembly_notes.append(f"忽略未知 operation={s.operation} 的形状 {s.shape_id}")

    # 2. 分离 plane 与非 plane
    planes = [s for s in add_shapes if s.shape_type == "plane"]
    non_plane_adds = [s for s in add_shapes if s.shape_type != "plane"]

    # 3. 选择 base shape：plane 优先（按面积最大）
    if planes:
        planes.sort(
            key=lambda s: float(s.params.get("width_mm", 0.0)) * float(s.params.get("height_mm", 0.0)),
            reverse=True,
        )
        plan.base_shape = planes[0]
        try:
            w_b = float(planes[0].params.get("width_mm", 0.0))
            h_b = float(planes[0].params.get("height_mm", 0.0))
            plan.assembly_notes.append(f"基准形状：平面 {planes[0].shape_id} ({w_b:.2f}×{h_b:.2f}mm)")
        except (TypeError, ValueError):
            plan.assembly_notes.append(f"基准形状：平面 {planes[0].shape_id}（参数非法）")
        # 其余 plane 作为辅助参考（不参与布尔）
        for p in planes[1:]:
            plan.auxiliary_shapes.append(p)
            plan.assembly_notes.append(f"辅助参考平面：{p.shape_id}（不参与布尔运算）")

    # 4. 非平面 add 形状按体积从大到小排序
    if non_plane_adds:
        non_plane_adds.sort(key=_shape_volume_mm3, reverse=True)
        # 如果还没 base，用第一个非 plane 作为 base
        if plan.base_shape is None:
            plan.base_shape = non_plane_adds[0]
            plan.add_shapes = non_plane_adds[1:]
            vol = _shape_volume_mm3(non_plane_adds[0])
            plan.assembly_notes.append(
                f"基准形状：{non_plane_adds[0].shape_type} {non_plane_adds[0].shape_id}（体积={vol:.2f}mm³）"
            )
        else:
            plan.add_shapes = non_plane_adds
            for s in non_plane_adds:
                vol = _shape_volume_mm3(s)
                plan.assembly_notes.append(f"附加形状：{s.shape_type} {s.shape_id}（体积={vol:.2f}mm³）")

    # 5. subtract 形状按 shape_id 排序（保持稳定）
    subtract_shapes.sort(key=lambda s: s.shape_id)
    plan.subtract_shapes = subtract_shapes
    for s in subtract_shapes:
        plan.assembly_notes.append(f"减运算形状：{s.shape_type} {s.shape_id}（孔/凹槽）")

    # 6. 计算 blank_bbox
    bbox = BoundingBox()
    if plan.base_shape is not None:
        bbox.union_bbox(_shape_bbox(plan.base_shape))
    for s in plan.add_shapes:
        bbox.union_bbox(_shape_bbox(s))
    for s in plan.subtract_shapes:
        # subtract 形状的 bbox 也参与（避免孔超出毛坯边界，影响 CAM 刀路）
        bbox.union_bbox(_shape_bbox(s))

    if bbox.is_empty:
        plan.assembly_notes.append("⚠ 无可用形状，blank_bbox 为空")
    else:
        plan.blank_bbox = bbox.expand(blank_margin_mm)
        plan.assembly_notes.append(
            f"毛坯 bbox（含 {blank_margin_mm:.2f}mm 余量）："
            f"size={plan.blank_bbox.size_x:.2f}×"
            f"{plan.blank_bbox.size_y:.2f}×"
            f"{plan.blank_bbox.size_z:.2f}mm"
        )

    # 7. 装配顺序：base add subtract
    order: list[str] = []
    if plan.base_shape is not None:
        order.append(plan.base_shape.shape_id)
    for s in plan.add_shapes:
        order.append(s.shape_id)
    for s in plan.subtract_shapes:
        order.append(s.shape_id)
    plan.assembly_order = order

    # 8. 工程师审核提示（项目记忆硬约束：human-in-the-loop）
    if plan.base_shape is None:
        plan.assembly_notes.append("⚠ 无基准形状，STEP 输出将仅包含辅助几何，不可直接用于 CAM")

    if not plan.has_solid:
        plan.assembly_notes.append("⚠ 装配计划无可写入实体的形状，STEP 输出将仅含 construction geometry")

    plan.assembly_notes.append("工程师必须审核装配顺序与基准选择后再生成最终 STEP")

    return plan


def get_assembly_summary(plan: AssemblyPlan) -> dict[str, Any]:
    """生成装配摘要（用于 API 响应）。"""
    return {
        "has_base": plan.base_shape is not None,
        "base_shape_type": plan.base_shape.shape_type if plan.base_shape else None,
        "base_shape_id": plan.base_shape.shape_id if plan.base_shape else None,
        "add_count": len(plan.add_shapes),
        "subtract_count": len(plan.subtract_shapes),
        "auxiliary_count": len(plan.auxiliary_shapes),
        "total_shape_count": plan.total_shape_count,
        "has_solid": plan.has_solid,
        "blank_size_mm": (
            [
                round(plan.blank_bbox.size_x, 3),
                round(plan.blank_bbox.size_y, 3),
                round(plan.blank_bbox.size_z, 3),
            ]
            if not plan.blank_bbox.is_empty
            else None
        ),
        "blank_center_mm": ([round(c, 3) for c in plan.blank_bbox.center] if not plan.blank_bbox.is_empty else None),
        "assembly_order": list(plan.assembly_order),
        "notes_count": len(plan.assembly_notes),
    }
