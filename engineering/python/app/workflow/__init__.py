"""工作流编排引擎（Workflow Orchestration Engine）.

对应 ADR-005 阶段 1：基于 networkx DAG 的任务编排，支持并行/串行/断点续跑。

模块组成：
    - validator: DAG 校验（复用 WorkflowSpec.validate() + 扩展检查）
    - dag_store: 工作流运行状态持久化（workflow_runs / workflow_run_nodes 表）
    - runner: WorkflowRunner，实现 IWorkflowRunner 契约

设计要点：
    1. Runner 不直接调用 AsyncTaskManager，而是通过 ITaskExecutor 契约解耦
    2. 节点失败时下游递归标记为 SKIPPED（契约层 TaskStatus.SKIPPED）
    3. 断点续跑：从 dag_store 加载已完成节点，仅重跑 PENDING/FAILED 节点
    4. 事件流：SSE 推送 WorkflowEvent，前端实时可视化 DAG 节点状态
"""

from app.workflow.dag_store import DAGStore, get_dag_store
from app.workflow.runner import WorkflowRunner, get_workflow_runner
from app.workflow.validator import validate_workflow_spec, WorkflowValidationError

__all__ = [
    "DAGStore",
    "get_dag_store",
    "WorkflowRunner",
    "get_workflow_runner",
    "validate_workflow_spec",
    "WorkflowValidationError",
]
