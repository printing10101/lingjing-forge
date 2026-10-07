"""体素仿真 → DNC 下发硬闸的**接缝**闭环测试（变异验证）。

为什么需要这个文件（2026-10-07 全库定级时发现的覆盖缝）：
- `test_cam_voxel_pipeline.py` 证明了「坏程序会让 voxel_check_passed=False」，
  但只跑到 status=validated，没有走到闸门；
- `test_dnc_nc_gate.py` 证明了「voxel_check_passed=False 会被拦」，
  但它是**手写注入**一条 False 的任务记录，从未由真实仿真产出。

两套测试各自绿，接缝（真实 G 代码 → 真实体素仿真 → 真实落盘 → 真实闸门）
却没有任何测试穿过。这正是「门禁绿 ≠ 业务链通」的形态。本文件把这条缝
用一条链穿通，并用**变异验证**证明闸门真有牙齿：
同一份程序，只改一个 Z 值，必须从「放行」翻转为「拦截」。

测试策略：
- 真实组件链（GCodeLoader + InternalValidator + CamAdapter(internal_only) +
  VoxelValidator + CamTaskStore 进程单例），不 mock 任何校验判定
- G 代码含装刀（T01 M06）与主轴启动（M03），否则阶段 7 程序级运动学校验
  （K002/K007）会正确地判错，测不到体素层
- 毛坯/安全高度与阶段 7 默认口径一致（stock 200x150x50，stock_top_z=50，
  safe_z=80）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.cam_validation import (
    CamTaskStore,
    CamValidationPipeline,
    VoxelValidator,
)
from app.cam_validation.cam_adapter import CamAdapter
from app.cam_validation.cam_store import CamValidationTaskStatus
from app.cam_validation.gcode_loader import GCodeLoader
from app.audit.audit_log import AuditLog
from app.cam_validation.internal_validator import InternalValidator
from app.config import CamValidationConfig
from app.dnc.nc_gate import ALLOW_UNVALIDATED_ENV, get_dispatch_block_reason

# ── 程序样本 ───────────────────────────────────────────────────────────────
# 基准面铣（阶段 7 口径下的「干净程序」）：安全高度定位 → 下刀到 Z49（1mm 切深）
# → 走一刀 → 抬回 safe_z → 第二刀位下刀 → 抬刀 → 结束。
#
# 为什么切深只有 1mm：体素掩注按 voxel_size=1mm 单层切除，侧刃扫掠域不落空，
# 因此切深 >1mm 后在原位垂直抬刀会被判 critical 碰撞（实测：Z45 单层 → 1 处；
# 48→45 分层 → 2 处）。那是**保守误报**（方向安全：宁可多拦），本文件不把它
# 当基准，另用 test_multilayer_retract_is_currently_flagged 钉住该口径。
BASE_GCODE = """G90 G21 G17
T01 M06
M03 S2000
G00 X100 Y75 Z80
G01 Z49 F300
G01 X150 Y75 F800
G00 Z80
G00 X150 Y100
G01 Z49 F300
G00 Z80
M30
"""

# 变异 1（过切）：唯一改动是把第一刀切削 Z 从 49 压到 -5（低于毛坯底面 Z=0）
OVERCUT_MUTANT = BASE_GCODE.replace("G01 Z49 F300\nG01 X150 Y75", "G01 Z-5 F300\nG01 X150 Y75")

# 变异 2（快速下扎进材料）：唯一改动是把第一刀下刀由 G01 换成 G00（快进切入）
PLUNGE_MUTANT = BASE_GCODE.replace("G01 Z49 F300\nG01 X150 Y75", "G00 Z20\nG01 X150 Y75")

# 变异 3（安全高度以下横移）：唯一改动是删掉第一刀后的抬刀段
LATERAL_BELOW_SAFE_MUTANT = BASE_GCODE.replace("G00 Z80\nG00 X150 Y100", "G00 X150 Y100")


def _build_report(tmp_path: Path, gcode: str, name: str) -> tuple[str, str]:
    """用阶段 6 产物口径写 report.json + .nc，返回 (report_path, gcode_path)。"""
    gcode_path = tmp_path / f"{name}.nc"
    gcode_path.write_text(gcode, encoding="utf-8")
    # 特征行段覆盖切削段（第 5-7 行），与产品 report 口径一致
    data = {
        "task_id": f"gcode_{name}",
        "task_status": "succeeded",
        "gcode_file_path": str(gcode_path),
        "gcode_total_lines": len(gcode.splitlines()),
        "controller_type": "fanuc_0i",
        "material_name": "45#钢",
        "safe_z": 80.0,
        "stock_top_z": 50.0,
        "cam_validation_required": True,
        "prediction_method": "analytical",
        "tool_diameter_mm": 10.0,
        "feature_results": [
            {"feature_id": "feat_001", "feature_type": "plane", "line_range": [5, 10]},
        ],
    }
    report_path = tmp_path / f"{name}.report.json"
    report_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(report_path), str(gcode_path)


def _make_pipeline(tmp_path: Path) -> CamValidationPipeline:
    cfg = CamValidationConfig(output_dir=str(tmp_path / "cam_out"))
    return CamValidationPipeline(
        cfg=cfg,
        loader=GCodeLoader(project_root=str(tmp_path)),
        validator=InternalValidator(cfg),
        adapter=CamAdapter(cfg),
        voxel_validator=VoxelValidator(cfg),
    )


def _run_to_succeeded(tmp_path: Path, gcode: str, name: str) -> tuple[CamValidationPipeline, str]:
    """把一份 G 代码真实推过阶段 7：create → run → 逐特征审核 → confirm。

    返回 (pipeline, task_id)。闸门要求 status=SUCCEEDED，所以必须走完审核确认，
    这一步本身就是被测链路的一部分。
    """
    report_path, _ = _build_report(tmp_path, gcode, name)
    pipeline = _make_pipeline(tmp_path)
    task = pipeline.create_task(source_gcode_report_path=report_path, cam_backend="internal_only")
    asyncio.run(pipeline.run_pipeline(task.task_id))
    stored = pipeline._store.get_task(task.task_id)
    for fr in stored.feature_validation_results:
        pipeline.review_task(
            task_id=task.task_id,
            feature_id=fr.feature_id,
            review_status="confirmed",
        )
    pipeline.confirm_task(task_id=task.task_id, reviewer="engineer_gate")
    return pipeline, task.task_id


@pytest.fixture(autouse=True)
def _clean_store():
    """CamTaskStore 是进程级单例，用例后清空避免跨用例污染。"""
    yield
    CamTaskStore().clear()


class TestVoxelToDncGateSeam:
    """真实仿真结果必须能翻到闸门上——这条缝单独测，两套半程测试都替不了。"""

    @pytest.mark.unit
    def test_baseline_program_reaches_gate_and_allows(self, tmp_path: Path):
        """正向对照：干净程序走完阶段 7 → 闸门放行（证明拦截不是环境噪声导致）。

        没有这一条，下面的「被拦」可能只是因为路径没打通而恒拦。
        """
        _, gcode_path = _build_report(tmp_path, BASE_GCODE, "base")
        pipeline, task_id = _run_to_succeeded(tmp_path, BASE_GCODE, "base")
        task = pipeline._store.get_task(task_id)

        assert task.status == CamValidationTaskStatus.SUCCEEDED.value
        assert task.voxel_check_passed is True, f"基准程序不应触发碰撞: {task.errors}"

        assert get_dispatch_block_reason(gcode_path) is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "mutant,label",
        [
            (OVERCUT_MUTANT, "overcut_below_stock_bottom"),
            (PLUNGE_MUTANT, "rapid_plunge_into_material"),
            (LATERAL_BELOW_SAFE_MUTANT, "lateral_rapid_below_safe_z"),
        ],
        ids=["overcut_below_stock_bottom", "rapid_plunge_into_material", "lateral_rapid_below_safe_z"],
    )
    def test_mutated_program_blocked_by_gate(self, tmp_path: Path, mutant: str, label: str):
        """变异验证：只改一处，同一链路必须从放行翻转为拦截。"""
        _, gcode_path = _build_report(tmp_path, mutant, label)
        pipeline, task_id = _run_to_succeeded(tmp_path, mutant, label)
        task = pipeline._store.get_task(task_id)

        assert task.status == CamValidationTaskStatus.SUCCEEDED.value
        assert task.voxel_check_passed is False
        assert task.voxel_collision_count > 0

        reason = get_dispatch_block_reason(gcode_path)
        assert reason is not None, "变异程序被闸门放行——体素仿真结果没有传到下发判定"
        assert "仿真强制闭环" in reason or "体素" in reason
        # 拦截原因必须是「体素未通过」，不能是「查无记录」这类同义反复
        assert "未找到" not in reason, f"闸门走了无记录分支，链路接缝没穿通：{reason}"
        assert str(task.voxel_collision_count) in reason, f"拦截提示未回显碰撞数：{reason}"

    @pytest.mark.unit
    def test_two_programs_decided_independently(self, tmp_path: Path):
        """同一 store 里好/坏两个程序各自判定，不串味（追溯靠路径而非全局状态）。"""
        _, good_path = _build_report(tmp_path, BASE_GCODE, "good")
        _run_to_succeeded(tmp_path, BASE_GCODE, "good")

        _, bad_path = _build_report(tmp_path, OVERCUT_MUTANT, "bad")
        _run_to_succeeded(tmp_path, OVERCUT_MUTANT, "bad")

        assert get_dispatch_block_reason(good_path) is None, "同仓有失败任务不得连累已合格程序"
        reason = get_dispatch_block_reason(bad_path)
        assert reason is not None and "未找到" not in reason

    @pytest.mark.unit
    @pytest.mark.xfail(
        reason=(
            "已知缺口（2026-10-07 变异验证发现）：闸门只按 source_gcode_file_path 追溯，"
            "CamValidationTask 未记录内容指纹（无 sha256/mtime 字段），"
            "阶段 7 判合格后原地改写 .nc 文件仍会被放行。修复方向：任务落盘时记 G 代码"
            "内容哈希，下发前重算比对，不一致按「未校验」处理。"
        ),
        strict=False,
    )
    def test_post_validation_file_edit_must_not_pass_gate(self, tmp_path: Path):
        """校验通过后原地改坏 NC —— 必须重新拦截（当前实现做不到）。"""
        _, gcode_path = _build_report(tmp_path, BASE_GCODE, "tamper")
        _run_to_succeeded(tmp_path, BASE_GCODE, "tamper")
        assert get_dispatch_block_reason(gcode_path) is None, "前置条件：合格程序应先放行"

        # 工程师/脚本在校验之后把同一文件改成过切程序（真实误用路径：改刀轨不重跑校验）
        Path(gcode_path).write_text(OVERCUT_MUTANT, encoding="utf-8")

        reason = get_dispatch_block_reason(gcode_path)
        assert reason is not None, "内容已变但闸门仍按旧校验记录放行——仿真强制闭环可被原地改文件绕过"

    @pytest.mark.unit
    def test_multilayer_retract_through_machined_slot_is_not_flagged(self, tmp_path: Path):
        """分层铣削后沿已加工槽垂直抬刀：不是碰撞，不得被硬闸拦。

        历史缺陷（2026-10-07 修正）：体素掩码把刀体画在刀尖**之下**
        （`ToolModel.voxel_mask` 的 z 取号错误），所以切深以上那一圈侧刃带
        永远留在网格裡；任何「切深 >1mm 后原位垂直抬刀」都被判 critical 碰撞。
        当时这条被当成"保守误报"钉住。现在按物理改正：刀体占据刀尖之上，
        槽内材料被切除，抬刀穿过的是空槽 → 必须放行。
        同时快移检查改为按程序顺序（见 voxel_validator 第 5 步），
        所以扎进实心材料的 G00 仍然会被拦（见上面两个变异用例）。
        """
        deep = BASE_GCODE.replace("G01 Z49 F300", "G01 Z45 F300")
        _, gcode_path = _build_report(tmp_path, deep, "deep_retract")
        pipeline, task_id = _run_to_succeeded(tmp_path, deep, "deep_retract")
        task = pipeline._store.get_task(task_id)

        assert task.voxel_check_passed is True, f"沿已加工槽抬刀被误判碰撞: {task.errors}"
        assert task.voxel_collision_count == 0
        assert get_dispatch_block_reason(gcode_path) is None

    @pytest.mark.unit
    def test_plunge_after_cut_still_detected_by_program_order(self, tmp_path: Path):
        """时序检查的红线：先切空一处材料，不能掩盖另一处 G00 扎实心材料。

        修正侧刃口径后，如果快移检查仍在"所有切削完成后"统一执行，
        扎刀点会被随后的切削抹平而漏检。本用例把顺序语义钉住。
        """
        # 第一刀在 X100 处切空一个柱，第二处 G00 直接扎进 X150 的实心材料
        program = """G90 G21 G17
