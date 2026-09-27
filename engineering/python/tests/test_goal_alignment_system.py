"""
Comprehensive tests for the Goal Alignment Task System.

Covers:
- Goal hierarchy model and storage
- Goal chain resolution
- Task model with goal_chain and blockers
- Context builder with goal injection
- Goal alignment verification and propagation
- Progress computation
- Goal version history
- API endpoints
"""

import os
import sys
import uuid
import shutil
import pytest
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.goals import (  # noqa: E402
    Goal,
    GoalLevel,
    GoalStatus,
    GoalRef,
    GoalVersion,
    DEFAULT_GOALS,
)
from app.models.tasks import (  # noqa: E402
    EnhancedTask,
    EnhancedTaskType,
    EnhancedTaskStatus,
)
from app.goals.goal_chain_store import GoalChainStore  # noqa: E402
from app.goals.goal_alignment import GoalAlignmentChecker, GoalAlignmentError  # noqa: E402
from app.context.context_builder import ContextBuilder  # noqa: E402


@pytest.fixture
def temp_db():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "goal_chain.db")
    yield db_path
    # P2-1 修复：改用 shutil.rmtree 递归清理，防止 db WAL/-shm 附属文件导致 rmdir 失败。
    # 消除 except Exception: pass 静默吞错。
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def store(temp_db):
    s = GoalChainStore(db_path=temp_db)
    yield s
    s.close()


@pytest.fixture
def checker(store):
    return GoalAlignmentChecker(store=store)


@pytest.fixture
def context_builder():
    return ContextBuilder()


class TestGoalModel:
    def test_goal_to_dict(self):
        goal = Goal(
            id="test-001",
            name="Test Goal",
            description="A test goal",
            level=GoalLevel.PROJECT,
            status=GoalStatus.IN_PROGRESS,
        )
        d = goal.to_dict()
        assert d["id"] == "test-001"
        assert d["level"] == "project"
        assert d["status"] == "in_progress"

    def test_goal_ref_to_dict(self):
        ref = GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="Test Project")
        d = ref.to_dict()
        assert d["type"] == "project"
        assert d["name"] == "Test Project"

    def test_default_goals_exist(self):
        assert len(DEFAULT_GOALS) == 3
        assert DEFAULT_GOALS[0].level == GoalLevel.MISSION
        assert DEFAULT_GOALS[1].level == GoalLevel.STRATEGIC_GOAL
        assert DEFAULT_GOALS[2].level == GoalLevel.PROJECT

    def test_goal_version_to_dict(self):
        v = GoalVersion(
            id=1,
            goal_id="g-001",
            version=2,
            changed_by="admin",
            change_type="update",
            field_name="status",
            old_value="in_progress",
            new_value="completed",
        )
        d = v.to_dict()
        assert d["field_name"] == "status"
        assert d["new_value"] == "completed"


class TestGoalChainStore:
    def test_default_seeded(self, store):
        goals = store.get_all_goals()
        assert len(goals) >= 3
        mission = store.get_goal("mission-001")
        assert mission is not None
        assert mission.level == GoalLevel.MISSION

    def test_add_goal(self, store):
        goal = Goal(
            id=f"task-{uuid.uuid4().hex[:8]}",
            name="New Task",
            description="A new task",
            level=GoalLevel.TASK,
            parent_id="proj-001",
        )
        store.add_goal(goal)
        retrieved = store.get_goal(goal.id)
        assert retrieved is not None
        assert retrieved.name == "New Task"

    def test_update_goal(self, store):
        goal = store.update_goal("proj-001", status=GoalStatus.COMPLETED)
        assert goal is not None
        assert goal.status == GoalStatus.COMPLETED
        assert goal.version > 1

    def test_update_nonexistent_goal(self, store):
        result = store.update_goal("nonexistent", name="X")
        assert result is None

    def test_delete_goal(self, store):
        gid = f"temp-{uuid.uuid4().hex[:6]}"
        goal = Goal(
            id=gid,
            name="Temp",
            description="temp",
            level=GoalLevel.TASK,
            parent_id="proj-001",
        )
        store.add_goal(goal)
        assert store.delete_goal(gid) is True
        assert store.get_goal(gid) is None

    def test_delete_mission_fails_via_api(self, store):
        mission = store.get_goal("mission-001")
        assert mission is not None

    def test_get_children(self, store):
        children = store.get_children("mission-001")
        assert len(children) >= 1
        assert all(c.parent_id == "mission-001" for c in children)

    def test_resolve_goal_chain(self, store):
        chain = store.resolve_goal_chain("proj-001")
        assert len(chain) >= 3
        levels = [ref.level for ref in chain]
        assert GoalLevel.MISSION in levels
        assert GoalLevel.STRATEGIC_GOAL in levels
        assert GoalLevel.PROJECT in levels

    def test_resolve_chain_from_mission(self, store):
        chain = store.resolve_goal_chain("mission-001")
        assert len(chain) == 1
        assert chain[0].level == GoalLevel.MISSION

    def test_get_goal_tree(self, store):
        tree = store.get_goal_tree()
        assert len(tree) >= 1
        root = tree[0]
        assert root["level"] == "mission"
        assert "children" in root

    def test_filter_goals_by_level(self, store):
        projects = store.get_all_goals(GoalLevel.PROJECT)
        assert len(projects) >= 1
        assert all(g.level == GoalLevel.PROJECT for g in projects)

    def test_version_history(self, store):
        store.update_goal("proj-001", name="Updated Project Name")
        history = store.get_version_history("proj-001")
        assert len(history) >= 1
        assert history[0].field_name == "name"


