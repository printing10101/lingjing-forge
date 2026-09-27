"""P1-3 白盒测试：DXF 六阶段流水线编排声明（零框架依赖，CI 独立跑）。"""

from __future__ import annotations

import pytest

from app.dxf._pipeline_stages import (
    STAGES,
    StageKey,
    StageStatus,
    is_fatal_stage,
    progress_of,
    should_abort_after,
    stage_failure_is_fatal,
    stage_index,
    stage_name,
    summarize_pipeline,
)


# 阶段声明对齐


class TestStageDeclaration:
    def test_six_stages_in_order(self) -> None:
        assert [s.key for s in STAGES] == [
            StageKey.PARSE,
            StageKey.FEATURES,
            StageKey.MODEL_CONVERT,
            StageKey.DATA_ASSEMBLY,
            StageKey.PROCESS_PLANNING,
            StageKey.VALIDATION,
        ]

    def test_stage_names_match_pipeline(self) -> None:
        # 与 app/dxf/pipeline.py DxfPipelineStage(name=...) 逐字对齐
        assert stage_name(StageKey.PARSE) == "DXF解析"
        assert stage_name(StageKey.FEATURES) == "特征提取"
        assert stage_name(StageKey.MODEL_CONVERT) == "3D模型转换"
        assert stage_name(StageKey.DATA_ASSEMBLY) == "数据组装"
        assert stage_name(StageKey.PROCESS_PLANNING) == "工艺规划"
        assert stage_name(StageKey.VALIDATION) == "结果验证"

    def test_stage_index(self) -> None:
        assert stage_index(StageKey.PARSE) == 0
        assert stage_index(StageKey.MODEL_CONVERT) == 2
        assert stage_index(StageKey.VALIDATION) == 5

    def test_string_key_accepted(self) -> None:
        assert stage_name("parse") == "DXF解析"
        assert stage_failure_is_fatal("model_convert") is False

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError):
            stage_name("bogus")


class TestFatalStages:
    def test_model_convert_is_degradable(self) -> None:
        # pipeline.py：Stage 3 失败仅降级继续（非致命）
        assert stage_failure_is_fatal(StageKey.MODEL_CONVERT) is False
        assert is_fatal_stage(StageKey.MODEL_CONVERT) is False

    @pytest.mark.parametrize(
        "key",
        [
            StageKey.PARSE,
            StageKey.FEATURES,
            StageKey.DATA_ASSEMBLY,
            StageKey.PROCESS_PLANNING,
            StageKey.VALIDATION,
        ],
    )
    def test_other_stages_are_fatal(self, key: StageKey) -> None:
        assert stage_failure_is_fatal(key) is True


class TestShouldAbort:
    def test_success_never_aborts(self) -> None:
        assert should_abort_after(StageKey.PARSE, failed=False) is False
        assert should_abort_after(StageKey.VALIDATION, failed=False) is False

    def test_fatal_stage_failure_aborts(self) -> None:
        assert should_abort_after(StageKey.PARSE, failed=True) is True
        assert should_abort_after(StageKey.FEATURES, failed=True) is True
        assert should_abort_after(StageKey.DATA_ASSEMBLY, failed=True) is True
        assert should_abort_after(StageKey.PROCESS_PLANNING, failed=True) is True
        assert should_abort_after(StageKey.VALIDATION, failed=True) is True

    def test_degradable_stage_failure_continues(self) -> None:
        # pipeline.py：3D模型转换失败 降级继续
        assert should_abort_after(StageKey.MODEL_CONVERT, failed=True) is False


class TestProgress:
    def test_empty_is_zero(self) -> None:
        assert progress_of({}) == 0.0

    def test_all_pending_zero(self) -> None:
        statuses = {k.value: StageStatus.PENDING.value for k in StageKey}
        assert progress_of(statuses) == 0.0

    def test_first_stage_done(self) -> None:
        statuses = {
            StageKey.PARSE.value: StageStatus.SUCCESS.value,
            StageKey.FEATURES.value: StageStatus.PENDING.value,
        }
        assert progress_of(statuses) == pytest.approx(1 / 6, abs=1e-4)

    def test_all_done_full(self) -> None:
        statuses = {k.value: StageStatus.SUCCESS.value for k in StageKey}
        assert progress_of(statuses) == 1.0

    def test_failed_counts_as_done(self) -> None:
        # 致命失败中止：已执行阶段计入完成度
        statuses = {
            StageKey.PARSE.value: StageStatus.SUCCESS.value,
            StageKey.FEATURES.value: StageStatus.FAILED.value,
        }
        assert progress_of(statuses) == pytest.approx(2 / 6, abs=1e-4)

    def test_missing_keys_ignored(self) -> None:
        statuses = {StageKey.PARSE.value: StageStatus.SUCCESS.value}
        assert progress_of(statuses) == pytest.approx(1 / 6, abs=1e-4)

    def test_accepts_plain_string_status(self) -> None:
        statuses = {"parse": "success", "features": "success"}
        assert progress_of(statuses) == pytest.approx(2 / 6, abs=1e-4)


class TestSummarize:
    def test_success(self) -> None:
        assert summarize_pipeline({}, success=True) == "DXF流水线处理成功"

    def test_failure_first_stage(self) -> None:
        statuses = {StageKey.PARSE.value: StageStatus.FAILED.value}
        assert summarize_pipeline(statuses, success=False) == "流水线在DXF解析阶段失败"

    def test_failure_later_stage(self) -> None:
        statuses = {
            StageKey.PARSE.value: StageStatus.SUCCESS.value,
            StageKey.PROCESS_PLANNING.value: StageStatus.FAILED.value,
        }
        assert summarize_pipeline(statuses, success=False) == "流水线在工艺规划阶段失败"

    def test_failure_without_stage_info(self) -> None:
        assert summarize_pipeline({}, success=False) == "DXF流水线执行失败"
