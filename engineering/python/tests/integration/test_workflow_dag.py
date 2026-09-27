"""工作流 DAG 编排引擎集成测试.

对应 ADR-005 阶段 1 验收标准（core-contracts-design.md 第 1230-1233 行）：
    - 5 节点 DAG 一键跑通，节点失败时下游自动 SKIPPED
    - 断点续跑：失败节点修复后从失败点继续，不重跑已完成节点

覆盖场景：
    1. 5 节点 DAG（菱形依赖）一键跑通 → 全部 completed + artifact 引用解析正确
    2. 中间节点失败 → 下游递归 SKIPPED → workflow failed
    3. 断点续跑：首次节点 C 失败 → 修复后 resume_from → A/B 跳过、C/D/E 重跑

CI 标记：@pytest.mark.integration（被 ci.yml Job 2 `pytest tests/integration/ -m integration` 收集）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest
import pytest_asyncio

from app.contracts.task import (
    Artifact,
    TaskContext,
    TaskHandler,
    TaskResult,
    TaskStatus,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
)
from app.tasks.registry import get_task_registry, reset_task_registry
from app.workflow.dag_store import DAGStore
from app.workflow.runner import WorkflowRunner, reset_workflow_runner


# Mock TaskHandler：可编程的测试 handler


class _ScriptedHandler:
    """可编程的 TaskHandler 实现.

    每次执行根据 ``call_index`` 取 ``results_sequence[call_index]`` 返回。
    用于模拟"首次失败、重试成功"的断点续跑场景。

    实现 TaskHandler Protocol（结构化子类型，无需继承）。
    """

    def __init__(
        self,
        task_type: str,
        results_sequence: list[TaskResult],
        *,
        description: str = "scripted test handler",
        capture: Optional[list[TaskContext]] = None,
    ) -> None:
        self._task_type = task_type
        self._results_sequence = results_sequence
        self._description = description
        self._capture = capture
        self.call_count = 0

    def name(self) -> str:
        return self._task_type

    def description(self) -> str:
        return self._description

    def input_schema(self) -> dict[str, Any]:
        return {}

    def output_schema(self) -> dict[str, Any]:
        return {}

    async def execute(self, ctx: TaskContext) -> TaskResult:
        if self._capture is not None:
            self._capture.append(ctx)
        idx = min(self.call_count, len(self._results_sequence) - 1)
        result = self._results_sequence[idx]
        self.call_count += 1
        # 模拟少量计算耗时，让调度循环能感知并发
        await asyncio.sleep(0.01)
        return result


def _make_artifact(name: str, uri: str, art_type: str = "file") -> Artifact:
    return Artifact(name=name, type=art_type, uri=uri)


def _ok_result(output_name: str, uri: str, art_type: str = "file") -> TaskResult:
    return TaskResult(
        status=TaskStatus.COMPLETED,
        outputs={output_name: _make_artifact(output_name, uri, art_type)},
        metrics={"latency_ms": 10.0},
    )


def _failed_result(error: str = "模拟失败") -> TaskResult:
    return TaskResult(
        status=TaskStatus.FAILED,
        error=error,
        error_code="TEST_INJECTED_FAILURE",
    )


# 数据库 fixture：内存 SQLite + 完整 schema


@pytest_asyncio.fixture
async def in_memory_dag_store(monkeypatch):
    """提供基于内存 SQLite 的 DAGStore + 完整表结构.

    每个测试函数独立一份内存数据库（隔离）。
    """
    # 强制 SQLite 内存模式
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    # 清空单例，使下次 get_sessionmaker 重新基于新 DB_URL 创建
    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    # 创建全部表（workflow_runs / workflow_run_nodes 复用 training_task.Base）
    from app.database.models.training_task import init_db

    await init_db()

    # 重置 workflow / registry 单例
    reset_workflow_runner()
    reset_task_registry()
    yield DAGStore()
    # 清理
    reset_workflow_runner()
    reset_task_registry()
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


# 工作流规格构造工具


def _build_diamond_dag(
    *,
    c_fail_first: bool = False,
) -> tuple[WorkflowSpec, dict[str, _ScriptedHandler]]:
    """构建 5 节点菱形 DAG:
        A → B → D
        A → C → D
        D → E
    （B/C 并行，D 等待 B+C，E 等待 D）

    Args:
        c_fail_first: 若 True，节点 C 首次执行返回 FAILED（用于断点续跑场景）。
    """
    # 节点 A：根节点，输出 out_a
    # 节点 B/C：消费 ${A.out_a}，输出 out_b / out_c
    # 节点 D：消费 ${B.out_b} + ${C.out_c}，输出 out_d
    # 节点 E：消费 ${D.out_d}，输出 out_e
    nodes = [
        WorkflowNode(node_id="A", task_type="task_a", params={"step": "root"}),
        WorkflowNode(
            node_id="B",
            task_type="task_b",
            inputs={"in_a": "${A.out_a}"},
        ),
        WorkflowNode(
            node_id="C",
            task_type="task_c",
            inputs={"in_a": "${A.out_a}"},
        ),
        WorkflowNode(
            node_id="D",
            task_type="task_d",
            inputs={"in_b": "${B.out_b}", "in_c": "${C.out_c}"},
        ),
        WorkflowNode(
            node_id="E",
            task_type="task_e",
            inputs={"in_d": "${D.out_d}"},
        ),
    ]
    edges = [
        WorkflowEdge(upstream="A", downstream="B"),
        WorkflowEdge(upstream="A", downstream="C"),
        WorkflowEdge(upstream="B", downstream="D"),
        WorkflowEdge(upstream="C", downstream="D"),
        WorkflowEdge(upstream="D", downstream="E"),
    ]

    # 工作流级输出
    outputs = {
        "final_report": "${E.out_e}",
        "intermediate_d": "${D.out_d}",
    }

    spec = WorkflowSpec(
        name="diamond_dag_test",
        version="1.0.0",
        nodes=nodes,
        edges=edges,
        outputs=outputs,
        metadata={"max_concurrent": 4},
    )

    handlers: dict[str, _ScriptedHandler] = {
        "task_a": _ScriptedHandler("task_a", [_ok_result("out_a", "file://A/out_a")]),
        "task_b": _ScriptedHandler("task_b", [_ok_result("out_b", "file://B/out_b")]),
        "task_c": _ScriptedHandler(
            "task_c",
            ([_failed_result("C 节点首次失败")] if c_fail_first else []) + [_ok_result("out_c", "file://C/out_c")],
        ),
        "task_d": _ScriptedHandler("task_d", [_ok_result("out_d", "file://D/out_d")]),
        "task_e": _ScriptedHandler("task_e", [_ok_result("out_e", "file://E/out_e")]),
    }
    return spec, handlers


def _register_handlers(handlers: dict[str, _ScriptedHandler]) -> None:
    registry = get_task_registry()
    for handler in handlers.values():
        registry.register(handler, plugin_id="test_plugin")


async def _wait_for_terminal(
    runner: WorkflowRunner,
    workflow_run_id: str,
    *,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """轮询工作流状态直到进入终态（completed/failed/cancelled）."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        status = await runner.get_status(workflow_run_id)
        run_status = status.get("status")
        if run_status in {"completed", "failed", "cancelled"}:
            return status
        await asyncio.sleep(0.05)
    # 超时返回最后状态
    return await runner.get_status(workflow_run_id)


