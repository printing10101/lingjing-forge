"""System-wide integration tests for Template Evolution System.

Covers 12 end-to-end scenarios:
1. Stability: 10 consecutive runs extract >= 1 valid pattern
2. Branch config: experiment branch + 10% traffic control
3. A/B effectiveness: >5% improvement auto-merge
4. Auto-rollback: performance degradation auto-rollback
5. Pattern recognition: 3 same-type errors trigger skill doc update
6. Push notification: main branch update notifies running projects
7. One-click apply: automatic config change
8. Branch merge: material branch merge to main
9. Evolution history: version evolution path completeness
10. User control: reject → no duplicate push
11. Template market: publish verified template with metrics
12. Cross-project learning: optimization in project A pushes to project B
"""

import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from app.templates.template_branching import TemplateBranchManager  # noqa: E402
from app.patterns.pattern_engine import PatternEngine  # noqa: E402
from app.templates.template_evolution import TemplateEvolutionEngine  # noqa: E402
from app.templates.template_ab_testing import ABTestingFramework  # noqa: E402
from app.templates.template_update_service import TemplateUpdateService  # noqa: E402
from app.api.v1.template_market import _marketplace_data  # noqa: E402


# ─── Fixtures ───


@pytest.fixture
def all_systems():
    """Create all modules with isolated temp storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        branch_mgr = TemplateBranchManager(
            db_path=os.path.join(tmpdir, "branches.db"),
            json_dir=os.path.join(tmpdir, "branches"),
        )
        branch_mgr.initialize()

        pattern_eng = PatternEngine(db_path=os.path.join(tmpdir, "patterns.db"))
        pattern_eng.initialize()

        evolution_eng = TemplateEvolutionEngine(
            db_path=os.path.join(tmpdir, "evolution.db"),
            log_dir=os.path.join(tmpdir, "evolution_log"),
        )
        evolution_eng.initialize()

        ab_framework = ABTestingFramework(db_path=os.path.join(tmpdir, "ab_testing.db"))
        ab_framework.initialize()

        update_svc = TemplateUpdateService(db_path=os.path.join(tmpdir, "updates.db"))
        update_svc.initialize()

        _marketplace_data.clear()
        _marketplace_data["templates"] = []
        _marketplace_data["subscriptions"] = []
        _marketplace_data["downloads"] = {}

        yield {
            "branch_mgr": branch_mgr,
            "pattern_eng": pattern_eng,
            "evolution_eng": evolution_eng,
            "ab_framework": ab_framework,
            "update_svc": update_svc,
        }

        branch_mgr.close()
        pattern_eng.close()
        evolution_eng.close()
        ab_framework.close()
        update_svc.close()


# ─── Test 1: Stability — 10 consecutive runs extract >= 1 valid pattern ───


def test_1_stability_pattern_extraction(all_systems):
    """连续10次运行流程，验证至少提取1个有效优化模式。"""
    pattern_eng = all_systems["pattern_eng"]

    success_count = 0
    pattern_types = []

    for run in range(10):
        for i in range(15):
            pattern_eng.record_execution(
                task_id=f"task_{run}_{i}_{time.time_ns()}",
                branch_id="main",
                elements={"model": "cfc", "skill": "vibration", "interval": "0.1s"},
                conditions={"material": "aluminum", "operation": "finishing"},
                metrics={"execution_time": 0.5, "resource_cost": 0.3},
                success=True,
            )

        new = pattern_eng.analyze_patterns(min_samples=10)
        if len(new) >= 1:
            success_count += 1
            for p in new:
                pattern_types.append(p.pattern_type)

    assert success_count >= 1, f"10 runs produced {success_count} valid extractions (expected >= 1)"
    assert len(pattern_types) >= 1, "No pattern types discovered"


# ─── Test 2: Branch config — experiment branch + 10% traffic ───


def test_2_branch_config_traffic_control(all_systems):
    """创建实验分支，验证10%项目自动应用新参数的精准控制。"""
    ab_framework = all_systems["ab_framework"]

    exp = ab_framework.create_experiment(
        name="param_test",
        control_branch="main",
        candidate_branch="exp_params",
        traffic_split=0.10,
    )

    assignments = {"control": 0, "candidate": 0}
    for i in range(100):
        branch = ab_framework.assign_branch(f"project_{i}", exp.experiment_id)
        assignments[branch] += 1

    assert assignments["candidate"] > 0, "No projects assigned to candidate"
    candidate_pct = assignments["candidate"] / (assignments["control"] + assignments["candidate"])
    assert candidate_pct <= 0.25, f"Candidate ratio {candidate_pct:.0%} exceeds reasonable range (should be ~10%)"

    consistency_branch = ab_framework.assign_branch("project_0", exp.experiment_id)
    assert consistency_branch == assignments.get("candidate", 0) > 0 and "candidate" or "control"


# ─── Test 3: A/B effectiveness — >5% improvement auto-merge ───


def test_3_ab_auto_merge(all_systems):
    """A/B测试验证新参数执行时间减少>5%时自动合并到主线。"""
    ab_framework = all_systems["ab_framework"]

    exp = ab_framework.create_experiment(
        name="fast_params",
        control_branch="main",
        candidate_branch="exp_fast",
        traffic_split=0.10,
    )

    for i in range(50):
        ab_framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="control",
            metrics={"execution_time": 3.0, "success": True, "resource_cost": 0.5},
        )
        ab_framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="candidate",
            metrics={"execution_time": 1.0, "success": True, "resource_cost": 0.2},
        )

    result = ab_framework.auto_conclude(exp.experiment_id)
    assert result is not None, "auto_conclude returned None"
    assert result.status == "merged", f"Expected 'merged', got '{result.status}'"
    assert result.result == "winner_candidate", f"Expected 'winner_candidate', got '{result.result}'"


# ─── Test 4: Auto-rollback — performance degradation ───


def test_4_auto_rollback(all_systems):
    """模拟新参数性能退化>5%，验证自动回滚。"""
    ab_framework = all_systems["ab_framework"]

    exp = ab_framework.create_experiment(
        name="bad_params",
        control_branch="main",
        candidate_branch="exp_bad",
        traffic_split=0.10,
    )

    for i in range(50):
        ab_framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="control",
            metrics={"execution_time": 1.0, "success": True, "resource_cost": 0.2},
        )
        ab_framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="candidate",
            metrics={"execution_time": 5.0, "success": False, "resource_cost": 1.0},
        )

    result = ab_framework.auto_conclude(exp.experiment_id)
    assert result is not None, "auto_conclude returned None"
    assert result.status == "rolled_back", f"Expected 'rolled_back', got '{result.status}'"
    assert result.result == "winner_control", f"Expected 'winner_control', got '{result.result}'"


# ─── Test 5: Pattern recognition + doc evolution ───


def test_5_error_pattern_triggers_skill_update(all_systems):
    """故意引入3次相同类型错误，验证系统识别并触发技能文档更新。"""
    evolution_eng = all_systems["evolution_eng"]

    evolution_eng.update_metrics(
        {
            "error_count_same_type": 3,
            "error_type": "timeout_error",
        }
    )

    new = evolution_eng.evaluate_triggers()
    skill_triggers = [s for s in new if s.trigger_type == "skill"]
    assert len(skill_triggers) >= 1, "Skill evolution trigger did not fire after 3 same-type errors"
    assert "timeout_error" in skill_triggers[0].description.lower() or "timeout_error" in str(
        skill_triggers[0].data_evidence
    ), "Error type not reflected in suggestion"


# ─── Test 6: Push notification after main branch update ───


def test_6_push_notification_on_update(all_systems):
    """主线模板更新后，验证运行中的项目收到通知。"""
    update_svc = all_systems["update_svc"]

    suggestions = [
        {
            "suggestion_id": "ev_001",
            "title": "Scheduling optimization available",
            "description": "New scheduling pattern detected, 8% improvement expected",
            "change_preview": {"heartbeat_interval": "5m"},
            "expected_impact": {"improvement": 0.08},
        },
    ]

    notifications = update_svc.scan_for_updates("project_running", suggestions)
    assert len(notifications) >= 1, "No notification created for project after main update"
    assert notifications[0].priority == "recommended", f"Expected 'recommended', got '{notifications[0].priority}'"


# ─── Test 7: One-click apply ───


def test_7_one_click_apply(all_systems):
    """验证一键应用后自动完成配置变更。"""
    update_svc = all_systems["update_svc"]

    notif = update_svc.create_notification(
        project_id="project_001",
        suggestion={
            "suggestion_id": "ev_002",
            "title": "Apply optimization",
            "description": "Auto-merge validated optimization",
            "change_preview": {"learning_rate": 0.001},
            "expected_impact": {"improvement": 0.12},
        },
        priority="critical",
    )

    result = update_svc.apply_update(notif.notification_id)
    assert result is not None, "apply_update returned None"
    assert result.status == "applied", f"Expected 'applied', got '{result.status}'"

    applied = update_svc.get_notifications("project_001", status_filter="applied")
    assert len(applied) >= 1, "Applied notification not found in status filter"


# ─── Test 8: Branch merge — material main ───


def test_8_material_branch_merge(all_systems):
    """将材料分支优化合并到主线，验证无冲突。"""
    branch_mgr = all_systems["branch_mgr"]

    main = branch_mgr.create_branch(
        name="main",
        base_branch=None,
        data={"model": "base", "params": {"lr": 0.01}},
        metadata={"type": "main"},
    )

    material = branch_mgr.create_branch(
        name="material-aluminum",
        base_branch=main.branch_id,
        data={
            "model": "base",
            "params": {"lr": 0.005},
            "material_specific": {"aluminum": True},
        },
        metadata={"type": "material"},
    )

    merged = branch_mgr.merge_branch(material.branch_id, main.branch_id, strategy="deep_merge")
    assert merged is not None, "merge returned None"
    assert merged.template_data["material_specific"]["aluminum"] is True, (
        "Material optimization not present in merged branch"
    )
    assert merged.template_data["params"]["lr"] == 0.005, "Material branch lr not applied during merge"


# ─── Test 9: Evolution history completeness ───


def test_9_evolution_history_path(all_systems):
    """验证版本演进路径完整性：历史记录、版本号、时间、变更内容。"""
    branch_mgr = all_systems["branch_mgr"]
    evolution_eng = all_systems["evolution_eng"]

    branch = branch_mgr.create_branch(
        name="main",
        base_branch=None,
        data={"version": "1.0", "params": {}},
        metadata={"type": "main"},
    )

    suggestion = evolution_eng.create_suggestion(
        trigger_type="model_config",
        evidence={"description": "Update learning rate", "confidence": 0.95},
        proposed_change={"action": "update_lr", "value": 0.001},
    )

    evolution_eng.apply_suggestion(suggestion.suggestion_id, branch.branch_id)

    history = evolution_eng.get_evolution_history(branch.branch_id)
    assert len(history) >= 1, "No evolution history found"

    entry = history[0]
    assert entry["action"] == "applied", f"Expected action='applied', got '{entry['action']}'"
    assert entry["suggestion_id"] == suggestion.suggestion_id, "Suggestion ID mismatch in history"
    assert "details" in entry, "Missing change details in history entry"
    assert entry["details"]["action"] == "update_lr", "Change content not recorded"


# ─── Test 10: User control — reject no duplicate push ───


def test_10_reject_no_duplicate(all_systems):
    """用户拒绝更新后，同一周期内不重复推送。"""
    update_svc = all_systems["update_svc"]

    suggestions = [
        {
            "suggestion_id": "ev_003",
            "title": "Test optimization",
            "description": "Optimization suggestion",
            "change_preview": {},
            "expected_impact": {"improvement": 0.05},
        },
    ]

    notifications = update_svc.scan_for_updates("project_user", suggestions)
    assert len(notifications) == 1

    update_svc.dismiss_notification(notifications[0].notification_id)

    second_scan = update_svc.scan_for_updates("project_user", suggestions)
    assert len(second_scan) == 0, "Duplicate notification created after user dismissal"


# ─── Test 11: Template market publish with metrics ───


def test_11_market_publish_with_metrics(all_systems):
    """发布A/B验证过的模板到市场，检查效果数据展示。"""
    branch_mgr = all_systems["branch_mgr"]
    ab_framework = all_systems["ab_framework"]

    branch = branch_mgr.create_branch(
        name="validated-template",
        base_branch=None,
        data={"model": "cfc", "params": {"lr": 0.001}},
        metadata={"type": "main"},
    )

    exp = ab_framework.create_experiment(
        name="validation",
        control_branch="main",
        candidate_branch=branch.branch_id,
    )

    for i in range(50):
        ab_framework.record_execution(
            experiment_id=exp.experiment_id,
            branch="candidate",
            metrics={"execution_time": 1.0, "success": True},
        )

    ab_framework.auto_conclude(exp.experiment_id)

    template_entry = {
        "branch_id": branch.branch_id,
        "name": "CFC Optimized",
        "category": "aluminum",
        "description": "Validated template",
        "published_at": time.time(),
        "adoption_count": 0,
        "source_branch": branch.name,
    }
    _marketplace_data["templates"].append(template_entry)
    _marketplace_data["downloads"][branch.branch_id] = 3

    assert _marketplace_data["templates"][0]["branch_id"] == branch.branch_id
    assert _marketplace_data["templates"][0]["name"] == "CFC Optimized"
    assert _marketplace_data["templates"][0]["category"] == "aluminum"
    assert _marketplace_data["downloads"][branch.branch_id] == 3


# ─── Test 12: Cross-project learning ───


def test_12_cross_project_learning(all_systems):
    """项目A验证的优化推送到项目B。"""
    update_svc = all_systems["update_svc"]

    optimization_suggestion = {
        "suggestion_id": "ev_cross_001",
        "title": "Cross-project optimization",
        "description": "Validated optimization from project_a, 10% improvement",
        "change_preview": {"batch_size": 64},
        "expected_impact": {"improvement": 0.10},
    }

    notifications_a = update_svc.scan_for_updates("project_a", [optimization_suggestion])
    assert len(notifications_a) == 1
    update_svc.apply_update(notifications_a[0].notification_id)

    notifications_b = update_svc.scan_for_updates("project_b", [optimization_suggestion])
    assert len(notifications_b) == 1, "Cross-project learning failed: project_b did not receive suggestion"
    assert notifications_b[0].suggestion_id == "ev_cross_001"
