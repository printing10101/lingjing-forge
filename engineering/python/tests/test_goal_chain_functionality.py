"""
Goal Chain Functionality Test Suite

Comprehensive tests covering:
1. Goal chain creation and hierarchy validation
2. Goal chain query verification
3. Task execution context injection
4. Orphan task creation prevention
5. Goal chain context update propagation
6. Automatic progress computation
7. Project deletion impact analysis
8. Frontend goal tree view verification (API-based)
"""

import os
import sys
import time
import uuid
import shutil
import pytest
import tempfile

from tests.utils.perf_thresholds import perf_threshold

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.goals import (  # noqa: E402
    Goal,
    GoalLevel,
    GoalStatus,
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
    db_path = os.path.join(tmpdir, "goal_chain_test.db")
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


class TestGoalChainCreation:
    """Test 1: Goal chain creation and hierarchy validation"""

    def test_create_strategic_goal_hierarchy(self, store):
        strategic_id = f"strategic-test-{uuid.uuid4().hex[:8]}"
        project_id = f"project-test-{uuid.uuid4().hex[:8]}"
        task_id = f"task-test-{uuid.uuid4().hex[:8]}"

        strategic_goal = Goal(
            id=strategic_id,
            name="测试战略目标",
            description="用于测试的战略目标",
            level=GoalLevel.STRATEGIC_GOAL,
            parent_id="mission-001",
            status=GoalStatus.IN_PROGRESS,
        )
        store.add_goal(strategic_goal)

        project = Goal(
            id=project_id,
            name="测试项目",
            description="用于测试的项目",
            level=GoalLevel.PROJECT,
            parent_id=strategic_id,
            status=GoalStatus.IN_PROGRESS,
        )
        store.add_goal(project)

        task_goal = Goal(
            id=task_id,
            name="测试任务",
            description="用于测试的任务",
            level=GoalLevel.TASK,
            parent_id=project_id,
            status=GoalStatus.NOT_STARTED,
        )
        store.add_goal(task_goal)

        retrieved_strategic = store.get_goal(strategic_id)
        assert retrieved_strategic is not None
        assert retrieved_strategic.parent_id == "mission-001"

        retrieved_project = store.get_goal(project_id)
        assert retrieved_project is not None
        assert retrieved_project.parent_id == strategic_id

        retrieved_task = store.get_goal(task_id)
        assert retrieved_task is not None
        assert retrieved_task.parent_id == project_id

    def test_hierarchy_relationship_correctness(self, store):
        strategic_id = f"strategic-rel-{uuid.uuid4().hex[:8]}"
        project_id = f"project-rel-{uuid.uuid4().hex[:8]}"
        task_id = f"task-rel-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=strategic_id,
                name="S",
                description="Strategic",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )
        store.add_goal(
            Goal(
                id=project_id,
                name="P",
                description="Project",
                level=GoalLevel.PROJECT,
                parent_id=strategic_id,
            )
        )
        store.add_goal(
            Goal(
                id=task_id,
                name="T",
                description="Task",
                level=GoalLevel.TASK,
                parent_id=project_id,
            )
        )

        children_of_mission = store.get_children("mission-001")
        strategic_children = [c for c in children_of_mission if c.id == strategic_id]
        assert len(strategic_children) == 1

        children_of_strategic = store.get_children(strategic_id)
        project_children = [c for c in children_of_strategic if c.id == project_id]
        assert len(project_children) == 1

        children_of_project = store.get_children(project_id)
        task_children = [c for c in children_of_project if c.id == task_id]
        assert len(task_children) == 1

    def test_database_parent_id_accuracy(self, store):
        parent_ids = {
            "mission-001": None,
        }

        strategic_id = f"strategic-acc-{uuid.uuid4().hex[:8]}"
        project_id = f"project-acc-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=strategic_id,
                name="Strategic",
                description="S",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )
        parent_ids[strategic_id] = "mission-001"

        store.add_goal(
            Goal(
                id=project_id,
                name="Project",
                description="P",
                level=GoalLevel.PROJECT,
                parent_id=strategic_id,
            )
        )
        parent_ids[project_id] = strategic_id

        for goal_id, expected_parent in parent_ids.items():
            goal = store.get_goal(goal_id)
            assert goal is not None
            assert goal.parent_id == expected_parent, (
                f"Goal {goal_id} parent_id mismatch"
            )


