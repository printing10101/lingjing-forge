"""数据集契约：定义数据集、版本、血缘的统一接口.

对应 ADR-005 第 4 章。本文件只定义接口与数据结构，不包含实现。
现有 app/training/data_lake.py 通过 contract_adapter 适配此契约。

契约稳定性：Stable（v1.0.0），向后兼容扩展。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.utils.time import utcnow
from typing import Any
from collections.abc import AsyncIterator


class DatasetStatus(str, Enum):
    """数据集版本状态.

    状态机：
        DRAFT → PUBLISHED（不可变）
        PUBLISHED → DEPRECATED → ARCHIVED
    """

    DRAFT = "draft"
    PUBLISHED = "published"  # 不可变
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


# 合法状态转换（PUBLISHED 之后内容不可变，只能改状态）
VALID_DATASET_STATUS_TRANSITIONS: dict[DatasetStatus, set[DatasetStatus]] = {
    DatasetStatus.DRAFT: {DatasetStatus.PUBLISHED, DatasetStatus.ARCHIVED},
    DatasetStatus.PUBLISHED: {DatasetStatus.DEPRECATED, DatasetStatus.ARCHIVED},
    DatasetStatus.DEPRECATED: {DatasetStatus.ARCHIVED},
    DatasetStatus.ARCHIVED: set(),
}


@dataclass
class DatasetSchema:
    """数据集 schema 契约.

    fields 结构：
        {"column_name": {"type": "float"|"int"|"str"|"bool"|"datetime"|"list"|"dict",
                          "required": bool, "description": str}}
    """

    fields: dict[str, dict[str, Any]]
    primary_key: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """校验 schema 自身合法性。返回错误列表。"""
        errors: list[str] = []
        valid_types = {"float", "int", "str", "bool", "datetime", "list", "dict"}

        if not self.fields:
            errors.append("DatasetSchema.fields 不能为空")
            return errors

        for col_name, col_def in self.fields.items():
            if not col_name:
                errors.append("字段名不能为空")
                continue
            if "type" not in col_def:
                errors.append(f"字段 {col_name} 缺少 type 定义")
                continue
            if col_def["type"] not in valid_types:
                errors.append(f"字段 {col_name} 的 type 不合法: {col_def['type']}，合法值: {valid_types}")

        # 主键字段必须在 fields 中存在
        for pk in self.primary_key:
            if pk not in self.fields:
                errors.append(f"primary_key 字段 {pk} 未在 fields 中定义")

        return errors


@dataclass
class DatasetVersion:
    """数据集版本契约（不可变快照）。"""

    dataset_id: str
    version: str  # semver，如 "1.0.0"
    status: DatasetStatus
    schema: DatasetSchema
    content_hash: str  # sha256，内容寻址
    row_count: int
    size_bytes: int
    created_at: datetime
    created_by: str  # user_id 或 plugin_id
    storage_uri: str  # 实际存储位置
    lineage: str | None = None  # lineage record id

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise ValueError("DatasetVersion.dataset_id 不能为空")
        if not _is_valid_semver(self.version):
            raise ValueError(f"DatasetVersion.version 不是合法 semver: {self.version}")
        if not self.content_hash:
            raise ValueError("DatasetVersion.content_hash 不能为空")
        if self.row_count < 0:
            raise ValueError(f"DatasetVersion.row_count 不能为负数: {self.row_count}")
        if self.size_bytes < 0:
            raise ValueError(f"DatasetVersion.size_bytes 不能为负数: {self.size_bytes}")
        if not self.storage_uri:
            raise ValueError("DatasetVersion.storage_uri 不能为空")


def _is_valid_semver(version: str) -> bool:
    """校验 semver 格式：MAJOR.MINOR.PATCH（可选 -prerelease，含 prerelease 子段）.

    依据 semver.org 2.0.0 规范，prerelease 段可包含字母与点号（如 rc.1、alpha.beta.2）。
    本校验器要求主版本号严格三段式数字，prerelease 段非空即可。
    """
    if not version:
        return False
    # 先分离 prerelease（第一个 '-' 之后的内容），prerelease 可含点号
    if "-" in version:
        main, prerelease = version.split("-", 1)
        if not prerelease:
            return False  # 形如 "1.0.0-" 非法
    else:
        main = version
    parts = main.split(".")
    if len(parts) != 3:
        return False
    return all(p.isdigit() for p in parts)


@dataclass
class LineageRecord:
    """血缘记录契约.    记录"谁在什么时候用什么输入产出了什么输出"。"""

    record_id: str
    target: str  # "dataset://my-ds/v1" / "model://ltc-v1"
    source_type: str  # "task" / "workflow" / "manual" / "external"
    source_ref: str  # job_id / workflow_run_id / url
    inputs: list[str] = field(default_factory=list)  # 上游 artifact uri
    outputs: list[str] = field(default_factory=list)
    operation: str = ""  # "train" / "preprocess" / "augment"
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("LineageRecord.record_id 不能为空")
        if not self.target:
            raise ValueError("LineageRecord.target 不能为空")
        valid_source_types = {"task", "workflow", "manual", "external"}
        if self.source_type not in valid_source_types:
            raise ValueError(f"LineageRecord.source_type 不合法: {self.source_type}，合法值: {valid_source_types}")
        if not self.source_ref:
            raise ValueError("LineageRecord.source_ref 不能为空")


class IDatasetStore(ABC):
    """数据集存储契约.

    实现见 app/data/dataset_store.py（阶段 2 交付）。
    元数据存 SQLite，内容存文件系统（按 content_hash 寻址）。
    """

    @abstractmethod
    async def create(
        self,
        name: str,
        schema: DatasetSchema,
        *,
        owner_id: str,
        description: str = "",
    ) -> str:
        """创建数据集（返回 dataset_id）。初始状态 DRAFT，无版本。"""

    @abstractmethod
    async def commit_version(
        self,
        dataset_id: str,
        records: list[dict[str, Any]],
        *,
        version: str | None = None,  # None 则自动递增 patch
        lineage: LineageRecord | None = None,
    ) -> DatasetVersion:
        """提交一个不可变版本.

        计算 content_hash（sha256），写入存储，关联 lineage 记录。
        版本一旦 PUBLISHED 即不可修改（只能 deprecate/archive）。
        """

    @abstractmethod
    async def get_version(self, dataset_id: str, version: str | None = None) -> DatasetVersion:
        """获取版本。version=None 返回最新 published 版本。"""

    @abstractmethod
    def read(
        self,
        dataset_id: str,
        version: str | None = None,
        *,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """流式读取数据集版本内容（按 batch_size 分批）。

        注意：这是 async 生成器（用 ``async for`` 消费），因此声明为
        普通 ``def`` 而非 ``async def``（mypy 类型正确性要求）。
        """

    @abstractmethod
    async def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        """列出数据集的所有版本（按创建时间倒序）。"""

    @abstractmethod
    async def deprecate(self, dataset_id: str, version: str) -> None:
        """将版本标记为 DEPRECATED（不可逆，但内容仍可读）。"""


class ILineageStore(ABC):
    """血缘存储契约.

    实现见 app/data/lineage_store.py（阶段 2 交付）。
    """

    @abstractmethod
    async def record(self, lineage: LineageRecord) -> str:
        """记录一条血缘。返回 record_id。"""

    @abstractmethod
    async def get_upstream(self, target_uri: str, *, depth: int = 10) -> list[LineageRecord]:
        """查询上游血缘（递归到 depth 层）。"""

    @abstractmethod
    async def get_downstream(self, target_uri: str, *, depth: int = 10) -> list[LineageRecord]:
        """查询下游血缘（递归到 depth 层）。"""

    @abstractmethod
    async def visualize(self, target_uri: str) -> dict[str, Any]:
        """返回节点/边数据，前端渲染血缘图.

        返回格式：
            {
                "nodes": [{"id": uri, "label": ..., "type": ...}, ...],
                "edges": [{"source": uri, "target": uri, "operation": ...}, ...]
            }
        """
