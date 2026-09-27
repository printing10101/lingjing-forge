"""dreaming/progressive_publisher 覆盖率补强测试（W7.1）。

目标：把「AI 自我改进永远可灰度、可回滚」的信任话术锚定在测试上。
覆盖灰度发布核心路径：
- publish：SHADOW 建档 / FULL 沙箱校验 / FULL 应用失败自动回落 SHADOW
- promote：逐级晋级 / 指标阈值阻断 / 跨级拒绝 / FULL 双重校验
- demote：逐级降级至 SHADOW 再降级触发 DEPRECATED（等价回滚）
- persistence：灰度记录跨实例恢复
"""

from __future__ import annotations

from typing import Any

import pytest

from app.dreaming._publisher_models import PublicationStage
from app.dreaming.apply_rules import ApplyResult
from app.dreaming.progressive_publisher import ProgressivePublisher
from app.dreaming._validator_models import ValidationResult
from app.dreaming.rule_synthesizer import RuleDraft

pytestmark = pytest.mark.unit

GOOD_METRICS = {"accuracy": 0.9, "false_positive_rate": 0.05, "sample_size": 50, "error_rate": 0.02}


def _make_draft(rule_id: str = "r-dream-001") -> RuleDraft:
    return RuleDraft(
        rule_id=rule_id,
        rule_type="parameter_adjustment",
        description="测试灰度规则",
        condition={"param": "spindle_speed", "op": ">", "value": 8000},
        action={"action": "adjust", "target": "spindle_speed", "delta": -500},
        confidence=0.85,
    )


class _StubValidator:
    """可编程沙箱校验器桩。"""

    def __init__(self, passed: bool = True):
        self.passed = passed
        self.calls = 0

    def validate(self, rule: RuleDraft, **kwargs: Any) -> ValidationResult:
        self.calls += 1
        return ValidationResult(passed=self.passed, errors=[] if self.passed else ["stub 校验失败"])


class _StubApplicator:
    """可编程规则应用器桩（记录 FULL 应用调用）。"""

    def __init__(self, success: bool = True):
        self.success = success
        self.applied: list[str] = []

    def apply(self, rule: RuleDraft, skip_validation: bool = False, **kwargs: Any) -> ApplyResult:
        self.applied.append(rule.rule_id)
        return ApplyResult(
            success=self.success,
            rule_id=rule.rule_id,
            error=None if self.success else "stub 应用失败",
            audit_entry_seq=1,
        )


def _make_publisher(tmp_path, validator: _StubValidator | None = None, applicator: _StubApplicator | None = None):
    return ProgressivePublisher(
        state_dir=str(tmp_path / "pub_state"),
        validator=validator or _StubValidator(),
        applicator=applicator or _StubApplicator(),
    )


class TestPublish:
    def test_publish_to_shadow_creates_record(self, tmp_path):
        pub = _make_publisher(tmp_path)
        result = pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        assert result.success is True
        record = pub._records["r-dream-001"]
        assert record.current_stage == PublicationStage.SHADOW
        assert record.current_stage.traffic_percentage == 0.0

    def test_publish_state_persists_across_instances(self, tmp_path):
        state_dir = str(tmp_path / "pub_state")
        pub1 = ProgressivePublisher(
            state_dir=state_dir, validator=_StubValidator(), applicator=_StubApplicator()
        )
        pub1.publish(_make_draft(), stage=PublicationStage.SHADOW)

        pub2 = ProgressivePublisher(
            state_dir=state_dir, validator=_StubValidator(), applicator=_StubApplicator()
        )
        assert "r-dream-001" in pub2._records
        assert pub2._records["r-dream-001"].current_stage == PublicationStage.SHADOW

    def test_publish_full_with_failed_validation_rejected(self, tmp_path):
        pub = _make_publisher(tmp_path, validator=_StubValidator(passed=False))
        result = pub.publish(_make_draft(), stage=PublicationStage.FULL)
        assert result.success is False
        assert "沙箱" in (result.error or "")

    def test_publish_full_apply_failure_falls_back_to_shadow(self, tmp_path):
        pub = _make_publisher(
            tmp_path, validator=_StubValidator(passed=True), applicator=_StubApplicator(success=False)
        )
        result = pub.publish(_make_draft(), stage=PublicationStage.FULL)
        assert result.success is False
        record = pub._records["r-dream-001"]
        # 应用失败必须回落 SHADOW，绝不能停留在 FULL 假装全量生效
        assert record.current_stage == PublicationStage.SHADOW
        assert record.demoted_count == 1

    def test_publish_full_success_applies_rule(self, tmp_path):
        applicator = _StubApplicator(success=True)
        pub = _make_publisher(tmp_path, applicator=applicator)
        result = pub.publish(_make_draft(), stage=PublicationStage.FULL)
        assert result.success is True
        assert applicator.applied == ["r-dream-001"]
        assert pub._records["r-dream-001"].promoted_to_full is True


