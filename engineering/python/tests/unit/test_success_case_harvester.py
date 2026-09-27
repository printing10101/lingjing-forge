"""成功案例自动入工艺库（M4a）单元测试。"""

from __future__ import annotations

import pytest

from app.gcode_generation.gcode_store import FeatureGCodeResult
from app.gcode_generation.success_case_harvester import (
    FEATURE_TYPE_TO_QUAD_FEATURE,
    SUCCESS_CONFIDENCE,
    SUCCESS_SOURCE,
    build_quadruples_from_success,
    ingest_success_cases,
)
from app.rag.process_quadruple import ProcessQuadrupleIndex


def _make_feature(
    feature_id: str = "f1",
    feature_type: str = "plane",
    material_id: str = "aluminum_6061",
    spindle_rpm: float = 8000.0,
    axial_depth_mm: float = 1.5,
    limit_depth_mm: float = 3.0,
    stable: bool = True,
) -> FeatureGCodeResult:
    return FeatureGCodeResult(
        feature_id=feature_id,
        feature_type=feature_type,
        material_id=material_id,
        spindle_rpm=spindle_rpm,
        axial_depth_mm=axial_depth_mm,
        limit_depth_mm=limit_depth_mm,
        stable=stable,
        safety_margin_ratio=axial_depth_mm / limit_depth_mm,
    )


@pytest.fixture()
def index() -> ProcessQuadrupleIndex:
    """内存模式工艺四元组索引（无持久化）。"""
    return ProcessQuadrupleIndex(persist_dir=None)


# ----------------------------------------------------------------------
# build_quadruples_from_success
# ----------------------------------------------------------------------


class TestBuildQuadruples:
    def test_stable_feature_builds_quad(self) -> None:
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature()])
        assert len(quads) == 1
        q = quads[0]
        assert q.feature == "face"  # plane → face
        assert q.process == "mill"
        assert q.tool == "unspecified"
        assert q.material == "aluminum_6061"
        assert q.confidence == SUCCESS_CONFIDENCE == 0.9
        assert q.source == SUCCESS_SOURCE == "generated_validated"
        assert q.parameters["spindle_rpm"] == 8000.0
        assert q.parameters["axial_depth_mm"] == 1.5
        assert q.parameters["limit_depth_mm"] == 3.0
        assert q.parameters["safety_margin_ratio"] == pytest.approx(0.5)
        assert "success_case" in q.tags
        assert "task:t1" in q.tags
        assert "controller:fanuc" in q.tags

    def test_unstable_feature_excluded(self) -> None:
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature(stable=False)])
        assert quads == []

    def test_unknown_feature_type_skipped(self) -> None:
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature(feature_type="weird")])
        assert quads == []

    def test_all_mapped_types(self) -> None:
        results = [
            _make_feature(feature_id=fid, feature_type=ft)
            for ft, fid in [("plane", "f1"), ("cylinder", "f2"), ("hole", "f3"), ("boss", "f4")]
        ]
        quads = build_quadruples_from_success("t1", "heidenhain", results)
        assert {q.feature for q in quads} == {"face", "profile", "hole"}
        assert set(FEATURE_TYPE_TO_QUAD_FEATURE.values()) == {"face", "profile", "hole"}

    def test_empty_input(self) -> None:
        assert build_quadruples_from_success("t1", "fanuc", []) == []

    def test_material_normalization(self) -> None:
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature(material_id=" 45 Steel ")])
        assert quads[0].material == "45 steel"


# ----------------------------------------------------------------------
# ingest_success_cases（对接真实索引）
# ----------------------------------------------------------------------


class TestIngest:
    def test_ingest_adds_and_persists_in_memory(self, index: ProcessQuadrupleIndex) -> None:
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature()])
        added, skipped = ingest_success_cases(quads, index)
        assert (added, skipped) == (1, 0)
        assert index.get_stats()["total_quadruples"] == 1

    def test_same_params_deduped(self, index: ProcessQuadrupleIndex) -> None:
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature()])
        assert ingest_success_cases(quads, index) == (1, 0)
        # 同任务重跑：同 (feature, material, rpm, depth) 视为重复实证
        quads2 = build_quadruples_from_success("t1", "fanuc", [_make_feature()])
        assert ingest_success_cases(quads2, index) == (0, 1)
        assert index.get_stats()["total_quadruples"] == 1

    def test_different_params_both_ingested(self, index: ProcessQuadrupleIndex) -> None:
        quads = build_quadruples_from_success(
            "t1", "fanuc", [_make_feature(feature_id="f1"), _make_feature(feature_id="f2", spindle_rpm=6000.0)]
        )
        added, skipped = ingest_success_cases(quads, index)
        assert (added, skipped) == (2, 0)

    def test_manual_knowledge_not_treated_as_duplicate(self, index: ProcessQuadrupleIndex) -> None:
        """同参数的手册知识不应挡住实证入库（来源不同价值不同）。"""
        from app.rag.process_quadruple import ProcessQuadruple

        manual = ProcessQuadruple(
            feature="face",
            process="rough_mill",
            tool="endmill_d10",
            parameters={"spindle_rpm": 8000.0, "axial_depth_mm": 1.5},
            material="aluminum_6061",
            confidence=1.0,
            source="manual",
        )
        index.add(manual)
        quads = build_quadruples_from_success("t1", "fanuc", [_make_feature()])
        assert ingest_success_cases(quads, index) == (1, 0)

    def test_empty_input_noop(self, index: ProcessQuadrupleIndex) -> None:
        assert ingest_success_cases([], index) == (0, 0)
        assert index.get_stats()["total_quadruples"] == 0
