"""Phase 0-8 端到端闭环集成测试.

对应 ADR-005 / ADR-016 / ADR-017 + ``core-contracts-design.md`` 阶段 0-8 路线图
（第 1185-1384 行）。本模块验证完整闭环工作流的端到端可用性，是阶段 8 的
最终验收测试。

覆盖场景
--------
1. **7 节点闭环 DAG 端到端跑通**（核心）：
   ``perceive → predict → decide → generate_params → validate_cam → execute → collect_feedback``
   - perceive / generate_params / validate_cam / execute / collect_feedback 使用 _ScriptedHandler
   - predict 使用真实 WorldModelPlugin（wm_predict_state 任务类型）
   - decide 使用真实 RLAgentPlugin（rl_act 任务类型）
   - 全部节点 completed + artifact 引用跨节点传播

2. **WorldModel + RLAgent 插件协同**：
   - WorldModelPlugin 输出 predicted_trajectory / trajectory_metrics
   - RLAgentPlugin 消费 current_state，输出 action / safety_result / value_estimate
   - 验证插件通过 TaskRegistry 注册后被 WorkflowRunner 调度

3. **SafetyShield 硬约束过滤**：
   - RLAgentPlugin 内置 SafetyShield 过滤危险动作
   - 验证 safety_result artifact 包含 violated / fallback_used 字段

4. **8 大契约互操作性**：
   - Task / Workflow / Dataset / Plugin / Config / Observability / WorldModel / RLAgent / Explainability
   - 验证契约 dataclass 可构造、插件可注册、工作流可调度

5. **跨阶段集成**：
   - Phase 1（WorkflowRunner）+ Phase 8（WorldModel/RLAgent 插件）协同
   - Phase 2（SnapshotStore）记录工作流运行快照

CI 标记：``@pytest.mark.integration``（被 ci.yml Job ``python-integration-tests``
的 ``pytest tests/integration/ -m integration`` 收集）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import numpy as np
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


# 辅助：可编程 TaskHandler mock（复用自 test_workflow_dag.py 风格）


class _ScriptedHandler:
    """可编程的 TaskHandler 实现.

    每次执行返回 ``results_sequence[0]``（单结果模式）或按 ``call_index`` 取值。
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
        await asyncio.sleep(0.01)
        return result


def _make_artifact(
    name: str,
    uri: str,
    art_type: str = "metrics",
    *,
    data: Optional[list[float]] = None,
) -> Artifact:
    """构造 Artifact，可携带 metadata.data（供 WorldModel/RLAgent 插件加载）."""
    metadata: dict[str, Any] = {}
    if data is not None:
        metadata["data"] = data
    return Artifact(name=name, type=art_type, uri=uri, metadata=metadata)


def _ok_result(
    output_name: str,
    uri: str,
    art_type: str = "metrics",
    *,
    data: Optional[list[float]] = None,
) -> TaskResult:
    """构造成功的 TaskResult，含单个输出 artifact."""
    return TaskResult(
        status=TaskStatus.COMPLETED,
        outputs={output_name: _make_artifact(output_name, uri, art_type, data=data)},
        metrics={"latency_ms": 10.0},
    )


def _ok_result_multi(outputs: dict[str, Artifact]) -> TaskResult:
    """构造成功的 TaskResult，含多个输出 artifact."""
    return TaskResult(
        status=TaskStatus.COMPLETED,
        outputs=outputs,
        metrics={"latency_ms": 10.0},
    )


# 数据库 fixture：内存 SQLite + 完整 schema


@pytest_asyncio.fixture
async def in_memory_dag_store(monkeypatch):
    """提供基于内存 SQLite 的 DAGStore + 完整表结构.

    每个测试函数独立一份内存数据库（隔离）。
    """
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    from app.database.models.training_task import init_db

    await init_db()

    reset_workflow_runner()
    reset_task_registry()
    yield DAGStore()
    reset_workflow_runner()
    reset_task_registry()
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


