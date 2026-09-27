"""DNC 下发硬闸单元测试（仿真强制闭环）。

覆盖范围（app.dnc.nc_gate.get_dispatch_block_reason）：
- 无校验记录 → 拦截（提示先完成阶段 7 校验）
- 校验任务未 SUCCEEDED → 拦截
- SUCCEEDED 但 voxel_check_passed=None（闭环上线前历史任务）→ 拦截
- SUCCEEDED 但 voxel_check_passed=False → 拦截（含碰撞数提示）
- SUCCEEDED 且 voxel_check_passed=True → 放行
- LNN_DNC_ALLOW_UNVALIDATED_NC=1 显式逃生阀 → 放行（fail-open 需显式开启，
  且必须写入哈希链审计日志；审计失败则放行失败）
- 路径归一化：Windows 大小写 / 相对路径差异不影响追溯比对
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.audit.audit_log import AuditLog
from app.cam_validation.cam_store import CamTaskStore, CamValidationTask
from app.dnc.nc_gate import ALLOW_UNVALIDATED_ENV, get_dispatch_block_reason


def _make_task(
    task_id: str,
    gcode_path: str,
    status: str = "succeeded",
    voxel_check_passed: bool | None = True,
    voxel_collision_count: int = 0,
    kinematics_check_passed: bool | None = True,
    kinematics_error_count: int = 0,
    completed_at: float = 0.0,
) -> CamValidationTask:
    """构造最小 CamValidationTask（直接注入 store）。"""
    return CamValidationTask(
        task_id=task_id,
        source_gcode_report_path="/tmp/report.json",
        source_gcode_file_path=gcode_path,
        controller_type="fanuc_0i",
        status=status,
        voxel_check_passed=voxel_check_passed,
        voxel_collision_count=voxel_collision_count,
        kinematics_check_passed=kinematics_check_passed,
        kinematics_error_count=kinematics_error_count,
        completed_at=completed_at or time.time(),
    )


@pytest.fixture
def cam_store():
    """CamTaskStore 单例（每个用例后清空，避免跨用例污染）。"""
    store = CamTaskStore()
    yield store
    store.clear()


class TestDispatchGate:
    """DNC 下发闸门判定逻辑。"""

    @pytest.mark.unit
    def test_no_record_blocks(self, cam_store: CamTaskStore, tmp_path: Path):
        """无任何校验记录 → 拦截，提示完成阶段 7 校验。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        reason = get_dispatch_block_reason(str(program))
        assert reason is not None
        assert "校验记录" in reason

    @pytest.mark.unit
    def test_non_succeeded_task_blocks(self, cam_store: CamTaskStore, tmp_path: Path):
        """校验任务未 SUCCEEDED → 拦截并提示当前状态。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(_make_task("cam_gate_001", str(program), status="validated", voxel_check_passed=True))
        reason = get_dispatch_block_reason(str(program))
        assert reason is not None
        assert "尚未完成" in reason
        assert "validated" in reason

    @pytest.mark.unit
    def test_succeeded_without_voxel_blocks(self, cam_store: CamTaskStore, tmp_path: Path):
        """SUCCEEDED 但体素未执行（None，闭环上线前任务）→ 拦截。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(_make_task("cam_gate_002", str(program), voxel_check_passed=None))
        reason = get_dispatch_block_reason(str(program))
        assert reason is not None
        assert "体素" in reason

    @pytest.mark.unit
    def test_succeeded_with_failed_voxel_blocks(self, cam_store: CamTaskStore, tmp_path: Path):
        """SUCCEEDED 但体素仿真未通过 → 拦截并提示碰撞数。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(
            _make_task(
                "cam_gate_003",
                str(program),
                voxel_check_passed=False,
                voxel_collision_count=7,
            )
        )
        reason = get_dispatch_block_reason(str(program))
        assert reason is not None
        assert "未通过" in reason
        assert "7" in reason

    @pytest.mark.unit
    def test_succeeded_with_passed_voxel_allows(self, cam_store: CamTaskStore, tmp_path: Path):
        """SUCCEEDED + 体素通过 → 放行（reason 为 None）。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(_make_task("cam_gate_004", str(program), voxel_check_passed=True))
        assert get_dispatch_block_reason(str(program)) is None

    @pytest.mark.unit
    def test_succeeded_without_kinematics_blocks(self, cam_store: CamTaskStore, tmp_path: Path):
        """SUCCEEDED 但运动学校验未执行（None，闭环上线前任务）→ 拦截。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(
            _make_task("cam_gate_kin_none", str(program), kinematics_check_passed=None)
        )
        reason = get_dispatch_block_reason(str(program))
        assert reason is not None
        assert "运动学" in reason

    @pytest.mark.unit
    def test_succeeded_with_failed_kinematics_blocks(self, cam_store: CamTaskStore, tmp_path: Path):
        """SUCCEEDED 但运动学校验未通过（超行程/无主轴等）→ 拦截。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(
            _make_task(
                "cam_gate_kin_fail",
                str(program),
                kinematics_check_passed=False,
                kinematics_error_count=2,
            )
        )
        reason = get_dispatch_block_reason(str(program))
        assert reason is not None
        assert "运动学" in reason
        assert "2" in reason

    @pytest.mark.unit
    @pytest.mark.skipif(os.name != "nt", reason="路径大小写不敏感是 Windows 文件系统行为（normcase 在 Linux 为恒等）")
    def test_path_normalization_case_insensitive(self, cam_store: CamTaskStore, tmp_path: Path):
        """Windows 大小写差异不影响追溯比对。"""
        program = tmp_path / "Part.NC"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        cam_store.add_task(_make_task("cam_gate_005", str(program), voxel_check_passed=True))
        # 以不同大小写的同一文件路径查询
        variant = str(program)
        variant = variant.lower() if variant != variant.lower() else variant.upper()
        assert get_dispatch_block_reason(variant) is None

    @pytest.mark.unit
    def test_latest_completed_task_wins(self, cam_store: CamTaskStore, tmp_path: Path):
        """同一程序多次校验时，取最近完成的任务判定。"""
        program = tmp_path / "part.nc"
        program.write_text("G90 G21\nM30\n", encoding="utf-8")
        # 旧任务：体素失败；新任务：体素通过（completed_at 更大）
        cam_store.add_task(
            _make_task(
                "cam_gate_006_old",
                str(program),
                voxel_check_passed=False,
                voxel_collision_count=3,
                completed_at=time.time() - 100,
            )
        )
        cam_store.add_task(
            _make_task("cam_gate_006_new", str(program), voxel_check_passed=True, completed_at=time.time())
        )
        assert get_dispatch_block_reason(str(program)) is None


