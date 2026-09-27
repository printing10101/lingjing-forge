"""S6 可检性几何层单测：解析可推的哨兵几何 + 判据校验 + 可复现性（不联网、不走 LLM）。

判据本身冻结在 criteria_frozen_v1.json；这里只验"实现是否忠实于几何直觉"，
用两类有解析答案的形状：开阔平面（应完全可检）与径向内壁深孔（光学应失效）。
"""

from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq
import numpy as np
import pytest

from app.benchmarks.machinability_pilot.inspectability.access import (
    _CRITERIA_PATH,
    CriteriaError,
    GeometryCriteria,
    _area_weighted_samples,
    assess_case,
    optic_penalty_field,
    tactile_reach_field,
    view_directions,
)
from app.benchmarks.machinability_pilot.metrics_3d import load_mesh

BAND = 0.20  # mm，与 criteria 的默认口径一致


@pytest.fixture(scope="module")
def crit() -> GeometryCriteria:
    return GeometryCriteria.load()


def _export(case: cq.Workplane, tmp_path: Path, name: str):
    stl = tmp_path / name
    cq.exporters.export(case, str(stl), tolerance=0.01, angularTolerance=0.05)
    return load_mesh(stl)


def test_criteria_loads_frozen_and_reports_version(crit: GeometryCriteria) -> None:
    assert crit.raw["version"] == "1.1"
    assert crit.guard_factor_k == 3.0
    assert crit.raw["frozen_before_first_run"] is True


def test_view_directions_are_unit_and_isotropic() -> None:
    dirs = view_directions()
    assert len(dirs) == 42
    np.testing.assert_allclose(np.linalg.norm(dirs, axis=1), 1.0, atol=1e-9)
    # 方向集必须上下半球对称，否则顶面与底面会得到不同的惩罚
    for v in dirs:
        assert np.isclose((dirs * -v).sum(axis=1).max(), 1.0, atol=1e-9)


def test_open_flat_top_face_is_fully_certifiable(crit: GeometryCriteria, tmp_path: Path) -> None:
    """开阔平面：sec(0)=1 ⟹ 预算恰为 1/(k)·1 = 1/3，且顶面应全部有视线。"""
    mesh = _export(cq.Workplane("XY").box(40, 30, 5), tmp_path, "plate.stl")
    pts, normals = _area_weighted_samples(mesh, 900, crit.seed)
    penalty, reachable = optic_penalty_field(mesh, pts, normals, crit)

    top = normals[:, 2] > 0.9
    assert top.mean() > 0.1, "该网格应有可观的顶面样本"
    assert reachable[top].all(), "开阔顶面不应有任何点看不见"
    np.testing.assert_allclose(penalty[top], 1.0, atol=1.05)

    res = assess_case(mesh, crit, tolerance_band_mm=BAND, label="plate")
    assert res["uncertainty_budget_U95_over_T"] == pytest.approx(1 / 3, abs=0.05)
    assert res["criteria_version"] == "1.1"


def test_deep_bore_wall_defeats_normal_incidence(crit: GeometryCriteria, tmp_path: Path) -> None:
    """径向内孔壁：不存在 ≤60° 入射的视线 ⟹ 光学可达面积应有明显缺口。"""
    bored = cq.Workplane("XY").box(30, 30, 12).faces(">Z").workplane().hole(6.0)
    mesh = _export(bored, tmp_path, "bore.stl")
    pts, normals = _area_weighted_samples(mesh, 1200, crit.seed)
    _, reachable = optic_penalty_field(mesh, pts, normals, crit)
    assert reachable.mean() < 1.0, "深孔内壁应当存在光学不可达区"

    res = assess_case(mesh, crit, tolerance_band_mm=BAND, label="bored-block")
    assert res["frac_no_optic_view"] > 0.0
    assert 0.0 < res["U95_required_mm"] <= BAND / crit.guard_factor_k + 1e-12


def test_assessment_is_deterministic(crit: GeometryCriteria, tmp_path: Path) -> None:
    mesh = _export(cq.Workplane("XY").box(20, 20, 8), tmp_path, "cube.stl")
    a = assess_case(mesh, crit, tolerance_band_mm=BAND, label="x")
    b = assess_case(mesh, crit, tolerance_band_mm=BAND, label="x")
    assert a["penalty_p99"] == b["penalty_p99"]
    assert a["frac_no_tactile_reach"] == b["frac_no_tactile_reach"]


def test_closed_cavity_inner_surface_is_invisible_to_optics(
    crit: GeometryCriteria, tmp_path: Path
) -> None:
    """封闭型腔内壁：任何合格入射方向都要先穿过壳体 ⟹ 光学不可达。

    这条同时是给 A3b 划界：本层评的是"看不看得见/摸不摸得到该点"，
    摆杆要进入封闭型腔所需的全局开孔路径不建模，所以判据走光学一侧。
    """
    shell = cq.Workplane("XY").box(30, 30, 10).faces(">Z").workplane().shell(-2.0)
    mesh = _export(shell, tmp_path, "shell.stl")
    pts, normals = _area_weighted_samples(mesh, 1200, crit.seed)
    _, reachable = optic_penalty_field(mesh, pts, normals, crit)

    # 盒体 z ∈ [-5, 5]，2mm 壁厚 ⟹ 腔内顶面在 z=+3、外底面在 z=-5
    cavity_top = (normals[:, 2] < -0.9) & (pts[:, 2] > 2.5)
    outer_bottom = (normals[:, 2] < -0.9) & (pts[:, 2] < -4.5)
    assert cavity_top.mean() > 0.02, "该模型应有可观的封闭腔内壁样本"
    assert not reachable[cavity_top].any(), "封闭腔内壁不应被任何视线看到"
    assert reachable[outer_bottom].mean() > 0.9, "外底面被误判不可见"


def test_tactile_local_access_on_open_faces_is_not_falsely_blocked(
    crit: GeometryCriteria, tmp_path: Path
) -> None:
    """正对照：开阔立方体的顶面与侧壁都应局部可触，防止球/柄判据误伤。"""
    mesh = _export(cq.Workplane("XY").box(30, 20, 8), tmp_path, "block.stl")
    pts, normals = _area_weighted_samples(mesh, 900, crit.seed)
    ok = tactile_reach_field(mesh, pts, normals, crit)

    top = normals[:, 2] > 0.9
    side = np.abs(normals[:, 0]) > 0.9
    assert top.mean() > 0.1 and side.mean() > 0.1
    assert ok[top].mean() > 0.98, "开阔顶面被误判不可触"
    assert ok[side].mean() > 0.98, "竖直侧壁被误判不可触（柄部沿法向模型）"


def test_criteria_rejects_out_of_range_and_wrong_schema(tmp_path: Path, crit: GeometryCriteria) -> None:
    doc = json.loads(_CRITERIA_PATH.read_text(encoding="utf-8"))

    doc["schema"] = "other"
    p = tmp_path / "bad_schema.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CriteriaError, match="判据不符"):
        GeometryCriteria.load(p)

    doc["schema"] = "inspectability-criteria"
    doc["decision_rule"]["guard_factor_k"] = 0
    p2 = tmp_path / "bad_k.json"
    p2.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CriteriaError, match="判据越界"):
        GeometryCriteria.load(p2)

    missing = tmp_path / "nope.json"
    with pytest.raises(CriteriaError, match="判据缺失"):
        GeometryCriteria.load(missing)
