"""黄金案例 #1（A2）端到端回归.

运行真实 7 段链路到临时目录，断言产物与门禁结果；不依赖网络/LLM。
同时校验"同输入两次运行程序体哈希一致"（剔除注释与日期行）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import golden_case_01  # noqa: E402

pytestmark = pytest.mark.e2e


def test_golden_case_01_runs_end_to_end(tmp_path: Path):
    out_dir = tmp_path / "case_01"
    manifest = golden_case_01.run(golden_case_01.DEFAULT_DXF, out_dir)

    assert manifest["passed"] is True
    assert [stage["stage"] for stage in manifest["stages"]] == [
        "dxf_parse", "feature_extract", "step_build", "cutting_params",
        "chatter", "gcode", "voxel_validation", "manifest"]
    for name in ("dxf_parse.json", "features.json", "model.step", "cutting_params.json",
                 "chatter_report.json", "program.nc", "pipeline.json", "voxel_report.json"):
        assert (out_dir / name).exists(), name

    features = json.loads((out_dir / "features.json").read_text(encoding="utf-8"))
    assert features["hole_count"] == 25

    voxel = json.loads((out_dir / "voxel_report.json").read_text(encoding="utf-8"))
    assert voxel["passed"] is True
    assert voxel["collision_count"] == 0
    assert voxel["removed_voxel_count"] > 0

    params = json.loads((out_dir / "cutting_params.json").read_text(encoding="utf-8"))
    assert len(params["features"]) == 25
    assert all(row["stability_status"] in {"ok", "marginal"} for row in params["features"])

    program = (out_dir / "program.nc").read_text(encoding="utf-8")
    assert "M30" in program and "G21" in program


def test_golden_case_01_is_deterministic(tmp_path: Path):
    first = golden_case_01.run(golden_case_01.DEFAULT_DXF, tmp_path / "a")
    second = golden_case_01.run(golden_case_01.DEFAULT_DXF, tmp_path / "b")
    # 程序体（剔除注释/日期）逐字节一致
    assert first["program_body_sha256"] == second["program_body_sha256"]
    # STEP 头含生成时间戳，按几何体积比对而非字节
    def _geometry(manifest):
        return next(stage for stage in manifest["stages"] if stage["stage"] == "step_build")
    assert _geometry(first)["volume_mm3"] == _geometry(second)["volume_mm3"]
    assert _geometry(first)["holes_subtracted"] == _geometry(second)["holes_subtracted"]


def test_golden_case_01_cli_refuses_overwrite(tmp_path: Path, monkeypatch):
    out_dir = tmp_path / "case_01"
    golden_case_01.run(golden_case_01.DEFAULT_DXF, out_dir)
    monkeypatch.setattr(sys, "argv", ["golden_case_01.py", "--out-dir", str(out_dir)])
    assert golden_case_01.main() == 1
