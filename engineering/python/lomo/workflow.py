"""Workflow 资源类 —— 同步封装 ``/api/v1/workflows`` 端点。

对应 ``app.api.v1.workflows`` 路由模块。每个 SDK 方法对应一个 REST 端点，
请求体 / 查询参数与后端 Pydantic 模型严格对齐。

端点映射:
    +-------------------------------+-------------------------------------------+
    | SDK 方法                      | HTTP 端点                                 |
    +-------------------------------+-------------------------------------------+
    | validate(spec)                | POST /workflows/validate                  |
    | run(spec, inputs, owner_id)   | POST /workflows/run                       |
    | resume(run_id, spec, ...)     | POST /workflows/{run_id}/resume           |
    | get_status(run_id)            | GET  /workflows/{run_id}                  |
    | cancel(run_id)                | POST /workflows/{run_id}/cancel           |
    | subscribe(run_id)             | GET  /workflows/{run_id}/stream (SSE)     |
    | list(limit, offset)           | GET  /workflows                           |
    | delete(run_id)                | DELETE /workflows/{run_id}                |
    +-------------------------------+-------------------------------------------+

WorkflowSpec 结构（与后端 WorkflowSpecModel 对齐）::

    {
        "name": "demo",
        "version": "1.0.0",
        "nodes": [
            {
                "node_id": "n1",
                "task_type": "data_preprocess",
                "params": {...},
                "inputs": {"dataset": "artifact://input_ds"},
                "retry": 0,
                "timeout_seconds": 3600,
            }
        ],
        "edges": [{"upstream": "n1", "downstream": "n2"}],
        "inputs": {"input_ds": {"name": "...", "type": "dataset", "uri": "...", "metadata": {}}},
        "outputs": {"report": "artifact://report"},
        "metadata": {},
    }
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from lomo.client import LomoClient, SSEEventStream


class Workflow:
    """工作流资源访问器。通过 ``client.workflows`` 获取实例。"""

    def __init__(self, client: LomoClient) -> None:
        self._client = client

    # 校验与运行

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        """仅校验 WorkflowSpec，不启动运行。

        参数:
            spec: WorkflowSpec dict。

        返回:
            校验结果（含 nodes/edges 数量、是否合法、潜在问题列表）。
        """
        return self._client.post("/workflows/validate", json={"spec": spec})

    def run(
        self,
        spec: dict[str, Any],
        *,
        inputs: Optional[dict[str, Any]] = None,
        owner_id: Optional[str] = None,
    ) -> str:
        """提交工作流运行。

        参数:
            spec: WorkflowSpec dict。
            inputs: 可选，覆盖 ``spec.inputs`` 的运行时输入 artifact。
            owner_id: 运行发起者 ID（用于权限与审计）。

        返回:
            workflow_run_id（用于后续查询状态 / 订阅事件 / 取消）。
        """
        body: dict[str, Any] = {"spec": spec}
        if inputs is not None:
            body["inputs"] = inputs
        if owner_id is not None:
            body["owner_id"] = owner_id
        data = self._client.post("/workflows/run", json=body)
        if isinstance(data, dict):
            return data.get("workflow_run_id", "")
        return ""

    def resume(
        self,
        workflow_run_id: str,
        spec: dict[str, Any],
        *,
        inputs: Optional[dict[str, Any]] = None,
        owner_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """断点续跑工作流。

        从已完成的节点之后继续，仅重跑 PENDING/FAILED 节点。
        """
        body: dict[str, Any] = {"spec": spec}
        if inputs is not None:
            body["inputs"] = inputs
        if owner_id is not None:
            body["owner_id"] = owner_id
        return self._client.post(f"/workflows/{workflow_run_id}/resume", json=body)

    # 状态查询与控制

    def get_status(self, workflow_run_id: str) -> dict[str, Any]:
        """获取工作流运行状态（含每个节点的 status / 起止时间 / 错误信息）。"""
        return self._client.get(f"/workflows/{workflow_run_id}")

    def cancel(self, workflow_run_id: str) -> bool:
        """取消工作流运行。返回 True 表示取消请求已被接受。"""
        data = self._client.post(f"/workflows/{workflow_run_id}/cancel")
        if isinstance(data, dict):
            return bool(data.get("cancelled", data.get("ok", False)))
        return bool(data)

    def delete(self, workflow_run_id: str) -> dict[str, Any]:
        """删除工作流运行记录（含节点状态）。"""
        return self._client.delete(f"/workflows/{workflow_run_id}")

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """列出工作流运行记录（按 created_at 倒序）。"""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if owner_id is not None:
            params["owner_id"] = owner_id
        if status is not None:
            params["status"] = status
        return self._client.get("/workflows", params=params)

    # 事件流订阅

    def subscribe(self, workflow_run_id: str) -> Iterator[dict[str, Any]]:
        """订阅工作流事件流（SSE）。

        迭代产出解析后的事件 dict：``{"event": <type>, "data": <payload>}``。
        事件类型见后端 ``WorkflowEvent.event_type`` 枚举：
        ``node_started`` / ``node_completed`` / ``node_failed`` /
        ``node_skipped`` / ``workflow_completed`` / ``workflow_failed`` /
        ``workflow_cancelled``。

        示例::

            for ev in client.workflows.subscribe(run_id):
                if ev["event"] == "node_completed":
                    print(ev["data"]["node_id"], "done")
                elif ev["event"] == "workflow_completed":
                    break
        """
        stream = self._client.get(f"/workflows/{workflow_run_id}/stream", stream=True)
        if not isinstance(stream, SSEEventStream):
            raise TypeError(f"subscribe 期望 SSEEventStream，实际得到 {type(stream).__name__}")
        return iter(stream)


__all__ = ["Workflow"]
