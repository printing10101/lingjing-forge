"""P1-2 白盒测试：参数化几何审核状态机（零框架依赖，CI 独立跑）。"""

from __future__ import annotations

import pytest

from app.parametric_geometry._review_state_machine import (
    RV_CONFIRMED,
    RV_EDITED,
    RV_PENDING,
    RV_REJECTED,
    ST_CANCELLED,
    ST_FAILED,
    ST_PENDING,
    ST_REVIEWED,
    ST_RUNNING,
    ST_STEP_GENERATED,
    ST_SUCCEEDED,
    all_features_reviewed,
    assert_review_status_valid,
    assert_transition_allowed,
    can_execute,
    can_finalize,
    can_review,
    can_transition,
    is_terminal,
    is_valid_review_status,
    is_valid_task_status,
    next_status_after_review,
)


# 与既有枚举逐值对齐（防漂移）


class TestEnumAlignment:
    def test_task_status_matches_enum(self) -> None:
        from app.parametric_geometry.step_store import ParametricGeometryTaskStatus

        assert ParametricGeometryTaskStatus.PENDING.value == ST_PENDING
        assert ParametricGeometryTaskStatus.RUNNING.value == ST_RUNNING
        assert ParametricGeometryTaskStatus.STEP_GENERATED.value == ST_STEP_GENERATED
        assert ParametricGeometryTaskStatus.REVIEWED.value == ST_REVIEWED
        assert ParametricGeometryTaskStatus.SUCCEEDED.value == ST_SUCCEEDED
        assert ParametricGeometryTaskStatus.FAILED.value == ST_FAILED
        assert ParametricGeometryTaskStatus.CANCELLED.value == ST_CANCELLED

    def test_review_status_matches_enum(self) -> None:
        from app.parametric_geometry.step_store import StepReviewStatus

        assert StepReviewStatus.PENDING.value == RV_PENDING
        assert StepReviewStatus.CONFIRMED.value == RV_CONFIRMED
        assert StepReviewStatus.REJECTED.value == RV_REJECTED
        assert StepReviewStatus.EDITED.value == RV_EDITED


# 状态允许性判定


class TestCanExecute:
    def test_pending_allowed(self) -> None:
        assert can_execute(ST_PENDING) is True

    def test_failed_allowed(self) -> None:
        assert can_execute(ST_FAILED) is True

    def test_running_not_allowed(self) -> None:
        assert can_execute(ST_RUNNING) is False

    def test_step_generated_not_allowed(self) -> None:
        assert can_execute(ST_STEP_GENERATED) is False

    def test_reviewed_not_allowed(self) -> None:
        assert can_execute(ST_REVIEWED) is False

    def test_succeeded_not_allowed(self) -> None:
        assert can_execute(ST_SUCCEEDED) is False


class TestCanReview:
    def test_step_generated_allowed(self) -> None:
        assert can_review(ST_STEP_GENERATED) is True

    @pytest.mark.parametrize(
        "status",
        [ST_PENDING, ST_RUNNING, ST_REVIEWED, ST_SUCCEEDED, ST_FAILED, ST_CANCELLED],
    )
    def test_other_statuses_not_allowed(self, status: str) -> None:
        assert can_review(status) is False


class TestCanFinalize:
    def test_reviewed_allowed(self) -> None:
        assert can_finalize(ST_REVIEWED) is True

    @pytest.mark.parametrize(
        "status",
        [ST_PENDING, ST_RUNNING, ST_STEP_GENERATED, ST_SUCCEEDED, ST_FAILED],
    )
    def test_other_statuses_not_allowed(self, status: str) -> None:
        assert can_finalize(status) is False


class TestCanTransition:
    def test_pending_to_running(self) -> None:
        assert can_transition(ST_PENDING, ST_RUNNING) is True

    def test_running_to_step_generated(self) -> None:
        assert can_transition(ST_RUNNING, ST_STEP_GENERATED) is True

    def test_running_to_failed(self) -> None:
        assert can_transition(ST_RUNNING, ST_FAILED) is True

    def test_step_generated_to_reviewed(self) -> None:
        assert can_transition(ST_STEP_GENERATED, ST_REVIEWED) is True

    def test_reviewed_to_succeeded(self) -> None:
        assert can_transition(ST_REVIEWED, ST_SUCCEEDED) is True

    def test_pending_to_succeeded_illegal(self) -> None:
        assert can_transition(ST_PENDING, ST_SUCCEEDED) is False

    def test_reviewed_back_to_running_illegal(self) -> None:
        assert can_transition(ST_REVIEWED, ST_RUNNING) is False

    def test_unknown_status_illegal(self) -> None:
        assert can_transition("bogus", ST_RUNNING) is False


class TestIsTerminal:
    def test_terminal_statuses(self) -> None:
        assert is_terminal(ST_SUCCEEDED) is True
        assert is_terminal(ST_FAILED) is True
        assert is_terminal(ST_CANCELLED) is True

    def test_non_terminal_statuses(self) -> None:
        assert is_terminal(ST_PENDING) is False
        assert is_terminal(ST_RUNNING) is False
        assert is_terminal(ST_STEP_GENERATED) is False
        assert is_terminal(ST_REVIEWED) is False


# 审核完成判定


class TestAllFeaturesReviewed:
    def test_empty_list_false(self) -> None:
        assert all_features_reviewed([]) is False

    def test_all_confirmed_true(self) -> None:
        assert all_features_reviewed([RV_CONFIRMED, RV_CONFIRMED]) is True

    def test_mixed_reviewed_true(self) -> None:
        assert all_features_reviewed([RV_CONFIRMED, RV_REJECTED, RV_EDITED]) is True

    def test_any_pending_false(self) -> None:
        assert all_features_reviewed([RV_CONFIRMED, RV_PENDING]) is False

    def test_all_pending_false(self) -> None:
        assert all_features_reviewed([RV_PENDING, RV_PENDING]) is False


class TestNextStatusAfterReview:
    def test_all_reviewed_goes_reviewed(self) -> None:
        assert next_status_after_review(True, ST_STEP_GENERATED) == ST_REVIEWED

    def test_not_all_reviewed_keeps_current(self) -> None:
        assert next_status_after_review(False, ST_STEP_GENERATED) == ST_STEP_GENERATED


# 校验函数


class TestValidation:
    def test_valid_review_status(self) -> None:
        assert is_valid_review_status(RV_CONFIRMED) is True
        assert is_valid_review_status(RV_EDITED) is True

    def test_invalid_review_status(self) -> None:
        assert is_valid_review_status("bogus") is False
        assert is_valid_review_status("") is False

    def test_valid_task_status(self) -> None:
        assert is_valid_task_status(ST_RUNNING) is True
        assert is_valid_task_status(ST_CANCELLED) is True

    def test_invalid_task_status(self) -> None:
        assert is_valid_task_status("bogus") is False

    def test_assert_transition_allowed_ok(self) -> None:
        assert_transition_allowed(ST_PENDING, ST_RUNNING)  # 不抛

    def test_assert_transition_allowed_raises(self) -> None:
        with pytest.raises(ValueError, match="非法状态流转"):
            assert_transition_allowed(ST_PENDING, ST_SUCCEEDED)

    def test_assert_review_status_valid_ok(self) -> None:
        assert_review_status_valid(RV_REJECTED)  # 不抛

    def test_assert_review_status_valid_raises(self) -> None:
        with pytest.raises(ValueError, match="非法 review_status"):
            assert_review_status_valid("bogus")
