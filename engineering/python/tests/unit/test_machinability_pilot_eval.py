"""machinability pilot M2 评估链单测：指标数学 + 生成闭环（注入假后端，不走网络）。"""

from __future__ import annotations

import asyncio

import cadquery as cq
import numpy as np
import pytest
import trimesh

from app.benchmarks.machinability_pilot.generate_loop import generate_for_case
from app.benchmarks.machinability_pilot.metrics_3d import (
    GeometryNotNormalizedError,
    chamfer_distance,
    load_mesh,
    normalize_to_unit_box,
    voxel_iou,
)

VALID_BOX_SCRIPT = "result = cq.Workplane('XY').box(30, 20, 10)\n"
IMPORT_SCRIPT = "import os\nresult = cq.Workplane('XY').box(30, 20, 10)\n"


class FakeBackend:
    """duck-type OpenAICompatBackend：固定脚本响应，不发起网络请求。"""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.name = "fake"

    async def call(self, prompt: str, system: str | None = None) -> str:
        return self.replies.pop(0) if self.replies else VALID_BOX_SCRIPT


def _box_mesh(length: float, width: float, height: float, tmp_path) -> trimesh.Trimesh:
    stl = tmp_path / "box.stl"
    cq.exporters.export(
        cq.Workplane("XY").box(length, width, height), str(stl), tolerance=0.05, angularTolerance=0.2
    )
    return load_mesh(stl)


# ---------------------------------------------------------------- metrics


def test_normalize_to_unit_box(tmp_path) -> None:
    mesh = normalize_to_unit_box(_box_mesh(40, 20, 10, tmp_path))
    assert mesh.extents.max() == pytest.approx(1.0, abs=1e-6)
    assert mesh.bounds.mean(axis=0) == pytest.approx(0.0, abs=1e-6)


def test_chamfer_self_is_zero_and_disjoint_is_positive(tmp_path) -> None:
    a = normalize_to_unit_box(_box_mesh(30, 20, 10, tmp_path))
    cd_self = chamfer_distance(a, a, samples=2000)
    assert cd_self == pytest.approx(0.0, abs=1e-3)

    b = normalize_to_unit_box(_box_mesh(10, 10, 10, tmp_path))
    cd_diff = chamfer_distance(a, b, samples=2000)
    assert cd_diff > 1.0  # 单位盒坐标 ×1e3 口径


def test_voxel_iou_self_one_and_disjoint_small(tmp_path) -> None:
    a = normalize_to_unit_box(_box_mesh(30, 20, 10, tmp_path))
    iou_self, filled = voxel_iou(a, a, resolution=32)
    assert filled
    assert iou_self > 0.99

    b = normalize_to_unit_box(_box_mesh(10, 10, 10, tmp_path))
    iou_diff, _ = voxel_iou(a, b, resolution=32)
    assert iou_diff < iou_self


def test_metrics_reject_unnormalized_mesh(tmp_path) -> None:
    """漏归一化必须指名道姓地报错，而不是在 trimesh 深处抛无关异常。

    回归背景：直接把 70 mm 的原始叶片网格喂进 voxel_iou 时，pitch=1/64 被当成
    0.0156 mm 用，真实异常是 trimesh 的 `ValueError: max_iter exceeded!`，
    与原因（未归一化）完全脱钩；chamfer_distance 更糟——不报错，只给错量纲的数。
    """
    raw = _box_mesh(4000, 2000, 1000, tmp_path)  # 未归一化的大尺寸网格
    with pytest.raises(GeometryNotNormalizedError, match="normalize_to_unit_box"):
        chamfer_distance(raw, raw, samples=2000)
    with pytest.raises(GeometryNotNormalizedError, match="normalize_to_unit_box"):
        voxel_iou(raw, raw, resolution=32)


def test_metrics_handle_coarse_lofted_blade(tmp_path) -> None:
    """粗网格曲线曲面件（长方体原语覆盖不到的路径）必须能出两个有限指标。

    叶片翼型 loft 族 F06 的 STL 只有百余面、含大三角形，是最容易把体素化
    逼进 subdivide 迭代上限的几何形态，故用它做哨兵。
    """
    from app.benchmarks.machinability_pilot.case_families import FAMILY_REGISTRY

    stl = tmp_path / "f06.stl"
    fam = FAMILY_REGISTRY["F06"]
    cq.exporters.export(fam.build(fam.params_for("d3")), str(stl), tolerance=0.05, angularTolerance=0.2)

    truth = normalize_to_unit_box(load_mesh(stl))
    assert len(truth.faces) < 1000, "该测试的前提是粗网格，网格变密则失效"
    perturbed = truth.copy()
    perturbed.apply_scale(1.01)

    cd = chamfer_distance(truth, perturbed, samples=5000)
    iou, filled = voxel_iou(truth, perturbed, resolution=64)
    assert np.isfinite(cd) and cd > 0.0
    assert 0.0 < iou <= 1.0
    assert filled


# ---------------------------------------------------------------- generate loop


def test_generate_loop_success_path(tmp_path) -> None:
    truth = tmp_path / "truth.stl"
    cq.exporters.export(cq.Workplane("XY").box(30, 20, 10), str(truth), tolerance=0.05, angularTolerance=0.2)

    record = asyncio.run(generate_for_case(
        case_id="T01-d1",
        description="A box 30 x 20 x 10 mm.",
        truth_stl=truth,
        backend=FakeBackend([VALID_BOX_SCRIPT]),  # type: ignore[arg-type]
        run_dir=tmp_path,
        max_attempts=1,
    ))

    assert record.ok, f"应成功: {record.last_error}"
    assert record.attempts == 1
    assert record.chamfer_x1e3 is not None and record.chamfer_x1e3 < 5.0
    assert record.voxel_iou is not None and record.voxel_iou > 0.9
    assert (tmp_path / "T01-d1" / "model.step").exists()
    assert (tmp_path / "T01-d1" / "script_attempt1.py").exists()


def test_generate_loop_ast_failure_feeds_back(tmp_path) -> None:
    truth = tmp_path / "truth.stl"
    cq.exporters.export(cq.Workplane("XY").box(30, 20, 10), str(truth), tolerance=0.05, angularTolerance=0.2)

    record = asyncio.run(generate_for_case(
        case_id="T02-d1",
        description="A box.",
        truth_stl=truth,
        backend=FakeBackend([IMPORT_SCRIPT, IMPORT_SCRIPT]),  # type: ignore[arg-type]
        run_dir=tmp_path,
        max_attempts=2,
    ))

    assert not record.ok
    assert record.stage_failed == "ast"
    assert record.attempts == 2
    assert len(record.feedback_used) == 2
