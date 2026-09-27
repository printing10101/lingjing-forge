"""feature_extraction 白盒审计/分类/审核状态机 单元测试。

覆盖「自主代码重构」P1-1 产出的纯 Python 白盒逻辑：
- app.feature_extraction._feature_classifier（特征分类判定规则）
- app.feature_extraction._review_state_machine（审核状态机）

这两个模块零框架依赖（不 import scipy / torch / sklearn），
因此在 torch 环境残缺的 CI 上也可稳定运行。
"""

from __future__ import annotations

import math

import pytest

from app.feature_extraction._feature_classifier import (
    ACTION_CONFIRMED,
    ACTION_EDITED,
    ACTION_REJECTED,
    FEATURE_BOSS,
    FEATURE_HOLE,
    FeatureClassificationError,
    classify_hole_or_boss,
    classify_hole_or_boss_deep,
    is_known_feature_type,
    is_valid_review_action,
    validate_feature_params,
    validate_offset,
    validate_threshold,
)
from app.feature_extraction._review_state_machine import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_FEATURES_EXTRACTED,
    STATUS_PENDING,
    STATUS_REVIEWED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    FeatureReviewStateMachine,
    ReviewStateMachineError,
)
from app.feature_extraction.feature_store import ExtractedFeature

pytestmark = pytest.mark.unit


# validate_offset / validate_threshold


class TestValidateOffset:
    def test_valid_values(self):
        assert validate_offset(0.0) == 0.0
        assert validate_offset(3.14) == pytest.approx(3.14)
        assert validate_offset(-2.5) == pytest.approx(-2.5)

    def test_coerces_string_numeric(self):
        assert validate_offset("1.5") == pytest.approx(1.5)

    def test_nan_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_offset(math.nan)

    def test_inf_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_offset(math.inf)
        with pytest.raises(FeatureClassificationError):
            validate_offset(-math.inf)

    def test_non_numeric_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_offset("abc")
        with pytest.raises(FeatureClassificationError):
            validate_offset(None)


class TestValidateThreshold:
    def test_valid_positive(self):
        assert validate_threshold(0.5) == pytest.approx(0.5)

    def test_zero_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_threshold(0.0)

    def test_negative_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_threshold(-1.0)

    def test_nan_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_threshold(math.nan)

    def test_non_numeric_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_threshold("abc")
        with pytest.raises(FeatureClassificationError):
            validate_threshold(None)


# classify_hole_or_boss 判定规则


class TestClassifyHoleOrBoss:
    def test_depressed_is_hole(self):
        # offset < -threshold HOLE
        assert classify_hole_or_boss(-2.0, 1.0) == FEATURE_HOLE

    def test_raised_is_boss(self):
        # offset > +threshold BOSS
        assert classify_hole_or_boss(2.0, 1.0) == FEATURE_BOSS

    def test_within_threshold_defaults_hole(self):
        # |offset| <= threshold 默认 HOLE
        assert classify_hole_or_boss(0.5, 1.0) == FEATURE_HOLE
        assert classify_hole_or_boss(0.0, 1.0) == FEATURE_HOLE
        assert classify_hole_or_boss(-0.5, 1.0) == FEATURE_HOLE

    def test_exact_boundary_negative_is_hole(self):
        # offset == -threshold < 不成立，落入边界 默认 HOLE
        assert classify_hole_or_boss(-1.0, 1.0) == FEATURE_HOLE

    def test_exact_boundary_positive_default(self):
        # offset == +threshold > 不成立 默认 HOLE
        assert classify_hole_or_boss(1.0, 1.0) == FEATURE_HOLE

    def test_default_type_boss(self):
        # 无法判定时若指定 default=BOSS BOSS
        assert classify_hole_or_boss(0.0, 1.0, default_type=FEATURE_BOSS) == FEATURE_BOSS

    def test_invalid_default_type_raises(self):
        with pytest.raises(FeatureClassificationError):
            classify_hole_or_boss(0.0, 1.0, default_type="not_a_type")

    def test_invalid_threshold_raises(self):
        with pytest.raises(FeatureClassificationError):
            classify_hole_or_boss(1.0, 0.0)

    def test_invalid_offset_raises(self):
        with pytest.raises(FeatureClassificationError):
            classify_hole_or_boss(math.nan, 1.0)

    def test_deep_returns_normalized_offset(self):
        ftype, offset = classify_hole_or_boss_deep(-2.0, 1.0)
        assert ftype == FEATURE_HOLE
        assert offset == pytest.approx(-2.0)