T01 M06
M03 S2000
G00 X100 Y75 Z80
G01 Z45 F300
G00 Z80
G00 X150 Y100
G00 Z30
G01 Z45 F300
G00 Z80
M30
"""
        _, gcode_path = _build_report(tmp_path, program, "late_plunge")
        pipeline, task_id = _run_to_succeeded(tmp_path, program, "late_plunge")
        task = pipeline._store.get_task(task_id)

        assert task.voxel_check_passed is False, "G00 扎进未加工材料必须被拦（时序检查失效）"
        assert get_dispatch_block_reason(gcode_path) is not None


class TestGateEscapeHatchWithRealTask:
    """逃生阀语义必须显式、可审计——并把它实际覆盖的范围钉在测试里。"""

    @pytest.mark.unit
    def test_escape_hatch_bypasses_failed_voxel_and_audits(self, tmp_path: Path, monkeypatch):
        """LNN_DNC_ALLOW_UNVALIDATED_NC=1 时，连「已判不合格」的程序也会被放行。

        实测语义比文档措辞更宽：nc_gate 在查记录**之前**就判定逃生阀，
        所以它绕过的不只是"无追溯记录"，也包括 voxel_check_passed=False。
        本用例把这个事实钉住（不是主张它应该这样），并确保仍留审计痕迹。
        """
        _, gcode_path = _build_report(tmp_path, OVERCUT_MUTANT, "escape")
        pipeline, task_id = _run_to_succeeded(tmp_path, OVERCUT_MUTANT, "escape")
        assert pipeline._store.get_task(task_id).voxel_check_passed is False

        # 默认（阀门关闭）必须拦
        assert get_dispatch_block_reason(gcode_path) is not None

        monkeypatch.setenv(ALLOW_UNVALIDATED_ENV, "1")
        audit_dir = tmp_path / "audit"
        monkeypatch.setattr(
            "app.audit.audit_log.get_audit_log",
            lambda: AuditLog(log_dir=str(audit_dir)),
        )
        reason = get_dispatch_block_reason(gcode_path, machine_id="machine_gate")
        assert reason is None, "阀门开启时放行路径应返回 None（并由审计链留痕，见下）"

        audit_file = next(audit_dir.glob("*/audit.log"))
        entries = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
        events = [e for e in entries if e["ai_module"] == "dnc_unvalidated_nc_dispatch"]
        assert len(events) == 1, "无留痕的放行等于绕过安全闸门，审计链必须覆盖本次下发"
        assert events[0]["input_parameters"]["program_path"] == gcode_path
        # 被绕过的是一次真实的「不合格」判定，审计里必须看得见碰撞数
        assert events[0].get("metadata"), "逃生阀放行必须留下元数据"
