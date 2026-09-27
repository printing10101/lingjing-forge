"""高级加工特征：倒角、台阶、键槽。

这些是产品轨的核心 3D 建模扩展。
新功能通过 CadQueryGenerator.generate_with_features 入口调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cadquery as cq
from typing import cast

logger = logging.getLogger(__name__)


@dataclass
class ChamferSpec:
    """倒角规格。

    Attributes:
        edges_selector: CadQuery edges 选择器（默认所有边）
        length: 倒角长度（mm）
        angle: 倒角角度（度），默认 45°
    """

    length: float = 1.0
    angle: float = 45.0
    edges_selector: str = "|"

    def to_dict(self) -> dict:
        return {
            "type": "chamfer",
            "length": self.length,
            "angle": self.angle,
            "edges_selector": self.edges_selector,
        }


@dataclass
class FilletSpec:
    """圆角规格。"""

    radius: float = 1.0
    edges_selector: str = "|"

    def to_dict(self) -> dict:
        return {
            "type": "fillet",
            "radius": self.radius,
            "edges_selector": self.edges_selector,
        }


@dataclass
class StepSpec:
    """台阶特征。

    Attributes:
        offset_x: 沿 X 方向偏移（mm）
        offset_y: 沿 Y 方向偏移（mm）
        offset_z: 沿 Z 方向偏移（mm），从顶部向下
        length: 沿 X 方向新长度（None 表示不变）
        width: 沿 Y 方向新宽度（None 表示不变）
        height: 沿 Z 方向新高度（None 表示不变）
    """

    offset_x: float = 0.0
    offset_y: float = 0.0
    offset_z: float = 0.0
    length: float | None = None
    width: float | None = None
    height: float | None = None

    def to_dict(self) -> dict:
        return {
            "type": "step",
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "offset_z": self.offset_z,
            "length": self.length,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class SlotSpec:
    """键槽特征。

    Attributes:
        center_x: 中心 X 坐标（mm）
        center_y: 中心 Y 坐标（mm）
        length: 键槽长度（mm）
        width: 键槽宽度（mm）
        depth: 键槽深度（mm）
        axis: 'x' 或 'y'，键槽延伸方向
        surface_z: 键槽所在加工面 Z 坐标（None 表示顶面）
    """

    center_x: float = 0.0
    center_y: float = 0.0
    length: float = 20.0
    width: float = 5.0
    depth: float = 2.5
    axis: str = "x"
    surface_z: float | None = None

    def to_dict(self) -> dict:
        return {
            "type": "slot",
            "center_x": self.center_x,
            "center_y": self.center_y,
            "length": self.length,
            "width": self.width,
            "depth": self.depth,
            "axis": self.axis,
            "surface_z": self.surface_z,
        }


class AdvancedFeatureBuilder:
    """高级特征构造器。

    用法：
        builder = AdvancedFeatureBuilder()
        result = builder.apply_features(base, features_dict_list)
    """

    def apply_features(
        self,
        base: cq.Workplane,
        features: list[dict],
    ) -> cq.Workplane:
        """按顺序应用一组特征。

        features 是字典列表，每项包含 "type" 字段和对应参数。
        支持 type: chamfer, fillet, step, slot。
        """
        result = base
        for spec in features:
            ftype = spec.get("type", "").lower()
            try:
                if ftype == "chamfer":
                    result = self._apply_chamfer(result, spec)
                elif ftype == "fillet":
                    result = self._apply_fillet(result, spec)
                elif ftype == "step":
                    result = self._apply_step(result, spec)
                elif ftype == "slot":
                    result = self._apply_slot(result, spec)
                else:
                    logger.warning("未知特征类型: %s", ftype)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
                logger.error(
                    "特征应用失败 type=%s params=%s err=%s: %s",
                    ftype,
                    spec,
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
        return result

    def _apply_chamfer(self, wp: cq.Workplane, spec: dict) -> cq.Workplane:
        """应用倒角。"""
        length = float(spec.get("length", 1.0))
        return wp.chamfer(length)

    def _apply_fillet(self, wp: cq.Workplane, spec: dict) -> cq.Workplane:
        """应用圆角。"""
        radius = float(spec.get("radius", 1.0))
        return wp.fillet(radius)

    def _apply_step(self, wp: cq.Workplane, spec: dict) -> cq.Workplane:
        """应用台阶：在顶面切出一个新形状。

        实现思路：创建一个新的 step 实体，用 cut 减去。
        """
        length_new = spec.get("length")
        width_new = spec.get("width")
        height_new = spec.get("height")

        if length_new is None and width_new is None and height_new is None:
            return wp

        # 取当前 bbox
        bb = cast(cq.Shape, wp.val()).BoundingBox()
        cur_len = bb.xmax - bb.xmin
        cur_wid = bb.ymax - bb.ymin
        cur_h = bb.zmax - bb.zmin

        new_len = float(length_new) if length_new is not None else cur_len
        new_wid = float(width_new) if width_new is not None else cur_wid
        new_h = float(height_new) if height_new is not None else cur_h

        # 创建一个代表"保留部分"的 box，然后 cut 掉的部分
        # 这里采用叠加 cut 的简化策略
        # 1. 如果高度变化，从顶部 cut
        if height_new is not None and new_h < cur_h:
            cut_h = cur_h - new_h
            # 创建比当前模型大一圈的 cut box
            cut_wp = (
                cq.Workplane("XY")
                .transformed(offset=(bb.xmin - 1, bb.ymin - 1, bb.zmin + new_h))
                .box(cur_len + 2, cur_wid + 2, cut_h + 1)
            )
            wp = wp.cut(cut_wp)

        # 2. 如果长度或宽度变化，缩小
        if (length_new is not None and new_len < cur_len) or (width_new is not None and new_wid < cur_wid):
            cut_x = (cur_len - new_len) / 2
            cut_y = (cur_wid - new_wid) / 2
            if cut_x > 0:
                # 左右各 cut 一半
                side_wp = (
                    cq.Workplane("XY")
                    .transformed(
                        offset=(
                            bb.xmin - 1,
                            bb.ymin - 1,
                            bb.zmin - 1,
                        )
                    )
                    .box(cut_x + 1, cur_wid + 2, cur_h + 2)
                )
                wp = wp.cut(side_wp)
                side_wp2 = (
                    cq.Workplane("XY")
                    .transformed(
                        offset=(
                            bb.xmax - cut_x,
                            bb.ymin - 1,
                            bb.zmin - 1,
                        )
                    )
                    .box(cut_x + 1, cur_wid + 2, cur_h + 2)
                )
                wp = wp.cut(side_wp2)
            if cut_y > 0:
                side_wp = (
                    cq.Workplane("XY")
                    .transformed(
                        offset=(
                            bb.xmin - 1,
                            bb.ymin - 1,
                            bb.zmin - 1,
                        )
                    )
                    .box(cur_len + 2, cut_y + 1, cur_h + 2)
                )
                wp = wp.cut(side_wp)
                side_wp2 = (
                    cq.Workplane("XY")
                    .transformed(
                        offset=(
                            bb.xmin - 1,
                            bb.ymax - cut_y,
                            bb.zmin - 1,
                        )
                    )
                    .box(cur_len + 2, cut_y + 1, cur_h + 2)
                )
                wp = wp.cut(side_wp2)

        return wp

    def _apply_slot(self, wp: cq.Workplane, spec: dict) -> cq.Workplane:
        """应用键槽：在指定面切出长条形凹槽。"""
        cx = float(spec.get("center_x", 0.0))
        cy = float(spec.get("center_y", 0.0))
        length = float(spec.get("length", 20.0))
        width = float(spec.get("width", 5.0))
        depth = float(spec.get("depth", 2.5))
        axis = str(spec.get("axis", "x")).lower()
        surface_z = spec.get("surface_z")

        bb = cast(cq.Shape, wp.val()).BoundingBox()
        top_z = bb.zmax if surface_z is None else float(surface_z)

        # 键槽的 cut box
        if axis == "x":
            sx, sy, sz = length, width, depth
        else:
            sx, sy, sz = width, length, depth

        slot = cq.Workplane("XY").transformed(offset=(cx - sx / 2, cy - sy / 2, top_z - depth)).box(sx, sy, sz + 0.5)
        return wp.cut(slot)


__all__ = [
    "ChamferSpec",
    "FilletSpec",
    "StepSpec",
    "SlotSpec",
    "AdvancedFeatureBuilder",
]