def _node_status_map(run_status: dict[str, Any]) -> dict[str, str]:
    """从 run 字典抽取 {node_id: status} 映射."""
    nodes = run_status.get("nodes", []) or []
    return {n["node_id"]: n.get("status", "unknown") for n in nodes}


# 测试用例


@pytest.mark.integration
class TestWorkflowDagHappyPath:
    """5 节点菱形 DAG 一键跑通."""

    @pytest.mark.asyncio
    async def test_diamond_dag_completes_all_nodes(self, in_memory_dag_store):
        """菱形 DAG 全部节点 completed，artifact 引用正确解析."""
        spec, handlers = _build_diamond_dag()
        _register_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")

        final = await _wait_for_terminal(runner, workflow_run_id)
        assert final["status"] == "completed", (
            f"工作流应为 completed，实际: {final['status']}, error: {final.get('error')}"
        )

        node_status = _node_status_map(final)
        for node_id in ("A", "B", "C", "D", "E"):
            assert node_status.get(node_id) == "completed", (
                f"节点 {node_id} 应为 completed，实际: {node_status.get(node_id)}; 全部节点状态: {node_status}"
            )

    @pytest.mark.asyncio
    async def test_artifact_references_propagate(self, in_memory_dag_store):
        """artifact 引用解析：下游节点 ctx.inputs 包含上游输出 artifact."""
        spec, handlers = _build_diamond_dag()
        # 用 capture 列表记录 D 节点收到的 ctx
        d_capture: list[TaskContext] = []
        handlers["task_d"] = _ScriptedHandler(
            "task_d",
            [_ok_result("out_d", "file://D/out_d")],
            capture=d_capture,
        )
        _register_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        await _wait_for_terminal(runner, workflow_run_id)

        # D 节点应收到 B.out_b 和 C.out_c 两个 artifact
        assert len(d_capture) == 1, f"D 应只执行一次，实际 {len(d_capture)} 次"
        d_ctx = d_capture[0]
        assert "in_b" in d_ctx.inputs, "D 应收到 in_b 输入"
        assert "in_c" in d_ctx.inputs, "D 应收到 in_c 输入"
        assert d_ctx.inputs["in_b"].uri == "file://B/out_b"
        assert d_ctx.inputs["in_c"].uri == "file://C/out_c"

    @pytest.mark.asyncio
    async def test_workflow_outputs_resolved(self, in_memory_dag_store):
        """工作流级 outputs 解析为最终 artifact."""
        spec, handlers = _build_diamond_dag()
        _register_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id)

        outputs = final.get("outputs") or {}
        assert "final_report" in outputs, f"应解析 final_report 输出，实际 outputs: {list(outputs.keys())}"
        assert "intermediate_d" in outputs


