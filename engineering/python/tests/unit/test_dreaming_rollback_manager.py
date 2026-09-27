"""dreaming/rollback_manager 覆盖率补强测试（W7.1）。

覆盖回滚执行路径与信任话术锚点：
- hard_constraint / fully_deprecate → 直接 DEPRECATED（硬约束立即回滚）
- 普通异常 → 经 ProgressivePublisher.demote 逐级降级
- 回滚后冷却期与历史留痕
- demote 失败时的直接回滚兜底
"""

from __future__ import annotations

from typing import Any

import pytest

from app.dreaming._publisher_models import PublicationStage
from app.dreaming.apply_rules import RollbackResult
from app.dreaming.progressive_publisher import ProgressivePublisher
from app.dreaming.rollback_manager import RollbackManager
from app.dreaming._validator_models import ValidationResult

pytestmark = pytest.mark.unit


class _StubValidator:
    def validate(self, rule: Any, **kwargs: Any) -> ValidationResult:
        return ValidationResult(passed=True, errors=[])


class _StubApplicator:
    def __init__(self, rollback_success: bool = True):
        self.rollback_success = rollback_success
        self.rollback_calls: list[str] = []

    def apply(self, rule: Any, skip_validation: bool = False, **kwargs: Any):
        from app.dreaming.apply_rules import ApplyResult

        return ApplyResult(success=True, rule_id=rule.rule_id, audit_entry_seq=1)

    def rollback(self, rule_id: str, **kwargs: Any) -> RollbackResult:
        self.rollback_calls.append(rule_id)
        return RollbackResult(
            success=self.rollback_success,
            rule_id=rule_id,
            rolled_back_at="2026-09-07T00:00:00+00:00",
            previous_status="APPLIED",
            error=None if self.rollback_success else "stub 回滚失败",
        )


class _StubAuditRecorder:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def record_rule_application(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _make_manager(
    tmp_path,
    publisher: ProgressivePublisher,
    applicator: _StubApplicator | None = None,
    cooldown_hours: int = 24,
) -> tuple[RollbackManager, _StubAuditRecorder]:
    manager = RollbackManager(
        history_dir=str(tmp_path / "rb_history"),
        publisher=publisher,
        applicator=applicator or _StubApplicator(),
        cooldown_hours=cooldown_hours,
    )
    recorder = _StubAuditRecorder()
    manager._get_audit_recorder = lambda: recorder  # 隔离真实审计哈希链
    return manager, recorder


def _make_publisher(tmp_path) -> ProgressivePublisher:
    return ProgressivePublisher(
        state_dir=str(tmp_path / "pub_state"),
        validator=_StubValidator(),
        applicator=_StubApplicator(),
    )


class TestRollbackPaths:
    def test_hard_constraint_rollback_deprecates_immediately(self, tmp_path):
        """硬约束违反：跳过灰度逐级降级，直接 DEPRECATED——安全优先级最高。"""
        publisher = _make_publisher(tmp_path)
        applicator = _StubApplicator()
        manager, recorder = _make_manager(tmp_path, publisher, applicator)

        result = manager.rollback_rule("r-1", reason="违反 cam_validation_required", severity="hard_constraint")

        assert result.success is True
        assert result.fully_deprecated is True
        assert result.current_stage == PublicationStage.DEPRECATED.value
        assert applicator.rollback_calls == ["r-1"]
        assert recorder.events and recorder.events[0]["rollback_triggered"] is True

    def test_normal_severity_demotes_via_publisher(self, tmp_path):
        """普通异常：走灰度降级（FULL → ROLLING_50），不直接废弃。"""
        publisher = _make_publisher(tmp_path)

        class _Rule:
            rule_id = "r-2"

        publisher.publish(
            _Rule(),
            stage=PublicationStage.FULL,
            skip_validation=True,
        )
        manager, _ = _make_manager(tmp_path, publisher)
        result = manager.rollback_rule("r-2", reason="误报率升高", severity="production_anomaly")

        assert result.success is True
        assert result.fully_deprecated is False
        assert result.current_stage == PublicationStage.ROLLING_50.value

    def test_demote_failure_falls_back_to_direct_rollback(self, tmp_path):
        """降级失败（如规则不在灰度记录中）→ 直接 rollback 兜底，绝不悬空。"""
        publisher = _make_publisher(tmp_path)
        applicator = _StubApplicator()
        manager, _ = _make_manager(tmp_path, publisher, applicator)

        result = manager.rollback_rule("r-unknown", reason="记录缺失")

        assert result.success is True
        assert result.fully_deprecated is True
        assert applicator.rollback_calls == ["r-unknown"]

    def test_fully_deprecate_flag_forces_direct_rollback(self, tmp_path):
        publisher = _make_publisher(tmp_path)
        applicator = _StubApplicator()
        manager, _ = _make_manager(tmp_path, publisher, applicator)

        result = manager.rollback_rule("r-3", reason="人工判定不可信", fully_deprecate=True)
        assert result.fully_deprecated is True
        assert applicator.rollback_calls == ["r-3"]


class TestCooldownAndHistory:
    def test_rollback_sets_cooldown_and_resets_anomaly_counter(self, tmp_path):
        publisher = _make_publisher(tmp_path)
        manager, _ = _make_manager(tmp_path, publisher, cooldown_hours=24)

        manager._consecutive_anomalies["r-1"] = 3
        manager.rollback_rule("r-1", reason="x")
        assert manager.get_consecutive_anomaly_count("r-1") == 0
        assert "r-1" in manager._cooldowns

    def test_history_recorded_and_filterable(self, tmp_path):
        publisher = _make_publisher(tmp_path)
        manager, _ = _make_manager(tmp_path, publisher)

        manager.rollback_rule("r-a", reason="原因A")
        manager.rollback_rule("r-b", reason="原因B")

        all_history = manager.get_rollback_history()
        assert {h["rule_id"] for h in all_history} == {"r-a", "r-b"}

        only_a = manager.get_rollback_history(rule_id="r-a")
        assert len(only_a) == 1
        assert only_a[0]["reason"] == "原因A"

    def test_history_persists_across_instances(self, tmp_path):
        state = str(tmp_path / "rb_history")
        publisher = _make_publisher(tmp_path)
        manager1, _ = _make_manager(tmp_path, publisher)
        manager1.rollback_rule("r-h", reason="历史留痕")

        manager2 = RollbackManager(
            history_dir=state,
            publisher=publisher,
            applicator=_StubApplicator(),
        )
        manager2._get_audit_recorder = lambda: _StubAuditRecorder()
        history = manager2.get_rollback_history(rule_id="r-h")
        assert len(history) == 1
        assert history[0]["reason"] == "历史留痕"
