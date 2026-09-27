"""Boss feature recognition module.

Recognizes boss features (protruding features) from DXF parsing data,
including circular bosses, rectangular bosses, and stepped bosses.
Supports detection of coaxial circular patterns and merges them into stepped bosses.

Recognition workflow:
1. Iterate through input contours/entities, distinguish circular and rectangular profiles
2. Extract dimensions for each boss: diameter/side length, height, position
3. Detect coaxial circular patterns and merge into stepped bosses
4. Extract tolerance grades and other manufacturing attributes
5. Output a unified list of BossFeature objects

本模块为门面：实现已拆分至 _boss_models / _boss_recognize_mixin。
"""

from __future__ import annotations

from typing import Any

from app.process_planning._boss_models import (  # noqa: F401
    BossFeature,
    BossRecognitionResult,
    BossStep,
)
from app.process_planning._boss_recognize_mixin import _BossRecognizeMixin


class BossRecognizer(_BossRecognizeMixin):
    """Boss feature recognizer.

    Recognizes boss features from part description data, including
    circular, rectangular, and stepped bosses.

    Attributes:
        MIN_BOSS_DIAMETER: Minimum recognizable boss diameter (mm).
        MIN_BOSS_HEIGHT: Minimum recognizable boss height (mm).
        COAXIAL_THRESHOLD: Threshold for coaxial determination (mm).
    """

    MIN_BOSS_DIAMETER = 1.0
    MIN_BOSS_HEIGHT = 0.1
    COAXIAL_THRESHOLD = 0.05

    def validate_result(
        self,
        result: BossRecognitionResult,
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        """Validate the boss recognition result.

        Checks include:
        1. Boss count matches expected (if provided)
        2. All circular boss diameters are positive
        3. All boss heights are positive
        4. Position coordinates are valid (no NaN/Infinity)
        5. Stepped bosses have at least 2 steps
        6. No unhandled errors from recognition

        Args:
            result: Recognition result to validate.
            expected_count: Expected total number of bosses (optional).

        Returns:
            Validation report dictionary with 'is_valid', 'issues', and
            'passed_checks' keys.
        """
        issues: list[str] = []
        passed: list[str] = []

        if expected_count is not None:
            if result.total_count == expected_count:
                passed.append(f"凸台数量匹配: {result.total_count} == {expected_count}")
            else:
                issues.append(f"凸台数量不匹配: 识别到{result.total_count}个，期望{expected_count}个")
        else:
            passed.append(f"凸台总数: {result.total_count}")

        invalid_diameter = [b for b in result.bosses if b.is_circular() and b.diameter <= 0]
        if invalid_diameter:
            issues.append(
                f"{len(invalid_diameter)}个圆形凸台直径无效: {', '.join(b.boss_id for b in invalid_diameter)}"
            )
        else:
            passed.append("所有圆形凸台直径有效")

        invalid_height = [b for b in result.bosses if b.height <= 0]
        if invalid_height:
            issues.append(f"{len(invalid_height)}个凸台高度无效: {', '.join(b.boss_id for b in invalid_height)}")
        else:
            passed.append("所有凸台高度有效")

        import math

        invalid_pos = [
            b
            for b in result.bosses
            if any(math.isnan(v) or math.isinf(v) for v in [b.center_x, b.center_y, b.center_z])
        ]
        if invalid_pos:
            issues.append(f"{len(invalid_pos)}个凸台位置坐标无效")
        else:
            passed.append("所有凸台位置坐标有效")

        invalid_steps = [b for b in result.bosses if b.is_stepped() and len(b.steps) < 2]
        if invalid_steps:
            issues.append(f"{len(invalid_steps)}个阶梯凸台阶梯数不足: {', '.join(b.boss_id for b in invalid_steps)}")
        else:
            passed.append("所有阶梯凸台阶梯数有效")

        if result.errors:
            issues.append(f"识别过程中有{len(result.errors)}个错误")
        else:
            passed.append("识别过程无错误")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "passed_checks": passed,
        }
