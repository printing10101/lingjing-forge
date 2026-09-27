"""Datum selection module.

Implements datum feature selection and validation based on the 6-point
locating principle (3-2-1 rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.process_planning.feature_dependency import MachiningFeature


@dataclass
class DatumCandidate:
    """A candidate feature for use as a locating datum.

    Attributes:
        feature: The underlying machining feature.
        area: Feature area in mm².
        accuracy_score: Score based on tolerance grade (0-100).
        stability_score: Score based on geometric stability (0-100).
        accessibility_score: Score based on tool accessibility (0-100).
    """

    feature: MachiningFeature
    area: float
    accuracy_score: float
    stability_score: float
    accessibility_score: float

    def total_score(self) -> float:
        """Calculate the weighted total score for datum suitability.

        Weights: accuracy (35%), stability (30%), accessibility (20%),
        and area (15%).

        Returns:
            Weighted total score (0-100).
        """
        return (
            self.accuracy_score * 0.35
            + self.stability_score * 0.30
            + self.accessibility_score * 0.20
            + min(self.area / 10000, 1.0) * 10 * 0.15
        )


@dataclass
class DatumSelection:
    """Complete datum selection result following the 3-2-1 principle.

    Attributes:
        primary_datum: Primary datum candidate (constrains 3 DOF).
        secondary_datum: Secondary datum candidate (constrains 2 DOF).
        tertiary_datum: Tertiary datum candidate (constrains 1 DOF).
        locating_method: Locating method description.
        total_score: Overall selection score.
        degrees_constrained: Total degrees of freedom constrained.
        reasoning: List of reasoning strings explaining the selection.
    """

    primary_datum: DatumCandidate | None = None
    secondary_datum: DatumCandidate | None = None
    tertiary_datum: DatumCandidate | None = None
    locating_method: str = ""
    total_score: float = 0.0
    degrees_constrained: int = 0
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the datum selection to a dictionary representation.

        Returns:
            A dictionary containing selected datums, locating method,
            total score, degrees constrained, and reasoning.
        """
        return {
            "primary_datum": self.primary_datum.feature.name if self.primary_datum else None,
            "secondary_datum": self.secondary_datum.feature.name if self.secondary_datum else None,
            "tertiary_datum": self.tertiary_datum.feature.name if self.tertiary_datum else None,
            "locating_method": self.locating_method,
            "total_score": round(self.total_score, 2),
            "degrees_constrained": self.degrees_constrained,
            "reasoning": self.reasoning,
        }


