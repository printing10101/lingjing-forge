"""Acceptance tests for content-bound dispatch and complete evaluation semantics."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from app.cam_validation.cam_store import CamTaskStore, CamValidationTask
from app.dnc.nc_gate import ALLOW_UNVALIDATED_ENV, get_dispatch_block_reason
from app.evaluation.service import DimensionResult, EvalReport

pytestmark = pytest.mark.unit


@pytest.fixture
def approved_program(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_UNVALIDATED_ENV, raising=False)
    store = CamTaskStore()
    store.clear()
    program = tmp_path / "part.nc"
    content = b"G90 G21\r\nM30\r\n"
    program.write_bytes(content)
    task = CamValidationTask(task_id="bound-content", source_gcode_file_path=str(program),
        source_gcode_sha256=hashlib.sha256(content).hexdigest(), status="succeeded",
        voxel_check_passed=True, kinematics_check_passed=True, completed_at=1)
    store.add_task(task)
    yield program, task, content
    store.clear()


def test_legacy_task_without_fingerprint_is_blocked(approved_program):
    program, task, _ = approved_program
    task.source_gcode_sha256 = ""
    assert "指纹" in get_dispatch_block_reason(str(program))


def test_line_ending_change_requires_revalidation(approved_program):
    program, _, content = approved_program
    assert get_dispatch_block_reason(str(program)) is None
    program.write_bytes(content.replace(b"\r\n", b"\n"))
    assert "内容已变更" in get_dispatch_block_reason(str(program))


def test_api_sends_captured_bytes_despite_source_edit_and_cleans_snapshot(approved_program, monkeypatch):
    from app.api.v1 import dnc
    program, _, content = approved_program
    snapshots = []

    async def send(machine_id, path, name):
        program.write_bytes(b"G01 Z-999\nM30\n")
        snapshots.append(Path(path))
        assert path != str(program)
        assert Path(path).read_bytes() == content
        assert machine_id == "offline-test" and name == "part"
        return True

    monkeypatch.setattr(dnc.dnc_manager, "send_nc_program", send)
    result = asyncio.run(dnc.send_nc_program(dnc.NCSendRequest(
        machine_id="offline-test", program_path=str(program))))
    assert result["code"] == 0
    assert snapshots and not snapshots[0].exists()


def test_tampered_api_does_not_call_transport(approved_program, monkeypatch):
    from app.api.v1 import dnc
    program, _, _ = approved_program
    program.write_bytes(b"G01 Z-999\nM30\n")

    async def must_not_send(*args):
        pytest.fail("Changed content must not reach machine transport")

    monkeypatch.setattr(dnc.dnc_manager, "send_nc_program", must_not_send)
    result = asyncio.run(dnc.send_nc_program(dnc.NCSendRequest(
        machine_id="offline-test", program_path=str(program))))
    assert result.status_code == 403


@pytest.mark.parametrize("missing_status", ["skipped", "error"])
def test_partial_evaluation_is_not_full_validation(missing_status):
    report = EvalReport(True, 100, [DimensionResult("syntax", .5, "evaluated", True, 100),
        DimensionResult("geometry", .3, missing_status), DimensionResult("physics", .2, missing_status)])
    assert report.passed is True  # preserve the documented legacy scoring contract
    assert report.to_dict()["dimensions_complete"] is False
    assert report.to_dict()["fully_validated"] is False


@pytest.mark.parametrize("removed,cutting,physics_pass,expected", [
    (20, 2, True, True), (0, 2, True, False), (20, 0, True, False), (20, 2, False, False)])
def test_full_validation_requires_execution_material_removal_and_physics(removed, cutting, physics_pass, expected):
    report = EvalReport(True, 100, [DimensionResult("syntax", .5, "evaluated", True, 100),
        DimensionResult("geometry", .3, "evaluated", True, 100,
            detail={"removed_voxel_count": removed, "cutting_segments": cutting}),
        DimensionResult("physics", .2, "evaluated", physics_pass, 100)])
    assert report.dimensions_complete is True
    assert report.fully_validated is expected
