"""Fixture analysis module.

Implements fixture plan evaluation and optimization, recommending the
best clamping method based on a standard template library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.process_planning.feature_dependency import MachiningFeature, Setup


@dataclass
class FixtureRecommendation:
    """A recommended fixture configuration for a machining setup.

    Attributes:
        fixture_id: Unique fixture identifier.
        fixture_name: Human-readable fixture name.
        fixture_type: Fixture category/type.
        locating_method: Locating method description.
        clamping_points: List of clamping point dictionaries.
        clamping_force_n: Clamping force in Newtons.
        degrees_constrained: Number of degrees of freedom constrained.
        precautions: List of operational precautions.
        suitability_score: Suitability score (0-100).
        reasoning: List of reasoning strings explaining the recommendation.
    """

    fixture_id: str
    fixture_name: str
    fixture_type: str
    locating_method: str
    clamping_points: list[dict[str, Any]]
    clamping_force_n: float
    degrees_constrained: int
    precautions: list[str]
    suitability_score: float
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the fixture recommendation to a dictionary representation.

        Returns:
            A dictionary containing all fixture recommendation properties.
        """
        return {
            "fixture_id": self.fixture_id,
            "fixture_name": self.fixture_name,
            "fixture_type": self.fixture_type,
            "locating_method": self.locating_method,
            "clamping_points": self.clamping_points,
            "clamping_force_n": self.clamping_force_n,
            "degrees_constrained": self.degrees_constrained,
            "precautions": self.precautions,
            "suitability_score": round(self.suitability_score, 2),
            "reasoning": self.reasoning,
        }


@dataclass
class FixtureAnalysis:
    """Complete fixture analysis result for a part.

    Attributes:
        recommendations: List of fixture recommendations per surface.
        best_fixture: Best overall fixture recommendation.
        setup_count: Number of required setups.
        face_changes: List of face change descriptions.
    """

    recommendations: list[FixtureRecommendation] = field(default_factory=list)
    best_fixture: FixtureRecommendation | None = None
    setup_count: int = 0
    face_changes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the fixture analysis to a dictionary representation.

        Returns:
            A dictionary containing recommendations, best fixture, setup
            count, and face changes.
        """
        return {
            "recommendations": [r.to_dict() for r in self.recommendations],
            "best_fixture": self.best_fixture.to_dict() if self.best_fixture else None,
            "setup_count": self.setup_count,
            "face_changes": self.face_changes,
        }


class FixtureAnalyzer:
    """Fixture analyzer that recommends clamping solutions.

    Evaluates fixture options based on part type, material, and
    machining features, then recommends the best clamping method
    from a template library.
    """

    def __init__(self, templates_path: str | None = None) -> None:
        if templates_path is None:
            data_dir = Path(__file__).resolve().parent / "data"
            templates_path = str(data_dir / "fixture_templates.json")
        with open(templates_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._templates: dict[str, dict[str, Any]] = {f["id"]: f for f in data["fixtures"]}

    def analyze(
        self,
        features: list[MachiningFeature],
        part_type: str,
        material: str = "",
    ) -> FixtureAnalysis:
        analysis = FixtureAnalysis()

        surfaces = sorted(set(f.surface for f in features))
        analysis.face_changes = [f"Surface {s}" for s in surfaces]
        analysis.setup_count = len(surfaces)

        for surface in surfaces:
            surface_features = [f for f in features if f.surface == surface]
            rec = self._evaluate_for_features(surface_features, part_type, material)
            analysis.recommendations.append(rec)

        if analysis.recommendations:
            analysis.recommendations.sort(
                key=lambda r: r.suitability_score,
                reverse=True,
            )
            analysis.best_fixture = analysis.recommendations[0]

        return analysis

    def _evaluate_for_features(
        self,
        features: list[MachiningFeature],
        part_type: str,
        material: str = "",
    ) -> FixtureRecommendation:
        scores: dict[str, float] = {}

        is_shaft = part_type in ("shaft", "stepped_shaft", "cylinder")
        has_holes = any(f.is_hole() for f in features)
        has_faces = any(f.is_face() for f in features)
        max_dim = max(
            (f.dimensions.get("diameter", f.dimensions.get("length", 10)) for f in features),
            default=10,
        )

        if is_shaft:
            scores["three_jaw_chuck"] = 90
            scores["v_block"] = 75
            scores["vise"] = 30
            scores["toe_clamp"] = 20
        elif part_type in ("plate", "flange", "bracket"):
            scores["toe_clamp"] = 80
            scores["vise"] = 70
            scores["three_jaw_chuck"] = 40
            scores["v_block"] = 10
        else:
            scores["vise"] = 75
            scores["toe_clamp"] = 70
            scores["three_jaw_chuck"] = 30
            scores["v_block"] = 20

        if material and "aluminum" in material.lower():
            for k in scores:
                scores[k] = min(scores[k] + 5, 100)
        if material and ("titanium" in material.lower() or "alloy" in material.lower()):
            for k in scores:
                scores[k] = max(scores[k] - 5, 0)

        best_id = max(scores, key=lambda k: scores[k])
        t = self._templates.get(best_id, {})

        clamp_force = self._estimate_clamping_force(
            best_id,
            max_dim,
            material,
        )

        reasoning = [
            f"零件类型'{part_type}'匹配夹具类型'{t.get('name', best_id)}'",
            f"适用零件类型: {', '.join(t.get('applicable_parts', []))}",
        ]
        if is_shaft:
            reasoning.append("回转体零件首选三爪卡盘或V型块装夹")
        if has_holes:
            reasoning.append("含孔特征, 需确保钻孔方向无夹具干涉")
        if has_faces:
            reasoning.append("含平面特征, 需底面有效支撑")

        proc = t.get("precautions", [])[:5]

        return FixtureRecommendation(
            fixture_id=best_id,
            fixture_name=t.get("name", best_id),
            fixture_type=t.get("type", "general"),
            locating_method=t.get("locating_method", ""),
            clamping_points=[
                {
                    "type": t.get("clamping_principle", {}).get("description", ""),
                    "constrained_dof": t.get("clamping_principle", {}).get("constrained_dof", []),
                }
            ],
            clamping_force_n=clamp_force,
            degrees_constrained=t.get("degrees_constrained", 0),
            precautions=proc,
            suitability_score=scores[best_id],
            reasoning=reasoning,
        )

    def _estimate_clamping_force(
        self,
        fixture_id: str,
        max_dim: float,
        material: str,
    ) -> float:
        base = 1000.0
        if fixture_id == "three_jaw_chuck":
            base = max_dim * 80
        elif fixture_id == "vise":
            base = max_dim * 100
        elif fixture_id == "toe_clamp":
            base = max_dim * 60
        elif fixture_id == "v_block":
            base = max_dim * 70

        if "steel" in material.lower() or "45" in material.lower():
            base *= 1.2
        elif "titanium" in material.lower():
            base *= 1.5

        return round(base, 1)

    def suggest_fixture(
        self,
        setup: Setup,
        part_type: str = "general",
    ) -> FixtureRecommendation:
        features = [
            MachiningFeature(
                name=f"f_{n}",
                type=n,
                geometric_type="unknown",
                surface=setup.surface,
            )
            for n in setup.datum_features
        ]
        if not features:
            features = [
                MachiningFeature(
                    name="part",
                    type="block",
                    geometric_type="unknown",
                    surface=setup.surface,
                )
            ]
        return self._evaluate_for_features(features, part_type)