class DatumSelector:
    """Datum selector that chooses optimal locating features.

    Scores candidate features based on accuracy, stability, accessibility,
    and area, then selects primary/secondary/tertiary datums following
    the 3-2-1 locating principle. Supports both shaft and prismatic parts.
    """

    def select_datums(
        self,
        features: list[MachiningFeature],
        tolerance_requirements: dict[str, float] | None = None,
        material: str = "",
        blank_type: str = "",
        part_type: str = "general",
    ) -> DatumSelection:
        candidates = self._score_candidates(features)

        result = DatumSelection()

        if part_type in ("shaft", "stepped_shaft", "cylinder"):
            return self._select_for_shaft(candidates, result)
        else:
            return self._select_for_prismatic(candidates, result, part_type)

    def _score_candidates(
        self,
        features: list[MachiningFeature],
    ) -> list[DatumCandidate]:
        candidates: list[DatumCandidate] = []
        for f in features:
            if not f.is_datum_candidate:
                continue

            area = f.dimensions.get("area", f.dimensions.get("diameter", 10) ** 2 * 0.785)

            acc = 0.0
            grade_map = {
                "IT5": 100,
                "IT6": 90,
                "IT7": 75,
                "IT8": 50,
                "IT9": 30,
                "IT10": 15,
            }
            acc = grade_map.get(f.tolerance_grade, 20)

            stability = 0.0
            if f.geometric_type == "plane":
                stability = 85
            elif f.geometric_type == "cylinder" and f.type in ("outer_cylinder",):
                stability = 70
            elif f.geometric_type == "cylinder" and f.type in (
                "inner_bore",
                "through_hole",
            ):
                stability = 50
            elif f.type in ("center_hole",):
                stability = 60
            else:
                stability = 30

            if area > 5000:
                stability += 10
            if area > 10000:
                stability = min(stability + 10, 100)

            accessibility = 80
            if f.type in ("end_face", "outer_cylinder", "center_hole"):
                accessibility = 95

            candidates.append(
                DatumCandidate(
                    feature=f,
                    area=area,
                    accuracy_score=acc,
                    stability_score=stability,
                    accessibility_score=accessibility,
                )
            )

        candidates.sort(key=lambda c: c.total_score(), reverse=True)
        return candidates

    def _select_for_shaft(
        self,
        candidates: list[DatumCandidate],
        result: DatumSelection,
    ) -> DatumSelection:
        end_faces = [c for c in candidates if c.feature.type == "end_face"]
        cyls = [c for c in candidates if c.feature.type == "outer_cylinder"]
        centers = [c for c in candidates if c.feature.type == "center_hole"]

        result.locating_method = "双顶尖定位" if len(centers) >= 2 else "三爪卡盘+顶尖"

        if centers:
            result.primary_datum = centers[0]
            result.reasoning.append(
                f"选择{centers[0].feature.name}作为主基准：中心孔提供精确定位，"
                f"精度等级{centers[0].feature.tolerance_grade}"
            )
            result.degrees_constrained += 3
            if len(centers) >= 2:
                result.secondary_datum = centers[1]
                result.degrees_constrained += 2
                result.reasoning.append(f"选择{centers[1].feature.name}作为第二基准：尾座顶尖配合限制轴向自由度")
        elif end_faces:
            result.primary_datum = end_faces[0]
            result.degrees_constrained += 3
            result.reasoning.append(
                f"选择{end_faces[0].feature.name}作为主基准：端面提供轴向定位，面积{end_faces[0].area:.0f}mm²"
            )

        if cyls and not result.secondary_datum:
            result.secondary_datum = cyls[0]
            result.degrees_constrained += 2
            result.reasoning.append(f"选择{cyls[0].feature.name}作为辅助基准：外圆提供径向定位")

        if result.degrees_constrained < 6:
            remaining = [c for c in candidates if c not in ([result.primary_datum, result.secondary_datum])]
            if remaining:
                result.tertiary_datum = remaining[0]
                result.degrees_constrained += 1

        result.total_score = (
            (result.primary_datum.total_score() if result.primary_datum else 0) * 0.5
            + (result.secondary_datum.total_score() if result.secondary_datum else 0) * 0.3
            + (result.tertiary_datum.total_score() if result.tertiary_datum else 0) * 0.2
        )
        return result

    def _select_for_prismatic(
        self,
        candidates: list[DatumCandidate],
        result: DatumSelection,
        part_type: str = "general",
    ) -> DatumSelection:
        planes = [c for c in candidates if c.feature.geometric_type == "plane"]
        planes.sort(key=lambda c: c.area, reverse=True)

        if len(planes) >= 3:
            result.primary_datum = planes[0]
            result.secondary_datum = planes[1]
            result.tertiary_datum = planes[2]
            result.locating_method = "一面两销" if part_type in ("plate", "flange", "bracket") else "三面定位"
            result.degrees_constrained = 6

            result.reasoning.append(
                f"选择{planes[0].feature.name}(面积{planes[0].area:.0f}mm²)作为主基准："
                f"最大平面，限制3个自由度(Z移动, X转动, Y转动)"
            )
            result.reasoning.append(
                f"选择{planes[1].feature.name}(面积{planes[1].area:.0f}mm²)作为第二基准：限制2个自由度(X移动, Z转动)"
            )
            result.reasoning.append(f"选择{planes[2].feature.name}作为第三基准：限制1个自由度(Y移动)")
        elif planes:
            result.primary_datum = planes[0]
            result.degrees_constrained += 3
            result.locating_method = "一面两销"
            result.reasoning.append(
                f"选择{planes[0].feature.name}作为主基准，平面不足3个，建议采用一面两销方式补充定位"
            )

            holes = [c for c in candidates if c.feature.is_hole() and c.feature.is_datum_candidate]
            if holes:
                result.secondary_datum = holes[0]
                result.degrees_constrained += 2
                result.reasoning.append(f"选择{result.secondary_datum.feature.name}作为第二基准：销孔定位")
        else:
            result.locating_method = "虎钳装夹"
            if candidates:
                result.primary_datum = candidates[0]
                result.degrees_constrained += 5
                result.reasoning.append("平面基准不足，采用虎钳装夹限制5个自由度")

        result.total_score = (
            (result.primary_datum.total_score() if result.primary_datum else 0) * 0.5
            + (result.secondary_datum.total_score() if result.secondary_datum else 0) * 0.3
            + (result.tertiary_datum.total_score() if result.tertiary_datum else 0) * 0.2
        )
        return result

    def validate_datums(
        self,
        selection: DatumSelection,
        features: list[MachiningFeature],
    ) -> dict[str, Any]:
        issues: list[str] = []
        warnings: list[str] = []

        if selection.degrees_constrained < 6:
            warnings.append(f"定位方案仅限制{selection.degrees_constrained}个自由度，建议限制全部6个自由度")

        if selection.primary_datum is None:
            issues.append("未选择主定位基准")
        else:
            pf = selection.primary_datum.feature
            if pf.tolerance_grade not in ("IT5", "IT6", "IT7"):
                warnings.append(
                    f"主基准{selection.primary_datum.feature.name}精度等级为{pf.tolerance_grade}，建议使用IT7以上精度"
                )

        feature_names = {f.name for f in features}
        for datum in [
            selection.primary_datum,
            selection.secondary_datum,
            selection.tertiary_datum,
        ]:
            if datum is None:
                continue
            if datum.feature.name not in feature_names:
                issues.append(f"基准特征{datum.feature.name}不在零件特征列表中")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
        }
