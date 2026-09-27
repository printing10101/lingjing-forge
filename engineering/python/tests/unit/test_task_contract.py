"""任务契约单元测试.

对应 app/contracts/task.py。验证：
    - TaskStatus 状态机合法/非法转换
    - TaskPriority 数值语义
    - Artifact / TaskContext / TaskResult / TaskProgress 数据结构校验
    - WorkflowNode / WorkflowEdge / WorkflowSpec DAG 校验（环检测、引用合法性）
    - WorkflowEvent 事件类型校验
    - ITaskExecutor / IWorkflowRunner 抽象接口契约

CI 标记：@pytest.mark.unit（与 ci.yml `pytest -m unit` 对齐）。
"""
from __future__ import annotations

import pytest

from app.contracts.task import (
    Artifact,
    ITaskExecutor,
    ITaskRegistry,
    IWorkflowRunner,
    TaskContext,
    TaskHandler,
    TaskPriority,
    TaskProgress,
    TaskResult,
    TaskStatus,
    VALID_STATUS_TRANSITIONS,
    WorkflowEdge,
    WorkflowEvent,
    WorkflowNode,
    WorkflowSpec,
    is_valid_transition,
)


@pytest.mark.unit
class TestTaskStatus:
    """TaskStatus 枚举与状态机转换."""

    def test_enum_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.QUEUED == "queued"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"
        assert TaskStatus.SKIPPED == "skipped"

    def test_terminal_statuses_have_no_outgoing_transitions(self):
        """终态不应有合法出口转换."""
        for terminal in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED):
            assert VALID_STATUS_TRANSITIONS[terminal] == set(), (
                f"{terminal} 应为终态，但存在合法出口: {VALID_STATUS_TRANSITIONS[terminal]}"
            )

    @pytest.mark.parametrize(
        "from_status,to_status,expected",
        [
            (TaskStatus.PENDING, TaskStatus.QUEUED, True),
            (TaskStatus.PENDING, TaskStatus.RUNNING, True),
            (TaskStatus.PENDING, TaskStatus.CANCELLED, True),
            (TaskStatus.PENDING, TaskStatus.COMPLETED, False),  # 不能跨过 RUNNING
            (TaskStatus.QUEUED, TaskStatus.RUNNING, True),
            (TaskStatus.QUEUED, TaskStatus.CANCELLED, True),
            (TaskStatus.QUEUED, TaskStatus.COMPLETED, False),
            (TaskStatus.RUNNING, TaskStatus.COMPLETED, True),
            (TaskStatus.RUNNING, TaskStatus.FAILED, True),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED, True),
            (TaskStatus.RUNNING, TaskStatus.PENDING, False),  # 不可回退
            (TaskStatus.COMPLETED, TaskStatus.RUNNING, False),  # 终态
            (TaskStatus.FAILED, TaskStatus.RUNNING, False),
            (TaskStatus.CANCELLED, TaskStatus.RUNNING, False),
            (TaskStatus.SKIPPED, TaskStatus.RUNNING, False),
        ],
    )
    def test_valid_transition_matrix(self, from_status, to_status, expected):
        assert is_valid_transition(from_status, to_status) is expected

    def test_idempotent_transition_allowed(self):
        """同状态转换（幂等写）应允许."""
        for status in TaskStatus:
            assert is_valid_transition(status, status) is True


@pytest.mark.unit
class TestTaskPriority:
    """TaskPriority 数值越大优先级越高."""

    def test_enum_values(self):
        assert TaskPriority.LOW == 1
        assert TaskPriority.NORMAL == 5
        assert TaskPriority.HIGH == 8
        assert TaskPriority.CRITICAL == 10

    def test_ordering(self):
        assert TaskPriority.LOW < TaskPriority.NORMAL < TaskPriority.HIGH < TaskPriority.CRITICAL