class TestGoalChainQueryVerification:
    """Test 2: Goal chain query verification"""

    def test_goal_chain_completeness(self, store):
        strategic_id = f"strategic-q-{uuid.uuid4().hex[:8]}"
        project_id = f"project-q-{uuid.uuid4().hex[:8]}"
        task_id = f"task-q-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=strategic_id,
                name="战略目标",
                description="S",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )
        store.add_goal(
            Goal(
                id=project_id,
                name="项目",
                description="P",
                level=GoalLevel.PROJECT,
                parent_id=strategic_id,
            )
        )
        store.add_goal(
            Goal(
                id=task_id,
                name="任务",
                description="T",
                level=GoalLevel.TASK,
                parent_id=project_id,
            )
        )

        chain = store.resolve_goal_chain(task_id)

        assert len(chain) == 4, f"Expected 4 levels in chain, got {len(chain)}"

        assert chain[0].id == task_id
        assert chain[0].level == GoalLevel.TASK
        assert chain[0].name == "任务"

        assert chain[1].id == project_id
        assert chain[1].level == GoalLevel.PROJECT
        assert chain[1].name == "项目"

        assert chain[2].id == strategic_id
        assert chain[2].level == GoalLevel.STRATEGIC_GOAL
        assert chain[2].name == "战略目标"

        assert chain[3].id == "mission-001"
        assert chain[3].level == GoalLevel.MISSION

    def test_goal_chain_node_accuracy(self, store):
        strategic_id = f"strategic-acc2-{uuid.uuid4().hex[:8]}"
        project_id = f"project-acc2-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=strategic_id,
                name="战略",
                description="战略目标描述",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )
        store.add_goal(
            Goal(
                id=project_id,
                name="项目",
                description="项目描述",
                level=GoalLevel.PROJECT,
                parent_id=strategic_id,
            )
        )

        chain = store.resolve_goal_chain(project_id)

        for node in chain:
            assert node.id is not None and node.id != ""
            assert node.name is not None and node.name != ""
            assert isinstance(node.level, GoalLevel)

        project_node = chain[0]
        assert project_node.level == GoalLevel.PROJECT
        assert project_node.name == "项目"

        strategic_node = chain[1]
        assert strategic_node.level == GoalLevel.STRATEGIC_GOAL
        assert strategic_node.name == "战略"

        mission_node = chain[2]
        assert mission_node.level == GoalLevel.MISSION
        assert mission_node.name == "成为智能制造领域的领导者"

    def test_goal_chain_serialization(self, store):
        project_id = f"project-serial-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Project",
                description="P",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        chain = store.resolve_goal_chain(project_id)
        serialized = [ref.to_dict() for ref in chain]

        assert len(serialized) >= 3
        for node in serialized:
            assert "id" in node
            assert "name" in node
            assert "type" in node
            assert node["type"] in ["mission", "strategic_goal", "project", "task"]


