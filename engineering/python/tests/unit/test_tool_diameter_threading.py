"""刀具实参全链路打通测试：阶段 3 共识直径 → OperationPlan JSON → 阶段 6 report.json。

覆盖范围：
- _consensus_tool_diameter：一致取该值 / 混杂取最大（保守）/ 空取 None
- OperationPlan.to_dict ↔ load_operation_plan 序列化往返（含非法值容错）
- 阶段 6 run_pipeline 后 task.tool_diameter_mm 继承自 OperationPlan，
  report.json 导出携带该字段（阶段 7 gcode_loader 已消费）

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_tool_diameter_threading.py -v --no-cov
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.gcode_generation.generator_adapter import load_operation_plan
from app.gcode_generation.gcode_store import GCodeGenerationTask
from app.process_planning._stages_mixin import _StagesMixin
from app.process_planning.operation_sequencer import OperationPlan
from app.process_planning.tool_param_matcher import HoleProcessPlan


class _FakeMatchedTool:
    """MatchedTool 的最小替身（只用 tool.diameter_mm）。"""

    def __init__(self, diameter_mm: float | None) -> None:
        self.tool = MagicMock(diameter_mm=diameter_mm)


def _plan(diameters: list[float | None]) -> HoleProcessPlan:
    return HoleProcessPlan(
        hole_id="H001",
        hole_type="through_hole",
        operations=["钻孔"],
        tools=[_FakeMatchedTool(d) for d in diameters],
    )


class TestConsensusToolDiameter:
    """共识直径推导：宁保守不乐观。"""

    def test_uniform_diameter(self) -> None:
        assert _StagesMixin()._consensus_tool_diameter([_plan([8.0]), _plan([8.0])]) == 8.0

    def test_mixed_diameters_take_max(self) -> None:
        """直径混杂 → 取最大值（材料去除更多，碰撞仿真更保守）。"""
        assert _StagesMixin()._consensus_tool_diameter([_plan([8.0]), _plan([12.0]), _plan([6.0])]) == 12.0

    def test_no_tools_returns_none(self) -> None:
        assert _StagesMixin()._consensus_tool_diameter([_plan([]), _plan([None])]) is None
        assert _StagesMixin()._consensus_tool_diameter([]) is None

    def test_zero_and_negative_diameters_ignored(self) -> None:
        assert _StagesMixin()._consensus_tool_diameter([_plan([0.0]), _plan([-3.0])]) is None


class TestOperationPlanRoundtrip:
    """OperationPlan.to_dict ↔ load_operation_plan 序列化往返。"""

    def test_to_dict_includes_diameter_when_set(self, tmp_path: Path) -> None:
        plan = OperationPlan(tool_diameter_mm=8.0)
        assert plan.to_dict()["tool_diameter_mm"] == 8.0

    def test_to_dict_omits_diameter_when_none(self) -> None:
        """旧口径（未推导直径）的 JSON 保持字节兼容——不写 None 字段。"""
        assert "tool_diameter_mm" not in OperationPlan().to_dict()

    def test_load_operation_plan_reads_diameter(self, tmp_path: Path) -> None:
        raw = OperationPlan(tool_diameter_mm=6.5).to_dict()
        raw["operations"] = [
            {
                "seq": 1,
                "name": "OP01-A",
                "feature_name": "H001",
                "machining_method": "钻孔",
                "surface": "A",
                "tolerance_grade": "IT8",
            }
        ]
        raw["setups"] = [{"name": "S1", "surface": "A"}]
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        loaded = load_operation_plan(str(path))
        assert loaded.tool_diameter_mm == 6.5

    def test_load_operation_plan_invalid_diameter_ignored(self, tmp_path: Path) -> None:
        raw = OperationPlan().to_dict()
        raw["operations"] = [
            {
                "seq": 1,
                "name": "OP01-A",
                "feature_name": "H001",
                "machining_method": "钻孔",
                "surface": "A",
                "tolerance_grade": "IT8",
            }
        ]
        raw["setups"] = [{"name": "S1", "surface": "A"}]
        raw["tool_diameter_mm"] = "not-a-number"
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        assert load_operation_plan(str(path)).tool_diameter_mm is None


class TestStage6TaskCarriesDiameter:
    """阶段 6 任务继承 OperationPlan 直径并导出进 report.json。"""

    def test_task_assignment_and_export(self) -> None:
        task = GCodeGenerationTask(
            task_id="gc_test",
            source_chatter_report_path="ch.json",
            source_operation_plan_path="plan.json",
        )
        assert task.tool_diameter_mm is None
        assert "tool_diameter_mm" not in task.to_dict()

        task.tool_diameter_mm = 8.0
        assert task.to_dict()["tool_diameter_mm"] == 8.0

    @pytest.mark.unit
    def test_run_pipeline_assigns_diameter(self, monkeypatch) -> None:
        """run_pipeline 加载 OperationPlan 后把直径写入任务（链路最后一环）。"""
        plan = OperationPlan(tool_diameter_mm=7.5)
        _ = plan  # plan 经 patch 的 load_operation_plan 返回

        from app.gcode_generation import pipeline as pipeline_mod
        from app.gcode_generation.pipeline import GCodeGenerationPipeline

        task = GCodeGenerationTask(
            task_id="gc_thread",
            source_chatter_report_path="ch.json",
            source_operation_plan_path="plan.json",
        )

        chatter = MagicMock()
        chatter.feature_results = [MagicMock()]

        monkeypatch.setattr(pipeline_mod, "load_operation_plan", lambda _p: plan)
        monkeypatch.setattr(pipeline_mod, "get_task_store", lambda: MagicMock(get_task=lambda _id: task))
        monkeypatch.setattr(pipeline_mod, "generate_task_id", lambda: "gc_thread")
        monkeypatch.setattr(task, "__class__", GCodeGenerationTask, raising=False)

        pipeline = GCodeGenerationPipeline.__new__(GCodeGenerationPipeline)
        pipeline._store = MagicMock(get_task=lambda _id: task, update_task=lambda _t: None)
        pipeline._loader = MagicMock(load=lambda _p: chatter)
        pipeline._adapter = MagicMock(
            adapt=lambda **_kw: (
                MagicMock(is_valid=True, program_text="%", warnings=[], errors=[], checkpoints=[]),
                [],
            )
        )

        # store.update_task / export 等依赖宽松替身：只验证直径赋值这一环
        async def _run() -> None:
            try:
                await pipeline.run_pipeline("gc_thread")
            except Exception:  # noqa: BLE001 - 后续导出环节的替身缺口不掩盖断言
                pass

        asyncio.run(_run())
        assert task.tool_diameter_mm == 7.5
