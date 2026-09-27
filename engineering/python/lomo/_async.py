"""异步 SDK 客户端与资源类。

提供与 :mod:`lomo.client` / :mod:`lomo.workflow` / :mod:`lomo.dataset` /
:mod:`lomo.snapshot` 一一对应的异步 API，基于 :class:`httpx.AsyncClient`。
所有 IO 方法为 ``async def``，流式方法返回 ``AsyncIterator``。

适用场景:
    - asyncio 应用集成
    - Jupyter Notebook（配合 ``await``）
    - 高并发批量请求场景（如同时订阅多个工作流事件流）

示例::

    import asyncio
    from lomo import AsyncLomoClient

    async def main():
        async with AsyncLomoClient() as client:
            ds = await client.datasets.create(
                name="phm2010",
                schema={"fields": {...}, "primary_key": ["sample_id"]},
                owner_id="alice",
            )
            print(ds["dataset_id"])

            run_id = await client.workflows.run(spec={...}, owner_id="alice")
            async for ev in client.workflows.subscribe(run_id):
                if ev["event"] == "workflow_completed":
                    break

    asyncio.run(main())
"""

from __future__ import annotations

import json as _json
from typing import Any, AsyncIterator, Optional

import httpx

from lomo.client import DEFAULT_TIMEOUT, _BaseClient
from lomo.exceptions import (
    LomoAPIError,
    LomoConnectionError,
    LomoTimeoutError,
    LomoValidationError,
    _raise_for_envelope,
)


# 异步流式响应封装


class AsyncStreamingJSONL:
    """异步 JSONL 流式响应封装，用于 :meth:`AsyncDataset.read`。

    异步迭代产出每行反序列化后的 dict；遇到错误行（``{"error": ...}``）
    会抛 :class:`LomoAPIError`。
    """

    def __init__(self, resp: httpx.Response):
        self._resp = resp

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self.iter_json()

    async def iter_json(self) -> AsyncIterator[dict[str, Any]]:
        async for line in self._resp.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError as e:
                raise LomoConnectionError(f"流式响应中存在非法 JSON 行: {e}; line={line[:200]}") from e
            if isinstance(obj, dict) and "error" in obj:
                raise LomoAPIError(
                    str(obj.get("message", "stream error")),
                    code=1002,
                    detail=obj.get("error"),
                )
            yield obj

    @property
    def status_code(self) -> int:
        return self._resp.status_code


class AsyncSSEEventStream:
    """异步 SSE 事件流封装，用于 :meth:`AsyncWorkflow.subscribe`。

    异步迭代产出解析后的 dict 事件：``{"event": <type>, "data": <parsed json>}``。
    """

    def __init__(self, resp: httpx.Response):
        self._resp = resp

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self.iter_events()

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        event_type: Optional[str] = None
        data_buf: list[str] = []
        async for line in self._resp.aiter_lines():
            if not line:
                # 空行 = 事件边界
                if event_type is not None or data_buf:
                    data_str = "\n".join(data_buf)
                    try:
                        data = _json.loads(data_str) if data_str else {}
                    except _json.JSONDecodeError:
                        data = {"raw": data_str}
                    yield {"event": event_type or "message", "data": data}
                    event_type = None
                    data_buf = []
                continue
            if line.startswith(":"):
                continue  # SSE 注释行
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:") :].strip())
            else:
                # 未知前缀，按 data 处理
                data_buf.append(line)
        # 流结束时若仍有缓冲，刷出最后一个事件
        if event_type is not None or data_buf:
            data_str = "\n".join(data_buf)
            try:
                data = _json.loads(data_str) if data_str else {}
            except _json.JSONDecodeError:
                data = {"raw": data_str}
            yield {"event": event_type or "message", "data": data}


# 异步客户端


