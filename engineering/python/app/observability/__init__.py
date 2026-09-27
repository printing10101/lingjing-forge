"""可观测模块：trace / metric / log / snapshot 实现.

对应 core-contracts-design.md 第 7 章 / ADR-005 阶段 2.

子模块：
    - git_collector: git SHA + dirty 检测
    - trace: ITraceSink / IMetricSink / ILogSink 实现（内存 + JSONL）
    - snapshot: ISnapshotStore 实现（SQLite + 一键复现）
"""

from app.observability.git_collector import (
    GitCollector,
    GitInfo,
    collect_git_info,
    get_git_collector,
)
from app.observability.snapshot import SnapshotStore, get_snapshot_store
from app.observability.trace import (
    CompositeObservabilitySink,
    LogSink,
    MetricSink,
    TraceSink,
    get_observability_sink,
)

__all__ = [
    # git collector
    "GitCollector",
    "GitInfo",
    "collect_git_info",
    "get_git_collector",
    # trace / metric / log
    "TraceSink",
    "MetricSink",
    "LogSink",
    "CompositeObservabilitySink",
    "get_observability_sink",
    # snapshot
    "SnapshotStore",
    "get_snapshot_store",
]