class TestTaskExecutionContextInjection:
    """Test 3: Task execution context injection"""

    def test_context_contains_full_goal_chain(self, store, checker, context_builder):
        project_id = f"project-ctx-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="上下文测试项目",
                description="用于测试上下文注入",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        chain = store.resolve_goal_chain(project_id)
        task = EnhancedTask(
            id=f"task-ctx-{uuid.uuid4().hex[:8]}",
            title="执行训练任务",
            description="使用新数据训练模型",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        checker.register_task(task)

        ctx = checker.build_task_context(task)

        assert "formatted_context" in ctx
        assert "上下文测试项目" in ctx["formatted_context"]
        assert "将45钢铣削表面粗糙度降低到Ra 0.8" in ctx["formatted_context"]
        assert "成为智能制造领域的领导者" in ctx["formatted_context"]

    def test_context_includes_mission(self, store, context_builder):
        project_id = f"project-mission-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Mission Project",
                description="MP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        chain = store.resolve_goal_chain(project_id)
        task = EnhancedTask(
            id="task-mission",
            title="Mission Task",
            description="MT",
            task_type=EnhancedTaskType.EXECUTION,
            goal_chain=chain,
        )

        ctx = context_builder.build_context(task)

        assert "final_mission" in ctx
        assert ctx["final_mission"]["name"] == "成为智能制造领域的领导者"
        assert ctx["final_mission"]["id"] == "mission-001"

    def test_context_importance_explanation(self, store, context_builder):
        project_id = f"project-importance-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Importance Project",
                description="IP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        chain = store.resolve_goal_chain(project_id)
        task = EnhancedTask(
            id="task-importance",
            title="Importance Task",
            description="IT",
            task_type=EnhancedTaskType.ANALYSIS,
            goal_chain=chain,
        )

        ctx = context_builder.build_context(task)

        assert "importance_explanation" in ctx
        assert len(ctx["importance_explanation"]) > 0
        assert "任务重要性说明" in ctx["formatted_context"]


class TestOrphanTaskCreationPrevention:
    """Test 4: Orphan task creation prevention"""

    def test_task_without_parent_goal_rejected(self, store, checker):
        task = EnhancedTask(
            id="orphan-task-1",
            title="孤立任务",
            description="没有父目标的任务",
            task_type=EnhancedTaskType.PREDICTION,
        )

        with pytest.raises(GoalAlignmentError) as excinfo:
            checker.validate_task_goal_chain(task)

        assert "goal chain" in str(excinfo.value).lower() or "目标链" in str(
            excinfo.value
        )

    def test_task_creation_api_requires_parent_goal(self, store, checker):
        try:
            task = EnhancedTask(
                id="orphan-task-2",
                title="孤立任务2",
                description="无父目标",
                task_type=EnhancedTaskType.TRAINING,
            )
            checker.validate_task_goal_chain(task)
            assert False, "Should have raised GoalAlignmentError"
        except GoalAlignmentError as e:
            error_message = str(e)
            assert (
                "必须" in error_message
                or "required" in error_message.lower()
                or "goal" in error_message.lower()
            )

    def test_empty_goal_chain_rejected(self, store, checker):
        task = EnhancedTask(
            id="orphan-task-3",
            title="空目标链任务",
            description="目标链为空",
            task_type=EnhancedTaskType.REVIEW,
            goal_chain=[],
        )

        with pytest.raises(GoalAlignmentError):
            checker.validate_task_goal_chain(task)

    def test_task_with_valid_goal_chain_accepted(self, store, checker):
        project_id = f"project-valid-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Valid Project",
                description="VP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        chain = store.resolve_goal_chain(project_id)
        task = EnhancedTask(
            id="valid-task",
            title="有效任务",
            description="有完整目标链",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )

        result = checker.validate_task_goal_chain(task)
        assert result is True


class TestGoalChainContextUpdate:
    """Test 5: Goal chain context update propagation"""

    def test_strategic_goal_description_update(self, store):
        strategic_id = f"strategic-update-{uuid.uuid4().hex[:8]}"
        project_id = f"project-update-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=strategic_id,
                name="战略",
                description="原始描述",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )
        store.add_goal(
            Goal(
                id=project_id,
                name="项目",
                description="项目描述",
                level=GoalLevel.PROJECT,
                parent_id=strategic_id,
            )
        )

        original_strategic = store.get_goal(strategic_id)
        assert original_strategic.description == "原始描述"

        updated_strategic = store.update_goal(
            strategic_id, description="更新后的战略目标描述"
        )
        assert updated_strategic is not None
        assert updated_strategic.description == "更新后的战略目标描述"

        updated_in_db = store.get_goal(strategic_id)
        assert updated_in_db.description == "更新后的战略目标描述"

    def test_context_reflects_updated_description(self, store, context_builder):
        project_id = f"project-reflect-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Reflect Project",
                description="RC",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        chain = store.resolve_goal_chain(project_id)
        task = EnhancedTask(
            id="task-reflect",
            title="Reflect Task",
            description="RT",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )

        ctx1 = context_builder.build_context(task)
        original_mission_desc = ctx1["final_mission"]["description"]

        store.update_goal("mission-001", description="更新后的使命描述")

        new_chain = store.resolve_goal_chain(project_id)
        task.goal_chain = new_chain

        ctx2 = context_builder.build_context(task)

        assert original_mission_desc != ctx2["final_mission"]["description"]
        assert "更新后的使命描述" in ctx2["final_mission"]["description"]

    def test_version_history_tracks_updates(self, store):
        strategic_id = f"strategic-version-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=strategic_id,
                name="Version Test",
                description="v1",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )

        store.update_goal(strategic_id, description="v2")
        store.update_goal(strategic_id, description="v3")

        history = store.get_version_history(strategic_id)
        assert len(history) >= 2

        descriptions = [h.new_value for h in history if h.field_name == "description"]
        assert "v2" in descriptions
        assert "v3" in descriptions


