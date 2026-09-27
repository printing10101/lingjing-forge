"""Functional tests for A/B Testing Framework."""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from app.templates.template_ab_testing import ABTestingFramework  # noqa: E402


@pytest.fixture
def framework():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "ab_testing.db")
        fw = ABTestingFramework(db_path=db_path)
        fw.initialize()
        yield fw
        fw.close()


def test_create_experiment(framework):
    exp = framework.create_experiment(
        name="test_exp",
        control_branch="main",
        candidate_branch="exp_001",
        traffic_split=0.10,
    )
    assert exp.name == "test_exp"
    assert exp.status == "running"
    assert exp.traffic_split == 0.10


def test_record_execution(framework):
    exp = framework.create_experiment(
        name="test",
        control_branch="main",
        candidate_branch="exp_001",
    )
    for i in range(50):
        framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="control",
            metrics={"execution_time": 2.0, "success": True, "resource_cost": 0.5},
        )
        framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="candidate",
            metrics={"execution_time": 1.0, "success": True, "resource_cost": 0.3},
        )
    result = framework.evaluate(exp.experiment_id)
    assert result["status"] == "concluded"
    assert result["verdict"] == "winner_candidate"
    assert result["improvement"] > 0


def test_assign_branch_deterministic(framework):
    exp = framework.create_experiment(
        name="test",
        control_branch="main",
        candidate_branch="exp_001",
        traffic_split=0.50,
    )
    branch1 = framework.assign_branch("project_a", exp.experiment_id)
    branch2 = framework.assign_branch("project_a", exp.experiment_id)
    assert branch1 == branch2


def test_auto_merge_on_improvement(framework):
    exp = framework.create_experiment(
        name="test",
        control_branch="main",
        candidate_branch="exp_001",
    )
    for i in range(50):
        framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="control",
            metrics={"execution_time": 3.0, "success": True},
        )
        framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="candidate",
            metrics={"execution_time": 1.0, "success": True},
        )
    result = framework.auto_conclude(exp.experiment_id)
    assert result is not None
    assert result.status == "merged"


def test_auto_rollback_on_regression(framework):
    exp = framework.create_experiment(
        name="test",
        control_branch="main",
        candidate_branch="exp_001",
    )
    for i in range(50):
        framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="control",
            metrics={"execution_time": 1.0, "success": True},
        )
        framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="candidate",
            metrics={"execution_time": 5.0, "success": False},
        )
    result = framework.auto_conclude(exp.experiment_id)
    assert result is not None
    assert result.status == "rolled_back"


def test_insufficient_data(framework):
    exp = framework.create_experiment(
        name="test",
        control_branch="main",
        candidate_branch="exp_001",
    )
    framework.record_execution(
        experiment_id=exp.experiment_id,
        branch="control",
        metrics={"execution_time": 1.0, "success": True},
    )
    result = framework.evaluate(exp.experiment_id)
    assert result["status"] == "insufficient_data"


def test_list_experiments(framework):
    framework.create_experiment("exp1", "main", "exp_a")
    framework.create_experiment("exp2", "main", "exp_b")
    active = framework.get_active_experiments()
    assert len(active) == 2
    all_exps = framework.list_experiments()
    assert len(all_exps) == 2


def test_get_experiment_results(framework):
    exp = framework.create_experiment("test", "main", "exp_001")
    result = framework.get_experiment_results(exp.experiment_id)
    assert result["experiment_id"] == exp.experiment_id
    assert result["status"] == "running"


def test_get_nonexistent_experiment(framework):
    result = framework.get_experiment_results("nonexistent")
    assert result is None


def test_traffic_assignment_within_split(framework):
    exp = framework.create_experiment(
        name="test",
        control_branch="main",
        candidate_branch="exp_001",
        traffic_split=0.0,
    )
    branch = framework.assign_branch("project_x", exp.experiment_id)
    assert branch == "control"
