"""体素校验阶段管线集成测试（阶段 7 闭环强制层）。

覆盖范围：
- 安全程序：run_pipeline 后任务级 voxel_check_passed=True，特征归因正确，
  confirm 导出的 cam_report.json 含 voxel_simulation_report 摘要
- 快速下扎程序：voxel_check_passed=False，涉事特征 voxel_check_passed=False
  且 voxel_collision_blocks 记录涉事 block，任务 errors 含闭环拦截提示
- 体素校验器抛错（VoxelValidationError）→ 任务 FAILED（fail-closed）

测试策略：
- 真实组件链（GCodeLoader(project_root=tmp) + InternalValidator + CamAdapter
  internal_only + VoxelValidator），不走 subprocess、不依赖 trimesh/STL
- 默认毛坯 200x150x50 @ 1mm 体素（与 pipeline 传入的 _DEFAULT_STOCK_* 对齐）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.cam_validation import (
    CamValidationPipeline,
    CamTaskStore,
    VoxelValidator,
    VoxelValidationError,
)
from app.cam_validation.cam_adapter import CamAdapter
from app.cam_validation.gcode_loader import GCodeLoader
from app.cam_validation.internal_validator import InternalValidator
from app.config import CamValidationConfig

# 与 pipeline stages._common 的默认毛坯一致（pipeline 固定传入）
# 注意：fixture 是"真实可上机程序"口径——含装刀（T..M06）与主轴启动（M03），
# 否则阶段 7 的程序级运动学校验（K002/K007）会正确地判错。
SAFE_GCODE = """G90 G21 G17
T01 M06
M03 S2000
G00 X100 Y75 Z60
G01 Z45 F200
G00 Z60
G00 X150 Y100
G01 Z45 F200
G00 Z80
M30
"""

# 第 5 行快速下扎进材料（毛坯顶面 Z=50，下扎到 Z20）
PLUNGE_GCODE = """G90 G21 G17
T01 M06
M03 S2000
G00 X100 Y75 Z60
G00 Z20
G01 Z45 F200
G00 Z80
M30
"""


def _build_report(
    tmp_path: Path,
    gcode: str,
    feature_line_ranges: list[list[int]],
    tool_diameter_mm: float | None = None,
) -> str:
    """构造阶段 6 report.json + G 代码文件，返回 report 路径。

    tool_diameter_mm 非空时写入 report.json（模拟阶段 6 新版携带实际刀具参数）。
    """
    gcode_path = tmp_path / "part.nc"
    gcode_path.write_text(gcode, encoding="utf-8")
    data = {
        "task_id": "gcode_voxel_001",
        "task_status": "succeeded",
        "gcode_file_path": str(gcode_path),
        "gcode_total_lines": len(gcode.splitlines()),
        "controller_type": "fanuc_0i",
        "material_name": "45#钢",
        "safe_z": 80.0,
        "stock_top_z": 50.0,
        "cam_validation_required": True,
        "prediction_method": "analytical",
        "feature_results": [
            {"feature_id": f"feat_{i + 1:03d}", "feature_type": "plane", "line_range": lr}
            for i, lr in enumerate(feature_line_ranges)
        ],
    }
    if tool_diameter_mm is not None:
        data["tool_diameter_mm"] = tool_diameter_mm
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(report_path)


def _make_pipeline(tmp_path: Path) -> CamValidationPipeline:
    cfg = CamValidationConfig(output_dir=str(tmp_path / "cam_out"))
    return CamValidationPipeline(
        cfg=cfg,
        loader=GCodeLoader(project_root=str(tmp_path)),
        validator=InternalValidator(cfg),
        adapter=CamAdapter(cfg),
        voxel_validator=VoxelValidator(cfg),
    )


@pytest.fixture(autouse=True)
def _clean_store():
    """CamTaskStore 是进程级单例，用例后清空避免跨用例污染。"""
    yield
    CamTaskStore().clear()


class TestVoxelStageInPipeline:
    """体素校验阶段在完整管线中的行为。"""

    @pytest.mark.unit
    def test_safe_program_passes_and_exports_voxel_report(self, tmp_path: Path):
        """安全程序：体素通过 + 特征归因 True + cam_report.json 含体素摘要。"""
        report_path = _build_report(tmp_path, SAFE_GCODE, feature_line_ranges=[[5, 5], [8, 8]])
        pipeline = _make_pipeline(tmp_path)
        task = pipeline.create_task(source_gcode_report_path=report_path, cam_backend="internal_only")

        result = asyncio.run(pipeline.run_pipeline(task.task_id))

        assert result.status == "validated"
        assert task.voxel_check_passed is True
        assert task.voxel_collision_count == 0
        assert task.voxel_engine in ("rust", "python")

        store_task = pipeline._store.get_task(task.task_id)
        assert all(fr.voxel_check_passed for fr in store_task.feature_validation_results)

        # 审核 + 确认 → 导出 cam_report.json 含体素摘要
        for fr in store_task.feature_validation_results:
            pipeline.review_task(task_id=task.task_id, feature_id=fr.feature_id, review_status="confirmed")
        pipeline.confirm_task(task_id=task.task_id, reviewer="engineer_a")

        cam_report = json.loads(Path(store_task.cam_report_path).read_text(encoding="utf-8"))
        voxel_summary = cam_report["voxel_simulation_report"]
        assert voxel_summary["passed"] is True
        assert voxel_summary["collision_count"] == 0
        assert voxel_summary["engine"] in ("rust", "python")

    @pytest.mark.unit
    def test_rapid_plunge_fails_voxel_and_atributes_feature(self, tmp_path: Path):
        """快速下扎：任务级体素失败，涉事特征被归因（block 5，fixture 加装刀/主轴后行号平移）。"""
        report_path = _build_report(tmp_path, PLUNGE_GCODE, feature_line_ranges=[[5, 5], [6, 6]])
        pipeline = _make_pipeline(tmp_path)
        task = pipeline.create_task(source_gcode_report_path=report_path, cam_backend="internal_only")

        result = asyncio.run(pipeline.run_pipeline(task.task_id))

        # 体素失败不阻塞任务进入 VALIDATED（工程师审核），但任务级判定为未通过
        assert result.status == "validated"
        assert task.voxel_check_passed is False
        assert task.voxel_collision_count > 0

        store_task = pipeline._store.get_task(task.task_id)
        by_id = {fr.feature_id: fr for fr in store_task.feature_validation_results}
        # feat_001 覆盖第 5 行（下扎段）→ 归因碰撞
        assert by_id["feat_001"].voxel_check_passed is False
        assert 5 in by_id["feat_001"].voxel_collision_blocks
        # feat_002 覆盖第 6 行（正常切削）→ 不被误归因
        assert by_id["feat_002"].voxel_check_passed is True
        # 任务 errors 含 DNC 拦截提示（闭环语义）
        assert any("DNC" in e for e in store_task.errors)

    @pytest.mark.unit
    def test_voxel_engine_error_fails_task(self, tmp_path: Path, monkeypatch):
        """体素校验器抛错 → 任务 FAILED（fail-closed，不允许带未知状态下发）。"""
        report_path = _build_report(tmp_path, SAFE_GCODE, feature_line_ranges=[[5, 5], [8, 8]])
        pipeline = _make_pipeline(tmp_path)
        task = pipeline.create_task(source_gcode_report_path=report_path, cam_backend="internal_only")

        def _boom(**kwargs):
            raise VoxelValidationError("模拟体素内核崩溃")

        monkeypatch.setattr(pipeline._voxel_validator, "validate", lambda **kwargs: _boom(**kwargs))

        result = asyncio.run(pipeline.run_pipeline(task.task_id))
        assert result.status == "failed"
        store_task = pipeline._store.get_task(task.task_id)
        assert store_task.voxel_check_passed is None


class TestToolDiameterThreading:
    """刀具实参透传：任务提供 > report 携带 > 配置默认，来源如实落盘。"""

    @pytest.mark.unit
    def test_task_tool_diameter_overrides_and_exports(self, tmp_path: Path):
        """任务显式提供实际刀具 → tool_source=actual，导出报告含刀具信息。"""
        report_path = _build_report(tmp_path, SAFE_GCODE, feature_line_ranges=[[5, 5], [8, 8]])
        pipeline = _make_pipeline(tmp_path)
        task = pipeline.create_task(
            source_gcode_report_path=report_path,
            cam_backend="internal_only",
            tool_diameter_mm=8.0,
            tool_type="flat",
        )

        asyncio.run(pipeline.run_pipeline(task.task_id))

        store_task = pipeline._store.get_task(task.task_id)
        assert store_task.voxel_tool_diameter_mm == 8.0
        assert store_task.voxel_tool_source == "actual"
        # 实参来源不应触发"配置默认刀具"警告
        assert not any("配置默认刀具直径" in w for w in store_task.warnings)

        # 审核 + 确认 → cam_report.json 携带刀具来源
        for fr in store_task.feature_validation_results:
            pipeline.review_task(task_id=task.task_id, feature_id=fr.feature_id, review_status="confirmed")
        pipeline.confirm_task(task_id=task.task_id, reviewer="engineer_a")
        cam_report = json.loads(Path(store_task.cam_report_path).read_text(encoding="utf-8"))
        assert cam_report["voxel_simulation_report"]["tool_diameter_mm"] == 8.0
        assert cam_report["voxel_simulation_report"]["tool_source"] == "actual"

    @pytest.mark.unit
    def test_report_tool_diameter_used_when_task_omits(self, tmp_path: Path):
        """任务未提供但 report.json 携带 → tool_source=report。"""
        report_path = _build_report(
            tmp_path, SAFE_GCODE, feature_line_ranges=[[5, 5], [8, 8]], tool_diameter_mm=6.0
        )
        pipeline = _make_pipeline(tmp_path)
        task = pipeline.create_task(source_gcode_report_path=report_path, cam_backend="internal_only")

        asyncio.run(pipeline.run_pipeline(task.task_id))

        store_task = pipeline._store.get_task(task.task_id)
        assert store_task.voxel_tool_diameter_mm == 6.0
        assert store_task.voxel_tool_source == "report"
        assert not any("配置默认刀具直径" in w for w in store_task.warnings)

    @pytest.mark.unit
    def test_config_default_source_warns_engineer(self, tmp_path: Path):
        """两级都未提供 → 回退配置默认，任务警告明示需人工确认。"""
        report_path = _build_report(tmp_path, SAFE_GCODE, feature_line_ranges=[[5, 5], [8, 8]])
        pipeline = _make_pipeline(tmp_path)
        task = pipeline.create_task(source_gcode_report_path=report_path, cam_backend="internal_only")

        asyncio.run(pipeline.run_pipeline(task.task_id))

        store_task = pipeline._store.get_task(task.task_id)
        assert store_task.voxel_tool_source == "config_default"
        assert store_task.voxel_tool_diameter_mm == 10.0  # CamValidationConfig 默认
        assert any("配置默认刀具直径" in w and "确认" in w for w in store_task.warnings)

    @pytest.mark.unit
    def test_create_task_rejects_invalid_tool_diameter(self, tmp_path: Path):
        """非法刀具直径（负数/零）→ 创建即拒绝。"""
        report_path = _build_report(tmp_path, SAFE_GCODE, feature_line_ranges=[[5, 5]])
        pipeline = _make_pipeline(tmp_path)
        with pytest.raises(Exception, match="tool_diameter_mm"):
            pipeline.create_task(
                source_gcode_report_path=report_path,
                cam_backend="internal_only",
                tool_diameter_mm=-2.0,
            )