# 辅助判定函数


class TestHelperPredicates:
    def test_is_known_feature_type(self):
        assert is_known_feature_type("plane")
        assert is_known_feature_type("cylinder")
        assert is_known_feature_type("hole")
        assert is_known_feature_type("boss")
        assert is_known_feature_type("unknown")
        assert not is_known_feature_type("invalid")
        assert not is_known_feature_type("")

    def test_is_valid_review_action(self):
        assert is_valid_review_action(ACTION_CONFIRMED)
        assert is_valid_review_action(ACTION_REJECTED)
        assert is_valid_review_action(ACTION_EDITED)
        assert not is_valid_review_action("invalid_action")
        assert not is_valid_review_action("")


class TestValidateFeatureParams:
    def test_plain_copy(self):
        params = {"radius_mm": 10.0, "inlier_count": 5}
        out = validate_feature_params("hole", params)
        assert out == {"radius_mm": 10.0, "inlier_count": 5}
        # 不就地修改入参
        assert params == {"radius_mm": 10.0, "inlier_count": 5}

    def test_coerces_numeric_strings(self):
        out = validate_feature_params("boss", {"radius_mm": "5.5", "height_mm": "2"})
        assert out["radius_mm"] == pytest.approx(5.5)
        assert out["height_mm"] == 2.0

    def test_missing_optional_keys_ok(self):
        out = validate_feature_params("plane", {"normal": [0, 0, 1]})
        assert out == {"normal": [0, 0, 1]}

    def test_unknown_type_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_feature_params("not_a_type", {})

    def test_nan_value_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_feature_params("hole", {"radius_mm": math.nan})

    def test_non_numeric_radius_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_feature_params("hole", {"radius_mm": "abc"})

    def test_non_int_inlier_count_raises(self):
        with pytest.raises(FeatureClassificationError):
            validate_feature_params("hole", {"inlier_count": "abc"})


# FeatureReviewStateMachine 审核状态机


class TestReviewStateMachineTaskPredicates:
    def test_can_review_only_features_extracted(self):
        assert FeatureReviewStateMachine.can_review(STATUS_FEATURES_EXTRACTED)
        assert not FeatureReviewStateMachine.can_review(STATUS_PENDING)
        assert not FeatureReviewStateMachine.can_review(STATUS_RUNNING)
        assert not FeatureReviewStateMachine.can_review(STATUS_REVIEWED)
        assert not FeatureReviewStateMachine.can_review(STATUS_SUCCEEDED)
        assert not FeatureReviewStateMachine.can_review(STATUS_FAILED)
        assert not FeatureReviewStateMachine.can_review(STATUS_CANCELLED)

    def test_assert_reviewable_pass(self):
        FeatureReviewStateMachine.assert_reviewable(STATUS_FEATURES_EXTRACTED)

    def test_assert_reviewable_raises_on_other(self):
        with pytest.raises(ReviewStateMachineError) as exc:
            FeatureReviewStateMachine.assert_reviewable(STATUS_PENDING)
        assert "不允许审核" in str(exc.value)
        assert "features_extracted" in str(exc.value)

    def test_assert_valid_action(self):
        FeatureReviewStateMachine.assert_valid_action(ACTION_CONFIRMED)
        FeatureReviewStateMachine.assert_valid_action(ACTION_REJECTED)
        FeatureReviewStateMachine.assert_valid_action(ACTION_EDITED)

    def test_assert_valid_action_raises_on_invalid(self):
        with pytest.raises(ReviewStateMachineError) as exc:
            FeatureReviewStateMachine.assert_valid_action("invalid")
        assert "非法 action" in str(exc.value)

    def test_action_requires_edited_params(self):
        assert FeatureReviewStateMachine.action_requires_edited_params(ACTION_EDITED)
        assert not FeatureReviewStateMachine.action_requires_edited_params(ACTION_CONFIRMED)

    def test_can_export(self):
        assert FeatureReviewStateMachine.can_export(STATUS_FEATURES_EXTRACTED)
        assert FeatureReviewStateMachine.can_export(STATUS_REVIEWED)
        assert not FeatureReviewStateMachine.can_export(STATUS_SUCCEEDED)
        assert not FeatureReviewStateMachine.can_export(STATUS_PENDING)

    def test_assert_exportable_raises(self):
        with pytest.raises(ReviewStateMachineError) as exc:
            FeatureReviewStateMachine.assert_exportable(STATUS_PENDING)
        assert "不允许导出" in str(exc.value)

    def test_next_state_after_export(self):
        assert FeatureReviewStateMachine.next_state_after_export() == STATUS_SUCCEEDED