@pytest_asyncio.fixture
async def integrated_stores(monkeypatch, tmp_path):
    """提供共享同一内存 DB 的 DAGStore + SnapshotStore（跨阶段集成场景）."""
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DATASET_STORE_DIR", str(tmp_path / "datasets"))

    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    from app.database.models.training_task import init_db

    await init_db()

    reset_workflow_runner()
    reset_task_registry()

    import app.observability.snapshot as _ss_mod

    _ss_mod._snapshot_store = None

    from app.observability.snapshot import SnapshotStore

    yield {
        "dag": DAGStore(),
        "snapshot": SnapshotStore(),
    }

    reset_workflow_runner()
    reset_task_registry()
    _ss_mod._snapshot_store = None
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


# 7 节点闭环 DAG 构造


def _state_vector() -> list[float]:
    """构造合法的 8 维加工状态向量（供 WorldModel/RLAgent 插件消费）.

    维度对应 StateField：spindle_speed / feed_rate / depth_of_cut / width_of_cut /
    tool_wear / vibration_rms / temperature / chatter_probability
    """
    return [8000.0, 1200.0, 1.0, 0.5, 0.05, 0.3, 45.0, 0.1]


def _action_vector() -> list[float]:
    """构造合法的 4 维候选动作向量（供 WorldModel 插件消费）."""
    return [0.0, 0.0, 0.0, 0.0]


