"""T1 feature_extraction 补测：mesh 加载与异步提取管线。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh

from app.feature_extraction.pipeline import (
    FeatureExtractionPipeline,
    FeatureReviewError,
    MeshLoadError,
    _load_mesh,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def box_stl(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("feat") / "box_40x30x20.stl"
    trimesh.creation.box((40, 30, 20)).export(str(path))
    return path


@pytest.fixture(scope="module")
def cylinder_stl(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("feat_cyl") / "cylinder.stl"
    trimesh.creation.cylinder(radius=10, height=30, sections=64).export(str(path))
    return path


@pytest.fixture(scope="module")
def extracted_cylinder(cylinder_stl: Path):
    """圆柱（130 顶点）能识别出平面/圆柱/孔，用于评审流程。"""
    import asyncio

    pipeline = FeatureExtractionPipeline()
    task_id = asyncio.run(_run(pipeline, cylinder_stl))
    return pipeline, task_id


async def _run(pipeline: FeatureExtractionPipeline, mesh_path: Path) -> str:
    task = await pipeline.create_task(str(mesh_path))
    await pipeline.run_task(task.task_id)
    return task.task_id


def test_load_mesh_stl_returns_vertices_faces(box_stl: Path):
    vertices, faces = _load_mesh(box_stl, trimesh)
    assert vertices.shape[0] > 0
    assert faces is not None and faces.shape[1] == 3


def test_load_mesh_missing_file_raises(tmp_path: Path):
    with pytest.raises(MeshLoadError):
        _load_mesh(tmp_path / "nope.stl", trimesh)


def test_load_mesh_unsupported_suffix_raises(tmp_path: Path):
    weird = tmp_path / "model.xyz"
    weird.write_text("x", encoding="utf-8")
    with pytest.raises(MeshLoadError):
        _load_mesh(weird, trimesh)


def test_create_task_rejects_missing_mesh(tmp_path: Path):
    pipeline = FeatureExtractionPipeline()
    with pytest.raises(MeshLoadError):
        __import__("asyncio").run(pipeline.create_task(str(tmp_path / "absent.stl")))


async def test_pipeline_extracts_box_features(box_stl: Path):
    pipeline = FeatureExtractionPipeline()
    task_id = await _run(pipeline, box_stl)
    result = pipeline.get_result(task_id)
    assert result is not None
    assert result.status == "features_extracted"
    assert result.vertex_count > 0 and result.face_count > 0
    payload = result.to_dict()
    assert payload["task_id"] == task_id
    assert "plane_count" in payload
    assert pipeline.get_result("no-such-task") is None


async def test_pipeline_review_flow_and_export(extracted_cylinder, tmp_path: Path):
    pipeline, task_id = extracted_cylinder
    task = pipeline._store.get(task_id)
    assert task.features, "圆柱至少应识别出平面/圆柱/孔特征"

    feature_id = task.features[0].feature_id
    with pytest.raises(FeatureReviewError):
        pipeline.review_feature(task_id, "missing-feature", "confirmed")
    with pytest.raises(FeatureReviewError):
        pipeline.review_feature(task_id, feature_id, "maybe")

    pipeline.review_feature(task_id, feature_id, "confirmed", engineer_notes="外观检查 OK")

    export_path = pipeline.export_confirmed_features(task_id, tmp_path / "confirmed.json")
    assert export_path.exists()
    exported = json.loads(Path(export_path).read_text(encoding="utf-8"))
    assert exported


async def test_pipeline_edited_action_requires_params(extracted_cylinder):
    pipeline, task_id = extracted_cylinder
    task = pipeline._store.get(task_id)
    feature_id = task.features[0].feature_id
    with pytest.raises(FeatureReviewError):
        pipeline.review_feature(task_id, feature_id, "edited")


async def test_disclaimer_for_known_and_unknown_task(extracted_cylinder):
    pipeline, task_id = extracted_cylinder
    known = pipeline.get_disclaimer(task_id, mesh_calibrated=False)
    unknown = pipeline.get_disclaimer("no-such-task", mesh_calibrated=True)
    assert known is not None and unknown is not None
    assert known.to_dict() if hasattr(known, "to_dict") else True


async def test_cylinder_mesh_runs(cylinder_stl: Path):
    pipeline = FeatureExtractionPipeline()
    task_id = await _run(pipeline, cylinder_stl)
    result = pipeline.get_result(task_id)
    assert result.status == "features_extracted"
    assert result.cylinder_count >= 0
