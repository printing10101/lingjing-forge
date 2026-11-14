"""B1：推荐参数的稳定性证据测试。"""

from __future__ import annotations

import pytest

from app.cutting_parameters.cutting_store import RecommendedCuttingParams
from app.cutting_parameters.recommender import CuttingParamRecommender
from app.cutting_parameters.stability_evidence import (
    MARGIN_MARGINAL,
    MARGIN_OK,
    build_stability_evidence,
)

pytestmark = pytest.mark.unit


def test_evidence_contains_required_fields_and_status_semantics():
    evidence = build_stability_evidence(
        machine_type="vmc_850",
        tool_diameter_mm=10.0,
        num_flutes=4,
        spindle_rpm=3000.0,
        axial_depth_mm=1.0,
    )
    assert evidence["status"] in {"ok", "marginal", "violated", "unavailable"}
    assert evidence["status"] != "unavailable", evidence.get("reason")
    assert evidence["limit_depth_mm"] > 0
    assert evidence["margin_ratio"] == pytest.approx(
        evidence["limit_depth_mm"] / evidence["axial_depth_mm"], rel=1e-6
    )
    assert evidence["machine_params"]["natural_freq_hz"] > 0
    assert evidence["method"].startswith("Tlusty")
    expected = "ok" if evidence["margin_ratio"] >= MARGIN_OK else (
        "marginal" if evidence["margin_ratio"] >= MARGIN_MARGINAL else "violated")
    assert evidence["status"] == expected


def test_deep_cut_is_violated_and_provides_lobe_sample():
    evidence = build_stability_evidence(
        machine_type="vmc_850",
        tool_diameter_mm=10.0,
        num_flutes=4,
        spindle_rpm=3000.0,
        axial_depth_mm=50.0,
    )
    assert evidence["status"] == "violated"
    lobe = evidence.get("lobe_sample")
    assert lobe and "error" not in lobe, lobe
    assert lobe["min_limit_depth_mm"] is not None
    assert lobe["points"], "接近/越过边界时应给出叶瓣点列"
    assert all(len(point) == 2 for point in lobe["points"])


def test_invalid_parameters_report_unavailable_not_fabricated():
    evidence = build_stability_evidence(
        machine_type="vmc_850",
        tool_diameter_mm=-1.0,
        num_flutes=4,
        spindle_rpm=3000.0,
        axial_depth_mm=1.0,
    )
    assert evidence["status"] == "unavailable"
    assert "reason" in evidence
    assert "limit_depth_mm" not in evidence


def test_default_resolver_loads_material_database():
    """回归：默认路径曾少一层（engineering/app/...），导致 17 种材料全部加载失败."""
    from app.cutting_parameters.material_resolver import get_material_resolver, reset_material_resolver

    reset_material_resolver()
    resolver = get_material_resolver()
    ids = resolver.list_material_ids()
    assert len(ids) >= 17, f"材料库应加载至少 17 种，实际 {len(ids)}: {ids}"
    material = resolver.get_material("al_6061")
    assert material.cutting_speed_range, "al_6061 应有切削速度范围"
    reset_material_resolver()


def test_recommender_attaches_evidence_and_warns_when_violated(monkeypatch):
    recommender = CuttingParamRecommender()
    monkeypatch.setattr(recommender, "_pick_axial_depth", lambda *args, **kwargs: 50.0)

    params = recommender.recommend(
        feature_id="f1",
        feature_type="plane",
        material_id="al_6061",
        precision_tier="standard",
        tool_diameter_mm=10.0,
        num_flutes=4,
        machine_type="vmc_850",
    )

    assert isinstance(params, RecommendedCuttingParams)
    assert params.stability_evidence["status"] == "violated"
    assert any("稳定性裕度不足" in warning for warning in params.warnings), params.warnings
    payload = params.to_dict()
    assert payload["stability_evidence"]["status"] == "violated"


def test_recommender_default_evidence_is_present():
    params = CuttingParamRecommender().recommend(
        feature_id="f2",
        feature_type="plane",
        material_id="al_6061",
        precision_tier="standard",
        tool_diameter_mm=10.0,
        num_flutes=4,
        machine_type="vmc_850",
    )
    assert params.stability_evidence, "推荐结果必须带稳定性证据（拒绝只给数不给据）"
    assert params.stability_evidence["status"] in {"ok", "marginal", "violated"}
