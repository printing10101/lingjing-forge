"""Snapshot 资源类 —— 同步封装 ``/api/v1/snapshots`` 端点。

对应 ``app.api.v1.snapshots`` 路由模块。涵盖实验快照的列表、创建、
详情查询与一键复现。

端点映射:
    +-------------------------------+-----------------------------------------------+
    | SDK 方法                      | HTTP 端点                                     |
    +-------------------------------+-----------------------------------------------+
    | list(created_by, git_sha,...) | GET  /snapshots                               |
    | create(config, model_uri,...) | POST /snapshots                               |
    | get(snapshot_id)              | GET  /snapshots/{snapshot_id}                 |
    | reproduce(snapshot_id)        | POST /snapshots/{snapshot_id}/reproduce       |
    +-------------------------------+-----------------------------------------------+

CreateSnapshotRequest 字段（与后端 Pydantic 模型对齐）::

    {
        "config": {                          # 实验配置，可含 workflow_spec
            "workflow_spec": {...},
            "hyperparams": {"lr": 1e-3},
            "seed": 42,
        },
        "dataset_versions": [                # 关联数据集版本 URI
            "dataset://phm2010/1.0.0",
        ],
        "model_uri": "model://ltc/1.0.0",   # 必填
        "metrics": {"mae": 0.123, "r2": 0.956},
        "created_by": "alice",              # 必填
        "notes": "首次复现实验",
    }

一键复现:
    后端根据 ``snapshot.config['workflow_spec']`` 重建 WorkflowSpec 并启动
    新的工作流运行。SDK 的 :meth:`Snapshot.reproduce` 返回新的
    ``workflow_run_id``，可继续通过 :meth:`Workflow.subscribe` 订阅事件流。
"""

from __future__ import annotations

from typing import Any, Optional

from lomo.client import LomoClient


class Snapshot:
    """实验快照资源访问器。通过 ``client.snapshots`` 获取实例。"""

    def __init__(self, client: LomoClient) -> None:
        self._client = client

    def list(
        self,
        *,
        created_by: Optional[str] = None,
        git_sha: Optional[str] = None,
        model_uri: Optional[str] = None,
        detail: bool = False,
    ) -> dict[str, Any]:
        """列出实验快照（按 created_at 倒序）。

        参数:
            created_by: 按创建者过滤。
            git_sha: 按 git SHA 过滤（精确匹配）。
            model_uri: 按模型 URI 过滤（精确匹配）。
            detail: True 返回完整字段（含 config / environment），
                False 返回轻量摘要（用于列表视图，备注截断 120 字符）。
        """
        params: dict[str, Any] = {"detail": detail}
        if created_by is not None:
            params["created_by"] = created_by
        if git_sha is not None:
            params["git_sha"] = git_sha
        if model_uri is not None:
            params["model_uri"] = model_uri
        return self._client.get("/snapshots", params=params)

    def create(
        self,
        *,
        model_uri: str,
        created_by: str,
        config: Optional[dict[str, Any]] = None,
        dataset_versions: Optional[list[str]] = None,
        metrics: Optional[dict[str, float]] = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """创建实验快照（后端自动采集 git_sha 与 environment）。

        参数:
            model_uri: 模型 URI，如 ``model://ltc/1.0.0``（必填）。
            created_by: 创建者标识（用户 ID 或 agent ID，必填）。
            config: 实验配置 dict。可包含 ``workflow_spec`` 字段以支持
                一键复现；其余字段由调用方按实验实际填写（hyperparams /
                seed 等）。
            dataset_versions: 关联的数据集版本 URI 列表
                （``dataset://<name>/<version>``）。
            metrics: 实验指标，如 ``{"mae": 0.123, "r2": 0.956}``。
            notes: 备注信息。

        返回:
            完整 ExperimentSnapshot dict（含 snapshot_id / git_sha /
            environment / mlflow_run_id 等）。
        """
        body: dict[str, Any] = {
            "model_uri": model_uri,
            "created_by": created_by,
            "config": config or {},
            "dataset_versions": dataset_versions or [],
            "metrics": metrics or {},
            "notes": notes,
        }
        return self._client.post("/snapshots", json=body)

    def get(self, snapshot_id: str) -> dict[str, Any]:
        """获取快照详情（含完整 config / metrics / environment）。"""
        return self._client.get(f"/snapshots/{snapshot_id}")

    def reproduce(self, snapshot_id: str) -> str:
        """根据快照一键复现：重建 WorkflowSpec 并启动新工作流运行。

        前置条件: 创建快照时 ``config['workflow_spec']`` 必须为完整的
        WorkflowSpec dict。

        参数:
            snapshot_id: 实验快照 ID。

        返回:
            新的 workflow_run_id（可用于订阅事件流 ::

                run_id = client.snapshots.reproduce(snap_id)
                for ev in client.workflows.subscribe(run_id):
                    ...

            ）。

        抛出:
            LomoNotFoundError: 快照不存在。
            LomoValidationError: 快照不支持复现（config 中无 workflow_spec）
                或 workflow_spec 反序列化失败。
        """
        data = self._client.post(f"/snapshots/{snapshot_id}/reproduce")
        if isinstance(data, dict):
            return data.get("workflow_run_id", "")
        return ""


__all__ = ["Snapshot"]
