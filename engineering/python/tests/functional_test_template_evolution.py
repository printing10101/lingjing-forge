"""Functional tests for Template Evolution."""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from app.templates.template_evolution import TemplateEvolutionEngine  # noqa: E402


@pytest.fixture
def engine():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "evolution.db")
        log_dir = os.path.join(tmpdir, "evolution_log")
        eng = TemplateEvolutionEngine(db_path=db_path, log_dir=log_dir)
        eng.initialize()
        yield eng
        eng.close()


def test_update_metrics(engine):
    engine.update_metrics({"error_count_same_type": 5, "error_type": "timeout"})
    assert engine._metrics_data["error_count_same_type"]["value"] == 5


def test_skill_trigger_fires(engine):
    engine.update_metrics({"error_count_same_type": 5, "error_type": "timeout"})
    new = engine.evaluate_triggers()
    skill_suggestions = [s for s in new if s.trigger_type == "skill"]
    assert len(skill_suggestions) >= 1
    assert skill_suggestions[0].confidence >= 0.5


def test_model_config_trigger_fires(engine):
    engine.update_metrics(
        {
            "ab_test_winner": {"config_name": "cfc_v2", "improvement": 12.5},
            "confidence": 0.97,
        }
    )
    new = engine.evaluate_triggers()
    model_suggestions = [s for s in new if s.trigger_type == "model_config"]
    assert len(model_suggestions) >= 1


def test_approval_strategy_trigger_fires(engine):
    engine.update_metrics({"false_positive_rate": 0.35})
    new = engine.evaluate_triggers()
    approval_suggestions = [s for s in new if s.trigger_type == "approval_strategy"]
    assert len(approval_suggestions) >= 1


def test_heartbeat_trigger_fires(engine):
    engine.update_metrics({"gpu_utilization_avg_7d": 0.15})
    new = engine.evaluate_triggers()
    heartbeat_suggestions = [s for s in new if s.trigger_type == "heartbeat_routine"]
    assert len(heartbeat_suggestions) >= 1


def test_budget_trigger_fires(engine):
    engine.update_metrics({"overspend_rate": 0.30, "resource_waste_rate": 0.25})
    new = engine.evaluate_triggers()
    budget_suggestions = [s for s in new if s.trigger_type == "budget_strategy"]
    assert len(budget_suggestions) >= 1


def test_create_suggestion_manual(engine):
    suggestion = engine.create_suggestion(
        trigger_type="custom",
        evidence={"description": "Custom trigger", "confidence": 0.9},
        proposed_change={"action": "update_param", "key": "value"},
    )
    assert suggestion.trigger_type == "custom"
    assert suggestion.status == "pending"


def test_apply_suggestion(engine):
    suggestion = engine.create_suggestion(
        trigger_type="skill",
        evidence={"description": "Test"},
        proposed_change={"action": "test"},
    )
    result = engine.apply_suggestion(suggestion.suggestion_id, "branch_main")
    assert result is not None
    assert result.status == "applied"


def test_apply_nonexistent_suggestion(engine):
    result = engine.apply_suggestion("nonexistent", "branch_main")
    assert result is None


def test_list_suggestions(engine):
    engine.create_suggestion(
        trigger_type="skill", evidence={"description": "A"}, proposed_change={}
    )
    engine.create_suggestion(
        trigger_type="model_config", evidence={"description": "B"}, proposed_change={}
    )
    all_s = engine.list_suggestions()
    assert len(all_s) == 2
    pending = engine.list_suggestions(status_filter="pending")
    assert len(pending) == 2


def test_evolution_history(engine):
    suggestion = engine.create_suggestion(
        trigger_type="skill",
        evidence={"description": "Test"},
        proposed_change={"action": "test"},
    )
    engine.apply_suggestion(suggestion.suggestion_id, "branch_001")
    history = engine.get_evolution_history(branch_id="branch_001")
    assert len(history) >= 1
    assert history[0]["action"] == "applied"


def test_cooldown_prevents_duplicate_triggers(engine):
    engine.update_metrics({"error_count_same_type": 5, "error_type": "timeout"})
    new1 = engine.evaluate_triggers()
    assert len(new1) >= 1
    new2 = engine.evaluate_triggers()
    assert len(new2) == 0
