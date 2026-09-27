"""Dataset 资源类 —— 同步封装 ``/api/v1/datasets`` 端点。

对应 ``app.api.v1.datasets`` 路由模块。涵盖数据集 CRUD、版本管理、
流式读取、血缘记录与查询。

端点映射:
    +-----------------------------------+-------------------------------------------+
    | SDK 方法                          | HTTP 端点                                 |
    +-----------------------------------+-------------------------------------------+
    | list(owner_id, status, ...)       | GET  /datasets                            |
    | create(name, schema, owner_id)    | POST /datasets                            |
    | get(dataset_id)                   | GET  /datasets/{dataset_id}               |
    | list_versions(dataset_id)         | GET  /datasets/{dataset_id}/versions      |
    | commit_version(dataset_id, ...)   | POST /datasets/{dataset_id}/commit        |
    | read(dataset_id, version, ...)    | GET  /datasets/{dataset_id}/read (JSONL)  |
    | deprecate(dataset_id, version)    | POST /datasets/{dataset_id}/deprecate     |
    | record_lineage(lineage)           | POST /datasets/lineage                    |
    | get_lineage(target_uri, ...)      | GET  /datasets/lineage/{target_uri}       |
    +-----------------------------------+-------------------------------------------+

Schema 结构（与后端 DatasetSchemaModel 对齐）::

    {
        "fields": {
            "sample_id": {"type": "int", "required": True, "description": "样本 ID"},
            "force":     {"type": "float", "required": False},
        },
        "primary_key": ["sample_id"],
        "metadata": {"source": "PHM2010"},
    }

Lineage 结构（与后端 LineageModel 对齐）::

    {
        "target": "dataset://phm2010/1.0.0",
        "source_type": "task",        # task | workflow | manual | external
        "source_ref": "task://preprocess/abc123",
        "inputs": ["dataset://raw/1.0.0"],
        "outputs": ["dataset://phm2010/1.0.0"],
        "operation": "preprocess",
        "metadata": {},
    }
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from lomo.client import LomoClient, StreamingJSONL


class Dataset:
    """数据集资源访问器。通过 ``client.datasets`` 获取实例。"""

    def __init__(self, client: LomoClient) -> None:
        self._client = client

    # 列表与创建

    def list(
        self,
        *,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """列出数据集（按 created_at 倒序）。

        参数:
            owner_id: 按 owner 过滤。
            status: 按状态过滤，可选 ``draft`` / ``published`` /
                ``deprecated`` / ``archived``。
            limit: 1-1000，默认 100。
            offset: 分页偏移。
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if owner_id is not None:
            params["owner_id"] = owner_id
        if status is not None:
            params["status"] = status
        return self._client.get("/datasets", params=params)

    def create(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        owner_id: str,
        description: str = "",
    ) -> dict[str, Any]:
        """创建数据集（初始 DRAFT 状态，无版本）。

        参数:
            name: 数据集名称。
            schema: 数据集 schema dict（结构见模块文档）。
            owner_id: 所有者 ID。
            description: 可选描述。

        返回:
            ``{"dataset_id": "...", "status": "draft"}``
        """
        body = {
            "name": name,
            "schema": schema,
            "owner_id": owner_id,
            "description": description,
        }
        return self._client.post("/datasets", json=body)

    def get(self, dataset_id: str) -> dict[str, Any]:
        """获取数据集详情（含 schema 与版本概要）。"""
        return self._client.get(f"/datasets/{dataset_id}")

    # 版本管理

    def list_versions(self, dataset_id: str) -> dict[str, Any]:
        """列出数据集的所有版本（按创建时间倒序）。"""
        return self._client.get(f"/datasets/{dataset_id}/versions")

    def commit_version(
        self,
        dataset_id: str,
        *,
        records: Optional[list[dict[str, Any]]] = None,
        version: Optional[str] = None,
        lineage: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """提交一个不可变版本。

        参数:
            dataset_id: 数据集 ID。
            records: 该版本的记录列表。为空且 dataset 是 lake 适配器时，
                后端会自动从 lake 加载当前全部 records。
            version: semver 版本号。None 时自动递增 patch。
            lineage: 可选血缘记录（结构见模块文档）。

        返回:
            DatasetVersion dict（含 content_hash / row_count / storage_uri 等）。
        """
        body: dict[str, Any] = {}
        if records is not None:
            body["records"] = records
        else:
            body["records"] = []
        if version is not None:
            body["version"] = version
        if lineage is not None:
            body["lineage"] = lineage
        return self._client.post(f"/datasets/{dataset_id}/commit", json=body)

    def deprecate(self, dataset_id: str, version: str) -> dict[str, Any]:
        """废弃某版本（不可逆，但内容仍可读）。"""
        return self._client.post(f"/datasets/{dataset_id}/deprecate", params={"version": version})

    # 流式读取

    def read(
        self,
        dataset_id: str,
        *,
        version: Optional[str] = None,
        batch_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """流式读取数据集版本内容（JSONL）。

        迭代产出每行反序列化后的 dict。流中的错误行（``{"error": ...}``）
        会以 :class:`LomoAPIError` 抛出。

        示例::

            for row in client.datasets.read(ds_id, version="1.0.0"):
                process(row)
        """
        params: dict[str, Any] = {"batch_size": batch_size}
        if version is not None:
            params["version"] = version
        stream = self._client.get(f"/datasets/{dataset_id}/read", params=params, stream=True)
        if not isinstance(stream, StreamingJSONL):
            raise TypeError(f"read 期望 StreamingJSONL，实际得到 {type(stream).__name__}")
        return iter(stream)

    # 血缘

    def record_lineage(self, lineage: dict[str, Any]) -> dict[str, Any]:
        """记录一条血缘。

        参数:
            lineage: 血缘记录 dict（结构见模块文档）。

        返回:
            ``{"record_id": "..."}``
        """
        return self._client.post("/datasets/lineage", json=lineage)

    def get_lineage(
        self,
        target_uri: str,
        *,
        direction: str = "upstream",
        depth: int = 10,
    ) -> dict[str, Any]:
        """查询血缘图。

        参数:
            target_uri: 目标资源 URI（如 ``dataset://phm2010/1.0.0``）。
                会被自动 URL 编码后拼到 path，调用方无需编码。
            direction: ``upstream`` / ``downstream`` / ``visualize``。
                ``visualize`` 返回节点/边图数据，用于前端可视化。
            depth: 遍历深度，1-50。

        返回:
            upstream/downstream: ``{"target": ..., "direction": ..., "records": [...]}``
            visualize: ``{"target": ..., "graph": {...}}``
        """
        if direction not in {"upstream", "downstream", "visualize"}:
            from lomo.exceptions import LomoValidationError

            raise LomoValidationError(
                f"direction 必须为 upstream/downstream/visualize: {direction}",
                code=1002,
            )
        params = {"direction": direction, "depth": depth}
        # target_uri 作为 path 传入，httpx 会自动 URL 编码斜杠之外的特殊字符；
        # 但为避免斜杠被 path 压扁，使用原始字符串拼接（后端用 {target_uri:path} 接收）
        return self._client.get(f"/datasets/lineage/{target_uri}", params=params)


__all__ = ["Dataset"]