class AsyncLomoClient(_BaseClient):
    """异步 HTTP 客户端，基于 :class:`httpx.AsyncClient`。

    使用 ``async with`` 语句管理底层连接池；不使用上下文管理器时，需
    手动调用 :meth:`aclose` 释放连接。

    参数:
        base_url: 后端服务地址。默认读取 ``LOMO_BASE_URL`` 环境变量，否则
            使用 ``http://127.0.0.1:8000``。
        token: Bearer token。默认读取 ``LOMO_TOKEN`` 环境变量。
        timeout: 单次请求超时秒数，默认 30s。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(base_url, token=token, timeout=timeout)
        self._client = httpx.AsyncClient(timeout=self.timeout)

    # 生命周期

    async def __aenter__(self) -> "AsyncLomoClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """释放底层 httpx 连接池。"""
        await self._client.aclose()

    # 资源访问器（懒加载，避免循环导入）

    @property
    def workflows(self) -> "AsyncWorkflow":
        if self._workflows is None:
            self._workflows = AsyncWorkflow(self)
        return self._workflows

    @property
    def datasets(self) -> "AsyncDataset":
        if self._datasets is None:
            self._datasets = AsyncDataset(self)
        return self._datasets

    @property
    def snapshots(self) -> "AsyncSnapshot":
        if self._snapshots is None:
            self._snapshots = AsyncSnapshot(self)
        return self._snapshots

    # HTTP 方法

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict[str, Any]] = None,
        stream: bool = False,
    ) -> Any:
        """发送异步 HTTP 请求并解析响应信封。

        参数:
            method: HTTP 方法（GET/POST/PUT/DELETE）。
            path: 相对路径（如 ``/datasets``）或绝对 URL。
            json: 请求体（将被 httpx 序列化为 JSON）。
            params: 查询参数。
            stream: True 时返回异步流式响应对象
                （:class:`AsyncStreamingJSONL` / :class:`AsyncSSEEventStream`），
                不解析响应信封；False 时返回 ``data`` 字段。

        返回:
            stream=True 时返回 :class:`AsyncStreamingJSONL` 或
            :class:`AsyncSSEEventStream`；否则返回响应信封中的 ``data`` 字段。

        抛出:
            LomoTimeoutError: 请求超时。
            LomoConnectionError: 网络错误或非 JSON 响应。
            LomoAPIError 及子类: 后端返回非零 ``code``。
        """
        url = self._build_url(path)
        headers = self._headers(stream=stream)
        try:
            if stream:
                req = self._client.build_request(method, url, json=json, params=params, headers=headers)
                resp = await self._client.send(req, stream=True)
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" in ctype:
                    return AsyncSSEEventStream(resp)
                return AsyncStreamingJSONL(resp)
            resp = await self._client.request(method, url, json=json, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise LomoTimeoutError(f"请求超时: {e}") from e
        except httpx.HTTPError as e:
            raise LomoConnectionError(f"网络错误: {e}") from e

        try:
            payload = resp.json()
        except ValueError as e:
            raise LomoConnectionError(f"非 JSON 响应 (HTTP {resp.status_code}): {resp.text[:200]}") from e

        _raise_for_envelope(payload)
        return payload.get("data")

    async def get(self, path: str, *, params: Optional[dict] = None, stream: bool = False):
        return await self.request("GET", path, params=params, stream=stream)

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        stream: bool = False,
    ):
        return await self.request("POST", path, json=json, params=params, stream=stream)

    async def put(self, path: str, *, json: Any = None, params: Optional[dict] = None):
        return await self.request("PUT", path, json=json, params=params)

    async def delete(self, path: str, *, params: Optional[dict] = None):
        return await self.request("DELETE", path, params=params)


# 异步资源类 —— Workflow


class AsyncWorkflow:
    """异步工作流资源访问器。通过 ``async_client.workflows`` 获取实例。

    方法与同步 :class:`lomo.workflow.Workflow` 一一对应，区别仅在
    ``async/await`` 语义。
    """

    def __init__(self, client: AsyncLomoClient) -> None:
        self._client = client

    # 校验与运行

    async def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        """仅校验 WorkflowSpec，不启动运行。"""
        return await self._client.post("/workflows/validate", json={"spec": spec})

    async def run(
        self,
        spec: dict[str, Any],
        *,
        inputs: Optional[dict[str, Any]] = None,
        owner_id: Optional[str] = None,
    ) -> str:
        """提交工作流运行，返回 workflow_run_id。"""
        body: dict[str, Any] = {"spec": spec}
        if inputs is not None:
            body["inputs"] = inputs
        if owner_id is not None:
            body["owner_id"] = owner_id
        data = await self._client.post("/workflows/run", json=body)
        if isinstance(data, dict):
            return data.get("workflow_run_id", "")
        return ""

    async def resume(
        self,
        workflow_run_id: str,
        spec: dict[str, Any],
        *,
        inputs: Optional[dict[str, Any]] = None,
        owner_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """断点续跑工作流。从已完成的节点之后继续，仅重跑 PENDING/FAILED 节点。"""
        body: dict[str, Any] = {"spec": spec}
        if inputs is not None:
            body["inputs"] = inputs
        if owner_id is not None:
            body["owner_id"] = owner_id
        return await self._client.post(f"/workflows/{workflow_run_id}/resume", json=body)

    # 状态查询与控制

    async def get_status(self, workflow_run_id: str) -> dict[str, Any]:
        """获取工作流运行状态（含每个节点的 status / 起止时间 / 错误信息）。"""
        return await self._client.get(f"/workflows/{workflow_run_id}")

    async def cancel(self, workflow_run_id: str) -> bool:
        """取消工作流运行。返回 True 表示取消请求已被接受。"""
        data = await self._client.post(f"/workflows/{workflow_run_id}/cancel")
        if isinstance(data, dict):
            return bool(data.get("cancelled", data.get("ok", False)))
        return bool(data)

    async def delete(self, workflow_run_id: str) -> dict[str, Any]:
        """删除工作流运行记录（含节点状态）。"""
        return await self._client.delete(f"/workflows/{workflow_run_id}")

    async def list(
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
        return await self._client.get("/workflows", params=params)

    # 事件流订阅

    def subscribe(self, workflow_run_id: str) -> AsyncIterator[dict[str, Any]]:
        """订阅工作流事件流（SSE）。

        返回异步迭代器，使用 ``async for`` 消费解析后的事件 dict：
        ``{"event": <type>, "data": <payload>}``。

        事件类型见后端 ``WorkflowEvent.event_type`` 枚举：
        ``node_started`` / ``node_completed`` / ``node_failed`` /
        ``node_skipped`` / ``workflow_completed`` / ``workflow_failed`` /
        ``workflow_cancelled``。

        示例::

            async for ev in async_client.workflows.subscribe(run_id):
                if ev["event"] == "node_completed":
                    print(ev["data"]["node_id"], "done")
                elif ev["event"] == "workflow_completed":
                    break

        注意:
            HTTP 请求在迭代开始时才发送（lazy）。若不迭代，请求不会发出。
        """
        return self._subscribe_impl(workflow_run_id)

    async def _subscribe_impl(self, workflow_run_id: str) -> AsyncIterator[dict[str, Any]]:
        stream = await self._client.get(f"/workflows/{workflow_run_id}/stream", stream=True)
        if not isinstance(stream, AsyncSSEEventStream):
            raise TypeError(f"subscribe 期望 AsyncSSEEventStream，实际得到 {type(stream).__name__}")
        async for ev in stream:
            yield ev


# 异步资源类 —— Dataset


class AsyncDataset:
    """异步数据集资源访问器。通过 ``async_client.datasets`` 获取实例。

    方法与同步 :class:`lomo.dataset.Dataset` 一一对应，区别仅在
    ``async/await`` 语义。
    """

    def __init__(self, client: AsyncLomoClient) -> None:
        self._client = client

    # 列表与创建

    async def list(
        self,
        *,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """列出数据集（按 created_at 倒序）。"""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if owner_id is not None:
            params["owner_id"] = owner_id
        if status is not None:
            params["status"] = status
        return await self._client.get("/datasets", params=params)

    async def create(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        owner_id: str,
        description: str = "",
    ) -> dict[str, Any]:
        """创建数据集（初始 DRAFT 状态，无版本）。

        返回 ``{"dataset_id": "...", "status": "draft"}``。
        """
        body = {
            "name": name,
            "schema": schema,
            "owner_id": owner_id,
            "description": description,
        }
        return await self._client.post("/datasets", json=body)

    async def get(self, dataset_id: str) -> dict[str, Any]:
        """获取数据集详情（含 schema 与版本概要）。"""
        return await self._client.get(f"/datasets/{dataset_id}")

    # 版本管理

    async def list_versions(self, dataset_id: str) -> dict[str, Any]:
        """列出数据集的所有版本（按创建时间倒序）。"""
        return await self._client.get(f"/datasets/{dataset_id}/versions")

    async def commit_version(
        self,
        dataset_id: str,
        *,
        records: Optional[list[dict[str, Any]]] = None,
        version: Optional[str] = None,
        lineage: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """提交一个不可变版本。

        records 为空且 dataset 是 lake 适配器时，后端会自动从 lake 加载
        当前全部 records。
        """
        body: dict[str, Any] = {"records": records if records is not None else []}
        if version is not None:
            body["version"] = version
        if lineage is not None:
            body["lineage"] = lineage
        return await self._client.post(f"/datasets/{dataset_id}/commit", json=body)

    async def deprecate(self, dataset_id: str, version: str) -> dict[str, Any]:
        """废弃某版本（不可逆，但内容仍可读）。"""
        return await self._client.post(f"/datasets/{dataset_id}/deprecate", params={"version": version})

    # 流式读取

    def read(
        self,
        dataset_id: str,
        *,
        version: Optional[str] = None,
        batch_size: int = 1000,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式读取数据集版本内容（JSONL），返回异步迭代器。

        示例::

            async for row in async_client.datasets.read(ds_id, version="1.0.0"):
                process(row)

        注意:
            HTTP 请求在迭代开始时才发送（lazy）。若不迭代，请求不会发出。
        """
        return self._read_impl(dataset_id, version=version, batch_size=batch_size)

    async def _read_impl(
        self,
        dataset_id: str,
        *,
        version: Optional[str] = None,
        batch_size: int = 1000,
    ) -> AsyncIterator[dict[str, Any]]:
        params: dict[str, Any] = {"batch_size": batch_size}
        if version is not None:
            params["version"] = version
        stream = await self._client.get(f"/datasets/{dataset_id}/read", params=params, stream=True)
        if not isinstance(stream, AsyncStreamingJSONL):
            raise TypeError(f"read 期望 AsyncStreamingJSONL，实际得到 {type(stream).__name__}")
        async for row in stream:
            yield row

    # 血缘

    async def record_lineage(self, lineage: dict[str, Any]) -> dict[str, Any]:
        """记录一条血缘。返回 ``{"record_id": "..."}``。"""
        return await self._client.post("/datasets/lineage", json=lineage)

    async def get_lineage(
        self,
        target_uri: str,
        *,
        direction: str = "upstream",
        depth: int = 10,
    ) -> dict[str, Any]:
        """查询血缘图。

        参数:
            target_uri: 目标资源 URI（如 ``dataset://phm2010/1.0.0``）。
            direction: ``upstream`` / ``downstream`` / ``visualize``。
            depth: 遍历深度，1-50。
        """
        if direction not in {"upstream", "downstream", "visualize"}:
            raise LomoValidationError(
                f"direction 必须为 upstream/downstream/visualize: {direction}",
                code=1002,
            )
        params = {"direction": direction, "depth": depth}
        return await self._client.get(f"/datasets/lineage/{target_uri}", params=params)


