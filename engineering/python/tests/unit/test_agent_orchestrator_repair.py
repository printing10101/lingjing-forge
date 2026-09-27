"""W1.1 编排器校验修复闭环测试（对应 docs/产品叙事与战略对标-2026-09.md）。

覆盖四条路径：
1. 可修复错误（缺程序结束/负进给）→ 自动修复后重验通过；
2. 修复预算耗尽 → 校验步骤 FAILED + 回退转人工；
3. 不可修复错误（空程序）→ 立即转人工，repair_count=0；
4. 预算为 0（LNN_ORCHESTRATOR_MAX_REPAIRS=0）→ 功能等价关闭。
"""

from __future__ import annotations

import pytest

from app.agent.orchestrator import AgentOrchestrator, StepStatus

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

# 有程序号、无 M30 —— 触发 NO_PROGRAM_END（可修复）
GCODE_NO_END = "O1000\nG01 X10. F100.\n"
# 负进给在第 2 个有效行 —— 触发 NEGATIVE_FEED（可修复）
GCODE_NEG_FEED = "O1000\nG01 X10. F-50.\nM30\n"
# 只有注释行 —— 触发 EMPTY_PROGRAM（不可修复）
GCODE_EMPTY = "; only a comment\n"


def _make_orchestrator(tmp_path, gcode: str, **kwargs) -> AgentOrchestrator:
    """构造带桩步骤的编排器：上游四步用桩，validate_safety 用真实实现。"""
    orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "traces"), **kwargs)

    async def _dxf(input_data, context):
        return {"status": "success", "features": [], "metadata": {}}

    async def _pu(input_data, context):
        return {"status": "success", "task_type": "milling"}

    async def _pr(input_data, context):
        return {"status": "success", "parameters": {"spindle_rpm": 6000.0}, "operations": []}

    async def _gen(input_data, context):
        return {"status": "success", "gcode": gcode, "metadata": {}, "warnings": [], "errors": []}

    orch.register_step("dxf_parse", _dxf)
    orch.register_step("process_understanding", _pu)
    orch.register_step("parameter_recommend", _pr)
    orch.register_step("gcode_generate", _gen)
    return orch