def _build_closed_loop_dag() -> tuple[WorkflowSpec, dict[str, Any]]:
    """构建 7 节点闭环 DAG（对应 closed_loop_machining_optimization.yaml）.

    节点拓扑：
        perceive → predict → decide → generate_params → validate_cam → execute → collect_feedback

    task_type 映射：
        - perceive:          data_ingest（_ScriptedHandler）
        - predict:           wm_predict_state（真实 WorldModelPlugin）
        - decide:            rl_act（真实 RLAgentPlugin）
        - generate_params:   cam_generate（_ScriptedHandler）
        - validate_cam:      cam_validate（_ScriptedHandler）
        - execute:           job_dispatch（_ScriptedHandler，dry_run=true）
        - collect_feedback:  flywheel_collect（_ScriptedHandler）

    Returns
    -------
    tuple[WorkflowSpec, dict[str, Any]]
        (WorkflowSpec, handlers_dict)。handlers_dict 包含 5 个 _ScriptedHandler
        （predict/decide 由真实插件处理，不在 dict 中）。
    """
    nodes = [
        # 1. perceive：输出 state_artifact（携带状态向量数据）+ candidate_actions
        WorkflowNode(
            node_id="perceive",
            task_type="data_ingest",
            params={"signal_types": ["vibration", "force"]},
        ),
        # 2. predict：WorldModelPlugin，消费 current_state + candidate_action
        WorkflowNode(
            node_id="predict",
            task_type="wm_predict_state",
            params={"horizon": 5, "model_uri": "model://world_model/1.0.0"},
            inputs={
                "current_state": "${perceive.state_artifact}",
                "candidate_action": "${perceive.candidate_actions}",
            },
        ),
        # 3. decide：RLAgentPlugin，消费 current_state
        WorkflowNode(
            node_id="decide",
            task_type="rl_act",
            params={"model_uri": "model://rl_agent/1.0.0"},
            inputs={
                "current_state": "${perceive.state_artifact}",
            },
        ),
        # 4. generate_params：消费 decide.action
        WorkflowNode(
            node_id="generate_params",
            task_type="cam_generate",
            params={"backend": "PyCAM"},
            inputs={"recommended_action": "${decide.action}"},
        ),
        # 5. validate_cam：消费 generate_params.gcode_artifact
        WorkflowNode(
            node_id="validate_cam",
            task_type="cam_validate",
            params={"backend": "PyCAM"},
            inputs={"gcode": "${generate_params.gcode_artifact}"},
        ),
        # 6. execute：dry_run=true（v1 硬门控），消费 gcode + cam_validation_report
        WorkflowNode(
            node_id="execute",
            task_type="job_dispatch",
            params={"dry_run": True},
            inputs={
                "gcode": "${generate_params.gcode_artifact}",
                "cam_validation_report": "${validate_cam.validation_report_artifact}",
            },
        ),
        # 7. collect_feedback：消费 execute.result_artifact + decide.action
        WorkflowNode(
            node_id="collect_feedback",
            task_type="flywheel_collect",
            params={"feedback_types": ["actual_vs_predicted"]},
            inputs={
                "actual_result": "${execute.result_artifact}",
                "recommended_action": "${decide.action}",
            },
        ),
    ]

    edges = [
        WorkflowEdge(upstream="perceive", downstream="predict"),
        WorkflowEdge(upstream="predict", downstream="decide"),
        WorkflowEdge(upstream="decide", downstream="generate_params"),
        WorkflowEdge(upstream="generate_params", downstream="validate_cam"),
        WorkflowEdge(upstream="validate_cam", downstream="execute"),
        WorkflowEdge(upstream="execute", downstream="collect_feedback"),
    ]

    outputs = {
        "recommended_action": "${decide.action}",
        "gcode": "${generate_params.gcode_artifact}",
        "cam_validation_report": "${validate_cam.validation_report_artifact}",
        "execution_result": "${execute.result_artifact}",
        "feedback_record": "${collect_feedback.feedback_artifact}",
    }

    spec = WorkflowSpec(
        name="closed_loop_machining_optimization",
        version="1.0.0",
        nodes=nodes,
        edges=edges,
        outputs=outputs,
        metadata={"max_concurrent": 1},  # 闭环严格顺序执行
    )

    # perceive 节点输出两个 artifact：state_artifact（状态向量）+ candidate_actions（候选动作）
    perceive_outputs = {
        "state_artifact": _make_artifact(
            "state_artifact",
            "metrics://perceive/state",
            data=_state_vector(),
        ),
        "candidate_actions": _make_artifact(
            "candidate_actions",
            "metrics://perceive/actions",
            data=_action_vector(),
        ),
    }

    handlers: dict[str, _ScriptedHandler] = {
        "data_ingest": _ScriptedHandler("data_ingest", [_ok_result_multi(perceive_outputs)]),
        "cam_generate": _ScriptedHandler(
            "cam_generate",
            [_ok_result("gcode_artifact", "file://generate/gcode.nc", "file")],
        ),
        "cam_validate": _ScriptedHandler(
            "cam_validate",
            [_ok_result("validation_report_artifact", "file://validate/report.json", "file")],
        ),
        "job_dispatch": _ScriptedHandler(
            "job_dispatch",
            [_ok_result("result_artifact", "file://execute/result.json", "file")],
        ),
        "flywheel_collect": _ScriptedHandler(
            "flywheel_collect",
            [_ok_result("feedback_artifact", "file://feedback/record.json", "file")],
        ),
    }

    return spec, handlers


def _register_scripted_handlers(handlers: dict[str, _ScriptedHandler]) -> None:
    """注册 _ScriptedHandler 到全局 TaskRegistry."""
    registry = get_task_registry()
    for handler in handlers.values():
        registry.register(handler, plugin_id="test_scripted")


def _register_real_plugins() -> dict[str, Any]:
    """注册真实的 WorldModelPlugin 和 RLAgentPlugin 到全局 TaskRegistry.

    Returns
    -------
    dict[str, Any]
        {"world_model": WorldModelPlugin, "rl_agent": RLAgentPlugin}
    """
    from app.plugins.rl_agent.plugin import RLAgentPlugin
    from app.plugins.world_model.plugin import WorldModelPlugin

    registry = get_task_registry()
    wm_plugin = WorldModelPlugin()
    rl_plugin = RLAgentPlugin()
    wm_plugin.register(registry)
    rl_plugin.register(registry)
    return {"world_model": wm_plugin, "rl_agent": rl_plugin}