class TestPromote:
    def test_promote_unknown_rule_fails(self, tmp_path):
        pub = _make_publisher(tmp_path)
        result = pub.promote("r-nonexistent")
        assert result.success is False
        assert "未发布" in (result.error or "")

    def test_promote_shadow_to_canary_with_good_metrics(self, tmp_path):
        pub = _make_publisher(tmp_path)
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        result = pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS)
        assert result.success is True
        assert result.stage == PublicationStage.CANARY
        assert result.traffic_percentage == 0.01

    def test_promote_blocked_by_bad_metrics(self, tmp_path):
        pub = _make_publisher(tmp_path)
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        bad = {"accuracy": 0.3, "false_positive_rate": 0.05, "sample_size": 50, "error_rate": 0.02}
        result = pub.promote("r-dream-001", metrics_snapshot=bad)
        assert result.success is False
        assert "阈值" in (result.error or "")
        # 阶段不变
        assert pub._records["r-dream-001"].current_stage == PublicationStage.SHADOW

    def test_promote_rejects_stage_skipping(self, tmp_path):
        pub = _make_publisher(tmp_path)
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        result = pub.promote("r-dream-001", target_stage=PublicationStage.FULL)
        assert result.success is False
        assert "跨级" in (result.error or "")

    def test_promote_to_full_requires_rule_for_revalidation(self, tmp_path):
        pub = _make_publisher(tmp_path)
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS)  # → CANARY
        pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS)  # → ROLLING_10
        pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS)  # → ROLLING_50
        result = pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS)  # → FULL
        assert result.success is False
        assert "rule 参数" in (result.error or "")

    def test_promote_to_full_reruns_validation_and_applies(self, tmp_path):
        validator = _StubValidator(passed=True)
        applicator = _StubApplicator(success=True)
        pub = _make_publisher(tmp_path, validator=validator, applicator=applicator)
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        for _ in range(3):
            assert pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS).success
        result = pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS, rule=_make_draft())
        assert result.success is True
        assert result.stage == PublicationStage.FULL
        assert result.traffic_percentage == 1.0
        # FULL 晋级必须重新沙箱校验（双重校验）并真正应用
        assert validator.calls >= 1
        assert applicator.applied == ["r-dream-001"]

    def test_promote_to_full_with_failed_revalidation_rejected(self, tmp_path):
        pub = _make_publisher(tmp_path, validator=_StubValidator(passed=False))
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW, skip_validation=True)
        for _ in range(3):
            assert pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS).success
        result = pub.promote("r-dream-001", metrics_snapshot=GOOD_METRICS, rule=_make_draft())
        assert result.success is False
        assert "校验失败" in (result.error or "")
        assert pub._records["r-dream-001"].current_stage == PublicationStage.ROLLING_50

    def test_promote_at_full_is_noop_error(self, tmp_path):
        pub = _make_publisher(tmp_path, applicator=_StubApplicator(success=True))
        pub.publish(_make_draft(), stage=PublicationStage.FULL)
        result = pub.promote("r-dream-001")
        assert result.success is False
        assert "最高阶段" in (result.error or "")


class TestDemote:
    def test_demote_canary_to_shadow(self, tmp_path):
        pub = _make_publisher(tmp_path)
        pub.publish(_make_draft(), stage=PublicationStage.CANARY)
        result = pub.demote("r-dream-001", reason="指标恶化")
        assert result.success is True
        assert result.stage == PublicationStage.SHADOW

    def test_demote_at_shadow_triggers_deprecated_rollback(self, tmp_path):
        """SHADOW 再降级 = 回滚废弃——「AI 自我改进永远可回滚」的机制锚点。"""
        pub = _make_publisher(tmp_path)
        pub.publish(_make_draft(), stage=PublicationStage.SHADOW)
        result = pub.demote("r-dream-001", reason="影子期即异常")
        assert result.success is True
        assert result.stage == PublicationStage.DEPRECATED

    def test_demote_unknown_rule_fails(self, tmp_path):
        pub = _make_publisher(tmp_path)
        result = pub.demote("r-nonexistent", reason="x")
        assert result.success is False