@pytest.mark.integration
class TestWorkflowFailurePropagation:
    """节点失败传播：中间节点失败 → 下游递归 SKIPPED."""

    @pytest.mark.asyncio
    async def test_middle_node_failure_skips_downstream(self, in_memory_dag_store):
        """节点 B 失败 → D/E 全部 SKIPPED → workflow failed.

        拓扑：A → B → D → E
                A → C → D
        B 失败时，D 因依赖 B 也无法执行（SKIPPED），E 因依赖 D 也 SKIPPED。
        C 与 B 并行，应正常完成（不受 B 失败影响）。
        """
        spec, handlers = _build_diamond_dag()
        # 让 B 始终失败
        handlers["task_b"] = _ScriptedHandler("task_b", [_failed_result("B 节点注入失败")])
        _register_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id)

        assert final["status"] == "failed", f"工作流应为 failed，实际: {final['status']}"

        node_status = _node_status_map(final)
        assert node_status.get("A") == "completed", "A 应正常完成"
        assert node_status.get("B") == "failed", "B 应失败"
        assert node_status.get("C") == "completed", "C 与 B 并行，应正常完成"
        assert node_status.get("D") == "skipped", "D 依赖 B，应被 skipped"
        assert node_status.get("E") == "skipped", "E 依赖 D，应被 skipped"


