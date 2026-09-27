"""WorkflowRunner：IWorkflowRunner 契约实现.

基于 networkx DAG 实现工作流编排，支持：
    - 并行/串行节点调度（依据 DAG 拓扑序）
    - 节点失败时下游递归标记 SKIPPED
    - 断点续跑：从 DAGStore 加载已完成节点，仅重跑未完成节点
    - 取消传播：取消信号下发后，未启动节点标记 SKIPPED
    - 事件流：通过 ``subscribe`` 推送 WorkflowEvent（SSE 可消费）

设计要点（参见 ADR-005 第 3.3 节 / core-contracts-design.md 第 5 章）：

1. **节点执行不通过 ITaskExecutor**：工作流编排器直接调用 TaskHandler.execute，
   避免与 AsyncTaskManager 的全局信号量产生耦合（工作流有自己的并发控制）。
   每个节点的 job_id 仅作为日志追踪标识，不进入 AsyncTaskManager 队列。

2. **artifact 引用解析**：节点 ``inputs`` 字段形如
   ``{"input_name": "${upstream_node_id.output_name}"}``，
   runner 在节点启动前从 DAGStore.get_completed_node_outputs() 解析。

3. **断点续跑**：``run(spec, resume_from=workflow_run_id)`` 时，
   先加载该 run_id 的节点状态，COMPLETED 节点跳过，FAILED/PENDING 节点重跑。

4. **事件广播**：每个 run_id 维护一个 ``asyncio.Queue`` 列表，
   ``subscribe`` 注册新 Queue，``_emit`` 向所有 Queue 推送 WorkflowEvent 序列化文本。

5. **并发控制**：runner 内置 ``asyncio.Semaphore``，默认 ``max_concurrent=4``，
   避免单个工作流耗尽系统资源。可配置于 ``WorkflowSpec.metadata["max_concurrent"]``。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any
from collections.abc import AsyncIterator

try:
    import networkx as nx
except ImportError:  # pragma: no cover
    nx = None

from app.contracts.task import (
    Artifact,
    IWorkflowRunner,
    TaskContext,
    TaskResult,
    TaskStatus,
    WorkflowEvent,
    WorkflowSpec,
)
from app.tasks.registry import get_task_registry
from app.workflow.dag_store import DAGStore, get_dag_store
from app.workflow.validator import validate_or_raise

logger = logging.getLogger(__name__)


# 默认配置常量

DEFAULT_MAX_CONCURRENT_NODES: int = 4
DEFAULT_NODE_TIMEOUT_SECONDS: int = 3600
# 订阅 Queue 容量上限：超过此容量则丢弃最旧事件（背压保护）
SUBSCRIBER_QUEUE_MAXSIZE: int = 256
# 订阅心跳超时：Queue.get 等待超时后发心跳事件，避免连接静默断开
SUBSCRIBER_HEARTBEAT_TIMEOUT_SEC: float = 30.0


# 辅助函数


def _serialize(obj: Any) -> Any:
    """将 dataclass / enum / 自定义对象递归转为 JSON 可序列化结构。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, Artifact):
        return {
            "name": obj.name,
            "type": obj.type,
            "uri": obj.uri,
            "metadata": obj.metadata,
        }
    if isinstance(obj, TaskStatus):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


def _spec_to_dict(spec: WorkflowSpec) -> dict[str, Any]:
    """WorkflowSpec → 可持久化的 dict。"""
    return {
        "name": spec.name,
        "version": spec.version,
        "nodes": [
            {
                "node_id": n.node_id,
                "task_type": n.task_type,
                "params": n.params,
                "inputs": n.inputs,
                "retry": n.retry,
                "timeout_seconds": n.timeout_seconds,
            }
            for n in spec.nodes
        ],
        "edges": [{"upstream": e.upstream, "downstream": e.downstream} for e in spec.edges],
        "inputs": {k: _serialize(v) for k, v in spec.inputs.items()},
        "outputs": dict(spec.outputs),
        "metadata": dict(spec.metadata),
    }