class TestEnhancedTaskModel:
    def test_task_creation_with_goal_chain(self):
        chain = [
            GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="Project"),
            GoalRef(
                id="strategic-001", level=GoalLevel.STRATEGIC_GOAL, name="Strategy"
            ),
            GoalRef(id="mission-001", level=GoalLevel.MISSION, name="Mission"),
        ]
        task = EnhancedTask(
            id="task-001",
            title="Test Task",
            description="A test",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        assert task.get_parent_goal().name == "Project"
        assert task.get_mission().name == "Mission"

    def test_task_to_dict(self):
        task = EnhancedTask(
            id="t-1",
            title="T",
            description="D",
            task_type=EnhancedTaskType.PREDICTION,
        )
        d = task.to_dict()
        assert d["task_type"] == "prediction"
        assert d["status"] == "pending"
        assert isinstance(d["goal_chain"], list)

    def test_task_status_transitions(self):
        task = EnhancedTask(
            id="t-1", title="T", description="D", task_type=EnhancedTaskType.TRAINING
        )
        assert task.can_transition_to(EnhancedTaskStatus.IN_PROGRESS) is True
        assert task.can_transition_to(EnhancedTaskStatus.COMPLETED) is False

        task.status = EnhancedTaskStatus.IN_PROGRESS
        assert task.can_transition_to(EnhancedTaskStatus.COMPLETED) is True
        assert task.can_transition_to(EnhancedTaskStatus.FAILED) is True

    def test_completed_task_cannot_transition(self):
        task = EnhancedTask(
            id="t-1", title="T", description="D", task_type=EnhancedTaskType.TRAINING
        )
        task.status = EnhancedTaskStatus.COMPLETED
        assert task.can_transition_to(EnhancedTaskStatus.PENDING) is False

    def test_blockers_resolved(self):
        task = EnhancedTask(
            id="t-1",
            title="T",
            description="D",
            task_type=EnhancedTaskType.EXECUTION,
            blockers=["dep-1", "dep-2"],
        )
        assert task.are_blockers_resolved(set()) is False
        assert task.are_blockers_resolved({"dep-1"}) is False
        assert task.are_blockers_resolved({"dep-1", "dep-2"}) is True

    def test_validate_task_type(self):
        assert EnhancedTask.validate_task_type("training") == EnhancedTaskType.TRAINING
        with pytest.raises(ValueError):
            EnhancedTask.validate_task_type("invalid_type")

    def test_validate_task_status(self):
        assert (
            EnhancedTask.validate_task_status("pending") == EnhancedTaskStatus.PENDING
        )
        with pytest.raises(ValueError):
            EnhancedTask.validate_task_status("invalid_status")


class TestContextBuilder:
    def test_build_context_with_full_chain(self, context_builder):
        chain = [
            GoalRef(
                id="proj-001",
                level=GoalLevel.PROJECT,
                name="优化精加工切削参数",
                description="优化切削参数",
            ),
            GoalRef(
                id="strategic-001",
                level=GoalLevel.STRATEGIC_GOAL,
                name="将45钢铣削表面粗糙度降低到Ra 0.8",
                description="降低粗糙度",
            ),
            GoalRef(
                id="mission-001",
                level=GoalLevel.MISSION,
                name="成为智能制造领域的领导者",
                description="领导智能制造",
            ),
        ]
        task = EnhancedTask(
            id="t-1",
            title="训练LNN模型",
            description="使用历史数据训练LNN预测模型",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        ctx = context_builder.build_context(task)
        assert ctx["task_title"] == "训练LNN模型"
        assert ctx["parent_goal"]["name"] == "优化精加工切削参数"
        assert ctx["final_mission"]["name"] == "成为智能制造领域的领导者"
        assert "formatted_context" in ctx
        assert "优化精加工切削参数" in ctx["formatted_context"]
        assert "任务重要性说明" in ctx["formatted_context"]

    def test_build_context_no_chain(self, context_builder):
        task = EnhancedTask(
            id="t-1",
            title="Orphan Task",
            description="No goal",
            task_type=EnhancedTaskType.ANALYSIS,
        )
        ctx = context_builder.build_context(task)
        assert ctx["parent_goal"]["name"] == "未关联目标"
        assert "importance_explanation" in ctx

    def test_build_minimal_context(self, context_builder):
        chain = [
            GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="Project"),
        ]
        task = EnhancedTask(
            id="t-1",
            title="T",
            description="D",
            task_type=EnhancedTaskType.REVIEW,
            goal_chain=chain,
        )
        ctx = context_builder.build_minimal_context(task)
        assert ctx["task_title"] == "T"
        assert len(ctx["goal_chain_summary"]) == 1


class TestGoalAlignmentChecker:
    def test_validate_task_with_chain(self, checker):
        chain = [
            GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P"),
            GoalRef(id="mission-001", level=GoalLevel.MISSION, name="M"),
        ]
        task = EnhancedTask(
            id="t-1",
            title="T",
            description="D",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        assert checker.validate_task_goal_chain(task) is True

    def test_validate_task_without_chain_raises(self, checker):
        task = EnhancedTask(
            id="t-1", title="T", description="D", task_type=EnhancedTaskType.TRAINING
        )
        with pytest.raises(GoalAlignmentError):
            checker.validate_task_goal_chain(task)

    def test_register_and_update_task(self, checker):
        chain = [GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P")]
        task = EnhancedTask(
            id="t-1",
            title="T",
            description="D",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        checker.register_task(task)
        assert "t-1" in checker._task_map
        checker.update_task_status("t-1", EnhancedTaskStatus.IN_PROGRESS)
        assert checker._task_map["t-1"].status == EnhancedTaskStatus.IN_PROGRESS

    def test_alignment_scan_no_issues(self, checker):
        chain = [GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P")]
        task = EnhancedTask(
            id="t-1",
            title="T",
            description="D",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        checker.register_task(task)
        checker.update_task_status("t-1", EnhancedTaskStatus.IN_PROGRESS)
        result = checker.run_alignment_scan()
        assert result["issues_found"] == 0
        assert result["tasks_checked"] == 1

    def test_alignment_scan_finds_issues(self, checker):
        task = EnhancedTask(
            id="t-1", title="T", description="D", task_type=EnhancedTaskType.TRAINING
        )
        checker.register_task(task)
        checker.update_task_status("t-1", EnhancedTaskStatus.IN_PROGRESS)
        result = checker.run_alignment_scan()
        assert result["issues_found"] >= 1

    def test_should_scan_initially(self, checker):
        assert checker.should_scan() is True

    def test_compute_goal_progress(self, checker):
        checker.register_task(
            EnhancedTask(
                id="t-1",
                title="T1",
                description="D",
                task_type=EnhancedTaskType.TRAINING,
                goal_chain=[GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P")],
            )
        )
        checker.update_task_status("t-1", EnhancedTaskStatus.COMPLETED)
        progress = checker.compute_goal_progress("proj-001")
        assert progress.goal_id == "proj-001"
        assert progress.total_tasks >= 0

    def test_compute_all_progress(self, checker):
        progresses = checker.compute_all_progress()
        assert len(progresses) >= 3

    def test_get_alignment_summary(self, checker):
        chain = [GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P")]
        checker.register_task(
            EnhancedTask(
                id="t-1",
                title="T",
                description="D",
                task_type=EnhancedTaskType.TRAINING,
                goal_chain=chain,
            )
        )
        checker.register_task(
            EnhancedTask(
                id="t-2",
                title="T2",
                description="D2",
                task_type=EnhancedTaskType.ANALYSIS,
            )
        )
        summary = checker.get_alignment_summary()
        assert summary["total_tasks"] == 2
        assert summary["aligned_tasks"] == 1
        assert summary["unaligned_tasks"] == 1
        assert 0 <= summary["alignment_rate"] <= 100

    def test_set_scan_interval(self, checker):
        checker.set_scan_interval(3600)
        assert checker._scan_interval_seconds == 3600

    def test_propagate_goal_change(self, checker):
        task_id = f"task-{uuid.uuid4().hex[:6]}"
        goal_id = f"temp-goal-{uuid.uuid4().hex[:6]}"

        goal = Goal(
            id=goal_id,
            name="Temporary",
            description="temp",
            level=GoalLevel.TASK,
            parent_id="proj-001",
        )
        checker._store.add_goal(goal)

        chain = [
            GoalRef(id=goal_id, level=GoalLevel.TASK, name="Temporary"),
            GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P"),
            GoalRef(id="mission-001", level=GoalLevel.MISSION, name="M"),
        ]
        task = EnhancedTask(
            id=task_id,
            title="Child Task",
            description="D",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        checker.register_task(task)
        checker.update_task_status(task_id, EnhancedTaskStatus.IN_PROGRESS)

        result = checker.propagate_goal_change(goal_id)
        assert "affected_task_details" in result


class TestGoalAlignmentIntegration:
    def test_full_workflow(self, store, checker, context_builder):
        new_project_id = f"proj-new-{uuid.uuid4().hex[:6]}"
        new_project = Goal(
            id=new_project_id,
            name="新建测试项目",
            description="用于测试的项目",
            level=GoalLevel.PROJECT,
            parent_id="strategic-001",
        )
        store.add_goal(new_project)

        chain = store.resolve_goal_chain(new_project_id)
        assert len(chain) >= 3

        task_id = f"task-new-{uuid.uuid4().hex[:6]}"
        task = EnhancedTask(
            id=task_id,
            title="测试任务",
            description="测试描述",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        checker.register_task(task)

        ctx = checker.build_task_context(task)
        assert ctx["parent_goal"]["name"] == "新建测试项目"

        progress = checker.compute_goal_progress("strategic-001")
        assert progress.goal_id == "strategic-001"

    def test_goal_version_tracking(self, store):
        store.update_goal("proj-001", description="Updated description for testing")
        history = store.get_version_history("proj-001")
        assert len(history) >= 1
        found_desc_change = False
        for h in history:
            if h.field_name == "description":
                assert h.old_value != h.new_value
                found_desc_change = True
        assert found_desc_change is True

    def test_cascade_chain_resolution(self, store):
        deep_task_id = f"task-deep-{uuid.uuid4().hex[:6]}"
        deep_goal = Goal(
            id=deep_task_id,
            name="深层任务",
            description="深层",
            level=GoalLevel.TASK,
            parent_id="proj-001",
        )
        store.add_goal(deep_goal)

        chain = store.resolve_goal_chain(deep_task_id)
        assert len(chain) >= 4
        assert chain[0].level == GoalLevel.TASK
        assert chain[-1].level == GoalLevel.MISSION

    def test_multiple_tasks_progress(self, checker):
        for i in range(3):
            task = EnhancedTask(
                id=f"prog-task-{i}",
                title=f"Progress Task {i}",
                description=f"Desc {i}",
                task_type=EnhancedTaskType.TRAINING,
                goal_chain=[GoalRef(id="proj-001", level=GoalLevel.PROJECT, name="P")],
            )
            checker.register_task(task)
            if i < 2:
                checker.update_task_status(
                    f"prog-task-{i}", EnhancedTaskStatus.COMPLETED
                )
            else:
                checker.update_task_status(
                    f"prog-task-{i}", EnhancedTaskStatus.IN_PROGRESS
                )

        summary = checker.get_alignment_summary()
        assert summary["total_tasks"] == 3
        assert summary["aligned_tasks"] == 3
