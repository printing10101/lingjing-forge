"""碰撞检测模块。

提供刀具路径与工件/夹具的碰撞检测功能。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CollisionResult:
    """碰撞检测结果。"""

    collided: bool = False
    collision_points: list[tuple[float, float, float]] = field(default_factory=list)
    collision_segments: list[int] = field(default_factory=list)
    severity: str = "none"  # none, warning, critical

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "collided": self.collided,
            "collision_points": self.collision_points,
            "collision_segments": self.collision_segments,
            "severity": self.severity,
        }


class CollisionChecker:
    """碰撞检测器。"""

    def __init__(self) -> None:
        """初始化碰撞检测器。"""
        self._workpiece: Any = None
        self._fixtures: list[Any] = []
        self._tool_holder: Any = None

    def set_workpiece(self, geometry: Any) -> None:
        """设置工件几何体。

        Args:
            geometry: 工件几何体
        """
        self._workpiece = geometry
        logger.info("Workpiece set for collision detection")

    def add_fixture(self, geometry: Any) -> None:
        """添加夹具几何体。

        Args:
            geometry: 夹具几何体
        """
        self._fixtures.append(geometry)
        logger.info("Fixture added for collision detection")

    def set_tool_holder(self, geometry: Any) -> None:
        """设置刀柄几何体。

        Args:
            geometry: 刀柄几何体
        """
        self._tool_holder = geometry
        logger.info("Tool holder set for collision detection")

    def check_collision(
        self,
        tool_position: tuple[float, float, float],
        tool_geometry: Any,
    ) -> CollisionResult:
        """检查单个位置的碰撞。

        使用轴对齐包围盒(AABB)进行碰撞检测。假设几何体对象具有以下属性之一:
        - bbox: (x_min, y_min, z_min, x_max, y_max, z_max) 元组
        - bounding_box: 同上
        - 或通过 get_bbox() 方法返回

        Args:
            tool_position: 刀具位置 (x, y, z)
            tool_geometry: 刀具几何体

        Returns:
            碰撞检测结果
        """
        result = CollisionResult()

        # 获取刀具包围盒
        tool_bbox = self._extract_bbox(tool_geometry, tool_position)
        if tool_bbox is None:
            logger.warning("Cannot extract tool geometry bbox, skipping collision check")
            return result

        # 检查刀具与工件的碰撞
        if self._workpiece is not None:
            workpiece_bbox = self._extract_bbox(self._workpiece)
            if workpiece_bbox and self._aabb_intersect(tool_bbox, workpiece_bbox):
                result.collided = True
                result.collision_points.append(tool_position)
                result.severity = "critical"
                logger.warning("Tool collided with workpiece at %s", tool_position)

        # 检查刀具与夹具的碰撞
        for i, fixture in enumerate(self._fixtures):
            fixture_bbox = self._extract_bbox(fixture)
            if fixture_bbox and self._aabb_intersect(tool_bbox, fixture_bbox):
                result.collided = True
                result.collision_points.append(tool_position)
                result.severity = "critical"
                logger.warning("Tool collided with fixture %d at %s", i, tool_position)

        # 检查刀柄与工件的碰撞
        if self._tool_holder is not None and self._workpiece is not None:
            holder_bbox = self._extract_bbox(self._tool_holder)
            workpiece_bbox = self._extract_bbox(self._workpiece)
            if holder_bbox and workpiece_bbox and self._aabb_intersect(holder_bbox, workpiece_bbox):
                result.collided = True
                result.collision_points.append(tool_position)
                result.severity = "warning"
                logger.warning("Tool holder collided with workpiece at %s", tool_position)

        return result

    def check_toolpath(
        self,
        toolpath: Any,
        tool_geometry: Any,
    ) -> CollisionResult:
        """检查整个刀具路径的碰撞。

        将刀具路径离散化为采样点，在每个点检查碰撞并聚合结果。

        Args:
            toolpath: 刀具路径，可以是:
                - 列表/元组: [(x,y,z), ...] 或包含位置属性的对象列表
                - 具有 points/positions 属性的对象
            tool_geometry: 刀具几何体

        Returns:
            碰撞检测结果
        """
        result = CollisionResult()

        # 提取路径点
        points = self._extract_toolpath_points(toolpath)
        if not points:
            logger.warning("No points extracted from toolpath")
            return result

        # 在每个采样点检查碰撞
        sample_step = max(1, len(points) // 100)  # 最多采样100个点
        for i in range(0, len(points), sample_step):
            point = points[i]
            point_result = self.check_collision(point, tool_geometry)

            if point_result.collided:
                result.collided = True
                result.collision_points.extend(point_result.collision_points)
                result.collision_segments.append(i)

                # 升级严重程度
                if point_result.severity == "critical":
                    result.severity = "critical"
                elif result.severity != "critical" and point_result.severity == "warning":
                    result.severity = "warning"

        if result.collided:
            logger.warning(
                "Toolpath collision detected: %d collision points, severity=%s",
                len(result.collision_points),
                result.severity,
            )

        return result

    @staticmethod
    def _extract_bbox(
        geometry: Any, position: tuple[float, float, float] | None = None
    ) -> tuple[float, float, float, float, float, float] | None:
        """从几何体对象提取包围盒。

        支持多种几何体格式:
        - 具有 bbox 属性: (x_min, y_min, z_min, x_max, y_max, z_max)
        - 具有 bounding_box 属性
        - 具有 get_bbox() 方法
        - 字典格式: {"bbox": [...]} 或 {"min": [...], "max": [...]}

        Args:
            geometry: 几何体对象
            position: 可选的位置偏移 (x, y, z)

        Returns:
            包围盒 (x_min, y_min, z_min, x_max, y_max, z_max) 或 None
        """
        bbox = None

        # 尝试不同的属性/方法
        if hasattr(geometry, "bbox"):
            bbox = geometry.bbox
        elif hasattr(geometry, "bounding_box"):
            bbox = geometry.bounding_box
        elif hasattr(geometry, "get_bbox"):
            try:
                bbox = geometry.get_bbox()
            except (AttributeError, TypeError, ValueError) as e:
                logger.debug("get_bbox() 调用失败: %s", e)
                pass
        elif isinstance(geometry, dict):
            bbox = geometry.get("bbox") or (
                (*geometry["min"], *geometry["max"]) if "min" in geometry and "max" in geometry else None
            )
        elif isinstance(geometry, (list, tuple)) and len(geometry) == 6:
            # 直接是包围盒元组
            bbox = geometry

        if bbox is None:
            return None

        # 转换为元组
        try:
            x_min, y_min, z_min, x_max, y_max, z_max = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return None

        # 应用位置偏移
        if position is not None:
            dx, dy, dz = position
            x_min += dx
            y_min += dy
            z_min += dz
            x_max += dx
            y_max += dy
            z_max += dz

        return x_min, y_min, z_min, x_max, y_max, z_max

    @staticmethod
    def _aabb_intersect(
        bbox1: tuple[float, float, float, float, float, float],
        bbox2: tuple[float, float, float, float, float, float],
    ) -> bool:
        """检查两个轴对齐包围盒是否相交。

        Args:
            bbox1: 第一个包围盒 (x_min, y_min, z_min, x_max, y_max, z_max)
            bbox2: 第二个包围盒

        Returns:
            是否相交
        """
        x1_min, y1_min, z1_min, x1_max, y1_max, z1_max = bbox1
        x2_min, y2_min, z2_min, x2_max, y2_max, z2_max = bbox2

        # AABB 相交条件: 所有轴都有重叠
        return not (
            x1_max < x2_min
            or x2_max < x1_min
            or y1_max < y2_min
            or y2_max < y1_min
            or z1_max < z2_min
            or z2_max < z1_min
        )

    @staticmethod
    def _extract_toolpath_points(toolpath: Any) -> list[tuple[float, float, float]]:
        """从刀具路径对象提取位置点列表。

        支持多种格式:
        - 列表/元组: [(x,y,z), ...] 或 [[x,y,z], ...]
        - 具有 points/positions 属性的对象
        - 具有 waypoints 属性的对象

        Args:
            toolpath: 刀具路径对象

        Returns:
            位置点列表 [(x,y,z), ...]
        """
        points: list[tuple[float, float, float]] = []

        # 尝试不同的属性
        if hasattr(toolpath, "points"):
            raw_points = toolpath.points
        elif hasattr(toolpath, "positions"):
            raw_points = toolpath.positions
        elif hasattr(toolpath, "waypoints"):
            raw_points = toolpath.waypoints
        elif isinstance(toolpath, (list, tuple)):
            raw_points = toolpath
        else:
            return points

        # 转换点
        for point in raw_points:
            try:
                if hasattr(point, "__iter__"):
                    x, y, z = (float(v) for v in point[:3])
                    points.append((x, y, z))
                elif hasattr(point, "x") and hasattr(point, "y") and hasattr(point, "z"):
                    points.append((float(point.x), float(point.y), float(point.z)))
            except (TypeError, ValueError, IndexError):
                continue

        return points

    def clear(self) -> None:
        """清除所有几何体。"""
        self._workpiece = None
        self._fixtures.clear()
        self._tool_holder = None
        logger.info("Collision checker cleared")
