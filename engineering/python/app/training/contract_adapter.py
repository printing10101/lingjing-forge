"""TrainingDataLake → IDatasetStore 适配器.

对应 core-contracts-design.md 第 4 章 / ADR-005 阶段 2.

设计目标：
    - 不重写 ``app/training/data_lake.py``，通过适配器模式接入新契约
    - 旧代码可继续向 TrainingDataLake 写入数据
    - 新代码通过 IDatasetStore 契约读取/版本化 TrainingDataLake 数据
    - 元数据走标准 DatasetStore（SQLite + 内容寻址），内容来源为 lake

适配策略：
    - 每次 ``commit_version()`` 时，从 lake 加载全部当前 records，
      计算内容 hash 后写入标准 DatasetStore（去重存储）
    - 一旦 commit 即为不可变快照，后续 lake 的写入不影响已 commit 的版本
    - read / get_version / list_versions / deprecate 直接委托给 DatasetStore
    - ``create()`` 由适配器自动调用一次（注册 dataset，name 固定为 lake 的标识）

兼容性：
    - 适配器是可选的：旧代码用 TrainingDataLake 不变；新代码用适配器获得版本化能力
"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import AsyncIterator

from app.contracts.dataset import (
    DatasetSchema,
    DatasetVersion,
    IDatasetStore,
    LineageRecord,
)
from app.dependencies import get_dataset_store
from app.data.dataset_store import DatasetStore
from app.training.data_lake import TrainingDataLake

logger = logging.getLogger(__name__)


# TrainingDataLake 默认 schema（基于 data_lake.py 实际写入字段）
_DEFAULT_LAKE_SCHEMA = DatasetSchema(
    fields={
        "record_id": {"type": "str", "required": True, "description": "记录唯一 ID"},
        "timestamp": {"type": "datetime", "required": False, "description": "采样时间戳"},
        "material": {"type": "str", "required": False, "description": "材料牌号"},
        "tool_wear": {"type": "float", "required": False, "description": "刀具磨损量"},
        "vibration": {"type": "float", "required": False, "description": "振动信号"},
    },
    primary_key=["record_id"],
    metadata={"source": "training_data_lake"},
)


class TrainingDataLakeAdapter(IDatasetStore):
    """将 TrainingDataLake 适配为 IDatasetStore.

    一个适配器实例对应一个 TrainingDataLake 实例。
    通过 ``dataset_name`` 参数区分不同的 lake（必须唯一）。
    """

    def __init__(
        self,
        lake: TrainingDataLake | None = None,
        *,
        dataset_name: str = "training_data_lake",
        dataset_store: DatasetStore | None = None,
        schema: DatasetSchema | None = None,
        owner_id: str = "system",
    ) -> None:
        self._lake = lake or TrainingDataLake()
        self._dataset_name = dataset_name
        self._store = dataset_store or get_dataset_store()
        self._schema = schema or _DEFAULT_LAKE_SCHEMA
        self._owner_id = owner_id
        self._dataset_id: str | None = None  # 懒注册

    # 内部：懒注册 dataset

    async def _ensure_dataset(self) -> str:
        """确保 dataset 已注册（懒创建），返回 dataset_id."""
        if self._dataset_id is not None:
            return self._dataset_id

        # 尝试按 name 查找已存在的 dataset（通过 list_versions 反查）
        # DatasetStore.create 会因 unique name 抛错，所以先尝试用 owner+name 约定查找
        # 这里采用简单策略：直接尝试 create，捕获 unique 冲突后用稳定 ID
        # 由于 DatasetStore.create 内部对 unique name 有 DB 约束，
        # 我们用一个约定：dataset_id = "lake:" + dataset_name 的 hash
        # 这样多次实例化同 name 的 adapter 也能复用同一 dataset_id
        import hashlib

        stable_id = "lake-" + hashlib.sha256(self._dataset_name.encode("utf-8")).hexdigest()[:16]

        # 尝试 list_versions 来判断 dataset 是否已存在
        try:
            await self._store.get_version(stable_id)
            # 已存在
            self._dataset_id = stable_id
            return stable_id
        except KeyError:
            pass
        except Exception as e:
            # 其他异常（如 DB 未初始化）兜底为 None，由 create 路径处理；
            # 必须留痕：静默吞掉会让"DB 故障"与"数据集不存在"不可区分。
            logger.debug("TrainingDataLakeAdapter: get_version(%s) failed (%s), falling back to create", stable_id, e)

        # 不存在则创建
        try:
            new_id = await self._store.create(
                name=self._dataset_name,
                schema=self._schema,
                owner_id=self._owner_id,
                description=f"TrainingDataLake 适配数据集（{self._lake.storage_dir}）",
            )
            self._dataset_id = new_id
            logger.info(
                "TrainingDataLakeAdapter: 注册新 dataset %s (name=%s)",
                new_id,
                self._dataset_name,
            )
        except Exception as e:
            # unique 约束冲突 复用 stable_id
            logger.warning(
                "TrainingDataLakeAdapter: create 失败（%s），复用 stable_id=%s",
                e,
                stable_id,
            )
            self._dataset_id = stable_id
        return self._dataset_id

    # IDatasetStore 实现

    async def create(
        self,
        name: str,
        schema: DatasetSchema,
        *,
        owner_id: str,
        description: str = "",
    ) -> str:
        """创建新 dataset（委托给底层 DatasetStore）.

        注意：此方法创建的是一个**独立**的新 dataset，与 TrainingDataLake 的
        ``dataset_name`` 无关。若需把 lake 数据接入契约，应使用 ``commit_version()``。
        """
        return await self._store.create(name, schema, owner_id=owner_id, description=description)

    async def commit_version(
        self,
        dataset_id: str,
        records: list[dict[str, Any]],
        *,
        version: str | None = None,
        lineage: LineageRecord | None = None,
    ) -> DatasetVersion:
        """提交版本（委托给底层 DatasetStore）.

        若 ``dataset_id`` 等于适配器自管理的 lake dataset_id，
        且 ``records`` 为空列表，则自动从 lake 加载全部当前 records。
        """
        lake_id = await self._ensure_dataset()
        if dataset_id == lake_id and not records:
            records = self._lake.load_training_samples()
            logger.info(
                "TrainingDataLakeAdapter.commit_version: 从 lake 加载 %d 条记录",
                len(records),
            )
        return await self._store.commit_version(dataset_id, records, version=version, lineage=lineage)

    async def get_version(self, dataset_id: str, version: str | None = None) -> DatasetVersion:
        """获取版本（委托）."""
        return await self._store.get_version(dataset_id, version)

    async def read(
        self,
        dataset_id: str,
        version: str | None = None,
        *,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """读取版本内容（委托）."""
        async for batch in self._store.read(dataset_id, version, batch_size=batch_size):
            yield batch

    async def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        """列出所有版本（委托）."""
        return await self._store.list_versions(dataset_id)

    async def deprecate(self, dataset_id: str, version: str) -> None:
        """废弃版本（委托）."""
        await self._store.deprecate(dataset_id, version)

    # 便捷方法（非契约）

    async def snapshot_lake(
        self,
        *,
        version: str | None = None,
        lineage: LineageRecord | None = None,
    ) -> DatasetVersion:
        """便捷方法：把当前 lake 全量数据快照为一个新版本.

        等价于 ``commit_version(await _ensure_dataset(), [], version=..., lineage=...)``。
        """
        dataset_id = await self._ensure_dataset()
        return await self.commit_version(dataset_id, [], version=version, lineage=lineage)

    def write_training_sample(self, sample: dict[str, Any]) -> bool:
        """便捷方法：直接向 lake 写入一条样本.

        旧代码可继续用此方法写入；新代码读取通过 ``read()`` 读取已 commit 的快照。
        """
        return self._lake.write_training_sample(sample)

    def write_training_samples(self, samples: list[dict[str, Any]]) -> dict[str, int]:
        """便捷方法：批量向 lake 写入样本."""
        return self._lake.write_training_samples(samples)

    @property
    def lake(self) -> TrainingDataLake:
        """访问底层 TrainingDataLake（用于直接操作原始数据）."""
        return self._lake

    @property
    def dataset_id(self) -> str | None:
        """当前适配器注册的 dataset_id（未 commit 前为 None）."""
        return self._dataset_id


# 单例


_adapter: TrainingDataLakeAdapter | None = None


def get_training_data_lake_adapter() -> TrainingDataLakeAdapter:
    """获取全局 TrainingDataLakeAdapter 单例."""
    global _adapter
    if _adapter is None:
        _adapter = TrainingDataLakeAdapter()
    return _adapter


__all__ = [
    "TrainingDataLakeAdapter",
    "get_training_data_lake_adapter",
]
