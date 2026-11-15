"""编排器包络推导测试（防 200×100 固定基准面回归）。"""

from __future__ import annotations

import pytest

from app.agent.orchestrator import AgentOrchestrator

pytestmark = pytest.mark.unit


@pytest.fixture()
def orchestrator(tmp_path):
    return AgentOrchestrator(trace_log_dir=str(tmp_path))


def test_infer_dimensions_from_holes(orchestrator):
    holes = [
        {"id": "H1", "type": "through_hole", "position": {"x": 10.0, "y": 10.0}, "diameter": 6.0},
        {"id": "H2", "type": "through_hole", "position": {"x": 90.0, "y": 70.0}, "diameter": 6.0},
    ]
    overall = orchestrator._infer_overall_dimensions(holes, [])
    assert overall["min_x"] == pytest.approx(7.0)
    assert overall["min_y"] == pytest.approx(7.0)
    assert overall["length"] == pytest.approx(86.0)
    assert overall["width"] == pytest.approx(66.0)


def test_infer_dimensions_merges_planes(orchestrator):
    planes = [{"type": "top_plane", "position": {"x": 50.0, "y": 50.0},
               "dimensions": {"length": 100.0, "width": 80.0}}]
    overall = orchestrator._infer_overall_dimensions([], planes)
    assert overall == {"length": 100.0, "width": 80.0, "min_x": 0.0, "min_y": 10.0}


def test_infer_dimensions_empty_returns_empty(orchestrator):
    assert orchestrator._infer_overall_dimensions([], []) == {}


def test_build_part_description_includes_inferred_dimensions(orchestrator):
    desc = orchestrator._build_part_description(
        {"material": "45#钢",
         "holes": [{"id": "H1", "type": "through_hole", "position": {"x": 10.0, "y": 10.0}, "diameter": 6.0},
                   {"id": "H2", "type": "through_hole", "position": {"x": 90.0, "y": 90.0}, "diameter": 6.0}]},
        {})
    overall = desc["overall_dimensions"]
    assert overall["length"] == pytest.approx(86.0)
    assert overall["min_x"] == pytest.approx(7.0)


def test_build_part_description_prefers_explicit_dimensions(orchestrator):
    explicit = {"length": 120.0, "width": 80.0, "min_x": 0.0, "min_y": 0.0}
    desc = orchestrator._build_part_description(
        {"material": "45钢", "holes": [{"id": "H1", "position": {"x": 5.0, "y": 5.0}, "diameter": 2.0}],
         "overall_dimensions": explicit},
        {})
    assert desc["overall_dimensions"] == explicit