@pytest.mark.unit
class TestArtifact:
    """Artifact 数据结构校验."""

    @pytest.mark.parametrize("art_type", ["dataset", "model", "report", "metrics", "file"])
    def test_valid_construction(self, art_type):
        a = Artifact(name="foo", type=art_type, uri="file://x")
        assert a.name == "foo"
        assert a.type == art_type
        assert a.metadata == {}

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            Artifact(name="", type="file", uri="file://x")

    def test_invalid_type_rejected(self):
        with pytest.raises(ValueError, match="type"):
            Artifact(name="foo", type="unknown", uri="file://x")

    def test_empty_uri_rejected(self):
        with pytest.raises(ValueError, match="uri"):
            Artifact(name="foo", type="file", uri="")

    def test_metadata_defaults_to_empty_dict(self):
        a1 = Artifact(name="a", type="file", uri="file://x")
        a2 = Artifact(name="b", type="file", uri="file://y")
        # 默认 dict 不能在实例间共享（dataclass field(default_factory=dict) 保证）
        a1.metadata["k"] = "v"
        assert "k" not in a2.metadata


@pytest.mark.unit
class TestTaskContext:
    """TaskContext 默认值."""

    def test_defaults(self):
        ctx = TaskContext(job_id="job-1")
        assert ctx.job_id == "job-1"
        assert ctx.workflow_run_id is None
        assert ctx.inputs == {}
        assert ctx.config == {}
        assert ctx.retry_count == 0
        assert ctx.deadline_ts is None

    def test_with_workflow_run_id(self):
        ctx = TaskContext(job_id="job-1", workflow_run_id="wf-1")
        assert ctx.workflow_run_id == "wf-1"


@pytest.mark.unit
class TestTaskResult:
    """TaskResult 默认值."""

    def test_defaults(self):
        r = TaskResult(status=TaskStatus.COMPLETED)
        assert r.status == TaskStatus.COMPLETED
        assert r.outputs == {}
        assert r.metrics == {}
        assert r.error is None
        assert r.error_code is None


@pytest.mark.unit
class TestTaskProgress:
    """TaskProgress 边界校验."""

    @pytest.mark.parametrize("progress", [0.0, 0.5, 1.0])
    def test_valid_progress(self, progress):
        p = TaskProgress(job_id="j", status=TaskStatus.RUNNING, progress=progress)
        assert p.progress == progress

    @pytest.mark.parametrize("progress", [-0.01, 1.01, -1.0, 2.0])
    def test_out_of_range_progress_rejected(self, progress):
        with pytest.raises(ValueError, match="progress"):
            TaskProgress(job_id="j", status=TaskStatus.RUNNING, progress=progress)


