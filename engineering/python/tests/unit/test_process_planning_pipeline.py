"""ProcessPlanningPipeline 端到端工艺规划流水线 单元测试。

目标：为 python/app/process_planning/pipeline.py 提供高覆盖率的单元测试。
覆盖范围：
- ProcessPlanningPipeline 初始化（成功 / 数据管理器失败）
- run: 完整 6 阶段流水线（成功 / 输入验证失败 / 孔识别失败 / 知识库失败 /
       工序规划失败 / G代码生成异常 / 输出验证失败）
- _validate_input: 各类输入校验（None / 非 dict / 空 dict / 缺 material /
                   holes 非列表 / 知识库加载失败）
- _build_features: 基准面构造、孔特征映射、cavity/boss/plane 多种类型输入
- _validate_pipeline_output: 输出完整性校验（含不可靠识别 / 无工序 / 无 G 代码）
- PipelineStage.to_dict / PipelineResult.to_dict 数据序列化
- 异常路径: QueryError 使用默认方案、material 不存在、G 代码生成异常
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from app.data.process_data_manager import (
    DataLoadError,
    MaterialEntry,
    QueryError,
)
from app.process_planning import pipeline as pipeline_module
from app.process_planning.boss_recognizer import BossFeature
from app.process_planning.cavity_recognizer import CavityFeature
from app.process_planning.gcode_generator import GCodeResult
from app.process_planning.hole_recognizer import (
    HoleFeature,
    HoleRecognitionResult,
)
from app.process_planning.operation_sequencer import OperationPlan, Operation
from app.process_planning.pipeline import (
    PipelineResult,
    PipelineStage,
    ProcessPlanningPipeline,
)
from app.process_planning.plane_recognizer import PlaneFeature
from app.process_planning.tool_param_matcher import HoleProcessPlan


# Fixtures


@pytest.fixture
def mock_data_manager():
    """提供一个简单的数据管理器 mock，get_material_info 返回有效材料。"""
    mgr = mock.MagicMock()
    material = MaterialEntry(
        id="steel_45",
        name="45#钢",
        category="steel",
        density_gcm3=7.85,
        hardness_hb=197.0,
        tensile_strength_mpa=600.0,
        cutting_performance="good",
        description="常用中碳钢",
    )
    mgr.get_material_info.return_value = material
    mgr.__bool__ = mock.MagicMock(return_value=True)
    return mgr


@pytest.fixture
def successful_tool_plan() -> HoleProcessPlan:
    """一个标准的 HoleProcessPlan。"""
    return HoleProcessPlan(
        hole_id="H001",
        hole_type="through_hole",
        operations=["钻中心孔", "钻孔", "铰孔"],
        tools=[],
        estimated_time_min=3.0,
    )


@pytest.fixture
def successful_hole_result() -> HoleRecognitionResult:
    """一个包含 2 个孔的成功识别结果。"""
    h1 = HoleFeature(
        hole_id="H001",
        type="through_hole",
        position_x=10.0,
        position_y=20.0,
        position_z=0.0,
        diameter=8.0,
        depth=15.0,
        tolerance_grade="H7",
        surface_roughness_ra=1.6,
        surface="A",
    )
    h2 = HoleFeature(
        hole_id="H002",
        type="blind_hole",
        position_x=30.0,
        position_y=40.0,
        position_z=0.0,
        diameter=6.0,
        depth=10.0,
        tolerance_grade="H8",
        surface_roughness_ra=3.2,
        surface="A",
    )
    return HoleRecognitionResult(
        holes=[h1, h2],
        total_count=2,
        type_summary={"through_hole": 1, "blind_hole": 1},
        warnings=[],
        errors=[],
        accuracy_metrics={"overall": 0.99, "recognition_rate": 1.0},
    )


@pytest.fixture
def successful_gcode_result() -> GCodeResult:
    """一个合法的 GCodeResult。"""
    return GCodeResult(
        controller_type="fanuc_0i",
        program_number=1000,
        program_text="O1000\nG90 G54\nM30\n",
        total_lines=4,
        operations_count=2,
        tool_count=2,
        estimated_cycle_time_min=12.5,
    )


@pytest.fixture
def successful_operation_plan() -> OperationPlan:
    """一个标准的 OperationPlan。"""
    return OperationPlan(
        operations=[
            Operation(
                seq=1,
                name="OP01-面A",
                feature_name="基准面A-上表面",
                machining_method="铣削",
                surface="A",
                tolerance_grade="IT7",
                estimated_time_min=2.0,
            ),
            Operation(
                seq=2,
                name="OP02-H001",
                feature_name="H001",
                machining_method="钻孔",
                surface="A",
                tolerance_grade="IT7",
                estimated_time_min=3.0,
            ),
        ],
        setups=[],
        estimated_time_min=5.0,
        face_change_count=0,
        fixture_recommendations=[],
    )


@pytest.fixture
def minimal_part_description() -> dict[str, Any]:
    return {
        "material": "45#钢",
        "part_type": "general",
        "holes": [
            {
                "id": "H001",
                "type": "through_hole",
                "position": {"x": 10.0, "y": 20.0, "z": 0.0},
                "diameter": 8.0,
                "depth": 15.0,
                "tolerance_grade": "H7",
                "surface_roughness_ra": 1.6,
            }
        ],
    }


# PipelineStage & PipelineResult 基础测试


class TestPipelineStage:
    def test_default_values(self):
        stage = PipelineStage(name="测试阶段")
        assert stage.name == "测试阶段"
        assert stage.status == "pending"
        assert stage.duration_ms == 0.0
        assert stage.input_summary == ""
        assert stage.output_summary == ""
        assert stage.errors == []
        assert stage.warnings == []

    def test_to_dict(self):
        stage = PipelineStage(
            name="阶段X",
            status="success",
            duration_ms=12.345,
            input_summary="in",
            output_summary="out",
            errors=["e1"],
            warnings=["w1"],
        )
        d = stage.to_dict()
        assert d["name"] == "阶段X"
        assert d["status"] == "success"
        assert d["duration_ms"] == 12.35
        assert d["input_summary"] == "in"
        assert d["output_summary"] == "out"
        assert d["errors"] == ["e1"]
        assert d["warnings"] == ["w1"]


class TestPipelineResult:
    def test_default_values(self):
        r = PipelineResult()
        assert r.success is False
        assert r.stages == []
        assert r.process_plans == []
        assert r.total_duration_ms == 0.0
        assert r.summary == ""
        assert r.hole_recognition is None
        assert r.operation_plan is None
        assert r.gcode_result is None

    def test_to_dict_minimal(self):
        r = PipelineResult(success=True, summary="ok", total_duration_ms=100.0)
        d = r.to_dict()
        assert d["success"] is True
        assert d["total_duration_ms"] == 100.0
        assert d["summary"] == "ok"
        assert d["stages"] == []
        # 当子结果都为 None 时，不应包含相关字段
        assert "hole_recognition" not in d
        assert "process_plans" not in d
        assert "operation_plan" not in d
        assert "gcode" not in d

    def test_to_dict_full(self, successful_hole_result, successful_operation_plan, successful_gcode_result):
        stage = PipelineStage(name="s1", status="success", duration_ms=5.0)
        r = PipelineResult(
            success=True,
            stages=[stage],
            hole_recognition=successful_hole_result,
            process_plans=[
                HoleProcessPlan(
                    hole_id="H1",
                    hole_type="through_hole",
                    operations=["钻孔"],
                    tools=[],
                    estimated_time_min=1.0,
                )
            ],
            operation_plan=successful_operation_plan,
            gcode_result=successful_gcode_result,
            total_duration_ms=200.0,
            summary="done",
        )
        d = r.to_dict()
        assert d["success"] is True
        assert len(d["stages"]) == 1
        assert d["stages"][0]["name"] == "s1"
        assert "hole_recognition" in d
        assert d["hole_recognition"]["total_count"] == 2
        assert len(d["process_plans"]) == 1
        assert d["operation_plan"]["estimated_time_min"] == 5.0
        assert d["gcode"]["controller_type"] == "fanuc_0i"
        assert d["gcode"]["program_text"] == successful_gcode_result.program_text


# 初始化


class TestPipelineInit:
    def test_init_success(self, mock_data_manager):
        """正常初始化：知识库加载成功。"""
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        assert p._data_valid is True
        assert p._data_manager is mock_data_manager
        assert p._hole_recognizer is not None
        assert p._tool_matcher is not None
        assert p._operation_sequencer is not None
        assert p._gcode_generator is not None

    def test_init_data_load_error(self):
        """DataLoadError 触发降级：_data_valid=False,_data_manager=None。"""
        with mock.patch.object(
            pipeline_module,
            "ProcessPlanningDataManager",
            side_effect=DataLoadError("知识库加载失败"),
        ):
            p = ProcessPlanningPipeline()
        assert p._data_valid is False
        assert p._data_manager is None
        # 其它子模块仍应正常创建
        assert p._hole_recognizer is not None
        assert p._tool_matcher is not None

    def test_init_unexpected_exception(self):
        """其它异常也降级。"""
        with mock.patch.object(
            pipeline_module,
            "ProcessPlanningDataManager",
            side_effect=RuntimeError("意外错误"),
        ):
            p = ProcessPlanningPipeline()
        assert p._data_valid is False
        assert p._data_manager is None


# _validate_input


class TestValidateInput:
    def test_none_input(self, mock_data_manager):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input(None)
        assert stage.status == "failed"
        assert "None" in stage.errors[0]

    def test_non_dict_input(self, mock_data_manager):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input([1, 2, 3])
        assert stage.status == "failed"
        assert "list" in stage.errors[0]

    def test_empty_dict_input(self, mock_data_manager):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input({})
        assert stage.status == "failed"
        assert any("空字典" in e for e in stage.errors)

    def test_missing_material(self, mock_data_manager):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input({"holes": []})
        assert stage.status == "failed"
        assert any("material" in e for e in stage.errors)

    def test_holes_not_list(self, mock_data_manager):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input({"material": "45#钢", "holes": "not a list"})
        assert stage.status == "failed"
        assert any("holes" in e for e in stage.errors)

    def test_valid_input(self, mock_data_manager):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input({"material": "45#钢", "holes": [{"id": "H1"}]})
        assert stage.status == "success"
        assert stage.errors == []
        assert "45#钢" in stage.input_summary
        assert "1" in stage.input_summary

    def test_features_field_used_as_holes(self, mock_data_manager):
        """当没有 holes 但有 features 时，应兼容使用 features 字段。"""
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        stage = p._validate_input({"material": "45#钢", "features": [{"a": 1}, {"b": 2}]})
        assert stage.status == "success"
        assert "2" in stage.input_summary

    def test_data_invalid_warning(self):
        """当知识库加载失败时，输入验证应附加 warning。"""
        with mock.patch.object(
            pipeline_module,
            "ProcessPlanningDataManager",
            side_effect=DataLoadError("x"),
        ):
            p = ProcessPlanningPipeline()
        stage = p._validate_input({"material": "45#钢"})
        assert stage.status == "success"
        assert any("知识库" in w for w in stage.warnings)


# run: 完整流水线


class TestPipelineRunSuccess:
    def test_full_success(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_operation_plan,
        successful_gcode_result,
        successful_tool_plan,
    ):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()

        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", return_value=successful_tool_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", return_value=successful_gcode_result),
        ):
            result = p.run(minimal_part_description, controller_type="fanuc_0i")

        assert result.success is True
        # 流水线共 6 阶段: 输入验证、孔识别、知识库查询、工序规划、G代码生成、结果验证
        stage_names = [s.name for s in result.stages]
        assert "输入验证" in stage_names
        assert "孔特征识别" in stage_names
        assert "知识库查询" in stage_names
        assert "工序规划" in stage_names
        assert "G代码生成" in stage_names
        assert "结果验证" in stage_names
        assert result.hole_recognition is successful_hole_result
        assert result.operation_plan is successful_operation_plan
        assert result.gcode_result is successful_gcode_result
        assert result.total_duration_ms >= 0
        assert "流水线执行成功" in result.summary
        assert "45#钢" in result.summary

    def test_run_with_no_holes(
        self,
        mock_data_manager,
        successful_operation_plan,
        successful_gcode_result,
        successful_tool_plan,
    ):
        """无孔场景：孔识别返回空列表但无 error，流水线继续。"""
        empty_hr = HoleRecognitionResult(
            holes=[],
            total_count=0,
            type_summary={},
            warnings=[],
            errors=[],
            accuracy_metrics={"overall": 1.0},
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()

        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=empty_hr),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", return_value=successful_gcode_result),
        ):
            result = p.run(
                {"material": "45#钢", "part_type": "plate", "holes": []},
                program_number=2000,
            )

        assert result.success is True
        assert result.process_plans == []
        # 仍然有工序规划结果（来自操作排序器）
        assert result.operation_plan is successful_operation_plan
        assert result.gcode_result is successful_gcode_result
        assert "0个孔" in result.summary


class TestPipelineRunFailures:
    def test_input_validation_failure(self, mock_data_manager):
        """输入验证阶段失败：整体 success=False，仅有输入验证阶段。"""
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        result = p.run(None)
        assert result.success is False
        assert len(result.stages) == 1
        assert result.stages[0].name == "输入验证"
        assert "输入验证" in result.summary

    def test_hole_recognition_failure(self, mock_data_manager, minimal_part_description):
        """孔识别失败：流水线在第 2 阶段终止。"""
        hr = HoleRecognitionResult(
            holes=[],
            total_count=0,
            type_summary={},
            warnings=[],
            errors=["识别失败"],
            accuracy_metrics={"overall": 0.0},
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=hr):
            result = p.run(minimal_part_description)
        assert result.success is False
        # 阶段名是 "孔特征识别"
        assert any("孔特征识别" in s.name for s in result.stages)
        assert result.hole_recognition is hr
        assert "孔识别" in result.summary

    def test_material_not_found(self, mock_data_manager, minimal_part_description, successful_hole_result):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(p._tool_matcher, "get_material_info", return_value=None),
        ):
            result = p.run(minimal_part_description)
        assert result.success is False
        assert any("知识库" in s.name for s in result.stages)
        assert "材料" in result.summary

    def test_query_error_uses_default_plan(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_operation_plan,
        successful_gcode_result,
    ):
        """刀具匹配抛出 QueryError 时应使用默认方案，流水线继续。"""
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()

        call_count = {"n": 0}

        def side_effect_plan(*args, **kwargs):
            call_count["n"] += 1
            raise QueryError(f"未找到孔径 {args[2] if len(args) > 2 else 'x'}mm 的刀具")

        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", side_effect=side_effect_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", return_value=successful_gcode_result),
        ):
            result = p.run(minimal_part_description)

        # 知识库阶段仍然成功（被记录为 warning）
        kb_stage = next(s for s in result.stages if s.name == "知识库查询")
        assert kb_stage.status == "success"
        assert any("默认刀具" in w for w in kb_stage.warnings)
        # 默认方案会被附加到 process_plans
        assert all(p.hole_id != "" for p in result.process_plans)
        assert call_count["n"] == len(successful_hole_result.holes)

    def test_no_features(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_gcode_result,
        successful_operation_plan,
    ):
        """当 features 列表为空时（既无孔也无特征字典），仍会构造 1 个基准面。
        本测试主要验证 _build_features 在空输入下不抛异常并返回基准面。
        """
        empty_hr = HoleRecognitionResult(
            holes=[],
            total_count=0,
            type_summary={},
            warnings=[],
            errors=[],
            accuracy_metrics={"overall": 1.0},
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        # 验证 _build_features 在空输入下仍能返回基准面 MachiningFeature
        features = p._build_features(empty_hr, [], {})
        assert len(features) >= 1
        assert features[0].is_datum_candidate is True

    def test_operation_planning_exception(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_tool_plan,
    ):
        """工序规划抛出异常时的处理: 该路径目前会让异常向上冒,我们仅验证不崩溃。"""
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", return_value=successful_tool_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", side_effect=RuntimeError("sequencer crashed")),
        ):
            with pytest.raises(RuntimeError, match="sequencer crashed"):
                p.run(minimal_part_description)

    def test_gcode_generation_exception(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_operation_plan,
        successful_tool_plan,
    ):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", return_value=successful_tool_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", side_effect=RuntimeError("gcode boom")),
        ):
            result = p.run(minimal_part_description)
        assert result.success is False
        assert any("G代码生成" in s.name for s in result.stages)
        assert "G代码" in result.summary

    def test_output_validation_failure_short_gcode(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_operation_plan,
        successful_tool_plan,
    ):
        """结果验证阶段：G代码过短 -> 失败但 summary 仍然产生。"""
        bad_gcode = GCodeResult(
            controller_type="fanuc_0i",
            program_number=1000,
            program_text="",
            total_lines=0,
            operations_count=0,
            tool_count=0,
            estimated_cycle_time_min=0.0,
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", return_value=successful_tool_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", return_value=bad_gcode),
        ):
            result = p.run(minimal_part_description)

        # result.success 取决于 validation 阶段 status, 短 G 代码触发 failed
        # 同时其它 stage 是 success, 因此 success=False 但 stages 中其它阶段都成功
        validation_stage = next(s for s in result.stages if s.name == "结果验证")
        assert validation_stage.status == "failed"
        assert any("过短" in e for e in validation_stage.errors)

    def test_output_validation_warning_for_no_setup(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_tool_plan,
        successful_gcode_result,
    ):
        """空装夹列表触发 warning。"""
        plan_no_setup = OperationPlan(
            operations=[
                Operation(
                    seq=1,
                    name="OP01",
                    feature_name="F1",
                    machining_method="钻",
                    surface="A",
                    tolerance_grade="IT7",
                )
            ],
            setups=[],
            estimated_time_min=1.0,
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", return_value=successful_tool_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=plan_no_setup),
            mock.patch.object(p._gcode_generator, "generate", return_value=successful_gcode_result),
        ):
            result = p.run(minimal_part_description)
        validation_stage = next(s for s in result.stages if s.name == "结果验证")
        assert any("装夹" in w for w in validation_stage.warnings)

    def test_output_validation_warning_for_unreliable_recognition(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_tool_plan,
        successful_operation_plan,
        successful_gcode_result,
    ):
        """孔识别准确率 < 99% -> warning。"""
        hr = HoleRecognitionResult(
            holes=[],
            total_count=0,
            type_summary={},
            warnings=[],
            errors=[],
            accuracy_metrics={"overall": 0.8},
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=hr),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", return_value=successful_gcode_result),
        ):
            result = p.run(minimal_part_description)
        validation_stage = next(s for s in result.stages if s.name == "结果验证")
        assert any("可靠性偏低" in w for w in validation_stage.warnings)

    def test_gcode_completed_with_errors_status(
        self,
        mock_data_manager,
        minimal_part_description,
        successful_hole_result,
        successful_operation_plan,
        successful_tool_plan,
    ):
        """GCodeResult.is_valid=False 时,阶段状态应为 completed_with_errors。"""
        gcode_with_errors = GCodeResult(
            controller_type="fanuc_0i",
            program_number=1000,
            program_text="O1000\nG90\nM30\n",
            total_lines=4,
            operations_count=1,
            tool_count=1,
            estimated_cycle_time_min=5.0,
            errors=["语法警告"],
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        with (
            mock.patch.object(p._hole_recognizer, "recognize_holes", return_value=successful_hole_result),
            mock.patch.object(
                p._tool_matcher, "get_material_info", return_value=mock_data_manager.get_material_info.return_value
            ),
            mock.patch.object(p._tool_matcher, "plan_for_hole", return_value=successful_tool_plan),
            mock.patch.object(p._operation_sequencer, "plan_operations", return_value=successful_operation_plan),
            mock.patch.object(p._gcode_generator, "generate", return_value=gcode_with_errors),
        ):
            result = p.run(minimal_part_description)
        gcode_stage = next(s for s in result.stages if s.name == "G代码生成")
        assert gcode_stage.status == "completed_with_errors"


# _build_features


class TestBuildFeatures:
    def test_build_features_only_datum(self, mock_data_manager, successful_hole_result):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        empty_plans: list[HoleProcessPlan] = []
        features = p._build_features(successful_hole_result, empty_plans, {})
        # 即便没有孔,_build_features 仍会返回 1 个基准面 MachiningFeature
        assert len(features) == 1
        assert features[0].name == "基准面A-上表面"
        assert features[0].is_datum_candidate is True

    def test_build_features_with_holes(self, mock_data_manager, successful_hole_result, successful_tool_plan):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        plans = [
            HoleProcessPlan(hole_id=h.hole_id, hole_type=h.type, operations=["钻孔"], tools=[], estimated_time_min=1.0)
            for h in successful_hole_result.holes
        ]
        features = p._build_features(successful_hole_result, plans, {})
        # 1 基准面 + 2 孔
        assert len(features) == 3
        hole_features = [f for f in features if f.geometric_type == "cylinder"]
        assert len(hole_features) == 2
        assert hole_features[0].name == "H001"
        assert hole_features[0].type in ("through_hole", "blind_hole")

    def test_build_features_with_cavity_dict(self, mock_data_manager, successful_hole_result):
        cavity_dict = {
            "name": "C001",
            "type": "blind_pocket",
            "geometric_type": "pocket",
            "tolerance_grade": "IT7",
            "surface_roughness_ra": 1.6,
            "is_datum_candidate": False,
            "priority": "medium",
            "surface": "A",
            "dimensions": {"length": 50, "width": 30, "depth": 5},
            "parent_feature": "",
            "tolerances": {},
        }
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"cavities": [cavity_dict]})
        names = [f.name for f in features]
        assert "基准面A-上表面" in names
        assert "C001" in names

    def test_build_features_with_cavity_object(self, mock_data_manager, successful_hole_result):
        cavity = CavityFeature(
            cavity_id="C002",
            type="through_pocket",
            length=40.0,
            width=20.0,
            depth=0.0,
            center_x=10.0,
            center_y=5.0,
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"cavities": [cavity]})
        names = [f.name for f in features]
        assert "C002" in names

    def test_build_features_with_boss_dict(self, mock_data_manager, successful_hole_result):
        boss_dict = {
            "name": "B001",
            "type": "circular_boss",
            "geometric_type": "circular_boss",
            "tolerance_grade": "IT7",
            "surface_roughness_ra": 1.6,
            "is_datum_candidate": True,
            "priority": "high",
            "surface": "A",
            "dimensions": {"diameter": 30, "height": 10},
            "parent_feature": "",
            "tolerances": {},
        }
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"bosses": [boss_dict]})
        assert any(f.name == "B001" for f in features)

    def test_build_features_with_boss_object(self, mock_data_manager, successful_hole_result):
        boss = BossFeature(
            boss_id="B002",
            type="rectangular_boss",
            diameter=0.0,
            side_length=20.0,
            height=5.0,
            center_x=0.0,
            center_y=0.0,
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"bosses": [boss]})
        assert any(f.name == "B002" for f in features)

    def test_build_features_with_plane_dict(self, mock_data_manager, successful_hole_result):
        plane_dict = {
            "name": "P001",
            "type": "top_plane",
            "geometric_type": "plane",
            "tolerance_grade": "IT7",
            "surface_roughness_ra": 1.6,
            "is_datum_candidate": True,
            "priority": "high",
            "surface": "A",
            "dimensions": {"area": 10000},
            "parent_feature": "",
            "tolerances": {},
        }
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"planes": [plane_dict]})
        assert any(f.name == "P001" for f in features)

    def test_build_features_with_plane_object(self, mock_data_manager, successful_hole_result):
        plane = PlaneFeature(
            plane_id="P002",
            type="top_plane",
            area=2000.0,
            normal_z=1.0,
            length=100.0,
            width=50.0,
            center_x=0.0,
            center_y=0.0,
        )
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"planes": [plane]})
        assert any(f.name == "P002" for f in features)

    def test_build_features_skips_unknown_types(self, mock_data_manager, successful_hole_result):
        """不是 CavityFeature / dict 的项会被跳过。"""
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        features = p._build_features(successful_hole_result, [], {"cavities": ["invalid_string", 123]})
        # 仅基准面
        assert len(features) == 1


# _validate_pipeline_output


class TestValidateOutput:
    def test_valid_output(
        self,
        mock_data_manager,
        successful_hole_result,
        successful_operation_plan,
        successful_gcode_result,
    ):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            operation_plan=successful_operation_plan,
            gcode_result=successful_gcode_result,
        )
        errors, warnings = p._validate_pipeline_output(result)
        assert errors == []
        # 由于 recognition accuracy = 0.99,可靠;operation_plan.setups 为空触发 warning
        assert isinstance(warnings, list)

    def test_missing_operation_plan(self, mock_data_manager, successful_hole_result, successful_gcode_result):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            gcode_result=successful_gcode_result,
        )
        errors, _ = p._validate_pipeline_output(result)
        assert any("缺少工序规划" in e for e in errors)

    def test_empty_operation_plan(self, mock_data_manager, successful_hole_result, successful_gcode_result):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        empty_plan = OperationPlan(operations=[], setups=[], estimated_time_min=0.0)
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            operation_plan=empty_plan,
            gcode_result=successful_gcode_result,
        )
        errors, _ = p._validate_pipeline_output(result)
        assert any("工序规划结果为空" in e for e in errors)

    def test_missing_gcode(self, mock_data_manager, successful_hole_result, successful_operation_plan):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            operation_plan=successful_operation_plan,
        )
        errors, _ = p._validate_pipeline_output(result)
        assert any("缺少G代码" in e for e in errors)

    def test_gcode_too_short(self, mock_data_manager, successful_hole_result, successful_operation_plan):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        short_gc = GCodeResult(
            controller_type="fanuc_0i",
            program_number=1,
            program_text="  ",
            total_lines=0,
            operations_count=0,
            tool_count=0,
            estimated_cycle_time_min=0.0,
        )
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            operation_plan=successful_operation_plan,
            gcode_result=short_gc,
        )
        errors, _ = p._validate_pipeline_output(result)
        assert any("过短" in e for e in errors)

    def test_gcode_zero_tool_count_warning(self, mock_data_manager, successful_hole_result, successful_operation_plan):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        gc = GCodeResult(
            controller_type="fanuc_0i",
            program_number=1,
            program_text="O0001\nM30\n",
            total_lines=2,
            operations_count=0,
            tool_count=0,
            estimated_cycle_time_min=0.0,
        )
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            operation_plan=successful_operation_plan,
            gcode_result=gc,
        )
        _, warnings = p._validate_pipeline_output(result)
        assert any("未使用任何刀具" in w for w in warnings)

    def test_gcode_with_errors(self, mock_data_manager, successful_hole_result, successful_operation_plan):
        with mock.patch.object(pipeline_module, "ProcessPlanningDataManager", return_value=mock_data_manager):
            p = ProcessPlanningPipeline()
        gc = GCodeResult(
            controller_type="fanuc_0i",
            program_number=1,
            program_text="O0001\nG90\nM30\n",
            total_lines=3,
            operations_count=1,
            tool_count=1,
            estimated_cycle_time_min=1.0,
            errors=["语法错误"],
        )
        result = PipelineResult(
            hole_recognition=successful_hole_result,
            operation_plan=successful_operation_plan,
            gcode_result=gc,
        )
        errors, _ = p._validate_pipeline_output(result)
        assert any("语法错误" in e for e in errors)