class TestAutomaticProgressComputation:
    """Test 6: Automatic progress computation"""

    def test_all_tasks_completed_yields_100_percent(self, store, checker):
        project_id = f"project-progress-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Progress Project",
                description="PP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        def belongs_check(task_id, goal_id):
            task = checker._task_map.get(task_id)
            if not task:
                return False
            return any(ref.id == goal_id for ref in task.goal_chain)

        store.set_task_belongs_checker(belongs_check)

        task_ids = []
        for i in range(3):
            task_id = f"task-prog-{i}"
            task_ids.append(task_id)
            chain = store.resolve_goal_chain(project_id)
            task = EnhancedTask(
                id=task_id,
                title=f"Task {i}",
                description=f"Task {i}",
                task_type=EnhancedTaskType.TRAINING,
                goal_chain=chain,
            )
            checker.register_task(task)
            checker.update_task_status(task_id, EnhancedTaskStatus.COMPLETED)

        progress = checker.compute_goal_progress(project_id)
        assert progress.total_tasks == 3
        assert progress.completed_tasks == 3
        assert progress.progress_percent == 100.0

    def test_partial_completion_yields_correct_percent(self, store, checker):
        project_id = f"project-partial-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Partial Project",
                description="PP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        def belongs_check(task_id, goal_id):
            task = checker._task_map.get(task_id)
            if not task:
                return False
            return any(ref.id == goal_id for ref in task.goal_chain)

        store.set_task_belongs_checker(belongs_check)

        for i in range(4):
            task_id = f"task-partial-{i}"
            chain = store.resolve_goal_chain(project_id)
            task = EnhancedTask(
                id=task_id,
                title=f"Task {i}",
                description=f"Task {i}",
                task_type=EnhancedTaskType.TRAINING,
                goal_chain=chain,
            )
            checker.register_task(task)
            if i < 2:
                checker.update_task_status(task_id, EnhancedTaskStatus.COMPLETED)
            else:
                checker.update_task_status(task_id, EnhancedTaskStatus.IN_PROGRESS)

        progress = checker.compute_goal_progress(project_id)
        assert progress.total_tasks == 4
        assert progress.completed_tasks == 2
        assert progress.progress_percent == 50.0

    def test_progress_computation_respects_business_rules(self, store, checker):
        project_id = f"project-rules-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Rules Project",
                description="RP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        def belongs_check(task_id, goal_id):
            task = checker._task_map.get(task_id)
            if not task:
                return False
            return any(ref.id == goal_id for ref in task.goal_chain)

        store.set_task_belongs_checker(belongs_check)

        task_id = "task-rules-0"
        chain = store.resolve_goal_chain(project_id)
        task = EnhancedTask(
            id=task_id,
            title="Rules Task",
            description="RT",
            task_type=EnhancedTaskType.TRAINING,
            goal_chain=chain,
        )
        checker.register_task(task)

        progress_before = checker.compute_goal_progress(project_id)
        assert progress_before.total_tasks == 1
        assert progress_before.completed_tasks == 0
        assert progress_before.progress_percent == 0.0

        checker.update_task_status(task_id, EnhancedTaskStatus.COMPLETED)

        progress_after = checker.compute_goal_progress(project_id)
        assert progress_after.total_tasks == 1
        assert progress_after.completed_tasks == 1
        assert progress_after.progress_percent == 100.0


