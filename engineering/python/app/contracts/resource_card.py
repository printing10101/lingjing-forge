"""资源卡片契约：定义模型产物、数据集 README、lineage 摘要与卡片聚合视图.

对应 ADR-012（资源卡片）。本文件只定义数据结构与接口契约，
实现见 app/services/resource_card_service.py（服务层）、
app/api/v1/resource_cards.py（路由层）、
app/database/models/resource_card.py（ORM 持久化）。

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点：
    1. 不修改现有 Dataset / DatasetVersion / LineageRecord 契约（ADR-005 Stable），
       新增 dataset_readmes 表承载可编辑 README，新增 model_artifacts 表承载模型产物元数据
    2. ModelArtifact 通过 model_uri（model://<name>/<version>）与 ADR-011 项目同步对齐
    3. LineageSummary 不返回全图，而是按层分组（BFS，每层最多 10 节点）+ 关键路径，
       避免卡片渲染压力；需要全图时调用 ILineageStore.visualize()
    4. DatasetCard / ModelCard 是聚合视图，由服务层调用 IDatasetStore / ILineageStore /
       ISnapshotStore 拼接，前端单次请求获取完整卡片
    5. ModelArtifactStatus 状态机与 DatasetStatus 对齐（draft/published/deprecated/archived）

资源 URI 体系（与 ADR-005 / ADR-011 对齐）：
    model://<model_name>/<version>
    dataset://<dataset_id>/<version>
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# 模型产物类型常量


class ModelArtifactType:
    """模型产物类型常量.

    用于 model_artifacts.model_type 字段，决定模型文件的加载方式与框架依赖。
    与 ModelRegistryService 支持的模型类型对齐。
    """

    LNN = "lnn"  # 液态神经网络（CfC / LTC，PyTorch 实现）
    PYTORCH = "pytorch"  # 通用 PyTorch 模型（.pt / .pth）
    ONNX = "onnx"  # ONNX 格式（跨框架推理）
    SKLEARN = "sklearn"  # scikit-learn 模型（joblib pickle）
    OTHER = "other"  # 其他格式（TensorFlow / JAX / 自定义）

    @classmethod
    def all(cls) -> list[str]:
        """返回所有模型产物类型."""
        return [cls.LNN, cls.PYTORCH, cls.ONNX, cls.SKLEARN, cls.OTHER]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断模型产物类型是否合法."""
        return value in cls.all()


# 模型产物状态常量


class ModelArtifactStatus:
    """模型产物状态常量.

    状态机（与 DatasetStatus 对齐）：
        DRAFT → PUBLISHED（不可变，发布后内容固定）
        PUBLISHED → DEPRECATED → ARCHIVED
        DRAFT → ARCHIVED（直接归档未发布模型）

    PUBLISHED 状态的模型可被生产环境引用；DEPRECATED 仍可推理但不推荐新用途；
    ARCHIVED 仅保留历史记录，不参与推理。
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有模型产物状态."""
        return [cls.DRAFT, cls.PUBLISHED, cls.DEPRECATED, cls.ARCHIVED]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断模型产物状态是否合法."""
        return value in cls.all()


# 合法状态转换（与 DatasetStatus 对齐：PUBLISHED 之后内容不可变，只能改状态）
VALID_MODEL_STATUS_TRANSITIONS: dict[str, set[str]] = {
    ModelArtifactStatus.DRAFT: {
        ModelArtifactStatus.PUBLISHED,
        ModelArtifactStatus.ARCHIVED,
    },
    ModelArtifactStatus.PUBLISHED: {
        ModelArtifactStatus.DEPRECATED,
        ModelArtifactStatus.ARCHIVED,
    },
    ModelArtifactStatus.DEPRECATED: {ModelArtifactStatus.ARCHIVED},
    ModelArtifactStatus.ARCHIVED: set(),
}


# 数据集 README 作用域常量


class DatasetReadmeScope:
    """数据集 README 作用域常量.

    决定 dataset_readmes.version 字段的语义：
        - DATASET_LEVEL: version=None，表示整个数据集的 README（默认展示）
        - VERSION_LEVEL: version="1.0.0"，表示特定版本的 README（覆盖数据集级）
    """

    DATASET_LEVEL = "dataset_level"
    VERSION_LEVEL = "version_level"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有 README 作用域."""
        return [cls.DATASET_LEVEL, cls.VERSION_LEVEL]

    @classmethod
    def from_version(cls, version: str | None) -> str:
        """根据 version 字段推断作用域.

        Args:
            version: 版本号，None 表示数据集级 README

        Returns:
            DATASET_LEVEL 或 VERSION_LEVEL
        """
        return cls.VERSION_LEVEL if version else cls.DATASET_LEVEL


