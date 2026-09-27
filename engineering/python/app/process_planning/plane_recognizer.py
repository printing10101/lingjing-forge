"""Plane feature recognition module.

Recognizes planar surface features from DXF parsing data for subsequent
datum selection and machining strategy planning. Supports simultaneous
recognition of multiple plane features, distinguishing top/bottom/side surfaces.

Recognition workflow:
1. Iterate through input contours/entities, filter planar type contours
2. Extract area, normal vector, and boundary outline for each plane
3. Classify planes as top/bottom/side by normal vector direction
4. Calculate nominal dimensions (length, width) for each plane
5. Output a unified list of PlaneFeature objects

本模块为门面：实现已拆分至 _plane_models / _plane_recognize_mixin。
"""

from __future__ import annotations

from typing import Any

from app.process_planning._plane_models import (  # noqa: F401
    PlaneFeature,
    PlaneRecognitionResult,
)
from app.process_planning._plane_recognize_mixin import _PlaneRecognizeMixin


class PlaneRecognizer(_PlaneRecognizeMixin):
    """Plane feature recognizer.

    Recognizes planar surface features from part description data,
    classifying them as top, bottom, or side surfaces based on normal vectors.

    Attributes:
        MIN_PLANE_AREA: Minimum recognizable plane area (mm²).
        MAX_PLANE_AREA: Maximum recognizable plane area (mm²).
    """

    MIN_PLANE_AREA = 1.0
    MAX_PLANE_AREA = 1_000_000.0

    def validate_result(
        self,
        result: PlaneRecognitionResult,
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        """Validate the plane recognition result.

        Checks include:
        1. Plane count matches expected (if provided)
        2. All plane areas are positive
        3. All normal vectors are non-zero
        4. Position coordinates are valid (no NaN/Infinity)
        5. No unhandled errors from recognition

        Args:
            result: Recognition result to validate.
            expected_count: Expected total number of planes (optional).

        Returns:
            Validation report dictionary with 'is_valid', 'issues', and
            'passed_checks' keys.
        """
        issues: list[str] = []
        passed: list[str] = []

        if expected_count is not None:
            if result.total_count == expected_count:
                passed.append(f"平面数量匹配: {result.total_count} == {expected_count}")
            else:
                issues.append(f"平面数量不匹配: 识别到{result.total_count}个，期望{expected_count}个")
        else:
            passed.append(f"平面总数: {result.total_count}")

        invalid_area = [p for p in result.planes if p.area <= 0]
        if invalid_area:
            issues.append(f"{len(invalid_area)}个平面面积无效: {', '.join(p.plane_id for p in invalid_area)}")
        else:
            passed.append("所有平面面积有效")

        import math

        invalid_normal = [p for p in result.planes if all(abs(v) < 0.001 for v in [p.normal_x, p.normal_y, p.normal_z])]
        if invalid_normal:
            issues.append(f"{len(invalid_normal)}个平面法向量为零: {', '.join(p.plane_id for p in invalid_normal)}")
        else:
            passed.append("所有平面法向量有效")

        invalid_pos = [
            p
            for p in result.planes
            if any(math.isnan(v) or math.isinf(v) for v in [p.center_x, p.center_y, p.center_z])
        ]
        if invalid_pos:
            issues.append(f"{len(invalid_pos)}个平面位置坐标无效")
        else:
            passed.append("所有平面位置坐标有效")

        if result.errors:
            issues.append(f"识别过程中有{len(result.errors)}个错误")
        else:
            passed.append("识别过程无错误")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "passed_checks": passed,
        }
