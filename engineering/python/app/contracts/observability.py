"""可观测契约：定义 trace/metric/log/snapshot 的统一接口。

对应 core-contracts-design.md 第 7 章。

设计目标：
- 统一埋点格式：任何模块都用同一套 trace/metric/log 接口
- 新增实验快照（git SHA + 数据 hash + 配置 + 模型 + 指标 → 一个不可变 snapshot）
- 新增一键复现入口（从 snapshot 恢复完整实验环境）
- 与 MLflow 集成，但 MLflow 不是唯一后端

稳定性承诺：本文件为 Stable 契约 v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# LogLevel


class LogLevel(str, Enum):
    """结构化日志级别（与 Python logging 标准对齐）。"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# 合法的 span 状态
VALID_SPAN_STATUSES = {"ok", "error"}


# TraceSpan


@dataclass
class TraceSpan:
    """trace span 契约。

    Attributes:
        span_id: span 唯一 ID（建议 UUID 或 16-hex）
        trace_id: 所属 trace 的 ID（同一请求/工作流共享）
        parent_span_id: 父 span ID（根 span 为 None）
        name: span 名称（如 "ltc.train.epoch"）
        start_ts: 起始 Unix 时间戳（秒）
        end_ts: 结束 Unix 时间戳（秒）；未结束时为 None
        attributes: 业务属性（任意可序列化字典）
        events: 事件列表，每项 {"name": str, "ts": float, "payload": dict}
        status: 状态，"ok" 或 "error"
    """

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    name: str = ""
    start_ts: float = 0.0
    end_ts: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"

    def __post_init__(self) -> None:
        if not self.span_id or not isinstance(self.span_id, str):
            raise ValueError("TraceSpan.span_id must be a non-empty string")
        if not self.trace_id or not isinstance(self.trace_id, str):
            raise ValueError("TraceSpan.trace_id must be a non-empty string")
        if self.status not in VALID_SPAN_STATUSES:
            raise ValueError(f"TraceSpan.status must be one of {sorted(VALID_SPAN_STATUSES)}, got {self.status!r}")
        if self.end_ts is not None and self.end_ts < self.start_ts:
            raise ValueError(f"TraceSpan {self.span_id!r}: end_ts {self.end_ts} < start_ts {self.start_ts}")


# Metric


@dataclass
class Metric:
    """metric 契约。

    Attributes:
        name: 指标名（如 "ltc.train.loss"）
        value: 指标值（数值类型）
        timestamp: Unix 时间戳（秒）
        labels: 标签字典（Prometheus 风格，如 {"fold": "1", "epoch": "3"}）
        unit: 单位（如 "ms"/"loss"/"accuracy"）
    """

    name: str
    value: float
    timestamp: float
    labels: dict[str, str] = field(default_factory=dict)
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Metric.name must be a non-empty string")
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise ValueError(f"Metric {self.name!r}: value must be numeric, got {type(self.value).__name__}")
        # 强制转 float（统一序列化）
        self.value = float(self.value)
        if not isinstance(self.timestamp, (int, float)) or isinstance(self.timestamp, bool):
            raise ValueError(f"Metric {self.name!r}: timestamp must be numeric, got {type(self.timestamp).__name__}")
        self.timestamp = float(self.timestamp)


# LogEntry


@dataclass
class LogEntry:
    """结构化日志契约。

    Attributes:
        timestamp: Unix 时间戳（秒）
        level: 日志级别
        message: 日志消息
        logger: logger 名（通常为模块路径）
        attributes: 附加属性（任意可序列化字典）
        trace_id: 关联 trace ID（用于日志关联追踪）
        span_id: 关联 span ID
    """

    timestamp: float
    level: LogLevel
    message: str
    logger: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, (int, float)) or isinstance(self.timestamp, bool):
            raise ValueError(f"LogEntry.timestamp must be numeric, got {type(self.timestamp).__name__}")
        self.timestamp = float(self.timestamp)
        # 兼容字符串形式（便于从 JSON 反序列化）
        if isinstance(self.level, str):
            try:
                self.level = LogLevel(self.level)
            except ValueError as e:
                raise ValueError(f"LogEntry.level must be a valid LogLevel, got {self.level!r}") from e
        if not isinstance(self.level, LogLevel):
            raise ValueError(f"LogEntry.level must be LogLevel or valid string, got {self.level!r}")
        if not isinstance(self.message, str):
            raise ValueError("LogEntry.message must be a string")


# ExperimentSnapshot


