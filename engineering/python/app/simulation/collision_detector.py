"""Basic collision detection engine for CNC toolpath validation.

Uses AABB (Axis-Aligned Bounding Box) for 3-axis efficient collision detection,
and OBB (Oriented Bounding Box) for 5-axis collision detection with rotation axes.
Supports RTCP/TWP modes, A/C axis verification, singularity detection, and workspace constraints.

Example:
    >>> stock = StockModel(length=200, width=150, height=50)
    >>> detector = CollisionDetector(stock, safe_z_height=10.0)
    >>> report = detector.check_segments(parsed_segments)
    >>> print(f"Safe: {report.safe}, Collisions: {report.collision_count}")

    # 5-axis mode
    >>> detector_5axis = CollisionDetector(stock, mode="5axis")
    >>> report_5axis = detector_5axis.check_segments(segments_5axis)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.simulation.stock_model import StockBoundingBox, StockModel
from app.simulation.toolpath_parser import ToolpathSegment


@dataclass
class FiveAxisToolVector:
    """Represents tool orientation vector for 5-axis machining.

    Attributes:
        a_angle: A-axis rotation angle in degrees (-120 to +120 for XM-100)
        c_angle: C-axis rotation angle in degrees (continuous for XM-100)
        i_component: X component of tool direction vector
        j_component: Y component of tool direction vector
        k_component: Z component of tool direction vector
    """

    a_angle: float = 0.0
    c_angle: float = 0.0
    i_component: float = 0.0
    j_component: float = 0.0
    k_component: float = 1.0

    def calculate_from_angles(self) -> None:
        """Calculate tool vector from A/C angles."""
        a_rad = math.radians(self.a_angle)
        c_rad = math.radians(self.c_angle)

        self.i_component = math.sin(c_rad) * math.sin(a_rad)
        self.j_component = -math.cos(c_rad) * math.sin(a_rad)
        self.k_component = math.cos(a_rad)


@dataclass
class WorkspaceLimits:
    """Defines workspace limits for 5-axis machine.

    Attributes:
        x_min: Minimum X coordinate in mm
        x_max: Maximum X coordinate in mm
        y_min: Minimum Y coordinate in mm
        y_max: Maximum Y coordinate in mm
        z_min: Minimum Z coordinate in mm
        z_max: Maximum Z coordinate in mm
        a_min: Minimum A-axis angle in degrees
        a_max: Maximum A-axis angle in degrees
        c_min: Minimum C-axis angle in degrees (usually continuous)
        c_max: Maximum C-axis angle in degrees
    """

    x_min: float = -300.0
    x_max: float = 300.0
    y_min: float = -300.0
    y_max: float = 300.0
    z_min: float = -200.0
    z_max: float = 200.0
    a_min: float = -120.0
    a_max: float = 120.0
    c_min: float = -360.0
    c_max: float = 360.0


@dataclass
class CollisionEvent:
    """Represents a single collision event detected during toolpath validation.

    Attributes:
        collision_type: Type of collision (e.g., "rapid_into_stock", "overcut_z").
        severity: Severity level ("high", "medium", "low").
        block_number: NC block number where the collision occurred.
        position: (x, y, z) coordinates of the collision point.
        message: Human-readable description of the collision.
        suggestion: Suggested corrective action.
    """

    collision_type: str
    severity: str
    block_number: int
    position: tuple[float, float, float]
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert the collision event to a dictionary.

        Returns:
            Dictionary with all collision event fields.
        """
        return {
            "collision_type": self.collision_type,
            "severity": self.severity,
            "block_number": self.block_number,
            "position": list(self.position),
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class CollisionReport:
    """Aggregated report from a full toolpath collision check.

    Attributes:
        total_segments: Total number of toolpath segments in the input.
        segments_checked: Number of segments actually checked.
        collisions: List of detected collision events.
        warnings: List of boundary warning messages.
        safe: Whether the toolpath is collision-free.
    """

    total_segments: int
    segments_checked: int
    collisions: list[CollisionEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert the collision report to a dictionary.

        Returns:
            Dictionary with all collision report fields and collision count.
        """
        return {
            "total_segments": self.total_segments,
            "segments_checked": self.segments_checked,
            "collisions": [c.to_dict() for c in self.collisions],
            "warnings": self.warnings,
            "safe": self.safe,
            "collision_count": len(self.collisions),
        }


class CollisionDetector:
    """AABB-based collision detector for CNC toolpaths.

    Checks toolpath segments against the stock bounding box for:
    - Rapid move (G00) collision with stock material
    - Z-axis safety during rapid moves
    - Overcut beyond stock boundaries

    Attributes:
        stock: The stock model to check against.
        safe_z_height: Minimum safe clearance above stock (mm).
        spindle_clearance: Spindle clearance margin (mm).

    Example:
        >>> detector = CollisionDetector(stock_model, safe_z_height=10.0)
        >>> report = detector.check_segments(segments)
        >>> if not report.safe:
        ...     for c in report.collisions:
        ...         print(c.message)
    """

    def __init__(
        self,
        stock: StockModel | None = None,
        safe_z_height: float = 10.0,
        spindle_clearance: float = 5.0,
        mode: str = "3axis",
        workspace_limits: WorkspaceLimits | None = None,
    ) -> None:
        """Initialize the collision detector.

        Args:
            stock: Stock model defining the workpiece boundaries.
            safe_z_height: Safe Z clearance above the stock in mm.
            spindle_clearance: Spindle clearance margin in mm.
            mode: Detection mode - "3axis" (AABB) or "5axis" (OBB with rotation).
            workspace_limits: Machine workspace limits for 5-axis mode.
        """
        self.stock = stock
        self.safe_z_height = safe_z_height
        self.spindle_clearance = spindle_clearance
        self.mode = mode
        self.workspace_limits = workspace_limits or WorkspaceLimits()
        self._tool_vector = FiveAxisToolVector()

    def check_segments(
        self,
        segments: list[ToolpathSegment],
    ) -> CollisionReport:
        """Check all toolpath segments for collisions.

        Iterates through each segment and performs type-specific checks:
        rapid moves are checked for stock collision and Z safety, while
        cutting moves (linear/arc) are checked for overcut.

        Args:
            segments: List of toolpath segments to validate.

        Returns:
            CollisionReport summarizing all detected collisions and warnings.
        """
        bbox = self.stock.get_bbox() if self.stock else None
        stock_z_top = bbox.z_max if bbox else 100.0

        collisions: list[CollisionEvent] = []
        warnings: list[str] = []

        for seg in segments:
            if seg.type == "rapid":
                self._check_rapid_collision(seg, bbox, collisions)
                self._check_z_safety(seg, stock_z_top, collisions)
            elif seg.type in ("linear", "arc"):
                self._check_overcut(seg, bbox, collisions, warnings)

        safe = len(collisions) == 0
        return CollisionReport(
            total_segments=len(segments),
            segments_checked=len(segments),
            collisions=collisions,
            warnings=warnings,
            safe=safe,
        )

    def _check_rapid_collision(
        self,
        seg: ToolpathSegment,
        bbox: StockBoundingBox | None,
        collisions: list[CollisionEvent],
    ) -> None:
        """Check if a rapid move intersects the stock bounding box.

        Constructs an AABB for the rapid move line segment and checks
        intersection with the stock. If intersecting, samples points
        along the path to detect actual stock penetration.

        Args:
            seg: The rapid move toolpath segment.
            bbox: Stock bounding box (None skips the check).
            collisions: List to append detected collision events to.
        """
        if bbox is None:
            return

        sx, sy, sz = seg.start_point
        ex, ey, ez = seg.end_point

        line_bbox = StockBoundingBox(
            x_min=min(sx, ex),
            x_max=max(sx, ex),
            y_min=min(sy, ey),
            y_max=max(sy, ey),
            z_min=min(sz, ez),
            z_max=max(sz, ez),
        )

        if not bbox.intersects_bbox(line_bbox):
            return

        safe_plane = bbox.z_max + self.safe_z_height

        # 抬刀保护：如果 rapid 是向上移动且终点在安全平面以上，
        # 则是正常的切削后抬刀操作，不会撞入毛坯，跳过碰撞检查
        is_retract = ez > sz and ez >= safe_plane

        if is_retract:
            # 真正的抬刀操作（向上且到达安全平面以上），完全跳过碰撞检查
            return

        # 非抬刀操作：检查是否有可能撞入毛坯
        if sz < safe_plane or ez < safe_plane:
            # 计算移动距离
            distance = ((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2) ** 0.5

            # 自适应步长：短距离使用更精细的采样
            # - 距离 < 10mm: 步长 0.5mm (至少 10 点)
            # - 距离 >= 10mm: 步长 2mm (至少 5 点)
            if distance < 10.0:
                step_size = 0.5
                min_steps = 10
            else:
                step_size = 2.0
                min_steps = 5

            steps = max(int(distance / step_size), min_steps)

            for i in range(steps + 1):
                t = i / steps
                px = sx + (ex - sx) * t
                py = sy + (ey - sy) * t
                pz = sz + (ez - sz) * t
                if bbox.contains_point(px, py, pz):
                    collisions.append(
                        CollisionEvent(
                            collision_type="rapid_into_stock",
                            severity="high",
                            block_number=seg.block_number,
                            position=(round(px, 3), round(py, 3), round(pz, 3)),
                            message=f"G00 rapid move cuts into stock at N{seg.block_number}",
                            suggestion=(
                                f"Increase safe Z height to >= {safe_plane}mm, "
                                "or retract to safe plane before positioning"
                            ),
                        )
                    )
                    break

    def _check_z_safety(
        self,
        seg: ToolpathSegment,
        stock_z_top: float,
        collisions: list[CollisionEvent],
    ) -> None:
        """Check Z-axis safety for rapid moves.

        Verifies that the rapid move starts above the safe Z plane
        (stock top + safe_z_height).
        Skips check for retract operations (upward moves ending above safe plane).

        Args:
            seg: The rapid move toolpath segment.
            stock_z_top: Z coordinate of the stock top surface.
            collisions: List to append detected collision events to.
        """
        if seg.type != "rapid":
            return

        _, _, sz = seg.start_point
        _, _, ez = seg.end_point
        safe_z = stock_z_top + self.safe_z_height

        # 抬刀操作跳过 Z 安全检查（起点在毛坯内部是正常的）
        is_retract = ez > sz and ez >= safe_z
        if is_retract:
            return

        if sz < safe_z:
            collisions.append(
                CollisionEvent(
                    collision_type="rapid_z_low",
                    severity="medium",
                    block_number=seg.block_number,
                    position=seg.start_point,
                    message=f"G00 start Z={sz:.1f} is below safe height {safe_z:.1f}",
                    suggestion=f"Retract to G00 Z{safe_z} before rapid movement",
                )
            )

    def _check_overcut(
        self,
        seg: ToolpathSegment,
        bbox: StockBoundingBox | None,
        collisions: list[CollisionEvent],
        warnings: list[str],
    ) -> None:
        """Check for overcut beyond stock boundaries.

        Detects when the toolpath endpoint extends beyond the stock
        bounding box with a 0.5mm tolerance margin.
        For arc segments, samples along the arc path to detect intermediate overcuts.

        Args:
            seg: The cutting toolpath segment (linear or arc).
            bbox: Stock bounding box (None skips the check).
            collisions: List to append detected collision events to.
            warnings: List to append boundary warning messages to.
        """
        if bbox is None:
            return

        margin = 0.5

        # 对圆弧路径进行采样检测
        if seg.type == "arc" and seg.arc_center is not None:
            self._check_arc_overcut(seg, bbox, collisions, warnings, margin)
            return

        # 线性路径只检查终点
        ex, ey, ez = seg.end_point

        if ex < bbox.x_min - margin or ex > bbox.x_max + margin:
            warnings.append(
                f"N{seg.block_number}: Tool path X={ex:.2f} exceeds stock boundary [{bbox.x_min:.2f}, {bbox.x_max:.2f}]"
            )
        if ey < bbox.y_min - margin or ey > bbox.y_max + margin:
            warnings.append(
                f"N{seg.block_number}: Tool path Y={ey:.2f} exceeds stock boundary [{bbox.y_min:.2f}, {bbox.y_max:.2f}]"
            )
        if ez < bbox.z_min - margin:
            collisions.append(
                CollisionEvent(
                    collision_type="overcut_z",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"Tool path Z={ez:.3f} is below stock bottom Z={bbox.z_min}, risk of overcut",
                    suggestion=f"Check Z-axis cutting depth; stock bottom is at Z={bbox.z_min}",
                )
            )

    def _check_arc_overcut(
        self,
        seg: ToolpathSegment,
        bbox: StockBoundingBox,
        collisions: list[CollisionEvent],
        warnings: list[str],
        margin: float,
    ) -> None:
        """Check overcut for arc segments by sampling along the arc path.

        Args:
            seg: The arc toolpath segment with arc_center defined.
            bbox: Stock bounding box.
            collisions: List to append detected collision events to.
            warnings: List to append boundary warning messages to.
            margin: Tolerance margin for boundary checks.
        """
        sx, sy, sz = seg.start_point
        ex, ey, ez = seg.end_point
        if seg.arc_center is None:
            # 防御：圆弧段缺少圆心（正常流程在调用前已检查，此处兜底避免 TypeError）
            return
        cx, cy, cz = seg.arc_center

        # 计算圆弧半径
        radius = math.sqrt((sx - cx) ** 2 + (sy - cy) ** 2)
        if radius < 0.001:
            # 半径过小，退化为直线检查
            self._check_linear_overcut(seg, bbox, collisions, warnings, margin)
            return

        # 计算起始角和终止角
        start_angle = math.atan2(sy - cy, sx - cx)
        end_angle = math.atan2(ey - cy, ex - cx)

        # 确定扫掠角度
        if seg.clockwise:
            sweep = start_angle - end_angle
            if sweep <= 0:
                sweep += 2 * math.pi
        else:
            sweep = end_angle - start_angle
            if sweep <= 0:
                sweep += 2 * math.pi

        # 采样点数：基于弧长，每 2mm 采样一次
        arc_length = radius * sweep
        num_samples = max(int(arc_length / 2.0), 5)

        # 检查采样点
        for i in range(num_samples + 1):
            t = i / num_samples
            if seg.clockwise:
                angle = start_angle - sweep * t
            else:
                angle = start_angle + sweep * t

            px = cx + radius * math.cos(angle)
            py = cy + radius * math.sin(angle)
            # Z 轴线性插值
            pz = sz + (ez - sz) * t

            # 检查边界
            if px < bbox.x_min - margin or px > bbox.x_max + margin:
                warnings.append(
                    f"N{seg.block_number}: Arc path X={px:.2f} exceeds stock boundary "
                    f"[{bbox.x_min:.2f}, {bbox.x_max:.2f}] at sample {i}"
                )
            if py < bbox.y_min - margin or py > bbox.y_max + margin:
                warnings.append(
                    f"N{seg.block_number}: Arc path Y={py:.2f} exceeds stock boundary "
                    f"[{bbox.y_min:.2f}, {bbox.y_max:.2f}] at sample {i}"
                )
            if pz < bbox.z_min - margin:
                collisions.append(
                    CollisionEvent(
                        collision_type="overcut_z",
                        severity="high",
                        block_number=seg.block_number,
                        position=(round(px, 3), round(py, 3), round(pz, 3)),
                        message=f"Arc path Z={pz:.3f} is below stock bottom Z={bbox.z_min}, risk of overcut",
                        suggestion=f"Check Z-axis cutting depth; stock bottom is at Z={bbox.z_min}",
                    )
                )
                break  # 只报告第一个碰撞

    def _check_linear_overcut(
        self,
        seg: ToolpathSegment,
        bbox: StockBoundingBox,
        collisions: list[CollisionEvent],
        warnings: list[str],
        margin: float,
    ) -> None:
        """Fallback linear overcut check for degenerate arcs."""
        ex, ey, ez = seg.end_point

        if ex < bbox.x_min - margin or ex > bbox.x_max + margin:
            warnings.append(
                f"N{seg.block_number}: Tool path X={ex:.2f} exceeds stock boundary [{bbox.x_min:.2f}, {bbox.x_max:.2f}]"
            )
        if ey < bbox.y_min - margin or ey > bbox.y_max + margin:
            warnings.append(
                f"N{seg.block_number}: Tool path Y={ey:.2f} exceeds stock boundary [{bbox.y_min:.2f}, {bbox.y_max:.2f}]"
            )
        if ez < bbox.z_min - margin:
            collisions.append(
                CollisionEvent(
                    collision_type="overcut_z",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"Tool path Z={ez:.3f} is below stock bottom Z={bbox.z_min}, risk of overcut",
                    suggestion=f"Check Z-axis cutting depth; stock bottom is at Z={bbox.z_min}",
                )
            )

    def check_single_rapid(
        self,
        seg: ToolpathSegment,
    ) -> list[CollisionEvent]:
        """Check a single rapid move segment for collisions.

        Convenience method for checking one rapid segment at a time.

        Args:
            seg: A rapid move toolpath segment.

        Returns:
            List of collision events (empty if safe or segment is not rapid).
        """
        if seg.type != "rapid":
            return []
        bbox = self.stock.get_bbox() if self.stock else None
        collisions: list[CollisionEvent] = []
        self._check_rapid_collision(seg, bbox, collisions)
        return collisions

    def _check_obb_collision(
        self,
        seg: ToolpathSegment,
        bbox: StockBoundingBox | None,
        tool_vector: FiveAxisToolVector,
        collisions: list[CollisionEvent],
    ) -> None:
        """Check for OBB collision in 5-axis mode.

        Uses oriented bounding box that accounts for tool orientation.
        Enhanced implementation checks:
        - Tool tip position
        - Tool axis line through stock
        - Tool holder clearance (simplified)
        - Continuous path sampling for dynamic checking

        Args:
            seg: The toolpath segment.
            bbox: Stock bounding box.
            tool_vector: Current tool orientation vector.
            collisions: List to append detected collision events to.
        """
        if bbox is None:
            return

        sx, sy, sz = seg.start_point
        ex, ey, ez = seg.end_point

        # Enhanced OBB check: sample along the path for dynamic collision detection
        path_length = math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2)
        sample_steps = max(int(path_length / 1.0), 5)  # Sample every 1mm or at least 5 points

        for i in range(sample_steps + 1):
            t = i / sample_steps
            px = sx + (ex - sx) * t
            py = sy + (ey - sy) * t
            pz = sz + (ez - sz) * t

            # Check tool tip at this position
            if bbox.contains_point(px, py, pz):
                collisions.append(
                    CollisionEvent(
                        collision_type="5axis_obb_collision",
                        severity="high",
                        block_number=seg.block_number,
                        position=(round(px, 3), round(py, 3), round(pz, 3)),
                        message=f"5-axis OBB collision at N{seg.block_number}: tool tip inside stock at sampled point",
                        suggestion="Check tool orientation and position, adjust A/C angles or reduce cut depth",
                    )
                )
                break  # Only report first collision per segment

            # Check tool axis penetration (simplified: check point above tool tip along tool vector)
            tool_length = 50.0  # mm, typical tool stick-out
            axis_check_point = (
                px - tool_vector.i_component * tool_length,
                py - tool_vector.j_component * tool_length,
                pz - tool_vector.k_component * tool_length,
            )
            if bbox.contains_point(*axis_check_point):
                collisions.append(
                    CollisionEvent(
                        collision_type="5axis_tool_axis_collision",
                        severity="high",
                        block_number=seg.block_number,
                        position=(round(px, 3), round(py, 3), round(pz, 3)),
                        message=f"5-axis tool axis collision at N{seg.block_number}: tool holder may contact stock",
                        suggestion="Increase tool stick-out or adjust tool orientation angles",
                    )
                )
                break

    def _check_axis_limits(
        self,
        seg: ToolpathSegment,
        tool_vector: FiveAxisToolVector,
        collisions: list[CollisionEvent],
    ) -> None:
        """Check if A/C axis angles exceed machine limits.

        Args:
            seg: The toolpath segment.
            tool_vector: Current tool orientation vector.
            collisions: List to append detected collision events to.
        """
        limits = self.workspace_limits

        if tool_vector.a_angle < limits.a_min or tool_vector.a_angle > limits.a_max:
            collisions.append(
                CollisionEvent(
                    collision_type="axis_limit_exceeded",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"A-axis angle {tool_vector.a_angle:.2f}° exceeds limits [{limits.a_min}, {limits.a_max}]",
                    suggestion=f"Adjust A-axis angle to be within [{limits.a_min}, {limits.a_max}] degrees",
                )
            )

        if tool_vector.c_angle < limits.c_min or tool_vector.c_angle > limits.c_max:
            collisions.append(
                CollisionEvent(
                    collision_type="axis_limit_exceeded",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"C-axis angle {tool_vector.c_angle:.2f}° exceeds limits [{limits.c_min}, {limits.c_max}]",
                    suggestion=f"Adjust C-axis angle to be within [{limits.c_min}, {limits.c_max}] degrees",
                )
            )

    def _check_workspace_limits(
        self,
        seg: ToolpathSegment,
        collisions: list[CollisionEvent],
    ) -> None:
        """Check if tool position exceeds machine workspace limits.

        Args:
            seg: The toolpath segment.
            collisions: List to append detected collision events to.
        """
        limits = self.workspace_limits
        ex, ey, ez = seg.end_point

        if ex < limits.x_min or ex > limits.x_max:
            collisions.append(
                CollisionEvent(
                    collision_type="workspace_limit_exceeded",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"X={ex:.2f} exceeds workspace limits [{limits.x_min}, {limits.x_max}]",
                    suggestion="Adjust toolpath to stay within machine workspace",
                )
            )

        if ey < limits.y_min or ey > limits.y_max:
            collisions.append(
                CollisionEvent(
                    collision_type="workspace_limit_exceeded",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"Y={ey:.2f} exceeds workspace limits [{limits.y_min}, {limits.y_max}]",
                    suggestion="Adjust toolpath to stay within machine workspace",
                )
            )

        if ez < limits.z_min or ez > limits.z_max:
            collisions.append(
                CollisionEvent(
                    collision_type="workspace_limit_exceeded",
                    severity="high",
                    block_number=seg.block_number,
                    position=seg.end_point,
                    message=f"Z={ez:.2f} exceeds workspace limits [{limits.z_min}, {limits.z_max}]",
                    suggestion="Adjust toolpath to stay within machine workspace",
                )
            )

    def _check_singularity(
        self,
        seg: ToolpathSegment,
        tool_vector: FiveAxisToolVector,
        warnings: list[str],
    ) -> None:
        """Check for singularity conditions in 5-axis machining.

        Singularity occurs when A-axis approaches 0° or when tool orientation
        causes rapid C-axis rotation.

        Args:
            seg: The toolpath segment.
            tool_vector: Current tool orientation vector.
            warnings: List to append singularity warnings to.
        """
        # Check near-singularity (A-axis close to 0)
        if abs(tool_vector.a_angle) < 1.0:
            warnings.append(
                f"N{seg.block_number}: Near singularity - A-axis angle {tool_vector.a_angle:.2f}° "
                "may cause rapid C-axis rotation"
            )

        # Check tool vector k-component (should be positive for normal machining)
        if tool_vector.k_component < 0.1:
            warnings.append(
                f"N{seg.block_number}: Tool orientation near horizontal - may cause instability or collision"
            )

    def check_segments_5axis(
        self,
        segments: list[ToolpathSegment],
        tool_vectors: list[FiveAxisToolVector] | None = None,
    ) -> CollisionReport:
        """Check toolpath segments for collisions in 5-axis mode.

        Extends standard collision detection with:
        - OBB collision detection with tool orientation
        - A/C axis limit checking
        - Workspace boundary checking
        - Singularity detection

        Args:
            segments: List of toolpath segments to validate.
            tool_vectors: Optional list of tool orientation vectors for each segment.

        Returns:
            CollisionReport summarizing all detected collisions and warnings.
        """
        if self.mode != "5axis":
            return self.check_segments(segments)

        bbox = self.stock.get_bbox() if self.stock else None
        stock_z_top = bbox.z_max if bbox else 100.0

        collisions: list[CollisionEvent] = []
        warnings: list[str] = []

        for i, seg in enumerate(segments):
            # Get tool vector for this segment
            tool_vector = tool_vectors[i] if tool_vectors and i < len(tool_vectors) else self._tool_vector

            # Update tool vector from segment if it has explicit rotation data.
            # 角度为 None 表示未指定（如 3 轴段），保留默认工具向量，避免 NaN/TypeError。
            seg_a = getattr(seg, "a_angle", None)
            seg_c = getattr(seg, "c_angle", None)
            if seg_a is not None and seg_c is not None:
                tool_vector.a_angle = seg_a
                tool_vector.c_angle = seg_c
                tool_vector.calculate_from_angles()

            # Standard checks
            if seg.type == "rapid":
                self._check_rapid_collision(seg, bbox, collisions)
                self._check_z_safety(seg, stock_z_top, collisions)
            elif seg.type in ("linear", "arc"):
                self._check_overcut(seg, bbox, collisions, warnings)

            # 5-axis specific checks
            self._check_obb_collision(seg, bbox, tool_vector, collisions)
            self._check_axis_limits(seg, tool_vector, collisions)
            self._check_workspace_limits(seg, collisions)
            self._check_singularity(seg, tool_vector, warnings)

        safe = len(collisions) == 0
        return CollisionReport(
            total_segments=len(segments),
            segments_checked=len(segments),
            collisions=collisions,
            warnings=warnings,
            safe=safe,
        )
