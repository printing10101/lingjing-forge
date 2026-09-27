"""Functional tests for Pattern Engine."""

import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from app.patterns.pattern_engine import PatternEngine  # noqa: E402


@pytest.fixture
def engine():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "patterns.db")
        eng = PatternEngine(db_path=db_path)
        eng.initialize()
        yield eng
        eng.close()


def _record_batch(
    engine, n, success_rate=0.9, elements=None, conditions=None, metrics=None
):
    elements = elements or {"model": "cfc", "skill": "vibration"}
    conditions = conditions or {"material": "aluminum"}
    for i in range(n):
        engine.record_execution(
            task_id=f"task_{i}_{time.time_ns()}",
            branch_id="main",
            elements=elements,
            conditions=conditions,
            metrics=metrics or {"execution_time": 1.0, "resource_cost": 0.5},
            success=(i / n) < success_rate,
        )


def test_record_execution(engine):
    record = engine.record_execution(
        task_id="task_001",
        branch_id="main",
        elements={"model": "cfc"},
        conditions={"material": "aluminum"},
        metrics={"execution_time": 1.5},
        success=True,
    )
    assert record.task_id == "task_001"
    assert record.success is True
    assert record.elements == {"model": "cfc"}


def test_detect_workflow_patterns(engine):
    _record_batch(
        engine,
        15,
        success_rate=0.95,
        metrics={"execution_time": 0.8, "resource_cost": 0.3},
    )
    new = engine.analyze_patterns(min_samples=10)
    workflow = [p for p in new if p.pattern_type == "workflow"]
    assert len(workflow) >= 1
    assert workflow[0].metrics["success_rate"] >= 0.9


def test_detect_anti_patterns(engine):
    _record_batch(
        engine,
        15,
        success_rate=0.4,
        metrics={"execution_time": 5.0, "resource_cost": 10.0},
    )
    new = engine.analyze_patterns(min_samples=10)
    anti = [p for p in new if p.pattern_type == "anti_pattern"]
    assert len(anti) >= 1


def test_detect_combination_patterns(engine):
    for i in range(20):
        engine.record_execution(
            task_id=f"combo_{i}_{time.time_ns()}",
            branch_id="main",
            elements={"model": "cfc", "sampling": "0.1s"},
            conditions={"material": "aluminum", "operation": "finishing"},
            metrics={"execution_time": 0.5},
            success=True,
        )
    for i in range(20):
        engine.record_execution(
            task_id=f"base_{i}_{time.time_ns()}",
            branch_id="main",
            elements={"model": "base"},
            conditions={"material": "aluminum", "operation": "finishing"},
            metrics={"execution_time": 2.0},
            success=(i < 14),
        )
    new = engine.analyze_patterns(min_samples=15)
    combo = [p for p in new if p.pattern_type == "combination"]
    assert len(combo) >= 1


def test_get_patterns_by_type(engine):
    _record_batch(engine, 15, success_rate=0.95)
    engine.analyze_patterns(min_samples=10)
    workflow = engine.get_patterns(pattern_type="workflow")
    assert len(workflow) >= 1
    anti = engine.get_patterns(pattern_type="anti_pattern")
    assert len(anti) == 0


def test_get_anti_patterns(engine):
    _record_batch(engine, 15, success_rate=0.2)
    engine.analyze_patterns(min_samples=10)
    anti = engine.get_anti_patterns()
    assert len(anti) >= 1


def test_generate_suggestions(engine):
    _record_batch(engine, 15, success_rate=0.95)
    new = engine.analyze_patterns(min_samples=10)
    assert len(new) >= 1
    suggestion = engine.generate_suggestions(new[0].pattern_id)
    assert suggestion is not None
    assert "suggestion" in suggestion
    assert suggestion["sample_size"] >= 15


def test_suggestion_not_found(engine):
    result = engine.generate_suggestions("nonexistent")
    assert result is None