@dataclass
class ExperimentSnapshot:
    """实验快照契约（一键复现的最小单元）。

    Attributes:
        snapshot_id: 快照唯一 ID
        created_at: 创建时间
        created_by: 创建者（用户 ID 或 "system"）
        git_sha: 代码 git commit SHA
        code_dirty: 是否有未提交修改（True 时复现结果不可保证）
        config: 完整实验配置（已 materialize 的字典）
        dataset_versions: 数据集版本列表，形如 ["dataset://xxx/v1"]
        model_uri: 模型 URI，形如 "model://ltc-v1"
        metrics: 关键指标字典
        environment: 环境信息（python 版本/关键包版本）
        lineage_record_id: 关联的血缘记录 ID
        mlflow_run_id: 关联 MLflow run ID（可选）
        notes: 备注
    """

    snapshot_id: str
    created_at: datetime
    created_by: str
    git_sha: str
    code_dirty: bool
    config: dict[str, Any]
    dataset_versions: list[str]
    model_uri: str
    metrics: dict[str, float]
    environment: dict[str, str]
    lineage_record_id: str | None = None
    mlflow_run_id: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id or not isinstance(self.snapshot_id, str):
            raise ValueError("ExperimentSnapshot.snapshot_id must be a non-empty string")
        if not isinstance(self.created_at, datetime):
            raise ValueError(f"ExperimentSnapshot.created_at must be datetime, got {type(self.created_at).__name__}")
        if not self.created_by or not isinstance(self.created_by, str):
            raise ValueError("ExperimentSnapshot.created_by must be a non-empty string")
        if not self.git_sha or not isinstance(self.git_sha, str):
            raise ValueError("ExperimentSnapshot.git_sha must be a non-empty string")
        if not isinstance(self.code_dirty, bool):
            raise ValueError("ExperimentSnapshot.code_dirty must be bool")
        if not isinstance(self.config, dict):
            raise ValueError("ExperimentSnapshot.config must be a dict")
        if not isinstance(self.dataset_versions, list):
            raise ValueError("ExperimentSnapshot.dataset_versions must be a list")
        if not self.model_uri or not isinstance(self.model_uri, str):
            raise ValueError("ExperimentSnapshot.model_uri must be a non-empty string")
        if not isinstance(self.metrics, dict):
            raise ValueError("ExperimentSnapshot.metrics must be a dict")
        if not isinstance(self.environment, dict):
            raise ValueError("ExperimentSnapshot.environment must be a dict")
        # 强制 metrics 值转 float
        for k, v in self.metrics.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"ExperimentSnapshot.metrics[{k!r}] must be numeric, got {type(v).__name__}")
        self.metrics = {k: float(v) for k, v in self.metrics.items()}


# ITraceSink


class ITraceSink(ABC):
    """trace sink 契约。

    实现方负责管理 span 的生命周期与持久化。线程安全要求由实现方保证。
    """

    @abstractmethod
    def start_span(self, name: str, parent: str | None = None) -> str:
        """开启一个 span，返回 span_id。

        Args:
            name: span 名称
            parent: 父 span ID；None 表示根 span

        Returns:
            新 span 的 ID
        """

    @abstractmethod
    def end_span(self, span_id: str, status: str = "ok") -> None:
        """结束一个 span。

        Args:
            span_id: span ID
            status: "ok" 或 "error"
        """

    @abstractmethod
    def add_attribute(self, span_id: str, key: str, value: Any) -> None:
        """为 span 添加属性。"""

    @abstractmethod
    def add_event(self, span_id: str, name: str, payload: dict[str, Any]) -> None:
        """为 span 添加事件。"""


# IMetricSink


class IMetricSink(ABC):
    """metric sink 契约。

    实现方负责把指标推送到后端（Prometheus / MLflow / 文件）。
    """

    @abstractmethod
    def counter(self, name: str, value: float = 1, labels: dict[str, str] | None = None) -> None:
        """递增计数器。"""

    @abstractmethod
    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """设置 gauge 当前值。"""

    @abstractmethod
    def histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """记录 histogram 样本。"""


# ILogSink


class ILogSink(ABC):
    """log sink 契约。

    实现方负责把日志写入后端（文件 / stdout / 远程日志服务）。
    必须实现敏感数据脱敏（与 LogSanitizer 集成）。
    """

    @abstractmethod
    def log(self, entry: LogEntry) -> None:
        """写入一条结构化日志。"""


# ISnapshotStore


class ISnapshotStore(ABC):
    """实验快照存储契约。

    实现方负责：
    - 自动采集 git_sha / environment
    - 持久化 snapshot 到数据库 / 文件
    - 提供 reproduce 入口（与 IWorkflowRunner 集成）
    """

    @abstractmethod
    async def create(
        self,
        *,
        config: dict[str, Any],
        dataset_versions: list[str],
        model_uri: str,
        metrics: dict[str, float],
        created_by: str,
        notes: str = "",
    ) -> ExperimentSnapshot:
        """创建快照，自动采集 git_sha / environment，写入存储。

        Args:
            config: 完整实验配置
            dataset_versions: 数据集版本 URI 列表
            model_uri: 模型 URI
            metrics: 关键指标
            created_by: 创建者
            notes: 备注

        Returns:
            已持久化的 ExperimentSnapshot（含 snapshot_id）
        """

    @abstractmethod
    async def get(self, snapshot_id: str) -> ExperimentSnapshot:
        """按 ID 取快照，不存在抛 KeyError。"""

    @abstractmethod
    async def list(self, *, filters: dict[str, Any] | None = None) -> list[ExperimentSnapshot]:
        """列出快照，可选过滤。

        Args:
            filters: 过滤条件，如 {"created_by": "alice", "git_sha": "abc123"}

        Returns:
            快照列表（按 created_at 降序）
        """

    @abstractmethod
    async def reproduce(self, snapshot_id: str) -> str:
        """根据 snapshot 恢复环境并启动复现任务。

        Args:
            snapshot_id: 快照 ID

        Returns:
            workflow_run_id（复现工作流的运行 ID）
        """


# IObservabilitySink


class IObservabilitySink(ITraceSink, IMetricSink, ILogSink, ISnapshotStore):
    """可观测统一入口。

    业务模块通过此接口埋点，无需关心后端。实现方通常组合多个独立 sink，
    例如：
        class CompositeObservabilitySink(IObservabilitySink):
            def __init__(self, trace, metric, log, snapshot):
                self._trace = trace
                self._metric = metric
                self._log = log
                self._snapshot = snapshot
            # ... 委托给各个 sink

    这是契约层，不提供默认实现。
    """

    pass


__all__ = [
    "LogLevel",
    "TraceSpan",
    "Metric",
    "LogEntry",
    "ExperimentSnapshot",
    "ITraceSink",
    "IMetricSink",
    "ILogSink",
    "ISnapshotStore",
    "IObservabilitySink",
    "VALID_SPAN_STATUSES",
]