# 数据结构


def _is_valid_semver(version: str) -> bool:
    """校验 semver 格式：MAJOR.MINOR.PATCH（可选 -prerelease）.

    与 dataset.py 中的 _is_valid_semver 对齐，prerelease 段可含字母与点号。
    """
    if not version:
        return False
    if "-" in version:
        main, prerelease = version.split("-", 1)
        if not prerelease:
            return False
    else:
        main = version
    parts = main.split(".")
    if len(parts) != 3:
        return False
    return all(p.isdigit() for p in parts)


@dataclass
class ModelArtifact:
    """模型产物契约.

    持久化到 model_artifacts 表，承载模型元数据 + 指标 + README + 标签。
    model_uri 是唯一标识（model://<name>/<version>），与 ADR-011 项目同步对齐。
    """

    model_id: str  # mdl_ 前缀 + uuid
    model_uri: str  # model://<name>/<version>
    name: str  # 显示名（如 "LTC-ChatterPredictor"）
    model_type: str  # ModelArtifactType 常量
    version: str  # semver，如 "1.0.0"
    framework: str  # 框架版本，如 "torch-2.1.0"
    storage_uri: str  # 模型文件存储位置（file:// / s3:// / model://path）
    owner_id: str  # 所有者 user_id 或 plugin_id
    status: str = ModelArtifactStatus.DRAFT
    metrics: dict[str, Any] = field(default_factory=dict)  # 当前指标快照
    metrics_history: list[dict[str, Any]] = field(default_factory=list)  # 指标历史（追加式）
    readme_md: str = ""  # markdown README
    tags: list[str] = field(default_factory=list)  # 标签数组
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("ModelArtifact.model_id 不能为空")
        if not self.model_uri:
            raise ValueError("ModelArtifact.model_uri 不能为空")
        if not self.model_uri.startswith("model://"):
            raise ValueError(f"ModelArtifact.model_uri 必须以 model:// 开头: {self.model_uri}")
        if not self.name:
            raise ValueError("ModelArtifact.name 不能为空")
        if not ModelArtifactType.is_valid(self.model_type):
            raise ValueError(f"ModelArtifact.model_type 不合法: {self.model_type}，合法值: {ModelArtifactType.all()}")
        if not _is_valid_semver(self.version):
            raise ValueError(f"ModelArtifact.version 不是合法 semver: {self.version}")
        if not self.framework:
            raise ValueError("ModelArtifact.framework 不能为空")
        if not self.storage_uri:
            raise ValueError("ModelArtifact.storage_uri 不能为空")
        if not self.owner_id:
            raise ValueError("ModelArtifact.owner_id 不能为空")
        if not ModelArtifactStatus.is_valid(self.status):
            raise ValueError(f"ModelArtifact.status 不合法: {self.status}，合法值: {ModelArtifactStatus.all()}")

    def can_transition_to(self, new_status: str) -> bool:
        """检查状态转换是否合法."""
        return new_status in VALID_MODEL_STATUS_TRANSITIONS.get(self.status, set())

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应与 JSON 持久化）."""
        return {
            "model_id": self.model_id,
            "model_uri": self.model_uri,
            "name": self.name,
            "model_type": self.model_type,
            "version": self.version,
            "framework": self.framework,
            "storage_uri": self.storage_uri,
            "owner_id": self.owner_id,
            "status": self.status,
            "metrics": dict(self.metrics),
            "metrics_history": list(self.metrics_history),
            "readme_md": self.readme_md,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class DatasetReadme:
    """数据集 README 契约.

    持久化到 dataset_readmes 表，支持数据集级（version=None）与版本级（version="1.0.0"）README。
    版本级 README 覆盖数据集级，前端展示时优先取版本级，回退到数据集级。
    """

    readme_id: str  # readme_ 前缀 + uuid
    dataset_id: str  # 关联 datasets.id
    readme_md: str  # markdown 内容
    updated_by: str  # 最后更新者 user_id
    version: str | None = None  # None 表示数据集级 README
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.readme_id:
            raise ValueError("DatasetReadme.readme_id 不能为空")
        if not self.dataset_id:
            raise ValueError("DatasetReadme.dataset_id 不能为空")
        if not self.readme_md:
            raise ValueError("DatasetReadme.readme_md 不能为空")
        if not self.updated_by:
            raise ValueError("DatasetReadme.updated_by 不能为空")
        if self.version is not None and not _is_valid_semver(self.version):
            raise ValueError(f"DatasetReadme.version 不是合法 semver: {self.version}")

    @property
    def scope(self) -> str:
        """返回 README 作用域（DATASET_LEVEL / VERSION_LEVEL）."""
        return DatasetReadmeScope.from_version(self.version)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典."""
        return {
            "readme_id": self.readme_id,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "scope": self.scope,
            "readme_md": self.readme_md,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class LineageSummary:
    """血缘摘要契约.

    卡片视图的轻量血缘概览，避免全图渲染压力。

    字段说明：
        - upstream_count / downstream_count：全量计数（不限 depth）
        - upstream_layers / downstream_layers：按层分组的节点 URI（BFS），
          每层最多 10 个节点，超出部分仅在 count 中体现
        - key_path：target 到根节点的最短路径（用于卡片侧栏展示）
        - total_nodes：上游 + 下游 + target 自身的总节点数
    """

    target_uri: str  # 卡片目标的资源 URI
    upstream_count: int  # 上游节点总数（全量，不限 depth）
    downstream_count: int  # 下游节点总数（全量，不限 depth）
    upstream_layers: list[list[str]] = field(default_factory=list)  # [[layer1_uris], [layer2_uris], ...]
    downstream_layers: list[list[str]] = field(default_factory=list)
    key_path: list[str] = field(default_factory=list)  # target → 根的最短路径
    total_nodes: int = 0  # 总节点数（含 target）

    def __post_init__(self) -> None:
        if not self.target_uri:
            raise ValueError("LineageSummary.target_uri 不能为空")
        if self.upstream_count < 0:
            raise ValueError(f"LineageSummary.upstream_count 不能为负数: {self.upstream_count}")
        if self.downstream_count < 0:
            raise ValueError(f"LineageSummary.downstream_count 不能为负数: {self.downstream_count}")
        if self.total_nodes < 0:
            raise ValueError(f"LineageSummary.total_nodes 不能为负数: {self.total_nodes}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典."""
        return {
            "target_uri": self.target_uri,
            "upstream_count": self.upstream_count,
            "downstream_count": self.downstream_count,
            "upstream_layers": [list(layer) for layer in self.upstream_layers],
            "downstream_layers": [list(layer) for layer in self.downstream_layers],
            "key_path": list(self.key_path),
            "total_nodes": self.total_nodes,
        }


@dataclass
class DatasetCard:
    """数据集卡片契约（聚合视图）.

    由 ResourceCardService.get_dataset_card() 聚合以下数据源拼接而成：
        - dataset：IDatasetStore.get_dataset() 返回的元数据
        - latest_version：IDatasetStore.list_versions() 的最新版本
        - version_count / total_rows / total_size：从版本列表汇总
        - readme：DatasetReadme（优先版本级，回退数据集级，再回退 description）
        - lineage_summary：LineageSummary（target_uri = dataset://<id>/<version>）
    """

    dataset_id: str
    name: str
    description: str  # 原始 description（短文本）
    owner_id: str
    status: str  # DatasetStatus 值
    schema: dict[str, Any]  # DatasetSchema 序列化
    version_count: int  # 版本总数
    total_rows: int  # 所有版本累计行数
    total_size_bytes: int  # 所有版本累计字节数
    latest_version: dict[str, Any] | None = None  # 最新版本元数据
    readme: DatasetReadme | None = None  # README（None 表示未设置）
    lineage_summary: LineageSummary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise ValueError("DatasetCard.dataset_id 不能为空")
        if not self.name:
            raise ValueError("DatasetCard.name 不能为空")
        if not self.owner_id:
            raise ValueError("DatasetCard.owner_id 不能为空")
        if self.version_count < 0:
            raise ValueError(f"DatasetCard.version_count 不能为负数: {self.version_count}")
        if self.total_rows < 0:
            raise ValueError(f"DatasetCard.total_rows 不能为负数: {self.total_rows}")
        if self.total_size_bytes < 0:
            raise ValueError(f"DatasetCard.total_size_bytes 不能为负数: {self.total_size_bytes}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "status": self.status,
            "schema": dict(self.schema),
            "version_count": self.version_count,
            "total_rows": self.total_rows,
            "total_size_bytes": self.total_size_bytes,
            "latest_version": dict(self.latest_version) if self.latest_version else None,
            "readme": self.readme.to_dict() if self.readme else None,
            "lineage_summary": (self.lineage_summary.to_dict() if self.lineage_summary else None),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ModelCard:
    """模型卡片契约（聚合视图）.

    由 ResourceCardService.get_model_card() 聚合以下数据源拼接而成：
        - model_artifact：ModelArtifact 元数据
        - snapshot_count：关联该 model_uri 的 ExperimentSnapshot 数量
        - lineage_summary：LineageSummary（target_uri = model_uri）
        - metrics_history：从 model_artifact.metrics_history 取出，按时间排序
    """

    model: ModelArtifact  # 模型产物元数据
    snapshot_count: int  # 关联该模型的实验快照数
    lineage_summary: LineageSummary | None = None
    latest_snapshot: dict[str, Any] | None = None  # 最近一次快照摘要

    def __post_init__(self) -> None:
        if self.snapshot_count < 0:
            raise ValueError(f"ModelCard.snapshot_count 不能为负数: {self.snapshot_count}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "model": self.model.to_dict(),
            "snapshot_count": self.snapshot_count,
            "lineage_summary": (self.lineage_summary.to_dict() if self.lineage_summary else None),
            "latest_snapshot": (dict(self.latest_snapshot) if self.latest_snapshot else None),
        }


# 卡片聚合服务接口（可选实现，供插件扩展）


class IResourceCardService:
    """资源卡片聚合服务契约（接口占位，便于插件扩展与测试 mock）.

    实现见 app/services/resource_card_service.py。
    本接口仅定义方法签名，具体实现由 ResourceCardService 单例提供。

    所有方法均为 async，因为内部调用 IDatasetStore / ILineageStore / ISnapshotStore
    这些异步契约。
    """

    async def get_dataset_card(
        self,
        dataset_id: str,
        *,
        include_lineage: bool = True,
        lineage_depth: int = 3,
    ) -> DatasetCard:
        """获取数据集卡片（聚合 Dataset + Version 指标 + README + lineage 摘要）."""
        raise NotImplementedError

    async def get_model_card(
        self,
        model_id: str,
        *,
        include_lineage: bool = True,
        lineage_depth: int = 3,
    ) -> ModelCard:
        """获取模型卡片（聚合 ModelArtifact + Snapshot 数 + lineage 摘要）."""
        raise NotImplementedError

    async def get_lineage_summary(
        self,
        target_uri: str,
        *,
        max_depth: int = 3,
        max_nodes_per_layer: int = 10,
    ) -> LineageSummary:
        """获取 lineage 摘要（按层分组 + 关键路径）."""
        raise NotImplementedError


__all__ = [
    # 常量类
    "ModelArtifactType",
    "ModelArtifactStatus",
    "DatasetReadmeScope",
    # 状态转换表
    "VALID_MODEL_STATUS_TRANSITIONS",
    # 数据结构
    "ModelArtifact",
    "DatasetReadme",
    "LineageSummary",
    "DatasetCard",
    "ModelCard",
    # 服务接口
    "IResourceCardService",
]