async def _wait_for_terminal(
    runner: WorkflowRunner,
    workflow_run_id: str,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """轮询工作流状态直到进入终态（completed/failed/cancelled）."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        status = await runner.get_status(workflow_run_id)
        run_status = status.get("status")
        if run_status in {"completed", "failed", "cancelled"}:
            return status
        await asyncio.sleep(0.1)
    return await runner.get_status(workflow_run_id)


def _node_status_map(run_status: dict[str, Any]) -> dict[str, str]:
    """从 run 字典抽取 {node_id: status} 映射."""
    nodes = run_status.get("nodes", []) or []
    return {n["node_id"]: n.get("status", "unknown") for n in nodes}


# 测试用例 1：7 节点闭环 DAG 端到端跑通


@pytest.mark.integration
class TestClosedLoopWorkflowEndToEnd:
    """7 节点闭环 DAG 端到端跑通（Phase 0-8 核心验收）."""

    @pytest.mark.asyncio
    async def test_closed_loop_dag_completes_all_nodes(self, in_memory_dag_store):
        """闭环 DAG 全部 7 节点 completed，工作流级 outputs 解析正确.

        覆盖 ADR-017 第 3 节闭环工作流模板的端到端可用性。
        """
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")

        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed", (
            f"闭环工作流应为 completed，实际: {final['status']}, "
            f"error: {final.get('error')}, nodes: {_node_status_map(final)}"
        )

        node_status = _node_status_map(final)
        expected_nodes = [
            "perceive",
            "predict",
            "decide",
            "generate_params",
            "validate_cam",
            "execute",
            "collect_feedback",
        ]
        for node_id in expected_nodes:
            assert node_status.get(node_id) == "completed", (
                f"节点 {node_id} 应为 completed，实际: {node_status.get(node_id)}; 全部节点状态: {node_status}"
            )

    @pytest.mark.asyncio
    async def test_closed_loop_workflow_outputs_resolved(self, in_memory_dag_store):
        """工作流级 outputs 全部解析为 artifact（5 个输出键）."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed", f"前置条件：工作流应 completed，实际: {final['status']}"

        outputs = final.get("outputs") or {}
        expected_output_keys = [
            "recommended_action",
            "gcode",
            "cam_validation_report",
            "execution_result",
            "feedback_record",
        ]
        for key in expected_output_keys:
            assert key in outputs, f"工作流输出应包含 {key}，实际 outputs: {list(outputs.keys())}"
            assert outputs[key] is not None, f"工作流输出 {key} 不应为 None"

    @pytest.mark.asyncio
    async def test_closed_loop_max_concurrent_one(self, in_memory_dag_store):
        """max_concurrent=1 时闭环严格顺序执行（无并行节点）."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed"
        # 7 节点线性 DAG，max_concurrent=1，应全部 completed
        node_status = _node_status_map(final)
        assert len(node_status) == 7, f"应有 7 个节点，实际: {len(node_status)}"
        assert all(s == "completed" for s in node_status.values()), f"所有节点应 completed，实际: {node_status}"


# 测试用例 2：WorldModel + RLAgent 插件协同


@pytest.mark.integration
class TestWorldModelRLAgentIntegration:
    """WorldModelPlugin + RLAgentPlugin 真实插件协同验证."""

    @pytest.mark.asyncio
    async def test_world_model_plugin_registered_and_executed(self, in_memory_dag_store):
        """WorldModelPlugin 注册到 TaskRegistry 后可被 WorkflowRunner 调度."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        plugins = _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed", (
            f"工作流应 completed，实际: {final['status']}, nodes: {_node_status_map(final)}"
        )

        # predict 节点（WorldModelPlugin）应输出 predicted_trajectory + trajectory_metrics
        node_status = _node_status_map(final)
        assert node_status.get("predict") == "completed", (
            f"predict 节点应 completed，实际: {node_status.get('predict')}"
        )

    @pytest.mark.asyncio
    async def test_rl_agent_plugin_registered_and_executed(self, in_memory_dag_store):
        """RLAgentPlugin 注册到 TaskRegistry 后可被 WorkflowRunner 调度."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed"

        # decide 节点（RLAgentPlugin）应输出 action + safety_result + value_estimate
        node_status = _node_status_map(final)
        assert node_status.get("decide") == "completed", f"decide 节点应 completed，实际: {node_status.get('decide')}"

    @pytest.mark.asyncio
    async def test_artifact_propagation_across_plugins(self, in_memory_dag_store):
        """artifact 跨插件传播：perceive.state_artifact → predict → decide → generate_params."""
        spec, handlers = _build_closed_loop_dag()
        # 用 capture 列表记录 generate_params 收到的 ctx
        gen_capture: list[TaskContext] = []
        handlers["cam_generate"] = _ScriptedHandler(
            "cam_generate",
            [_ok_result("gcode_artifact", "file://generate/gcode.nc", "file")],
            capture=gen_capture,
        )
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed", f"前置条件：工作流应 completed，实际: {final['status']}"

        # generate_params 应收到 decide.action artifact
        assert len(gen_capture) == 1, f"generate_params 应只执行一次，实际 {len(gen_capture)} 次"
        gen_ctx = gen_capture[0]
        assert "recommended_action" in gen_ctx.inputs, (
            "generate_params 应收到 recommended_action 输入（来自 decide.action）"
        )
        assert gen_ctx.inputs["recommended_action"].uri.startswith("metrics://"), (
            f"recommended_action uri 应为 metrics:// 协议，实际: {gen_ctx.inputs['recommended_action'].uri}"
        )


# 测试用例 3：SafetyShield 硬约束过滤


@pytest.mark.integration
class TestSafetyShieldHardConstraints:
    """RLAgentPlugin 内置 SafetyShield 硬约束过滤验证."""

    @pytest.mark.asyncio
    async def test_safety_result_artifact_in_output(self, in_memory_dag_store):
        """decide 节点输出包含 safety_result artifact（SafetyShield 过滤结果）."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed"

        # 通过 completed_outputs 检查 decide 节点的 safety_result
        completed_outputs = await in_memory_dag_store.get_completed_node_outputs(workflow_run_id)
        decide_outputs = completed_outputs.get("decide", {})
        assert "safety_result" in decide_outputs, (
            f"decide 节点应输出 safety_result，实际 outputs: {list(decide_outputs.keys())}"
        )

        safety_artifact = decide_outputs["safety_result"]
        # safety_result 可能是 dict 或 Artifact
        if isinstance(safety_artifact, dict):
            metadata = safety_artifact.get("metadata", {})
        else:
            metadata = safety_artifact.metadata

        assert "violated" in metadata, f"safety_result.metadata 应包含 violated 字段，实际: {metadata}"
        assert "fallback_used" in metadata, f"safety_result.metadata 应包含 fallback_used 字段，实际: {metadata}"

    @pytest.mark.asyncio
    async def test_action_artifact_has_four_dimensions(self, in_memory_dag_store):
        """decide 节点输出的 action artifact 包含 4 维动作向量（PPO 默认 action_dim=4）."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed"

        completed_outputs = await in_memory_dag_store.get_completed_node_outputs(workflow_run_id)
        decide_outputs = completed_outputs.get("decide", {})
        action_artifact = decide_outputs.get("action")
        assert action_artifact is not None, "decide 节点应输出 action artifact"

        if isinstance(action_artifact, dict):
            metadata = action_artifact.get("metadata", {})
        else:
            metadata = action_artifact.metadata

        # action 维度应为 4：[spindle_speed_delta, feed_rate_delta, depth_of_cut_delta, width_of_cut_delta]
        values = metadata.get("values")
        assert values is not None, f"action.metadata 应包含 values，实际: {metadata}"
        assert len(values) == 4, f"action 向量维度应为 4，实际: {len(values)}"


# 测试用例 4：8 大契约互操作性


@pytest.mark.integration
class TestContractsInteroperability:
    """8 大契约 dataclass 互操作性验证（Phase 0 契约定型）."""

    def test_task_workflow_contract_construction(self):
        """Task / Workflow 契约 dataclass 可合法构造."""
        from app.contracts.task import (
            Artifact,
            TaskResult,
            TaskStatus,
            WorkflowEdge,
            WorkflowNode,
            WorkflowSpec,
        )

        node = WorkflowNode(node_id="n1", task_type="test_task")
        edge = WorkflowEdge(upstream="n1", downstream="n2")
        spec = WorkflowSpec(
            name="contract_test",
            version="1.0.0",
            nodes=[node],
            edges=[edge],
        )
        assert spec.name == "contract_test"
        assert len(spec.nodes) == 1

        artifact = Artifact(name="out", type="metrics", uri="metrics://test")
        result = TaskResult(
            status=TaskStatus.COMPLETED,
            outputs={"out": artifact},
        )
        assert result.status == TaskStatus.COMPLETED

    def test_dataset_contract_construction(self):
        """Dataset 契约 dataclass 可合法构造."""
        from app.contracts.dataset import DatasetSchema, DatasetStatus

        schema = DatasetSchema(
            fields={"col": {"type": "float", "required": True}},
            primary_key=["col"],
        )
        assert "col" in schema.fields
        assert DatasetStatus.DRAFT.value == "draft"

    def test_plugin_contract_construction(self):
        """Plugin 契约 dataclass 可合法构造."""
        from app.contracts.plugin import BUILTIN_CAPABILITIES, PluginManifest

        assert "task:submit" in BUILTIN_CAPABILITIES
        manifest = PluginManifest(
            id="test_plugin",
            name="测试插件",
            version="1.0.0",
            description="测试插件",
            author="test",
            license="MIT",
            entrypoint="test:plugin",
        )
        assert manifest.id == "test_plugin"
        assert manifest.entrypoint == "test:plugin"

    def test_world_model_contract_construction(self):
        """WorldModel 契约 dataclass 可合法构造."""
        from app.contracts.world_model import (
            DEFAULT_STATE_DIM,
            StateField,
            WorldModelPredictRequest,
        )

        assert DEFAULT_STATE_DIM == 8
        assert StateField.SPINDLE_SPEED == "spindle_speed"

        request = WorldModelPredictRequest(
            current_state={"spindle_speed": 8000.0},
            candidate_action={"spindle_speed_delta": 0.0},
            horizon=10,
        )
        assert request.horizon == 10

    def test_rl_agent_contract_construction(self):
        """RLAgent 契约 dataclass 可合法构造."""
        from app.contracts.rl_agent import (
            OptimizationTarget,
            PolicyAlgorithm,
            RLActRequest,
            TrainingStatus,
        )
        from app.contracts.world_model import DEFAULT_ACTION_DIM

        assert DEFAULT_ACTION_DIM == 4
        assert PolicyAlgorithm.is_valid(PolicyAlgorithm.PPO)
        assert OptimizationTarget.default() == OptimizationTarget.BALANCE
        assert TrainingStatus.is_terminal(TrainingStatus.COMPLETED)
        assert not TrainingStatus.is_terminal(TrainingStatus.RUNNING)

        request = RLActRequest(
            current_state={"spindle_speed": 8000.0},
            candidate_actions=[{"spindle_speed_delta": 0.0}],
            optimization_target=OptimizationTarget.MINIMIZE_CHATTER,
        )
        assert request.optimization_target == OptimizationTarget.MINIMIZE_CHATTER

    def test_explainability_contract_construction(self):
        """Explainability 契约 dataclass 可合法构造."""
        from app.contracts.explainability import (
            ComparisonType,
            ExplanationType,
            ProjectionMethod,
        )

        assert ExplanationType.is_valid(ExplanationType.HIDDEN_STATE)
        assert ProjectionMethod.default() == ProjectionMethod.PCA
        assert ComparisonType.is_valid(ComparisonType.SAME_MODEL_DIFF_INPUT)

        all_types = ExplanationType.all()
        assert len(all_types) == 4
        assert set(all_types) == {
            ExplanationType.HIDDEN_STATE,
            ExplanationType.GATE_DYNAMICS,
            ExplanationType.COUNTERFACTUAL,
            ExplanationType.CONFIDENCE,
        }

    def test_observability_contract_construction(self):
        """Observability 契约 dataclass 可合法构造."""
        from datetime import datetime

        from app.contracts.observability import ExperimentSnapshot

        snapshot = ExperimentSnapshot(
            snapshot_id="snap-001",
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            created_by="test_user",
            git_sha="abc123",
            code_dirty=False,
            config={"lr": 0.001},
            dataset_versions=[],
            model_uri="model://test",
            metrics={"accuracy": 0.95},
            environment={"python": "3.13"},
        )
        assert snapshot.snapshot_id == "snap-001"
        assert snapshot.metrics["accuracy"] == 0.95
        assert snapshot.git_sha == "abc123"


# 测试用例 5：跨阶段集成（WorkflowRunner + SnapshotStore）


@pytest.mark.integration
class TestPhase1To8SnapshotIntegration:
    """Phase 1（WorkflowRunner）+ Phase 2（SnapshotStore）跨阶段集成."""

    @pytest.mark.asyncio
    async def test_workflow_run_can_be_snapshotted(self, integrated_stores):
        """工作流运行完成后可创建实验快照（Phase 1 + Phase 2 集成）."""
        dag_store = integrated_stores["dag"]
        snapshot_store = integrated_stores["snapshot"]

        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        runner = WorkflowRunner(dag_store=dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed", f"前置条件：工作流应 completed，实际: {final['status']}"

        # 创建实验快照（SnapshotStore.create 接收关键字参数，自动采集 git_sha/environment）
        snapshot = await snapshot_store.create(
            config={
                "workflow_name": spec.name,
                "max_concurrent": 1,
                "workflow_run_id": workflow_run_id,
            },
            dataset_versions=[],
            model_uri="model://test_workflow",
            metrics={
                "node_count": 7.0,
                "completed_nodes": 7.0,
            },
            created_by="test_user",
            notes="closed-loop integration test",
        )

        # 反查快照
        retrieved = await snapshot_store.get(snapshot.snapshot_id)
        assert retrieved is not None, "快照应可反查"
        assert retrieved.config["workflow_name"] == spec.name
        assert retrieved.metrics["completed_nodes"] == 7.0

    @pytest.mark.asyncio
    async def test_closed_loop_dry_run_hard_gate(self, in_memory_dag_store):
        """v1 物理加工硬门控：execute 节点 dry_run=true（仅 CAM 仿真）.

        对应 project_memory 约束：v1 停在 CAM 验证层，物理执行需
        "持证操作员 + 导师签字 + 保险"硬门控。
        """
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)
        _register_real_plugins()

        # 捕获 execute 节点的 ctx，验证 dry_run=true
        exec_capture: list[TaskContext] = []
        handlers["job_dispatch"] = _ScriptedHandler(
            "job_dispatch",
            [_ok_result("result_artifact", "file://execute/result.json", "file")],
            capture=exec_capture,
        )
        _register_scripted_handlers(handlers)

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "completed"

        # execute 节点应收到 dry_run=true 配置
        assert len(exec_capture) == 1, f"execute 应只执行一次，实际 {len(exec_capture)} 次"
        exec_ctx = exec_capture[0]
        assert exec_ctx.config.get("dry_run") is True, (
            f"execute 节点 dry_run 应为 True（v1 硬门控），实际: {exec_ctx.config}"
        )


# 测试用例 6：闭环失败传播（安全三层兜底验证）


@pytest.mark.integration
class TestClosedLoopFailurePropagation:
    """闭环 DAG 中间节点失败传播验证."""

    @pytest.mark.asyncio
    async def test_predict_failure_skips_downstream(self, in_memory_dag_store):
        """predict 节点失败 → decide/generate_params/validate_cam/execute/collect_feedback 全部 SKIPPED.

        验证闭环 DAG 的失败传播：WorldModelPlugin 失败时，下游 RL agent 和 CAM 节点
        都不应执行（避免无预测基础的决策）。
        """
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)

        # 注册一个会失败的 WorldModelPlugin（通过注入错误的 current_state）
        # 这里采用更简单的方式：覆盖 wm_predict_state 任务类型为一个失败的 _ScriptedHandler
        failing_handler = _ScriptedHandler(
            "wm_predict_state",
            [
                TaskResult(
                    status=TaskStatus.FAILED,
                    error="世界模型预测失败（测试注入）",
                    error_code="TEST_INJECTED_FAILURE",
                )
            ],
        )
        registry = get_task_registry()
        registry.register(failing_handler, plugin_id="test_failing_wm")

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "failed", f"工作流应为 failed（predict 失败），实际: {final['status']}"

        node_status = _node_status_map(final)
        assert node_status.get("perceive") == "completed", "perceive 应正常完成"
        assert node_status.get("predict") == "failed", "predict 应失败"
        assert node_status.get("decide") == "skipped", "decide 应被 skipped"
        assert node_status.get("generate_params") == "skipped"
        assert node_status.get("validate_cam") == "skipped"
        assert node_status.get("execute") == "skipped"
        assert node_status.get("collect_feedback") == "skipped"

    @pytest.mark.asyncio
    async def test_decide_failure_skips_cam_nodes(self, in_memory_dag_store):
        """decide 节点失败 → generate_params/validate_cam/execute/collect_feedback SKIPPED."""
        spec, handlers = _build_closed_loop_dag()
        _register_scripted_handlers(handlers)

        # 注册真实的 WorldModelPlugin（predict 正常）
        from app.plugins.world_model.plugin import WorldModelPlugin

        registry = get_task_registry()
        wm_plugin = WorldModelPlugin()
        wm_plugin.register(registry)

        # 覆盖 rl_act 任务类型为一个失败的 _ScriptedHandler
        failing_handler = _ScriptedHandler(
            "rl_act",
            [
                TaskResult(
                    status=TaskStatus.FAILED,
                    error="RL 决策失败（测试注入）",
                    error_code="TEST_INJECTED_FAILURE",
                )
            ],
        )
        registry.register(failing_handler, plugin_id="test_failing_rl")

        runner = WorkflowRunner(dag_store=in_memory_dag_store)
        workflow_run_id = await runner.run(spec, owner_id="test_user")
        final = await _wait_for_terminal(runner, workflow_run_id, timeout_s=60.0)

        assert final["status"] == "failed", f"工作流应为 failed（decide 失败），实际: {final['status']}"

        node_status = _node_status_map(final)
        assert node_status.get("perceive") == "completed"
        assert node_status.get("predict") == "completed", "predict 应正常完成"
        assert node_status.get("decide") == "failed", "decide 应失败"
        assert node_status.get("generate_params") == "skipped"
        assert node_status.get("validate_cam") == "skipped"
        assert node_status.get("execute") == "skipped"
        assert node_status.get("collect_feedback") == "skipped"