def _resolve_artifact_ref(ref: str, completed_outputs: dict[str, dict[str, Any]]) -> Artifact | None:
    """解析 ``${node_id.output_name}`` 引用为 Artifact 实例。

    Args:
        ref: 形如 ``${node_x.out_metric}`` 的引用字符串。
        completed_outputs: ``{node_id: {output_name: artifact_dict}}``。

    Returns:
        Artifact 实例；解析失败返回 None。
    """
    if not ref.startswith("${") or not ref.endswith("}"):
        return None
    inner = ref[2:-1]
    if "." not in inner:
        return None
    ref_node_id, output_name = inner.split(".", 1)
    node_outputs = completed_outputs.get(ref_node_id) or {}
    art_dict = node_outputs.get(output_name)
    if art_dict is None:
        return None
    try:
        if isinstance(art_dict, Artifact):
            return art_dict
        return Artifact(**art_dict)
    except (TypeError, ValueError) as e:
        logger.warning("解析 artifact 引用 %s 失败: %s", ref, e)
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# WorkflowRunner 实现


class WorkflowRunner(IWorkflowRunner):
    """工作流执行器，实现 IWorkflowRunner 契约。

    单例：通过 :func:`get_workflow_runner` 获取。
    """

    def __init__(
        self,
        dag_store: DAGStore | None = None,
        registry: Any | None = None,
    ) -> None:
        self._store: DAGStore = dag_store or get_dag_store()
        self._registry = registry
        # 每个 workflow_run_id list[asyncio.Queue]（订阅者列表）
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # 每个 workflow_run_id asyncio.Event（取消信号）
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        # [H7] 保存 create_task 引用防止 GC 回收导致工作流被静默取消
        self._pending_exec_tasks: set = set()

    # 公共属性

    @property
    def registry(self):
        if self._registry is None:
            self._registry = get_task_registry()
        return self._registry

    # IWorkflowRunner 实现

    async def run(
        self,
        spec: WorkflowSpec,
        *,
        inputs: dict[str, Artifact] | None = None,
        resume_from: str | None = None,
        owner_id: str | None = None,
    ) -> str:
        """启动工作流，返回 workflow_run_id。

        Args:
            spec: 工作流规格。
            inputs: 工作流级输入 artifact（覆盖 spec.inputs）。
            resume_from: 若提供，从该 workflow_run_id 断点续跑
                （spec 必须与原 run 一致；runner 仅校验 name+version）。
            owner_id: 任务所有者（用于权限/审计）。
        """
        # 1. 校验 DAG
        validate_or_raise(spec)

        # 2. 断点续跑：复用原 run_id；否则创建新 run
        if resume_from:
            existing = await self._store.get_run(resume_from)
            if existing is None:
                raise ValueError(f"resume_from 指定的工作流不存在: {resume_from}")
            if existing.get("name") != spec.name or existing.get("version") != spec.version:
                raise ValueError(
                    f"resume_from 的 spec 不匹配: "
                    f"expected name={spec.name} version={spec.version}, "
                    f"got name={existing.get('name')} version={existing.get('version')}"
                )
            workflow_run_id = resume_from
            # 重置运行状态为 running（否则 _wait_for_terminal 会立即命中
            # 上次运行遗留的 failed/completed 终态，导致续跑任务未被等待）
            await self._store.update_run_status(
                workflow_run_id,
                "running",
                error=None,
                started_at=datetime.now(timezone.utc),
            )
            # 上次运行遗留的 skipped 节点重置为 pending（允许重跑；
            # 否则 _execute_node 的竞态防护会拦截历史 skipped，导致 D/E 永不执行）
            await self._store.reset_skipped_nodes(workflow_run_id)
            logger.info("工作流断点续跑: %s", workflow_run_id)
        else:
            spec_dict = _spec_to_dict(spec)
            inputs_serialized = {k: _serialize(v) for k, v in (inputs or spec.inputs).items()}
            workflow_run_id = await self._store.create_run(
                spec_dict,
                name=spec.name,
                version=spec.version,
                inputs=inputs_serialized,
                owner_id=owner_id,
                metadata=dict(spec.metadata),
            )
            # 初始化节点状态
            node_infos = [
                {
                    "node_id": n.node_id,
                    "task_type": n.task_type,
                    "params": n.params,
                }
                for n in spec.nodes
            ]
            await self._store.init_node_states(workflow_run_id, node_infos)
            await self._store.update_run_status(
                workflow_run_id,
                "running",
                started_at=datetime.now(timezone.utc),
            )
            logger.info("工作流已创建: %s (name=%s version=%s)", workflow_run_id, spec.name, spec.version)

        # 3. 注册取消信号
        async with self._lock:
            self._cancel_events[workflow_run_id] = asyncio.Event()

        # 4. 异步调度执行（不阻塞 run）
        # [H7] 保存任务引用到 set，防止 asyncio.create_task 弱引用被 GC 回收
        # 导致工作流执行被静默取消且无异常日志。
        exec_task = asyncio.create_task(self._execute_workflow(workflow_run_id, spec, inputs or spec.inputs))
        self._pending_exec_tasks.add(exec_task)
        exec_task.add_done_callback(self._pending_exec_tasks.discard)

        return workflow_run_id

    async def get_status(self, workflow_run_id: str) -> dict[str, Any]:
        """获取工作流运行状态（含各节点状态）。"""
        run_dict = await self._store.get_run(workflow_run_id)
        if run_dict is None:
            return {"error": "workflow_run_id 不存在", "workflow_run_id": workflow_run_id}
        return run_dict

    async def cancel(self, workflow_run_id: str) -> bool:
        """取消工作流。下游未启动的节点标记为 SKIPPED。"""
        async with self._lock:
            evt = self._cancel_events.setdefault(workflow_run_id, asyncio.Event())
        evt.set()

        # 标记所有 pending 节点为 skipped
        nodes = await self._store.get_node_states(workflow_run_id)
        for node in nodes:
            if node.get("status") == "pending":
                await self._store.update_node_state(
                    workflow_run_id,
                    node["node_id"],
                    status="skipped",
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="node_skipped",
                        node_id=node["node_id"],
                        payload={"reason": "workflow_cancelled"},
                        timestamp=time.time(),
                    ),
                )

        await self._store.update_run_status(
            workflow_run_id,
            "cancelled",
            completed_at=datetime.now(timezone.utc),
        )
        await self._emit(
            workflow_run_id,
            WorkflowEvent(
                workflow_run_id=workflow_run_id,
                event_type="workflow_cancelled",
                timestamp=time.time(),
            ),
        )
        logger.info("工作流已取消: %s", workflow_run_id)
        return True

    def subscribe(self, workflow_run_id: str) -> AsyncIterator[WorkflowEvent]:
        """订阅工作流事件流。

        返回异步生成器，每次 yield 一个 :class:`WorkflowEvent` 实例。
        终止条件：遇到 ``workflow_completed`` / ``workflow_failed`` / ``workflow_cancelled``。
        """
        return self._subscribe_generator(workflow_run_id)

    async def _subscribe_generator(self, workflow_run_id: str) -> AsyncIterator[WorkflowEvent]:
        """订阅生成器实现（独立方法以便使用 yield）。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.setdefault(workflow_run_id, []).append(queue)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SUBSCRIBER_HEARTBEAT_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    # 心跳：发布一个 None 事件让 SSE 保持连接
                    # 这里简单 continue；上层 SSE 路由可独立实现心跳
                    continue

                if event is None:
                    # 哨兵值：表示工作流已终结且事件已发完
                    return
                yield event
                if event.event_type in {
                    "workflow_completed",
                    "workflow_failed",
                    "workflow_cancelled",
                }:
                    return
        finally:
            async with self._lock:
                subs = self._subscribers.get(workflow_run_id, [])
                if queue in subs:
                    subs.remove(queue)

    # 内部：工作流执行主循环

    async def _execute_workflow(
        self,
        workflow_run_id: str,
        spec: WorkflowSpec,
        workflow_inputs: dict[str, Artifact],
    ) -> None:
        """工作流执行主循环。

        算法：
            1. 构建 networkx.DiGraph
            2. 加载已完成节点（断点续跑场景）
            3. 拓扑序逐节点调度：每轮收集所有入度=0 的节点，并行执行
            4. 节点失败：下游递归标记 SKIPPED，工作流标记 failed
            5. 全部完成：标记 workflow_completed，解析 spec.outputs
        """
        cancel_evt = self._cancel_events.get(workflow_run_id)
        if cancel_evt is None:
            cancel_evt = asyncio.Event()
            self._cancel_events[workflow_run_id] = cancel_evt

        max_concurrent = int(spec.metadata.get("max_concurrent", DEFAULT_MAX_CONCURRENT_NODES))
        semaphore = asyncio.Semaphore(max_concurrent)

        # 构建 DAG
        graph = self._build_graph(spec)

        # 加载已完成节点（断点续跑）
        completed_nodes = await self._load_completed_nodes(workflow_run_id)

        # 工作流级 inputs 注入到所有节点（作为 fallback）
        workflow_inputs_serialized = {k: _serialize(v) for k, v in workflow_inputs.items()}

        try:
            await self._schedule_nodes(
                workflow_run_id=workflow_run_id,
                spec=spec,
                graph=graph,
                completed_nodes=completed_nodes,
                workflow_inputs=workflow_inputs_serialized,
                semaphore=semaphore,
                cancel_evt=cancel_evt,
            )

            # 检查最终状态
            final_nodes = await self._store.get_node_states(workflow_run_id)
            has_failed = any(n.get("status") == "failed" for n in final_nodes)
            # 取消：cancel_evt 已设置，或存在无 error 的 skipped（取消信号标记）
            has_cancelled = any(n.get("status") == "skipped" and not n.get("error") for n in final_nodes)
            # 失败传播：skipped 且带 error（_propagate_skip 写入"上游节点 X 失败"）
            has_propagated_failure = any(n.get("status") == "skipped" and n.get("error") for n in final_nodes)

            if cancel_evt.is_set() or has_cancelled:
                await self._store.update_run_status(
                    workflow_run_id,
                    "cancelled",
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="workflow_cancelled",
                        timestamp=time.time(),
                    ),
                )
            elif has_failed or has_propagated_failure:
                await self._store.update_run_status(
                    workflow_run_id,
                    "failed",
                    error="一个或多个节点失败",
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="workflow_failed",
                        payload={"reason": "node_failed"},
                        timestamp=time.time(),
                    ),
                )
            else:
                # 解析工作流 outputs
                final_outputs = await self._resolve_workflow_outputs(workflow_run_id, spec)
                await self._store.update_run_status(
                    workflow_run_id,
                    "completed",
                    outputs=final_outputs,
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="workflow_completed",
                        payload={"outputs": final_outputs},
                        timestamp=time.time(),
                    ),
                )
        except Exception as e:
            logger.error("工作流 %s 执行异常: %s", workflow_run_id, e, exc_info=True)
            await self._store.update_run_status(
                workflow_run_id,
                "failed",
                error=str(e)[:2048],
                completed_at=datetime.now(timezone.utc),
            )
            await self._emit(
                workflow_run_id,
                WorkflowEvent(
                    workflow_run_id=workflow_run_id,
                    event_type="workflow_failed",
                    payload={"reason": "exception", "error": str(e)},
                    timestamp=time.time(),
                ),
            )
        finally:
            # 通知所有订阅者工作流已终结
            await self._notify_termination(workflow_run_id)

    def _build_graph(self, spec: WorkflowSpec):
        """构建 networkx.DiGraph。"""
        if nx is None:
            raise RuntimeError("networkx 未安装，无法构建 DAG")
        g = nx.DiGraph()
        for node in spec.nodes:
            g.add_node(node.node_id, spec=node)
        for edge in spec.edges:
            # 同一对节点可能有重复边，networkx 会自动去重
            g.add_edge(edge.upstream, edge.downstream)
        return g

    async def _load_completed_nodes(self, workflow_run_id: str) -> set[str]:
        """加载已完成节点（用于断点续跑跳过）。"""
        nodes = await self._store.get_node_states(workflow_run_id)
        return {n["node_id"] for n in nodes if n.get("status") == "completed"}

    async def _schedule_nodes(
        self,
        *,
        workflow_run_id: str,
        spec: WorkflowSpec,
        graph,
        completed_nodes: set[str],
        workflow_inputs: dict[str, Any],
        semaphore: asyncio.Semaphore,
        cancel_evt: asyncio.Event,
    ) -> None:
        """按拓扑序调度节点执行。

        每轮：
            1. 找出所有入度=0 且未完成的节点
            2. 并行执行（受 semaphore 限流）
            3. 节点完成后从图中移除，更新下游入度
            4. 失败节点的下游递归标记 SKIPPED
        """
        node_specs = {n.node_id: n for n in spec.nodes}
        # 复制图，避免修改原图
        working_graph = graph.copy()

        # 已完成节点先从图中移除
        for nid in completed_nodes:
            if nid in working_graph:
                working_graph.remove_node(nid)

        # 待执行节点的 futures
        pending_futures: dict[str, asyncio.Task] = {}

        while working_graph.number_of_nodes() > 0:
            if cancel_evt.is_set():
                # 取消信号：剩余节点全部标记 skipped
                for nid in list(working_graph.nodes()):
                    await self._store.update_node_state(
                        workflow_run_id,
                        nid,
                        status="skipped",
                        completed_at=datetime.now(timezone.utc),
                    )
                    await self._emit(
                        workflow_run_id,
                        WorkflowEvent(
                            workflow_run_id=workflow_run_id,
                            event_type="node_skipped",
                            node_id=nid,
                            payload={"reason": "workflow_cancelled"},
                            timestamp=time.time(),
                        ),
                    )
                return

            # 找出所有入度=0 的节点
            ready_nodes = [
                nid for nid in working_graph.nodes() if working_graph.in_degree(nid) == 0 and nid not in pending_futures
            ]

            if not ready_nodes and not pending_futures:
                # 没有就绪节点也没有正在执行的节点：可能存在死锁（环）
                logger.error("工作流 %s 调度死锁：剩余节点 %s", workflow_run_id, list(working_graph.nodes()))
                break

            # 启动就绪节点（并行）
            for nid in ready_nodes:
                node_spec = node_specs[nid]
                future = asyncio.create_task(
                    self._execute_node(
                        workflow_run_id=workflow_run_id,
                        node_spec=node_spec,
                        workflow_inputs=workflow_inputs,
                        semaphore=semaphore,
                        cancel_evt=cancel_evt,
                    )
                )
                pending_futures[nid] = future

            if not pending_futures:
                continue

            # 等待至少一个 future 完成
            done, _ = await asyncio.wait(
                pending_futures.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            # 处理完成的 futures
            completed_ids: list[str] = []
            failed_ids: list[str] = []
            for future in done:
                # 找到对应的 node_id
                nid = next(n for n, f in pending_futures.items() if f is future)
                try:
                    result_status = future.result()
                    if result_status == TaskStatus.COMPLETED:
                        completed_ids.append(nid)
                    elif result_status == TaskStatus.SKIPPED:
                        # 节点被跳过（取消或上游失败传播）：视为失败传播
                        failed_ids.append(nid)
                    else:
                        failed_ids.append(nid)
                except Exception as e:
                    logger.error("节点 %s 执行异常: %s", nid, e, exc_info=True)
                    failed_ids.append(nid)
                pending_futures.pop(nid, None)

            # 失败节点：递归标记下游为 skipped（必须在移除失败节点之前执行，
            # 否则 nx.descendants 找不到失败节点，下游永远不会被标记）
            for failed_nid in failed_ids:
                await self._propagate_skip(
                    workflow_run_id=workflow_run_id,
                    graph=working_graph,
                    failed_node=failed_nid,
                    cancel_evt=cancel_evt,
                )

            # 从图中移除完成/失败的节点
            for nid in completed_ids + failed_ids:
                if nid in working_graph:
                    working_graph.remove_node(nid)

    async def _propagate_skip(
        self,
        *,
        workflow_run_id: str,
        graph,
        failed_node: str,
        cancel_evt: asyncio.Event,
    ) -> None:
        """递归标记失败节点的所有下游为 skipped，并从图中移除。

        注意：此函数会修改 ``graph``（移除被跳过的节点）。
        """
        if nx is None:
            return
        # 收集所有下游节点（含间接）
        descendants = list(nx.descendants(graph, failed_node)) if failed_node in graph else []
        for desc_nid in descendants:
            if cancel_evt.is_set():
                # 已被取消信号标记，避免重复 emit
                continue
            current_state = await self._store.get_node_state(workflow_run_id, desc_nid)
            if current_state and current_state.get("status") not in {
                "pending",
            }:
                continue
            await self._store.update_node_state(
                workflow_run_id,
                desc_nid,
                status="skipped",
                error=f"上游节点 {failed_node} 失败",
                completed_at=datetime.now(timezone.utc),
            )
            await self._emit(
                workflow_run_id,
                WorkflowEvent(
                    workflow_run_id=workflow_run_id,
                    event_type="node_skipped",
                    node_id=desc_nid,
                    payload={"reason": "upstream_failed", "upstream": failed_node},
                    timestamp=time.time(),
                ),
            )
            if desc_nid in graph:
                graph.remove_node(desc_nid)

    async def _execute_node(
        self,
        *,
        workflow_run_id: str,
        node_spec,
        workflow_inputs: dict[str, Any],
        semaphore: asyncio.Semaphore,
        cancel_evt: asyncio.Event,
    ) -> TaskStatus:
        """执行单个节点。返回最终 TaskStatus。"""
        node_id = node_spec.node_id

        if cancel_evt.is_set():
            return TaskStatus.SKIPPED

        async with semaphore:
            if cancel_evt.is_set():
                return TaskStatus.SKIPPED

            # 失败传播竞态防护：上游失败传播已标记本节点 skipped 时不再执行
            current_state = await self._store.get_node_state(workflow_run_id, node_id)
            if current_state and current_state.get("status") == "skipped":
                return TaskStatus.SKIPPED

            # 1. 解析 inputs（artifact 引用）
            completed_outputs = await self._store.get_completed_node_outputs(workflow_run_id)
            resolved_inputs: dict[str, Artifact] = {}
            for input_name, ref in node_spec.inputs.items():
                art = _resolve_artifact_ref(ref, completed_outputs)
                if art is None:
                    # 尝试从工作流级 inputs 中取
                    if isinstance(ref, str) and ref in workflow_inputs:
                        raw = workflow_inputs[ref]
                        try:
                            art = raw if isinstance(raw, Artifact) else Artifact(**raw)
                        except (TypeError, ValueError):
                            art = None
                if art is None:
                    logger.warning(
                        "节点 %s 的输入 %s=%s 无法解析，将传 None",
                        node_id,
                        input_name,
                        ref,
                    )
                    continue
                resolved_inputs[input_name] = art

            # 2. 更新节点状态为 running
            job_id = f"wf-{uuid.uuid4().hex[:12]}"
            await self._store.update_node_state(
                workflow_run_id,
                node_id,
                status="running",
                job_id=job_id,
                inputs={k: _serialize(v) for k, v in resolved_inputs.items()},
                started_at=datetime.now(timezone.utc),
            )
            await self._emit(
                workflow_run_id,
                WorkflowEvent(
                    workflow_run_id=workflow_run_id,
                    event_type="node_started",
                    node_id=node_id,
                    payload={"job_id": job_id},
                    timestamp=time.time(),
                ),
            )

            # 3. 获取 TaskHandler
            try:
                handler = self.registry.get(node_spec.task_type)
            except KeyError:
                error_msg = f"task_type 未注册: {node_spec.task_type}"
                await self._store.update_node_state(
                    workflow_run_id,
                    node_id,
                    status="failed",
                    error=error_msg,
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="node_failed",
                        node_id=node_id,
                        payload={"error": error_msg},
                        timestamp=time.time(),
                    ),
                )
                return TaskStatus.FAILED

            # 4. 构造 TaskContext 并执行
            ctx = TaskContext(
                job_id=job_id,
                workflow_run_id=workflow_run_id,
                inputs=resolved_inputs,
                config=node_spec.params,
                retry_count=0,
                deadline_ts=time.time() + node_spec.timeout_seconds,
            )

            try:
                result: TaskResult = await asyncio.wait_for(
                    handler.execute(ctx),
                    timeout=node_spec.timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = TaskResult(
                    status=TaskStatus.FAILED,
                    error=f"节点执行超时 ({node_spec.timeout_seconds}s)",
                    error_code="TIMEOUT",
                )
            except asyncio.CancelledError:
                await self._store.update_node_state(
                    workflow_run_id,
                    node_id,
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc),
                )
                raise
            except Exception as e:
                result = TaskResult(
                    status=TaskStatus.FAILED,
                    error=str(e)[:2048],
                    error_code=type(e).__name__,
                )

            # 5. 持久化结果
            # 失败传播竞态防护：执行期间上游失败可能已把本节点标记 skipped，
            # 此时不覆盖 skipped 状态（避免 failed/completed 覆盖传播结果）。
            current_state = await self._store.get_node_state(workflow_run_id, node_id)
            if current_state and current_state.get("status") == "skipped":
                logger.debug("节点 %s 持久化前发现已 skipped，放弃覆盖", node_id)
                return TaskStatus.SKIPPED

            outputs_serialized = {k: _serialize(v) for k, v in result.outputs.items()}
            metrics_serialized = {k: float(v) for k, v in result.metrics.items() if isinstance(v, (int, float))}
            logger.debug("节点 %s 结果: status=%s outputs=%s", node_id, result.status, list(outputs_serialized))

            if cancel_evt.is_set():
                await self._store.update_node_state(
                    workflow_run_id,
                    node_id,
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc),
                )
                return TaskStatus.CANCELLED

            if result.status == TaskStatus.COMPLETED:
                await self._store.update_node_state(
                    workflow_run_id,
                    node_id,
                    status="completed",
                    outputs=outputs_serialized,
                    metrics=metrics_serialized,
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="node_completed",
                        node_id=node_id,
                        payload={
                            "outputs": outputs_serialized,
                            "metrics": metrics_serialized,
                        },
                        timestamp=time.time(),
                    ),
                )
                return TaskStatus.COMPLETED
            else:
                await self._store.update_node_state(
                    workflow_run_id,
                    node_id,
                    status="failed",
                    error=result.error or "节点执行失败",
                    outputs=outputs_serialized,
                    metrics=metrics_serialized,
                    completed_at=datetime.now(timezone.utc),
                )
                await self._emit(
                    workflow_run_id,
                    WorkflowEvent(
                        workflow_run_id=workflow_run_id,
                        event_type="node_failed",
                        node_id=node_id,
                        payload={
                            "error": result.error,
                            "error_code": result.error_code,
                        },
                        timestamp=time.time(),
                    ),
                )
                return TaskStatus.FAILED

    async def _resolve_workflow_outputs(self, workflow_run_id: str, spec: WorkflowSpec) -> dict[str, Any]:
        """解析工作流级 outputs（${node_id.output_name} 引用）。"""
        completed_outputs = await self._store.get_completed_node_outputs(workflow_run_id)
        result: dict[str, Any] = {}
        for out_name, ref in spec.outputs.items():
            art = _resolve_artifact_ref(ref, completed_outputs)
            if art is None:
                logger.warning("工作流输出 %s=%s 无法解析", out_name, ref)
                result[out_name] = None
            else:
                result[out_name] = _serialize(art)
        return result

    # 事件广播

    async def _emit(self, workflow_run_id: str, event: WorkflowEvent) -> None:
        """向所有订阅者推送事件。"""
        async with self._lock:
            subs = self._subscribers.get(workflow_run_id, [])
            dead_queues: list[asyncio.Queue] = []
            for q in subs:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # 队列满：丢弃最旧事件后重试
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except asyncio.QueueEmpty:
                        pass
                    logger.warning("订阅者队列满，丢弃旧事件: workflow_run_id=%s", workflow_run_id)
                except Exception as e:
                    logger.debug("订阅者队列异常: %s", e)
                    dead_queues.append(q)
            for q in dead_queues:
                if q in subs:
                    subs.remove(q)

    async def _notify_termination(self, workflow_run_id: str) -> None:
        """通知所有订阅者工作流已终结（推送哨兵 None）。"""
        async with self._lock:
            subs = self._subscribers.get(workflow_run_id, [])
        for q in subs:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                # 队列满：丢弃最旧事件后重试
                try:
                    q.get_nowait()
                    q.put_nowait(None)
                except asyncio.QueueEmpty:
                    pass


# 单例访问

_runner: WorkflowRunner | None = None
# [A-H17] 懒初始化 asyncio.Lock，避免模块导入时绑定到错误的事件循环
_runner_lock: asyncio.Lock | None = None


def _get_runner_lock() -> asyncio.Lock:
    """[A-H17] 懒初始化 asyncio.Lock，绑定到当前运行的事件循环。"""
    global _runner_lock
    if _runner_lock is None:
        _runner_lock = asyncio.Lock()
    return _runner_lock


async def get_workflow_runner() -> WorkflowRunner:
    """获取全局 WorkflowRunner 单例。"""
    global _runner
    if _runner is None:
        async with _get_runner_lock():
            if _runner is None:
                _runner = WorkflowRunner()
    return _runner


def reset_workflow_runner() -> None:
    """重置全局单例（仅用于测试）。"""
    global _runner
    _runner = None