# 异步资源类 —— Snapshot


class AsyncSnapshot:
    """异步实验快照资源访问器。通过 ``async_client.snapshots`` 获取实例。

    方法与同步 :class:`lomo.snapshot.Snapshot` 一一对应，区别仅在
    ``async/await`` 语义。
    """

    def __init__(self, client: AsyncLomoClient) -> None:
        self._client = client

    async def list(
        self,
        *,
        created_by: Optional[str] = None,
        git_sha: Optional[str] = None,
        model_uri: Optional[str] = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        """列出实验快照（按 created_at 倒序）。"""
        params: dict[str, Any] = {"detail": detail}
        if created_by is not None:
            params["created_by"] = created_by
        if git_sha is not None:
            params["git_sha"] = git_sha
        if model_uri is not None:
            params["model_uri"] = model_uri
        return await self._client.get("/snapshots", params=params)

    async def create(
        self,
        *,
        model_uri: str,
        created_by: str,
        config: Optional[dict[str, Any]] = None,
        dataset_versions: Optional[list[str]] = None,
        metrics: Optional[dict[str, float]] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """创建实验快照（后端自动采集 git_sha 与 environment）。"""
        body: dict[str, Any] = {
            "model_uri": model_uri,
            "created_by": created_by,
            "config": config or {},
            "dataset_versions": dataset_versions or [],
            "metrics": metrics or {},
            "notes": notes,
        }
        return await self._client.post("/snapshots", json=body)

    async def get(self, snapshot_id: str) -> dict[str, Any]:
        """获取快照详情（含完整 config / metrics / environment）。"""
        return await self._client.get(f"/snapshots/{snapshot_id}")

    async def reproduce(self, snapshot_id: str) -> str:
        """根据快照一键复现：重建 WorkflowSpec 并启动新工作流运行。

        前置条件: 创建快照时 ``config['workflow_spec']`` 必须为完整的
        WorkflowSpec dict。

        返回:
            新的 workflow_run_id。
        """
        data = await self._client.post(f"/snapshots/{snapshot_id}/reproduce")
        if isinstance(data, dict):
            return data.get("workflow_run_id", "")
        return ""


__all__ = [
    "AsyncLomoClient",
    "AsyncStreamingJSONL",
    "AsyncSSEEventStream",
    "AsyncWorkflow",
    "AsyncDataset",
    "AsyncSnapshot",
]