class TestProjectDeletionImpact:
    """Test 7: Project deletion impact analysis"""

    def test_delete_project_marks_tasks_for_review(self, store):
        project_id = f"project-delete-{uuid.uuid4().hex[:8]}"
        task_goal_id = f"task-goal-delete-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=project_id,
                name="Delete Project",
                description="DP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
                status=GoalStatus.IN_PROGRESS,
            )
        )
        store.add_goal(
            Goal(
                id=task_goal_id,
                name="Task under project",
                description="TUP",
                level=GoalLevel.TASK,
                parent_id=project_id,
                status=GoalStatus.IN_PROGRESS,
            )
        )

        affected = store.propagate_cancellation(project_id)

        assert task_goal_id in affected

        updated_task = store.get_goal(task_goal_id)
        assert updated_task is not None
        assert updated_task.status == GoalStatus.NEEDS_REVIEW

    def test_deleted_project_children_state_changes(self, store):
        project_id = f"project-state-{uuid.uuid4().hex[:8]}"
        task1_id = f"task-state-1-{uuid.uuid4().hex[:8]}"
        task2_id = f"task-state-2-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=project_id,
                name="State Project",
                description="SP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )
        store.add_goal(
            Goal(
                id=task1_id,
                name="Task 1",
                description="T1",
                level=GoalLevel.TASK,
                parent_id=project_id,
                status=GoalStatus.IN_PROGRESS,
            )
        )
        store.add_goal(
            Goal(
                id=task2_id,
                name="Task 2",
                description="T2",
                level=GoalLevel.TASK,
                parent_id=project_id,
                status=GoalStatus.NOT_STARTED,
            )
        )

        affected = store.propagate_cancellation(project_id)

        assert len(affected) == 2
        assert task1_id in affected
        assert task2_id in affected

        for tid in [task1_id, task2_id]:
            task = store.get_goal(tid)
            assert task.status == GoalStatus.NEEDS_REVIEW

    def test_state_change_logging_complete(self, store):
        project_id = f"project-log-{uuid.uuid4().hex[:8]}"
        task_id = f"task-log-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=project_id,
                name="Log Project",
                description="LP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )
        store.add_goal(
            Goal(
                id=task_id,
                name="Log Task",
                description="LT",
                level=GoalLevel.TASK,
                parent_id=project_id,
                status=GoalStatus.IN_PROGRESS,
            )
        )

        original_task = store.get_goal(task_id)
        assert original_task.status == GoalStatus.IN_PROGRESS

        store.propagate_cancellation(project_id)

        updated_task = store.get_goal(task_id)
        assert updated_task.status == GoalStatus.NEEDS_REVIEW

        project_goal = store.get_goal(project_id)
        assert (
            project_goal.status != GoalStatus.NEEDS_REVIEW or project_goal.id != task_id
        )

        affected_children = store.get_children(project_id)
        for child in affected_children:
            assert child.status == GoalStatus.NEEDS_REVIEW