class TestDispatchGateEscapeHatch:
    """显式逃生阀：LNN_DNC_ALLOW_UNVALIDATED_NC=1 放行未校验程序（fail-open 需显式开启）。

    放行必须写入哈希链审计日志；审计写入失败则放行失败（fail-closed）。
    """

    @pytest.mark.unit
    def test_env_override_allows_unvalidated_and_writes_audit(
        self, cam_store: CamTaskStore, tmp_path: Path, monkeypatch
    ):
        """逃生阀开启时，无校验记录的程序放行，且审计记录落盘（哈希链日志）。"""
        monkeypatch.setenv(ALLOW_UNVALIDATED_ENV, "1")
        monkeypatch.setattr(
            "app.audit.audit_log.get_audit_log", lambda: AuditLog(log_dir=str(tmp_path))
        )
        program = tmp_path / "legacy.nc"
        program.write_text("O1234\nM30\n", encoding="utf-8")
        assert get_dispatch_block_reason(str(program), machine_id="machine_01") is None

        audit_file = next(tmp_path.glob("*/audit.log"))
        entries = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
        dispatch_events = [e for e in entries if e["ai_module"] == "dnc_unvalidated_nc_dispatch"]
        assert len(dispatch_events) == 1
        event = dispatch_events[0]
        assert event["input_parameters"]["program_path"] == str(program)
        assert event["metadata"]["machine_id"] == "machine_01"
        assert event["metadata"]["escape_hatch_env"] == ALLOW_UNVALIDATED_ENV
        # 哈希链字段必须完整（防篡改链的入口记录）
        assert event["entry_hash"] and event["prev_hash"] == "GENESIS"

    @pytest.mark.unit
    def test_audit_failure_blocks_unvalidated_dispatch(
        self, cam_store: CamTaskStore, tmp_path: Path, monkeypatch
    ):
        """审计写入失败 → 逃生阀放行同样失败（无留痕的未校验下发必须 fail-closed）。"""
        monkeypatch.setenv(ALLOW_UNVALIDATED_ENV, "1")

        class _BrokenAuditLog:
            def log_security_event(self, **_kwargs):
                raise RuntimeError("Failed to write security audit log (disk full)")

        monkeypatch.setattr(
            "app.audit.audit_log.get_audit_log", lambda: _BrokenAuditLog()
        )
        program = tmp_path / "legacy3.nc"
        program.write_text("O1234\nM30\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="audit log"):
            get_dispatch_block_reason(str(program), machine_id="machine_02")

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_env_disabled_keeps_fail_closed(self, cam_store: CamTaskStore, tmp_path: Path, monkeypatch, value: str):
        """逃生阀关闭（非真值）时保持 fail-closed。"""
        monkeypatch.setenv(ALLOW_UNVALIDATED_ENV, value)
        program = tmp_path / "legacy2.nc"
        program.write_text("O1234\nM30\n", encoding="utf-8")
        assert get_dispatch_block_reason(str(program)) is not None