class TestReviewStateMachineReviewSemantics:
    def test_is_feature_pending(self):
        assert FeatureReviewStateMachine.is_feature_pending("pending")
        assert not FeatureReviewStateMachine.is_feature_pending("confirmed")
        assert not FeatureReviewStateMachine.is_feature_pending("")

    def test_all_features_reviewed_empty_true(self):
        assert FeatureReviewStateMachine.all_features_reviewed([]) is True

    def test_all_features_reviewed(self):
        assert FeatureReviewStateMachine.all_features_reviewed(["confirmed", "rejected", "edited"]) is True

    def test_all_features_reviewed_false_when_pending(self):
        assert FeatureReviewStateMachine.all_features_reviewed(["confirmed", "pending"]) is False

    def test_all_features_reviewed_any_pending_false(self):
        assert FeatureReviewStateMachine.all_features_reviewed(["confirmed", "edited", "pending"]) is False

    def test_next_state_after_all_reviewed_with_features(self):
        assert FeatureReviewStateMachine.next_state_after_all_reviewed(has_features=True) == STATUS_REVIEWED

    def test_next_state_after_all_reviewed_without_features(self):
        assert FeatureReviewStateMachine.next_state_after_all_reviewed(has_features=False) == STATUS_REVIEWED

    def test_idempotent_all_reviewed(self):
        """幂等：相同输入重复判定 → 相同输出。"""
        statuses = ["confirmed", "edited"]
        assert FeatureReviewStateMachine.all_features_reviewed(statuses) is True
        assert FeatureReviewStateMachine.all_features_reviewed(statuses) is True
        assert FeatureReviewStateMachine.next_state_after_all_reviewed(True) == STATUS_REVIEWED

    def test_all_status_constants_match_enums(self):
        from app.feature_extraction import FeatureExtractionTaskStatus

        assert STATUS_PENDING == FeatureExtractionTaskStatus.PENDING.value
        assert STATUS_RUNNING == FeatureExtractionTaskStatus.RUNNING.value
        assert STATUS_FEATURES_EXTRACTED == FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value
        assert STATUS_REVIEWED == FeatureExtractionTaskStatus.REVIEWED.value
        assert STATUS_SUCCEEDED == FeatureExtractionTaskStatus.SUCCEEDED.value
        assert STATUS_FAILED == FeatureExtractionTaskStatus.FAILED.value
        assert STATUS_CANCELLED == FeatureExtractionTaskStatus.CANCELLED.value


# Pipeline 集成回归：状态机委托路径（绕过 torch 环境，直接驱动真实 store）


def _feature(fid, ftype="plane", review="pending", params=None) -> ExtractedFeature:
    return ExtractedFeature(
        feature_id=fid,
        feature_type=ftype,
        params=params or {"radius_mm": 10.0, "height_mm": 5.0},
        confidence=0.8,
        review_status=review,
    )


