"""Plane 识别逻辑 mixin（从 plane_recognizer 拆出）。"""

from __future__ import annotations

from typing import Any

from app.core.error_taxonomy import ErrorCategory, ManufacturingError
from app.process_planning._plane_models import PlaneFeature, PlaneRecognitionResult


class _PlaneRecognizeMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    MIN_PLANE_AREA: Any

    def recognize_from_part_description(
        self,
        part_description: dict[str, Any],
    ) -> PlaneRecognitionResult:
        """Recognize plane features from a part description dictionary.

        Args:
            part_description: Part description dictionary containing 'planes',
                'features', or 'contours' fields.

        Returns:
            PlaneRecognitionResult with all recognized plane features.

        Raises:
            ManufacturingError: If the part description is empty or None.
        """
        if not part_description:
            raise ManufacturingError(
                category=ErrorCategory.FEATURE_RECOGNITION_INCOMPLETE,
                detail="零件描述数据不能为空",
            )

        planes: list[PlaneFeature] = []
        warnings: list[str] = []
        errors: list[str] = []
        accuracy_metrics: dict[str, float] = {"overall": 0.99}

        raw_planes = part_description.get("planes", [])
        raw_features = part_description.get("features", [])
        plane_features = [
            f
            for f in raw_features
            if f.get("geometric_type") in ("plane", "planar_surface", "end_face")
            or f.get("type") in ("top_plane", "bottom_plane", "side_plane", "end_face", "plane")
        ]
        for pf in plane_features:
            pos = pf.get("position", {})
            dims = pf.get("dimensions", {})
            normal = pf.get("normal", pf.get("normal_vector", [0, 0, 1]))
            raw_planes.append(
                {
                    "id": pf.get("name", f"P{len(raw_planes) + 1:03d}"),
                    "type": pf.get("type", "top_plane"),
                    "position": pos,
                    "length": dims.get("length", pf.get("length", 0)),
                    "width": dims.get("width", pf.get("width", 0)),
                    "area": dims.get("area", pf.get("area", 0)),
                    "normal": normal,
                    "tolerance_grade": pf.get("tolerance_grade", "IT8"),
                    "surface": pf.get("surface", "A"),
                    "is_datum_candidate": pf.get("is_datum_candidate", False),
                    "boundary": pf.get("boundary", pf.get("boundary_contour", [])),
                }
            )

        contours = part_description.get("contours", part_description.get("entities", []))
        for i, contour in enumerate(contours):
            if self._is_plane_contour(contour):
                fig_id = f"P{i + 1:03d}"
                if not any(p.get("id") == fig_id for p in raw_planes):
                    normal = contour.get("normal", contour.get("normal_vector", [0, 0, 1]))
                    if not isinstance(normal, list) or len(normal) != 3:
                        normal = [0, 0, 1]
                    plane_type = self._classify_plane_type(normal)

                    boundary = contour.get("boundary", contour.get("vertices", contour.get("points", [])))
                    length, width = self._estimate_dimensions(boundary)

                    raw_planes.append(
                        {
                            "id": fig_id,
                            "type": plane_type,
                            "position": contour.get("center", contour.get("position", {})),
                            "length": contour.get("length", length),
                            "width": contour.get("width", width),
                            "area": contour.get("area", length * width),
                            "normal": normal,
                            "boundary": boundary,
                            "surface": contour.get("surface", "A"),
                        }
                    )

        if not raw_planes:
            warnings.append("未找到任何平面特征定义")

        for i, raw in enumerate(raw_planes):
            try:
                plane_id = raw.get("id", raw.get("plane_id", f"P{i + 1:03d}"))
                plane_type = raw.get("type", "top_plane")

                pos = raw.get("position", {})
                if isinstance(pos, (list, tuple)):
                    pos = {
                        "x": pos[0] if len(pos) > 0 else 0,
                        "y": pos[1] if len(pos) > 1 else 0,
                        "z": pos[2] if len(pos) > 2 else 0,
                    }

                center_x = float(pos.get("x", 0))
                center_y = float(pos.get("y", 0))
                center_z = float(pos.get("z", 0))

                normal = raw.get("normal", raw.get("normal_vector", [0.0, 0.0, 1.0]))
                if not isinstance(normal, list) or len(normal) != 3:
                    normal = [0.0, 0.0, 1.0]

                normal_x = float(normal[0])
                normal_y = float(normal[1])
                normal_z = float(normal[2])

                area = float(raw.get("area", 0))
                length = float(raw.get("length", 0))
                width = float(raw.get("width", 0))

                if area <= 0 and length > 0 and width > 0:
                    area = length * width
                elif area > 0 and length <= 0 and width > 0:
                    length = area / width
                elif area > 0 and width <= 0 and length > 0:
                    width = area / length

                if area <= 0:
                    errors.append(f"平面 {plane_id} 面积无效: {area}mm²")
                    continue

                if area < self.MIN_PLANE_AREA:
                    warnings.append(
                        f"平面 {plane_id} 面积过小 ({area:.2f}mm²)，低于最小可识别面积 {self.MIN_PLANE_AREA}mm²"
                    )

                boundary = raw.get("boundary", raw.get("boundary_contour", []))
                if isinstance(boundary, list) and boundary and isinstance(boundary[0], dict):
                    boundary = [[float(pt.get("x", 0)), float(pt.get("y", 0))] for pt in boundary]

                if not isinstance(boundary, list):
                    boundary = []

                if plane_type not in ("top_plane", "bottom_plane", "side_plane"):
                    plane_type = self._classify_plane_type([normal_x, normal_y, normal_z])

                plane = PlaneFeature(
                    plane_id=str(plane_id),
                    type=plane_type,
                    area=area,
                    normal_x=normal_x,
                    normal_y=normal_y,
                    normal_z=normal_z,
                    length=length,
                    width=width,
                    center_x=center_x,
                    center_y=center_y,
                    center_z=center_z,
                    boundary=boundary,
                    surface=str(raw.get("surface", "A")),
                    tolerance_grade=str(raw.get("tolerance_grade", "IT8")),
                    surface_roughness_ra=float(raw.get("surface_roughness_ra", 3.2)),
                    is_datum_candidate=bool(raw.get("is_datum_candidate", False)),
                    metadata=raw.get("metadata", {}),
                )
                planes.append(plane)

            except (ValueError, TypeError, KeyError) as e:
                errors.append(f"解析平面条目 {raw.get('id', i)} 时出错: {type(e).__name__}")
                continue

        type_summary: dict[str, int] = {}
        for p in planes:
            type_summary[p.type] = type_summary.get(p.type, 0) + 1

        accuracy_metrics["recognized_count"] = float(len(planes))
        if raw_planes:
            recognition_rate = len(planes) / max(len(raw_planes), 1)
            accuracy_metrics["recognition_rate"] = min(recognition_rate, 1.0)
            accuracy_metrics["overall"] = accuracy_metrics["recognition_rate"]

        return PlaneRecognitionResult(
            planes=planes,
            total_count=len(planes),
            type_summary=type_summary,
            warnings=warnings,
            errors=errors,
            accuracy_metrics=accuracy_metrics,
        )

    def recognize_from_contours(
        self,
        contours: list[dict[str, Any]],
    ) -> PlaneRecognitionResult:
        """Recognize plane features from a list of contour dictionaries.

        Args:
            contours: List of contour dictionaries with plane geometry data.

        Returns:
            PlaneRecognitionResult with recognized plane features.
        """
        part_description = {"contours": contours}
        return self.recognize_from_part_description(part_description)

    def _is_plane_contour(self, contour: dict[str, Any]) -> bool:
        """Check whether a contour represents a plane feature.

        Args:
            contour: Contour dictionary with 'shape', 'type', or 'normal' fields.

        Returns:
            True if the contour matches a known plane type or has a normal vector.
        """
        shape = contour.get("shape", contour.get("type", ""))
        if shape in ("plane", "planar_surface", "face", "rectangle"):
            return True
        normal = contour.get("normal", contour.get("normal_vector"))
        if normal is not None:
            return True
        return False

    def _classify_plane_type(self, normal: list[float]) -> str:
        """Classify a plane as top, bottom, or side based on its normal vector.

        Args:
            normal: 3-component normal vector [nx, ny, nz].

        Returns:
            Plane type string: 'top_plane', 'bottom_plane', or 'side_plane'.
        """
        if len(normal) < 3:
            return "top_plane"
        nz = abs(normal[2])
        nx = abs(normal[0])
        ny = abs(normal[1])

        if nz >= max(nx, ny) or nz > 0.9:
            return "top_plane" if normal[2] > 0 else "bottom_plane"
        return "side_plane"

    def _estimate_dimensions(
        self,
        boundary: list[list[float]] | list[dict[str, float]],
    ) -> tuple[float, float]:
        """Estimate the length and width of a plane from its boundary points.

        Args:
            boundary: List of boundary points as either [[x, y], ...] or
                [{'x': ..., 'y': ...}, ...].

        Returns:
            Tuple of (length, width) in mm. Returns (0.0, 0.0) if boundary is empty.
        """
        if not boundary:
            return 0.0, 0.0

        xs: list[float] = []
        ys: list[float] = []
        for pt in boundary:
            if isinstance(pt, (list, tuple)):
                xs.append(float(pt[0]) if len(pt) > 0 else 0.0)
                ys.append(float(pt[1]) if len(pt) > 1 else 0.0)
            elif isinstance(pt, dict):
                xs.append(float(pt.get("x", 0)))
                ys.append(float(pt.get("y", 0)))

        if not xs or not ys:
            return 0.0, 0.0

        length = max(xs) - min(xs)
        width = max(ys) - min(ys)
        return length, width
