"""可观测 sink 实现：trace / metric / log 的内存 + JSONL 文件后端.

对应 core-contracts-design.md 第 7 章 / ADR-005 阶段 2.

设计要点：
    1. 三种 sink（ITraceSink / IMetricSink / ILogSink）独立实现，可单独使用
    2. 内存中维护有界缓冲（LRU 淘汰），便于近实时查询
    3. 可选 JSONL 文件持久化，每个 span/metric/log 一行 JSON
    4. 线程安全：所有写操作使用 threading.RLock
    5. 敏感数据脱敏：与 LogSanitizer 集成（如可用）

不依赖网络/外部服务，适合桌面应用本地部署。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from app.contracts.observability import (
    ILogSink,
    IMetricSink,
    ITraceSink,
    LogEntry,
    LogLevel,
    Metric,
    TraceSpan,
    VALID_SPAN_STATUSES,
)

logger = logging.getLogger(__name__)


# 默认内存缓冲上限（每类）
_DEFAULT_MAX_SPANS = 10000
_DEFAULT_MAX_METRICS_PER_NAME = 10000
_DEFAULT_MAX_LOGS = 50000
_DEFAULT_FLUSH_INTERVAL = 100  # 每 N 条刷盘一次


class TraceSink(ITraceSink):
    """trace sink 内存 + JSONL 文件实现."""

    def __init__(
        self,
        *,
        max_spans: int = _DEFAULT_MAX_SPANS,
        log_file: str | None = None,
    ) -> None:
        self._spans: dict[str, TraceSpan] = {}
        self._max_spans = max(100, max_spans)
        self._log_file = log_file
        self._lock = threading.RLock()
        self._pending_writes: list[TraceSpan] = []
        self._flush_threshold = _DEFAULT_FLUSH_INTERVAL

        if log_file:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)

    # ITraceSink 实现

    def start_span(self, name: str, parent: str | None = None) -> str:
        """开启一个 span，返回 span_id."""
        span_id = uuid.uuid4().hex
        span = TraceSpan(
            span_id=span_id,
            trace_id=parent or span_id,  # 简化：parent 为空时 trace_id = span_id
            parent_span_id=parent,
            name=name,
            start_ts=time.time(),
        )
        with self._lock:
            self._spans[span_id] = span
            self._enforce_bounds_locked()
            self._pending_writes.append(span)
            if len(self._pending_writes) >= self._flush_threshold:
                self._flush_locked()
        return span_id

    def end_span(self, span_id: str, status: str = "ok") -> None:
        """结束一个 span."""
        if status not in VALID_SPAN_STATUSES:
            raise ValueError(f"TraceSink.end_span: status 必须是 {sorted(VALID_SPAN_STATUSES)}, 得到 {status!r}")
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                logger.warning("TraceSink.end_span: span %s 不存在", span_id)
                return
            span.end_ts = time.time()
            span.status = status
            self._pending_writes.append(span)
            if len(self._pending_writes) >= self._flush_threshold:
                self._flush_locked()

    def add_attribute(self, span_id: str, key: str, value: Any) -> None:
        """为 span 添加属性."""
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                logger.warning("TraceSink.add_attribute: span %s 不存在", span_id)
                return
            span.attributes[key] = value

    def add_event(self, span_id: str, name: str, payload: dict[str, Any]) -> None:
        """为 span 添加事件."""
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                logger.warning("TraceSink.add_event: span %s 不存在", span_id)
                return
            span.events.append({"name": name, "ts": time.time(), "payload": payload})

    # 查询 API（非契约部分，便于调试/前端拉取）

    def get_span(self, span_id: str) -> TraceSpan | None:
        """获取 span（不存在返回 None）."""
        with self._lock:
            return self._spans.get(span_id)

    def list_spans(self, *, trace_id: str | None = None, limit: int = 100) -> list[TraceSpan]:
        """列出 span（按 start_ts 倒序）."""
        with self._lock:
            spans = list(self._spans.values())
        if trace_id:
            spans = [s for s in spans if s.trace_id == trace_id]
        spans.sort(key=lambda s: s.start_ts, reverse=True)
        return spans[:limit]

    def flush(self) -> None:
        """强制刷盘待写 span."""
        with self._lock:
            self._flush_locked()

    # 内部

    def _enforce_bounds_locked(self) -> None:
        """超过上限时按 FIFO 淘汰最老 span."""
        while len(self._spans) > self._max_spans:
            # dict 保留插入顺序，弹出第一个
            oldest_id = next(iter(self._spans))
            self._spans.pop(oldest_id, None)

    def _flush_locked(self) -> None:
        """将 pending writes 写入 JSONL 文件（持有锁）."""
        if not self._log_file or not self._pending_writes:
            return
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                for span in self._pending_writes:
                    f.write(_span_to_jsonl(span) + "\n")
            self._pending_writes.clear()
        except OSError as e:
            logger.warning("TraceSink: 写入 JSONL 失败: %s", e)


class MetricSink(IMetricSink):
    """metric sink 内存 + JSONL 文件实现（Prometheus 风格 + 时序存储）."""

    def __init__(
        self,
        *,
        max_per_name: int = _DEFAULT_MAX_METRICS_PER_NAME,
        log_file: str | None = None,
    ) -> None:
        self._series: dict[str, deque[Metric]] = defaultdict(deque)
        self._max_per_name = max(100, max_per_name)
        self._log_file = log_file
        self._lock = threading.RLock()
        self._pending_writes: list[Metric] = []

        if log_file:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)

    # IMetricSink 实现

    def counter(self, name: str, value: float = 1, labels: dict[str, str] | None = None) -> None:
        """递增计数器."""
        self._record(name, value, labels, unit="counter")

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """设置 gauge 当前值."""
        self._record(name, value, labels, unit="gauge")

    def histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """记录 histogram 样本."""
        self._record(name, value, labels, unit="histogram")

    # 查询 API

    def list_metrics(
        self,
        *,
        name: str | None = None,
        limit: int = 100,
    ) -> list[Metric]:
        """列出 metric（按 timestamp 倒序）."""
        with self._lock:
            if name:
                metrics = list(self._series.get(name, deque()))
            else:
                metrics = []
                for q in self._series.values():
                    metrics.extend(q)
        metrics.sort(key=lambda m: m.timestamp, reverse=True)
        return metrics[:limit]

    def flush(self) -> None:
        """强制刷盘."""
        with self._lock:
            self._flush_locked()

    # 内部

    def _record(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None,
        *,
        unit: str,
    ) -> None:
        metric = Metric(
            name=name,
            value=value,
            timestamp=time.time(),
            labels=labels or {},
            unit=unit,
        )
        with self._lock:
            q = self._series[name]
            q.append(metric)
            while len(q) > self._max_per_name:
                q.popleft()
            self._pending_writes.append(metric)
            if len(self._pending_writes) >= _DEFAULT_FLUSH_INTERVAL:
                self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._log_file or not self._pending_writes:
            return
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                for m in self._pending_writes:
                    f.write(_metric_to_jsonl(m) + "\n")
            self._pending_writes.clear()
        except OSError as e:
            logger.warning("MetricSink: 写入 JSONL 失败: %s", e)


class LogSink(ILogSink):
    """log sink 内存 + JSONL 文件实现.

    集成 LogSanitizer（如可用）做敏感数据脱敏。
    """

    def __init__(
        self,
        *,
        max_logs: int = _DEFAULT_MAX_LOGS,
        log_file: str | None = None,
    ) -> None:
        self._logs: deque[LogEntry] = deque()
        self._max_logs = max(100, max_logs)
        self._log_file = log_file
        self._lock = threading.RLock()
        self._sanitizer = _load_sanitizer()

        if log_file:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)

    # ILogSink 实现

    def log(self, entry: LogEntry) -> None:
        """写入一条结构化日志."""
        sanitized = self._sanitize(entry)
        with self._lock:
            self._logs.append(sanitized)
            while len(self._logs) > self._max_logs:
                self._logs.popleft()
            self._append_to_file_locked(sanitized)

    # 查询 API

    def list_logs(
        self,
        *,
        level: LogLevel | None = None,
        logger_name: str | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[LogEntry]:
        """列出日志（按 timestamp 倒序）."""
        with self._lock:
            logs = list(self._logs)
        if level:
            logs = [entry for entry in logs if entry.level == level]
        if logger_name:
            logs = [entry for entry in logs if entry.logger == logger_name]
        if trace_id:
            logs = [entry for entry in logs if entry.trace_id == trace_id]
        logs.sort(key=lambda entry: entry.timestamp, reverse=True)
        return logs[:limit]

    # 内部

    def _sanitize(self, entry: LogEntry) -> LogEntry:
        """脱敏处理（如 LogSanitizer 可用）."""
        if self._sanitizer is None:
            return entry
        try:
            sanitized_msg = self._sanitizer.sanitize(entry.message)
            sanitized_attrs = self._sanitizer.sanitize_dict(entry.attributes)
            return LogEntry(
                timestamp=entry.timestamp,
                level=entry.level,
                message=sanitized_msg,
                logger=entry.logger,
                attributes=sanitized_attrs,
                trace_id=entry.trace_id,
                span_id=entry.span_id,
            )
        except Exception as e:
            logger.warning("LogSink: 脱敏失败（原样保留）: %s", e)
            return entry

    def _append_to_file_locked(self, entry: LogEntry) -> None:
        if not self._log_file:
            return
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(_log_to_jsonl(entry) + "\n")
        except OSError as e:
            logger.warning("LogSink: 写入 JSONL 失败: %s", e)


# 组合 sink


class CompositeObservabilitySink(ITraceSink, IMetricSink, ILogSink):
    """组合可观测 sink（trace + metric + log 三合一）.

    不实现 ISnapshotStore（snapshot 由 SnapshotStore 单独管理，
    因为它需要异步 DB 操作，与同步的 trace/metric/log 不便耦合）。
    """

    def __init__(
        self,
        trace_sink: TraceSink | None = None,
        metric_sink: MetricSink | None = None,
        log_sink: LogSink | None = None,
    ) -> None:
        self._trace = trace_sink or TraceSink()
        self._metric = metric_sink or MetricSink()
        self._log = log_sink or LogSink()

    # ITraceSink
    def start_span(self, name: str, parent: str | None = None) -> str:
        return self._trace.start_span(name, parent)

    def end_span(self, span_id: str, status: str = "ok") -> None:
        self._trace.end_span(span_id, status)

    def add_attribute(self, span_id: str, key: str, value: Any) -> None:
        self._trace.add_attribute(span_id, key, value)

    def add_event(self, span_id: str, name: str, payload: dict[str, Any]) -> None:
        self._trace.add_event(span_id, name, payload)

    # IMetricSink
    def counter(self, name: str, value: float = 1, labels: dict[str, str] | None = None) -> None:
        self._metric.counter(name, value, labels)

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self._metric.gauge(name, value, labels)

    def histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self._metric.histogram(name, value, labels)

    # ILogSink
    def log(self, entry: LogEntry) -> None:
        self._log.log(entry)

    # 便捷访问
    @property
    def trace(self) -> TraceSink:
        return self._trace

    @property
    def metric(self) -> MetricSink:
        return self._metric

    @property
    def log_sink(self) -> LogSink:
        return self._log

    def flush(self) -> None:
        """强制刷盘所有 sink."""
        self._trace.flush()
        self._metric.flush()


# 序列化辅助


def _span_to_jsonl(span: TraceSpan) -> str:
    """span → JSONL 行."""
    return json.dumps(
        {
            "type": "span",
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "start_ts": span.start_ts,
            "end_ts": span.end_ts,
            "attributes": span.attributes,
            "events": span.events,
            "status": span.status,
        },
        ensure_ascii=False,
        default=str,
    )


def _metric_to_jsonl(metric: Metric) -> str:
    """metric → JSONL 行."""
    return json.dumps(
        {
            "type": "metric",
            "name": metric.name,
            "value": metric.value,
            "timestamp": metric.timestamp,
            "labels": metric.labels,
            "unit": metric.unit,
        },
        ensure_ascii=False,
        default=str,
    )


def _log_to_jsonl(entry: LogEntry) -> str:
    """log → JSONL 行."""
    return json.dumps(
        {
            "type": "log",
            "timestamp": entry.timestamp,
            "level": entry.level.value if isinstance(entry.level, LogLevel) else str(entry.level),
            "message": entry.message,
            "logger": entry.logger,
            "attributes": entry.attributes,
            "trace_id": entry.trace_id,
            "span_id": entry.span_id,
        },
        ensure_ascii=False,
        default=str,
    )


def _load_sanitizer():
    """尝试加载 LogSanitizer（不可用时返回 None）."""
    try:
        from app.utils.log_sanitizer import LogSanitizer

        return LogSanitizer()
    except Exception:
        return None


# 单例


_composite_sink: CompositeObservabilitySink | None = None
_singleton_lock = threading.Lock()


def get_observability_sink() -> CompositeObservabilitySink:
    """获取全局 CompositeObservabilitySink 单例.

    默认不持久化到文件（纯内存）。需要文件持久化时通过环境变量配置：
        OBSERVABILITY_TRACE_FILE=/path/to/trace.jsonl
        OBSERVABILITY_METRIC_FILE=/path/to/metric.jsonl
        OBSERVABILITY_LOG_FILE=/path/to/log.jsonl
    """
    global _composite_sink
    if _composite_sink is None:
        with _singleton_lock:
            if _composite_sink is None:
                _composite_sink = _build_default_sink()
    return _composite_sink


def _build_default_sink() -> CompositeObservabilitySink:
    """根据环境变量构建默认 sink."""
    trace_file = os.environ.get("OBSERVABILITY_TRACE_FILE")
    metric_file = os.environ.get("OBSERVABILITY_METRIC_FILE")
    log_file = os.environ.get("OBSERVABILITY_LOG_FILE")

    return CompositeObservabilitySink(
        trace_sink=TraceSink(log_file=trace_file) if trace_file else None,
        metric_sink=MetricSink(log_file=metric_file) if metric_file else None,
        log_sink=LogSink(log_file=log_file) if log_file else None,
    )


__all__ = [
    "TraceSink",
    "MetricSink",
    "LogSink",
    "CompositeObservabilitySink",
    "get_observability_sink",
]