class _FakeCfg:
    """最小配置桩：避免真实 config 触发 torch/scipy 崩溃。"""

    max_concurrent = 1
    output_dir = "/tmp"  # type: ignore[assignment]


def _make_pipeline(tmp_path):
    """绕过 FeatureExtractionPipeline.__init__，注入真实 store + 桩 cfg。"""
    from app.feature_extraction import FeatureStore
    from app.feature_extraction.pipeline import FeatureExtractionPipeline

    store = FeatureStore(persist_dir=tmp_path / "tasks")
    pipeline = FeatureExtractionPipeline.__new__(FeatureExtractionPipeline)
    pipeline._store = store  # type: ignore[attr-defined]
    pipeline._cfg = _FakeCfg()  # type: ignore[attr-defined]
    return pipeline, store


def _make_task(store, features):
    import time

    from app.feature_extraction import (
        FeatureExtractionTask,
        FeatureExtractionTaskStatus,
    )

    task = FeatureExtractionTask(
        task_id="t1",
        created_at=time.time(),
        updated_at=time.time(),
        status=FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value,
        input_mesh_path="/tmp/mesh.ply",
        features=features,
    )
    store.create(task)
    return task


class TestPipelineReviewStateMachineIntegration:
    def test_review_confirmed_then_all_reviewed(self, tmp_path):
        pipeline, store = _make_pipeline(tmp_path)
        _make_task(store, [_feature("f1"), _feature("f2")])
        pipeline.review_feature("t1", "f1", "confirmed", reviewed_by="eng")
        pipeline.review_feature("t1", "f2", "rejected", reviewed_by="eng")
        assert store.get("t1").status == "reviewed"

    def test_review_invalid_action_rejected(self, tmp_path):
        pipeline, _store = _make_pipeline(tmp_path)
        _make_task(_store, [_feature("f1")])
        with pytest.raises(Exception) as exc:
            pipeline.review_feature("t1", "f1", "invalid_action")
        assert "非法 action" in str(exc.value)

    def test_review_wrong_status_rejected(self, tmp_path):
        pipeline, store = _make_pipeline(tmp_path)
        task = _make_task(store, [_feature("f1")])
        from app.feature_extraction import FeatureExtractionTaskStatus

        store.update(task.task_id, status=FeatureExtractionTaskStatus.PENDING.value)
        with pytest.raises(Exception) as exc:
            pipeline.review_feature("t1", "f1", "confirmed")
        assert "不允许审核" in str(exc.value)

    def test_review_edited_requires_params(self, tmp_path):
        pipeline, store = _make_pipeline(tmp_path)
        _make_task(store, [_feature("f1")])
        with pytest.raises(Exception) as exc:
            pipeline.review_feature("t1", "f1", "edited", edited_params=None)
        assert "edited_params" in str(exc.value)

    def test_export_filters_and_succeeds(self, tmp_path):
        import json

        pipeline, store = _make_pipeline(tmp_path)
        _make_task(
            store,
            [
                _feature("f1", review="confirmed", params={"radius_mm": 10.0}),
                _feature("f2", review="rejected", params={"radius_mm": 99.0}),
            ],
        )
        store.update("t1", status="reviewed")
        out = pipeline.export_confirmed_features("t1", output_path=tmp_path / "out.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["feature_count"] == 1
        assert data["features"][0]["feature_id"] == "f1"
        assert store.get("t1").status == "succeeded"

    def test_export_wrong_status_rejected(self, tmp_path):
        pipeline, store = _make_pipeline(tmp_path)
        _make_task(store, [_feature("f1", review="confirmed")])
        assert store.get("t1").status == "features_extracted"
        # NOT allowed until reviewed at this state? features_extracted IS exportable
        assert FeatureReviewStateMachine.can_export("features_extracted")
        store.update("t1", status="pending")
        with pytest.raises(Exception) as exc:
            pipeline.export_confirmed_features("t1", output_path=tmp_path / "x.json")
        assert "不允许导出" in str(exc.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