@pytest.mark.unit
class TestWorkflowNode:
    """WorkflowNode 构造校验."""

    def test_valid_construction(self):
        n = WorkflowNode(node_id="n1", task_type="train")
        assert n.node_id == "n1"
        assert n.task_type == "train"
        assert n.params == {}
        assert n.inputs == {}
        assert n.retry == 0
        assert n.timeout_seconds == 3600

    def test_empty_node_id_rejected(self):
        with pytest.raises(ValueError, match="node_id"):
            WorkflowNode(node_id="", task_type="train")

    def test_empty_task_type_rejected(self):
        with pytest.raises(ValueError, match="task_type"):
            WorkflowNode(node_id="n1", task_type="")

    def test_negative_retry_rejected(self):
        with pytest.raises(ValueError, match="retry"):
            WorkflowNode(node_id="n1", task_type="train", retry=-1)

    def test_non_positive_timeout_rejected(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            WorkflowNode(node_id="n1", task_type="train", timeout_seconds=0)
        with pytest.raises(ValueError, match="timeout_seconds"):
            WorkflowNode(node_id="n1", task_type="train", timeout_seconds=-1)


@pytest.mark.unit
class TestWorkflowEdge:
    """WorkflowEdge 自环校验."""

    def test_valid_edge(self):
        e = WorkflowEdge(upstream="a", downstream="b")
        assert e.upstream == "a"
        assert e.downstream == "b"

    def test_self_loop_rejected(self):
        with pytest.raises(ValueError, match="自环"):
            WorkflowEdge(upstream="a", downstream="a")


@pytest.mark.unit
class TestWorkflowSpecValidate:
    """WorkflowSpec DAG 校验：无环 / 引用合法性."""

    @staticmethod
    def _make_node(node_id: str, task_type: str = "train") -> WorkflowNode:
        return WorkflowNode(node_id=node_id, task_type=task_type)

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            WorkflowSpec(name="", version="1.0.0", nodes=[self._make_node("n1")], edges=[])

    def test_empty_nodes_rejected(self):
        with pytest.raises(ValueError, match="nodes"):
            WorkflowSpec(name="wf", version="1.0.0", nodes=[], edges=[])

    def test_linear_dag_valid(self):
        """A → B → C 线性 DAG 无环."""
        spec = WorkflowSpec(
            name="linear",
            version="1.0.0",
            nodes=[self._make_node("A"), self._make_node("B"), self._make_node("C")],
            edges=[WorkflowEdge("A", "B"), WorkflowEdge("B", "C")],
            outputs={"final": "${C.output}"},
        )
        errors = spec.validate()
        assert errors == []

    def test_branch_and_merge_dag_valid(self):
        """分支 + 合并 DAG 无环."""
        spec = WorkflowSpec(
            name="branch",
            version="1.0.0",
            nodes=[self._make_node("A"), self._make_node("B"), self._make_node("C"), self._make_node("D")],
            edges=[
                WorkflowEdge("A", "B"),
                WorkflowEdge("A", "C"),
                WorkflowEdge("B", "D"),
                WorkflowEdge("C", "D"),
            ],
        )
        assert spec.validate() == []

    def test_duplicate_node_id_detected(self):
        spec = WorkflowSpec(
            name="dup",
            version="1.0.0",
            nodes=[self._make_node("A"), self._make_node("A")],
            edges=[],
        )
        errors = spec.validate()
        assert any("重复" in e for e in errors)

    def test_edge_references_unknown_upstream(self):
        spec = WorkflowSpec(
            name="bad",
            version="1.0.0",
            nodes=[self._make_node("A")],
            edges=[WorkflowEdge("ghost", "A")],
        )
        errors = spec.validate()
        assert any("上游节点" in e and "ghost" in e for e in errors)

    def test_edge_references_unknown_downstream(self):
        spec = WorkflowSpec(
            name="bad",
            version="1.0.0",
            nodes=[self._make_node("A")],
            edges=[WorkflowEdge("A", "ghost")],
        )
        errors = spec.validate()
        assert any("下游节点" in e and "ghost" in e for e in errors)

    def test_cycle_detected_three_nodes(self):
        """A → B → C → A 三节点环应被检测."""
        spec = WorkflowSpec(
            name="cycle",
            version="1.0.0",
            nodes=[self._make_node("A"), self._make_node("B"), self._make_node("C")],
            edges=[
                WorkflowEdge("A", "B"),
                WorkflowEdge("B", "C"),
                WorkflowEdge("C", "A"),
            ],
        )
        errors = spec.validate()
        assert any("环" in e for e in errors)

    def test_self_cycle_detected(self):
        """虽然 WorkflowEdge 拒绝自环，但通过 2 节点互引形成的环也应被检测."""
        spec = WorkflowSpec(
            name="two-cycle",
            version="1.0.0",
            nodes=[self._make_node("A"), self._make_node("B")],
            edges=[WorkflowEdge("A", "B"), WorkflowEdge("B", "A")],
        )
        errors = spec.validate()
        assert any("环" in e for e in errors)

    def test_node_input_ref_unknown_node(self):
        spec = WorkflowSpec(
            name="bad-ref",
            version="1.0.0",
            nodes=[
                WorkflowNode(
                    node_id="A",
                    task_type="train",
                    inputs={"data": "${ghost.output}"},
                ),
            ],
            edges=[],
        )
        errors = spec.validate()
        assert any("ghost" in e for e in errors)

    def test_node_input_ref_malformed(self):
        spec = WorkflowSpec(
            name="malformed",
            version="1.0.0",
            nodes=[
                WorkflowNode(
                    node_id="A",
                    task_type="train",
                    inputs={"data": "not_a_ref"},
                ),
            ],
            edges=[],
        )
        errors = spec.validate()
        assert any("格式错误" in e for e in errors)

    def test_node_input_ref_missing_dot(self):
        spec = WorkflowSpec(
            name="no-dot",
            version="1.0.0",
            nodes=[
                WorkflowNode(
                    node_id="A",
                    task_type="train",
                    inputs={"data": "${A_output}"},
                ),
            ],
            edges=[],
        )
        errors = spec.validate()
        assert any("分隔符" in e for e in errors)

    def test_workflow_output_ref_valid(self):
        spec = WorkflowSpec(
            name="ok",
            version="1.0.0",
            nodes=[self._make_node("A")],
            edges=[],
            outputs={"result": "${A.output}"},
        )
        assert spec.validate() == []

    def test_workflow_output_ref_unknown_node(self):
        spec = WorkflowSpec(
            name="bad-out",
            version="1.0.0",
            nodes=[self._make_node("A")],
            edges=[],
            outputs={"result": "${ghost.output}"},
        )
        errors = spec.validate()
        assert any("ghost" in e for e in errors)


@pytest.mark.unit
class TestWorkflowEvent:
    """WorkflowEvent event_type 校验."""

    @pytest.mark.parametrize(
        "event_type",
        [
            "node_started",
            "node_completed",
            "node_failed",
            "node_skipped",
            "workflow_completed",
            "workflow_failed",
            "workflow_cancelled",
        ],
    )
    def test_valid_event_types(self, event_type):
        ev = WorkflowEvent(workflow_run_id="wf-1", event_type=event_type)
        assert ev.event_type == event_type

    def test_invalid_event_type_rejected(self):
        with pytest.raises(ValueError, match="event_type"):
            WorkflowEvent(workflow_run_id="wf-1", event_type="unknown_event")

    def test_optional_fields_default(self):
        ev = WorkflowEvent(workflow_run_id="wf-1", event_type="node_started")
        assert ev.node_id is None
        assert ev.payload is None
        assert ev.timestamp == 0.0


@pytest.mark.unit
class TestTaskHandlerProtocol:
    """TaskHandler 是 Protocol，结构化子类型无需继承."""

    def test_structural_subtyping(self):
        class FakeHandler:
            def name(self) -> str:
                return "fake"

            def description(self) -> str:
                return "fake desc"

            def input_schema(self) -> dict:
                return {}

            def output_schema(self) -> dict:
                return {}

            async def execute(self, ctx: TaskContext) -> TaskResult:
                return TaskResult(status=TaskStatus.COMPLETED)

        # 不继承 TaskHandler，但结构匹配
        h: TaskHandler = FakeHandler()  # type: ignore[assignment]
        assert h.name() == "fake"


@pytest.mark.unit
class TestAbstractInterfaces:
    """ITaskRegistry / ITaskExecutor / IWorkflowRunner 抽象类不能直接实例化."""

    def test_task_registry_abstract(self):
        with pytest.raises(TypeError):
            ITaskRegistry()  # type: ignore[abstract]

    def test_task_executor_abstract(self):
        with pytest.raises(TypeError):
            ITaskExecutor()  # type: ignore[abstract]

    def test_workflow_runner_abstract(self):
        with pytest.raises(TypeError):
            IWorkflowRunner()  # type: ignore[abstract]

    def test_task_executor_can_be_subclassed(self):
        class DummyExecutor(ITaskExecutor):
            async def submit(self, task_type, params, *, owner_id=None, idempotency_key=None,
                             priority=TaskPriority.NORMAL, timeout_seconds=3600) -> str:
                return "job-1"

            async def get(self, job_id: str) -> TaskResult:
                return TaskResult(status=TaskStatus.COMPLETED)

            async def cancel(self, job_id: str) -> bool:
                return True

            def subscribe(self, job_id: str):
                # 简化：返回空异步生成器
                import types as _types

                async def _gen():
                    if False:
                        yield  # pragma: no cover

                return _gen()

        ex = DummyExecutor()
        assert ex is not None