@pytest.mark.integration
class TestWorkflowResumeFromFailure:
    """断点续跑：失败节点修复后从失败点继续."""

    @pytest.mark.asyncio
    async def test_resume_reruns_only_failed_and_pending(self, in_memory_dag_store):
        """断点续跑场景：
        1. 首次运行：C 首次失败 → D/E skipped → workflow failed
        2. 修复后 resume_from 同一 run_id：
            - A/B 已 completed → 跳过（不重跑）
            - C 之前 failed → 重跑（这次成功）
            - D/E 之前 skipped → 重跑
        3. 最终全部 completed
        """
        spec, handlers = _build_diamond_dag(c_fail_first=True)
        _register_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)

        # 首次运行：C 失败
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        first_final = await _wait_for_terminal(runner, workflow_run_id)

        assert first_final["status"] == "failed", f"首次应 failed（C 失败），实际: {first_final['status']}"
        first_nodes = _node_status_map(first_final)
        assert first_nodes.get("A") == "completed"
        assert first_nodes.get("B") == "completed"
        assert first_nodes.get("C") == "failed"
        assert first_nodes.get("D") == "skipped"
        assert first_nodes.get("E") == "skipped"

        # 校验 handler 调用次数：A/B/C 各 1 次，D/E 0 次
        assert handlers["task_a"].call_count == 1
        assert handlers["task_b"].call_count == 1
        assert handlers["task_c"].call_count == 1, "C 应只调一次（首次失败）"
        assert handlers["task_d"].call_count == 0
        assert handlers["task_e"].call_count == 0

        # 断点续跑：C 这次成功
        # _ScriptedHandler 的 results_sequence 第二项是成功结果
        # 重置 WorkflowRunner 单例但不重置 DAGStore（保留 DB 状态）
        resume_runner = WorkflowRunner(dag_store=in_memory_dag_store)
        resumed_id = await resume_runner.run(spec, resume_from=workflow_run_id, owner_id="test_user")
        assert resumed_id == workflow_run_id, "断点续跑应复用同一 run_id"

        second_final = await _wait_for_terminal(resume_runner, resumed_id)
        assert second_final["status"] == "completed", (
            f"续跑后应 completed，实际: {second_final['status']}, error: {second_final.get('error')}"
        )

        second_nodes = _node_status_map(second_final)
        for node_id in ("A", "B", "C", "D", "E"):
            assert second_nodes.get(node_id) == "completed", (
                f"续跑后 {node_id} 应 completed，实际: {second_nodes.get(node_id)}"
            )

        # 验证未重跑已完成节点：A/B 调用次数应仍为 1
        assert handlers["task_a"].call_count == 1, "A 不应重跑"
        assert handlers["task_b"].call_count == 1, "B 不应重跑"
        # C 应被重跑（首次失败 续跑成功），call_count=2
        assert handlers["task_c"].call_count == 2, "C 应被重跑（首次失败）"
        # D/E 之前 skipped，续跑时应执行
        assert handlers["task_d"].call_count == 1, "D 应在续跑时执行"
        assert handlers["task_e"].call_count == 1, "E 应在续跑时执行"

    @pytest.mark.asyncio
    async def test_resume_rejects_mismatched_spec(self, in_memory_dag_store):
        """resume_from 拒绝不匹配的 spec（name 或 version 不同）."""
        spec, handlers = _build_diamond_dag()
        _register_handlers(handlers)
        runner = WorkflowRunner(dag_store=in_memory_dag_store)

        workflow_run_id = await runner.run(spec, owner_id="test_user")
        await _wait_for_terminal(runner, workflow_run_id)

        # 构造一个 name 不同但节点相同的 spec
        mismatched_spec = WorkflowSpec(
            name="different_workflow_name",
            version=spec.version,
            nodes=spec.nodes,
            edges=spec.edges,
        )
        with pytest.raises(ValueError, match="spec 不匹配"):
            await runner.run(mismatched_spec, resume_from=workflow_run_id)


@pytest.mark.integration
class TestWorkflowEventStream:
    """工作流事件流订阅（subscribe）."""

    @pytest.mark.asyncio
    async def test_subscribe_receives_terminal_event(self, in_memory_dag_store):
        """订阅者应收到 workflow_completed 终态事件."""
        spec, handlers = _build_diamond_dag()
        _register_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")

        # 订阅事件流（run 已异步启动，订阅可能错过早期事件，但终态事件应能收到）
        events = []
        async for event in runner.subscribe(workflow_run_id):
            events.append(event)
            if event.event_type in {
                "workflow_completed",
                "workflow_failed",
                "workflow_cancelled",
            }:
                break

        # 至少应收到一个终态事件
        assert len(events) >= 1, "应至少收到一个事件"
        terminal_types = {"workflow_completed", "workflow_failed", "workflow_cancelled"}
        assert events[-1].event_type in terminal_types, f"最后一个事件应为终态，实际: {events[-1].event_type}"