class TestRepairLoop:
    async def test_repair_fixes_missing_program_end(self, tmp_path):
        """缺 M30 → 自动追加 M30 → 重验通过。"""
        orch = _make_orchestrator(tmp_path, GCODE_NO_END)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})

        assert result.success, result.fallback_reason
        assert result.repair_count == 1
        assert len(result.repair_history) == 1
        record = result.repair_history[0]
        assert record["error_codes"] == ["NO_PROGRAM_END"]
        assert record["actions"][0]["action"] == "append_program_end"
        assert any("M30" in a for a in record["applied"])
        # trace 中带修复轮次标记
        step_names = [s.step_name for s in result.steps]
        assert "gcode_generate#repair1" in step_names
        assert "validate_safety#repair1" in step_names
        # 最终产物通过校验，且重生成产物确实带上了 M30
        assert result.final_output.get("safety_valid") is True
        gen_out = [s for s in result.steps if s.step_name == "gcode_generate#repair1"][0]
        assert "M30" in gen_out.output["gcode"]

    async def test_repair_clamps_negative_feed(self, tmp_path):
        """负进给 → 取绝对值 → 重验通过。"""
        orch = _make_orchestrator(tmp_path, GCODE_NEG_FEED)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})

        assert result.success, result.fallback_reason
        assert result.repair_count == 1
        gen_out = [s for s in result.steps if s.step_name == "gcode_generate#repair1"][0]
        assert "F-50" not in gen_out.output["gcode"]
        assert "F50" in gen_out.output["gcode"]

    async def test_repair_budget_exhaustion_escalates(self, tmp_path):
        """校验永远不通过 → 预算耗尽 → 校验步骤 FAILED + 转人工。"""
        orch = _make_orchestrator(tmp_path, GCODE_NO_END, max_repair_attempts=2)

        # 用桩校验器模拟"修了也修不好"的持续失败（动作可规划但无效）
        async def _stub_validate(input_data, context):
            return {
                "status": "validation_failed",
                "safety_valid": False,
                "error_codes": ["NO_PROGRAM_END"],
                "warning_count": 0,
                "safety_report": {
                    "issues": [
                        {
                            "code": "NO_PROGRAM_END",
                            "severity": "error",
                            "message": "G 代码缺少程序结束指令（M30/M02）",
                            "context": {},
                        }
                    ]
                },
            }

        orch.register_step("validate_safety", _stub_validate)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})

        assert not result.success
        assert result.fallback_triggered
        assert "预算" in result.fallback_reason
        assert result.repair_count == 2
        assert result.steps[-1].status == StepStatus.FAILED
        assert result.steps[-1].step_name == "validate_safety#repair2"

    async def test_unrepairable_error_escalates_without_repair(self, tmp_path):
        """空程序（仅注释）→ 无可修复动作 → 立即转人工，repair_count=0。"""
        orch = _make_orchestrator(tmp_path, GCODE_EMPTY)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})

        assert not result.success
        assert result.fallback_triggered
        assert result.repair_count == 0
        assert result.repair_history == []
        assert "不支持自动修复" in result.fallback_reason

    async def test_zero_budget_disables_repair_loop(self, tmp_path):
        """预算 0 = 功能关闭：首次校验失败直接回退。"""
        orch = _make_orchestrator(tmp_path, GCODE_NO_END, max_repair_attempts=0)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})

        assert not result.success
        assert result.fallback_triggered
        assert result.repair_count == 0
        assert "预算（0 次）已耗尽" in result.fallback_reason

    async def test_upstream_failure_bypasses_repair_loop(self, tmp_path):
        """上游步骤（非校验）失败 → 走原有回退路径，不进入修复闭环。"""
        orch = _make_orchestrator(tmp_path, GCODE_NO_END)

        async def _bad_dxf(input_data, context):
            raise ValueError("dxf 文件损坏")

        orch.register_step("dxf_parse", _bad_dxf)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})

        assert not result.success
        assert result.fallback_triggered
        assert result.repair_count == 0
        assert "dxf_parse" in result.fallback_reason


class TestRepairTrace:
    async def test_pipeline_result_dict_includes_repair_fields(self, tmp_path):
        """to_dict / trace 序列化包含 repair_count 与 repair_history。"""
        orch = _make_orchestrator(tmp_path, GCODE_NO_END)
        result = await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})
        d = result.to_dict()
        assert d["repair_count"] == 1
        assert d["repair_history"][0]["attempt"] == 1

    async def test_statistics_count_repaired_pipelines(self, tmp_path):
        orch = _make_orchestrator(tmp_path, GCODE_NO_END)
        await orch.execute_pipeline("dxf_to_gcode", {"dxf_path": "x.dxf"})
        stats = orch.get_statistics()
        assert stats["repaired_pipelines"] == 1
        assert stats["repair_actions_total"] == 1


class TestPlanRepairs:
    def test_unmixable_error_blocks_all_repairs(self, tmp_path):
        """可修复与不可修复错误混合出现时，宁可转人工也不做部分修复。"""
        orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "t"))
        report = {
            "issues": [
                {"code": "NO_PROGRAM_END", "severity": "error", "message": "", "context": {}},
                {"code": "EMPTY_PROGRAM", "severity": "error", "message": "", "context": {}},
            ]
        }
        assert orch._plan_repairs(report) == []

    def test_warnings_do_not_trigger_repair(self, tmp_path):
        """仅 warning 级问题不进入修复闭环（交由工程师审核）。"""
        orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "t"))
        report = {
            "issues": [
                {"code": "UNKNOWN_G_M_CODE", "severity": "warning", "message": "", "context": {}}
            ]
        }
        assert orch._plan_repairs(report) == []
