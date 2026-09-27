"""任务契约：定义任务、工作流、任务模板的统一接口.

对应 ADR-005 第 3 章。本文件只定义接口与数据结构，不包含实现。
现有 AsyncTaskManager 通过 app/tasks/contract_adapter.py 适配此契约。

契约稳定性：Stable（v1.0.0），向后兼容扩展。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol
from collections.abc import AsyncIterator


class TaskStatus(str, Enum):
    """任务状态枚举。

    状态机合法转换：
        PENDING  → QUEUED, RUNNING, CANCELLED
        QUEUED   → RUNNING, CANCELLED
        RUNNING  → COMPLETED, FAILED, CANCELLED
        COMPLETED → （终态）
        FAILED    → （终态，可由工作流层 retry）
        CANCELLED → （终态）
        SKIPPED   → （终态，工作流中前置失败时由编排器标记）
    """

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # 工作流中前置失败时的跳过状态


class TaskType(str, Enum):
    """任务类型枚举（从 tasks/task_manager.py 提升到 contracts 层以避免循环依赖）。"""

    LNN_TRAINING = "lnn_training"
    LNN_INFERENCE = "lnn_inference"
    LNN_BATCH_INFERENCE = "lnn_batch_inference"
    DATA_PROCESSING = "data_processing"
    MODEL_EXPORT = "model_export"
    MODEL_QUANTIZATION = "model_quantization"
    UNKNOWN = "unknown"


# 合法状态转换矩阵（与现有 AsyncTaskManager.VALID_STATUS_TRANSITIONS 对齐，
# 额外补充 SKIPPED 作为工作流编排器使用的终态）
VALID_STATUS_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.SKIPPED: set(),
}


def is_valid_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """检查状态转换是否合法。"""
    if from_status == to_status:
        return True  # 幂等写允许
    return to_status in VALID_STATUS_TRANSITIONS.get(from_status, set())


class TaskPriority(int, Enum):
    """任务优先级（数值越大优先级越高）。"""

    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


@dataclass
class Artifact:
    """任务输入输出产物契约.

    type 取值约定：
        - "dataset": 数据集版本，URI 形如 "dataset://my-ds/v3"
        - "model": 模型，URI 形如 "model://ltc-v1"
        - "report": 报告文件，URI 形如 "file://reports/xxx.pdf"
        - "metrics": 指标集合，URI 形如 "metrics://job-xxx"
        - "file": 通用文件，URI 形如 "file://path/to/file"
    """

    name: str
    type: str
    uri: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Artifact.name 不能为空")
        if self.type not in {"dataset", "model", "report", "metrics", "file"}:
            raise ValueError(f"Artifact.type 不合法: {self.type}")
        if not self.uri:
            raise ValueError("Artifact.uri 不能为空")


@dataclass
class TaskContext:
    """任务运行时上下文，由编排器注入。"""

    job_id: str
    workflow_run_id: str | None = None
    inputs: dict[str, Artifact] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    deadline_ts: float | None = None  # Unix ts，超时自动 CANCELLED


@dataclass
class TaskResult:
    """任务执行结果契约。"""

    status: TaskStatus
    outputs: dict[str, Artifact] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None


@dataclass
class TaskProgress:
    """任务进度事件契约（SSE 推送）。"""

    job_id: str
    status: TaskStatus
    progress: float  # 0.0 .. 1.0
    message: str | None = None
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError(f"TaskProgress.progress 必须在 [0,1]，当前: {self.progress}")


class TaskHandler(Protocol):
    """任务处理器协议.

    插件通过实现此协议注册任务类型。实现可以是普通类或函数式对象，
    无需继承（结构化子类型）。
    """

    def name(self) -> str: ...
    def description(self) -> str: ...
    def input_schema(self) -> dict[str, Any]: ...
    def output_schema(self) -> dict[str, Any]: ...
    async def execute(self, ctx: TaskContext) -> TaskResult: ...


class ITaskRegistry(ABC):
    """任务类型注册表契约.

    插件通过此注册表声明自己提供的任务类型。注册表实例由核心层维护，
    在插件 on_load 时调用 register()。
    """

    @abstractmethod
    def register(self, handler: TaskHandler, *, plugin_id: str) -> None:
        """注册任务处理器。重复注册同一 task_type 由实现决定（默认覆盖+告警）。"""

    @abstractmethod
    def get(self, task_type: str) -> TaskHandler:
        """获取任务处理器。未注册时抛出 KeyError。"""

    @abstractmethod
    def list(self) -> list[dict[str, Any]]:
        """列出所有已注册任务类型的元信息（name/description/schemas/plugin_id）。"""


class ITaskExecutor(ABC):
    """单任务执行器契约.

    现有 AsyncTaskManager 通过 contract_adapter 适配此接口。
    本接口不包含 Workflow 编排能力，DAG 编排见 IWorkflowRunner。
    """

    @abstractmethod
    async def submit(
        self,
        task_type: str,
        params: dict[str, Any],
        *,
        owner_id: str | None = None,
        idempotency_key: str | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout_seconds: int = 3600,
    ) -> str:
        """提交任务，返回 job_id。"""

    @abstractmethod
    async def get(self, job_id: str) -> TaskResult:
        """获取任务结果（已完成/失败/取消）或当前快照。"""

    @abstractmethod
    async def cancel(self, job_id: str) -> bool:
        """取消任务。返回 True 表示已成功发出取消信号（不保证立即终止）。"""

    @abstractmethod
    def subscribe(self, job_id: str) -> AsyncIterator[TaskProgress]:
        """订阅任务进度事件流（SSE/Stream）。"""


# Workflow DAG 契约


@dataclass
class WorkflowNode:
    """DAG 节点：一个任务实例。"""

    node_id: str
    task_type: str
    params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)
    # inputs 形如 {"input_name": "${upstream_node_id.output_name}"}
    retry: int = 0
    timeout_seconds: int = 3600

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("WorkflowNode.node_id 不能为空")
        if not self.task_type:
            raise ValueError("WorkflowNode.task_type 不能为空")
        if self.retry < 0:
            raise ValueError(f"WorkflowNode.retry 不能为负数: {self.retry}")
        if self.timeout_seconds <= 0:
            raise ValueError(f"WorkflowNode.timeout_seconds 必须为正数: {self.timeout_seconds}")


@dataclass
class WorkflowEdge:
    """DAG 边：依赖关系（upstream 必须先完成，downstream 才能开始）。"""

    upstream: str  # node_id
    downstream: str  # node_id

    def __post_init__(self) -> None:
        if self.upstream == self.downstream:
            raise ValueError(f"WorkflowEdge 不能形成自环: upstream==downstream=={self.upstream}")


@dataclass
class WorkflowSpec:
    """工作流规格契约（可序列化为 YAML 模板）。"""

    name: str
    version: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    inputs: dict[str, Artifact] = field(default_factory=dict)  # 工作流级输入
    outputs: dict[str, str] = field(default_factory=dict)  # 形如 {"out": "${node_id.out}"}
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("WorkflowSpec.name 不能为空")
        if not self.nodes:
            raise ValueError("WorkflowSpec.nodes 不能为空")

    def validate(self) -> list[str]:
        """校验 DAG 无环、节点引用合法、输入输出匹配.

        Returns:
            错误信息列表。空列表表示校验通过。
        """
        errors: list[str] = []

        # 1. 节点 id 唯一性
        node_ids = [n.node_id for n in self.nodes]
        seen: set[str] = set()
        for nid in node_ids:
            if nid in seen:
                errors.append(f"节点 id 重复: {nid}")
            seen.add(nid)

        node_id_set = set(node_ids)

        # 2. 边引用合法性
        for edge in self.edges:
            if edge.upstream not in node_id_set:
                errors.append(f"边引用了不存在的上游节点: {edge.upstream}")
            if edge.downstream not in node_id_set:
                errors.append(f"边引用了不存在的下游节点: {edge.downstream}")

        # 3. DAG 无环检测（Kahn 拓扑排序）
        if not errors:
            cycle = _detect_cycle(self.nodes, self.edges)
            if cycle:
                errors.append(f"工作流存在环: {' -> '.join(cycle)}")

        # 4. 节点 inputs 引用合法性（形如 ${node_id.output_name} 或 ${workflow_input_name}）
        workflow_input_names = set(self.inputs.keys()) if self.inputs else set()
        for node in self.nodes:
            for input_name, ref in node.inputs.items():
                ref_err = _validate_artifact_ref(
                    ref,
                    node_id_set,
                    node.node_id,
                    input_name,
                    workflow_input_names=workflow_input_names,
                )
                if ref_err:
                    errors.append(ref_err)

        # 5. 工作流 outputs 引用合法性
        for out_name, ref in self.outputs.items():
            ref_err = _validate_artifact_ref(
                ref,
                node_id_set,
                None,
                out_name,
                workflow_input_names=workflow_input_names,
            )
            if ref_err:
                errors.append(ref_err)

        return errors


def _validate_artifact_ref(
    ref: str,
    valid_node_ids: set[str],
    current_node_id: str | None,
    field_name: str,
    workflow_input_names: set[str] | None = None,
) -> str | None:
    """校验 artifact 引用合法性.

    支持两种引用形式:
    1. ``${node_id.output_name}`` —— 引用其他节点的输出（含 ``.`` 分隔符）
    2. ``${workflow_input_name}`` —— 引用工作流级 ``inputs`` 中定义的输入
       （无 ``.`` 分隔符，inner 必须命中 ``workflow_input_names``）

    Args:
        workflow_input_names: 工作流级输入名称集合；为空集合或 None 时
            表示该工作流未声明工作流级输入，此时无 ``.`` 的引用将报错。
    """
    if not ref.startswith("${") or not ref.endswith("}"):
        return f"artifact 引用格式错误（应为 ${{node_id.output_name}} 或 ${{workflow_input_name}}）: {field_name}={ref}"
    inner = ref[2:-1]
    if "." not in inner:
        # 无 "." 分隔符：视为工作流级输入引用，必须命中 workflow_input_names
        if workflow_input_names and inner in workflow_input_names:
            return None
        return (
            f"artifact 引用缺少 . 分隔符且不是工作流级输入: {field_name}={ref} (未在 workflow.inputs 中声明 '{inner}')"
        )
    ref_node_id, _ = inner.split(".", 1)
    if ref_node_id not in valid_node_ids:
        return f"artifact 引用了不存在的节点: {field_name}={ref} (节点 {ref_node_id} 未定义)"
    return None


def _detect_cycle(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[str]:
    """使用 Kahn 拓扑排序检测环。返回环上的节点序列（空列表表示无环）。"""
    in_degree: dict[str, int] = {n.node_id: 0 for n in nodes}
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adj[edge.upstream].append(edge.downstream)
        in_degree[edge.downstream] += 1

    queue: deque[str] = deque([nid for nid, deg in in_degree.items() if deg == 0])
    visited = 0
    while queue:
        nid = queue.popleft()
        visited += 1
        for next_nid in adj[nid]:
            in_degree[next_nid] -= 1
            if in_degree[next_nid] == 0:
                queue.append(next_nid)

    if visited == len(nodes):
        return []  # 无环

    # 存在环：找出环上的节点（in_degree > 0 的节点构成的子图中找环）
    remaining = {nid for nid, deg in in_degree.items() if deg > 0}
    # 在 remaining 子图中 DFS 找一条环路径
    sub_adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.upstream in remaining and edge.downstream in remaining:
            sub_adj[edge.upstream].append(edge.downstream)

    visited_dfs: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def dfs(node: str) -> list[str] | None:
        if node in path_set:
            idx = path.index(node)
            return path[idx:] + [node]
        if node in visited_dfs:
            return None
        visited_dfs.add(node)
        path.append(node)
        path_set.add(node)
        for nxt in sub_adj[node]:
            result = dfs(nxt)
            if result:
                return result
        path.pop()
        path_set.discard(node)
        return None

    for start in remaining:
        if start not in visited_dfs:
            cycle = dfs(start)
            if cycle:
                return cycle
    return list(remaining)  # 兜底：返回剩余节点


@dataclass
class WorkflowEvent:
    """工作流事件契约（SSE 推送）。"""

    workflow_run_id: str
    event_type: str  # node_started / node_completed / node_failed / workflow_completed
    node_id: str | None = None
    payload: dict[str, Any] | None = None  # TaskResult 或 TaskProgress 的序列化形式
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        valid_types = {
            "node_started",
            "node_completed",
            "node_failed",
            "node_skipped",
            "workflow_completed",
            "workflow_failed",
            "workflow_cancelled",
        }
        if self.event_type not in valid_types:
            raise ValueError(f"WorkflowEvent.event_type 不合法: {self.event_type}，合法值: {valid_types}")


class IWorkflowRunner(ABC):
    """工作流执行器契约.

    实现见 app/workflow/runner.py（阶段 1 交付）。基于 networkx DAG，
    支持并行/串行/断点续跑。
    """

    @abstractmethod
    async def run(
        self,
        spec: WorkflowSpec,
        *,
        inputs: dict[str, Artifact] | None = None,
        resume_from: str | None = None,  # workflow_run_id，断点续跑
        owner_id: str | None = None,
    ) -> str:
        """启动工作流，返回 workflow_run_id。"""

    @abstractmethod
    async def get_status(self, workflow_run_id: str) -> dict[str, Any]:
        """获取工作流运行状态（含各节点状态）。"""

    @abstractmethod
    async def cancel(self, workflow_run_id: str) -> bool:
        """取消工作流。下游未启动的节点标记为 SKIPPED。"""

    @abstractmethod
    def subscribe(self, workflow_run_id: str) -> AsyncIterator[WorkflowEvent]:
        """订阅工作流事件流。"""