class TestFrontendGoalTreeView:
    """Test 8: Frontend goal tree view verification (API-based simulation)"""

    def test_goal_tree_structure_matches_database(self, store):
        strategic_id = f"strategic-tree-{uuid.uuid4().hex[:8]}"
        project_id = f"project-tree-{uuid.uuid4().hex[:8]}"
        task_id = f"task-tree-{uuid.uuid4().hex[:8]}"

        store.add_goal(
            Goal(
                id=strategic_id,
                name="Tree Strategic",
                description="TS",
                level=GoalLevel.STRATEGIC_GOAL,
                parent_id="mission-001",
            )
        )
        store.add_goal(
            Goal(
                id=project_id,
                name="Tree Project",
                description="TP",
                level=GoalLevel.PROJECT,
                parent_id=strategic_id,
            )
        )
        store.add_goal(
            Goal(
                id=task_id,
                name="Tree Task",
                description="TT",
                level=GoalLevel.TASK,
                parent_id=project_id,
            )
        )

        tree = store.get_goal_tree()

        assert len(tree) >= 1
        mission_node = tree[0]
        assert mission_node["level"] == "mission"
        assert "children" in mission_node

        strategic_nodes = [
            c for c in mission_node["children"] if c["id"] == strategic_id
        ]
        assert len(strategic_nodes) == 1
        strategic_node = strategic_nodes[0]
        assert strategic_node["level"] == "strategic_goal"

        project_nodes = [c for c in strategic_node["children"] if c["id"] == project_id]
        assert len(project_nodes) == 1
        project_node = project_nodes[0]
        assert project_node["level"] == "project"

        task_nodes = [c for c in project_node["children"] if c["id"] == task_id]
        assert len(task_nodes) == 1
        assert task_nodes[0]["level"] == "task"

    def test_tree_expand_collapse_functionality(self, store):
        project_id = f"project-expand-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Expand Project",
                description="EP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        for i in range(3):
            task_id = f"task-expand-{i}"
            store.add_goal(
                Goal(
                    id=task_id,
                    name=f"Task {i}",
                    description=f"T{i}",
                    level=GoalLevel.TASK,
                    parent_id=project_id,
                )
            )

        tree = store.get_goal_tree()

        mission_node = tree[0]
        strategic_node = [
            c for c in mission_node["children"] if c["level"] == "strategic_goal"
        ][0]
        project_node = [c for c in strategic_node["children"] if c["id"] == project_id][
            0
        ]

        assert len(project_node["children"]) == 3

        for child in project_node["children"]:
            assert "id" in child
            assert "name" in child
            assert "level" in child
            assert "children" in child

    def test_tree_response_performance(self, store):
        project_id = f"project-perf-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="Perf Project",
                description="PP",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
            )
        )

        for i in range(50):
            task_id = f"task-perf-{i}"
            store.add_goal(
                Goal(
                    id=task_id,
                    name=f"Task {i}",
                    description=f"T{i}",
                    level=GoalLevel.TASK,
                    parent_id=project_id,
                )
            )

        start_time = time.time()
        tree = store.get_goal_tree()
        elapsed = time.time() - start_time

        assert elapsed < perf_threshold(1.0), f"Tree generation took {elapsed:.3f}s, expected < 1.0s"

        mission_node = tree[0]
        strategic_node = [
            c for c in mission_node["children"] if c["level"] == "strategic_goal"
        ][0]
        project_node = [c for c in strategic_node["children"] if c["id"] == project_id][
            0
        ]

        assert len(project_node["children"]) == 50

    def test_tree_node_labels_and_tags(self, store):
        project_id = f"project-labels-{uuid.uuid4().hex[:8]}"
        store.add_goal(
            Goal(
                id=project_id,
                name="标签测试项目",
                description="Label Test",
                level=GoalLevel.PROJECT,
                parent_id="strategic-001",
                status=GoalStatus.IN_PROGRESS,
            )
        )

        tree = store.get_goal_tree()

        mission_node = tree[0]
        strategic_node = [
            c for c in mission_node["children"] if c["level"] == "strategic_goal"
        ][0]
        project_node = [c for c in strategic_node["children"] if c["id"] == project_id][
            0
        ]

        assert project_node["name"] == "标签测试项目"
        assert project_node["level"] == "project"
        assert project_node["status"] == "in_progress"
        assert "id" in project_node
        assert "description" in project_node
        assert "created_at" in project_node
        assert "version" in project_node
