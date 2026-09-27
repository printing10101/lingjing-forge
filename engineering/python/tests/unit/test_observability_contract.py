"""可观测契约单元测试.

对应 core-contracts-design.md 第 7 章 / app/contracts/observability.py.

覆盖：
- LogLevel 枚举
- TraceSpan（status 合法性、end_ts >= start_ts）
- Metric（数值校验、强制 float 转换、bool 拒绝）
- LogEntry（level 兼容字符串、timestamp 数值校验）
- ExperimentSnapshot（datetime/bool/dict/list 校验、metrics float 转换）
- ITraceSink / IMetricSink / ILogSink / ISnapshotStore 抽象接口
- IObservabilitySink 联合接口
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.contracts.observability import (
    VALID_SPAN_STATUSES,
    ExperimentSnapshot,
    ILogSink,
    IMetricSink,
    IObservabilitySink,
    ISnapshotStore,
    ITraceSink,
    LogEntry,
    LogLevel,
    Metric,
    TraceSpan,
)


@pytest.mark.unit
@pytest.mark.contracts
class TestLogLevel:
    """LogLevel 枚举."""

    def test_enum_values(self):
        assert LogLevel.DEBUG == "debug"
        assert LogLevel.INFO == "info"
        assert LogLevel.WARNING == "warning"
        assert LogLevel.ERROR == "error"
        assert LogLevel.CRITICAL == "critical"

    def test_level_count(self):
        assert len(list(LogLevel)) == 5

    def test_is_str_enum(self):
        """LogLevel 是 str Enum，可直接当字符串用."""
        assert LogLevel.INFO == "info"


@pytest.mark.unit
@pytest.mark.contracts
class TestTraceSpan:
    """TraceSpan dataclass 构造校验."""

    def _make_span(self, **overrides) -> TraceSpan:
        defaults = dict(
            span_id="span-001",
            trace_id="trace-001",
            name="ltc.train.epoch",
            start_ts=1000.0,
            end_ts=1005.0,
            status="ok",
        )
        defaults.update(overrides)
        return TraceSpan(**defaults)

    def test_valid_span(self):
        span = self._make_span()
        assert span.span_id == "span-001"
        assert span.status == "ok"

    def test_empty_span_id_rejected(self):
        with pytest.raises(ValueError, match="span_id"):
            self._make_span(span_id="")

    def test_empty_trace_id_rejected(self):
        with pytest.raises(ValueError, match="trace_id"):
            self._make_span(trace_id="")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="status"):
            self._make_span(status="pending")  # 非 ok/error

    def test_end_ts_before_start_ts_rejected(self):
        """end_ts < start_ts 应报错."""
        with pytest.raises(ValueError, match="end_ts"):
            self._make_span(start_ts=1000.0, end_ts=999.0)

    def test_end_ts_none_allowed(self):
        """未结束的 span，end_ts 可为 None."""
        span = self._make_span(end_ts=None)
        assert span.end_ts is None

    def test_end_ts_equal_start_ts_allowed(self):
        """end_ts == start_ts 是允许的（瞬时 span）."""
        span = self._make_span(start_ts=1000.0, end_ts=1000.0)
        assert span.end_ts == span.start_ts

    def test_valid_statuses(self):
        """VALID_SPAN_STATUSES 包含 ok 与 error."""
        assert VALID_SPAN_STATUSES == {"ok", "error"}

    def test_default_attributes_and_events(self):
        span = self._make_span()
        assert span.attributes == {}
        assert span.events == []

    def test_parent_span_id_optional(self):
        span = self._make_span()
        assert span.parent_span_id is None


@pytest.mark.unit
@pytest.mark.contracts
class TestMetric:
    """Metric dataclass 构造校验."""

    def test_valid_metric(self):
        m = Metric(name="ltc.train.loss", value=0.05, timestamp=1000.0)
        assert m.name == "ltc.train.loss"
        assert m.value == 0.05

    def test_int_value_converted_to_float(self):
        """int 类型的 value 应被强制转为 float."""
        m = Metric(name="count", value=42, timestamp=1000.0)
        assert isinstance(m.value, float)
        assert m.value == 42.0

    def test_int_timestamp_converted_to_float(self):
        m = Metric(name="x", value=1.0, timestamp=1000)
        assert isinstance(m.timestamp, float)
        assert m.timestamp == 1000.0

    def test_bool_value_rejected(self):
        """bool 不应被当作数值接受."""
        with pytest.raises(ValueError, match="value"):
            Metric(name="x", value=True, timestamp=1000.0)  # type: ignore[arg-type]

    def test_bool_timestamp_rejected(self):
        with pytest.raises(ValueError, match="timestamp"):
            Metric(name="x", value=1.0, timestamp=True)  # type: ignore[arg-type]

    def test_string_value_rejected(self):
        with pytest.raises(ValueError, match="value"):
            Metric(name="x", value="0.05", timestamp=1000.0)  # type: ignore[arg-type]

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            Metric(name="", value=1.0, timestamp=1000.0)

    def test_default_labels_and_unit(self):
        m = Metric(name="x", value=1.0, timestamp=1000.0)
        assert m.labels == {}
        assert m.unit == ""


@pytest.mark.unit
@pytest.mark.contracts
class TestLogEntry:
    """LogEntry dataclass 构造校验."""

    def test_valid_entry_with_enum_level(self):
        entry = LogEntry(
            timestamp=1000.0,
            level=LogLevel.INFO,
            message="训练开始",
        )
        assert entry.level == LogLevel.INFO

    def test_valid_entry_with_string_level(self):
        """level 接受字符串（便于从 JSON 反序列化）."""
        entry = LogEntry(timestamp=1000.0, level="info", message="hello")
        assert entry.level == LogLevel.INFO

    def test_int_timestamp_converted_to_float(self):
        entry = LogEntry(timestamp=1000, level=LogLevel.INFO, message="x")
        assert isinstance(entry.timestamp, float)

    def test_bool_timestamp_rejected(self):
        with pytest.raises(ValueError, match="timestamp"):
            LogEntry(timestamp=True, level=LogLevel.INFO, message="x")  # type: ignore[arg-type]

    def test_invalid_string_level_rejected(self):
        with pytest.raises(ValueError, match="level"):
            LogEntry(timestamp=1000.0, level="verbose", message="x")

    def test_non_string_message_rejected(self):
        with pytest.raises(ValueError, match="message"):
            LogEntry(timestamp=1000.0, level=LogLevel.INFO, message=42)  # type: ignore[arg-type]

    def test_default_optional_fields(self):
        entry = LogEntry(timestamp=1000.0, level=LogLevel.INFO, message="x")
        assert entry.logger == ""
        assert entry.attributes == {}
        assert entry.trace_id is None
        assert entry.span_id is None


@pytest.mark.unit
@pytest.mark.contracts
class TestExperimentSnapshot:
    """ExperimentSnapshot dataclass 构造校验."""

    def _make_snapshot(self, **overrides) -> ExperimentSnapshot:
        defaults = dict(
            snapshot_id="snap-001",
            created_at=datetime.utcnow(),
            created_by="user-1",
            git_sha="abc123def456",
            code_dirty=False,
            config={"lr": 0.001, "epochs": 100},
            dataset_versions=["dataset://phm2010/v1"],
            model_uri="model://ltc-v1",
            metrics={"val_loss": 0.06, "pcc": 0.51},
            environment={"python": "3.10", "torch": "2.0.1"},
        )
        defaults.update(overrides)
        return ExperimentSnapshot(**defaults)

    def test_valid_snapshot(self):
        snap = self._make_snapshot()
        assert snap.snapshot_id == "snap-001"
        assert snap.code_dirty is False

    def test_metrics_int_converted_to_float(self):
        """metrics 中的 int 值应被转为 float."""
        snap = self._make_snapshot(metrics={"val_loss": 0, "pcc": 1})
        assert all(isinstance(v, float) for v in snap.metrics.values())

    def test_empty_snapshot_id_rejected(self):
        with pytest.raises(ValueError, match="snapshot_id"):
            self._make_snapshot(snapshot_id="")

    def test_non_datetime_created_at_rejected(self):
        with pytest.raises(ValueError, match="created_at"):
            self._make_snapshot(created_at="2026-01-01")  # type: ignore[arg-type]

    def test_empty_created_by_rejected(self):
        with pytest.raises(ValueError, match="created_by"):
            self._make_snapshot(created_by="")

    def test_empty_git_sha_rejected(self):
        with pytest.raises(ValueError, match="git_sha"):
            self._make_snapshot(git_sha="")

    def test_non_bool_code_dirty_rejected(self):
        with pytest.raises(ValueError, match="code_dirty"):
            self._make_snapshot(code_dirty="no")  # type: ignore[arg-type]

    def test_non_dict_config_rejected(self):
        with pytest.raises(ValueError, match="config"):
            self._make_snapshot(config=[("lr", 0.001)])  # type: ignore[arg-type]

    def test_non_list_dataset_versions_rejected(self):
        with pytest.raises(ValueError, match="dataset_versions"):
            self._make_snapshot(dataset_versions="dataset://x/v1")  # type: ignore[arg-type]

    def test_empty_model_uri_rejected(self):
        with pytest.raises(ValueError, match="model_uri"):
            self._make_snapshot(model_uri="")

    def test_non_dict_metrics_rejected(self):
        with pytest.raises(ValueError, match="metrics"):
            self._make_snapshot(metrics=[0.06])  # type: ignore[arg-type]

    def test_non_numeric_metric_value_rejected(self):
        with pytest.raises(ValueError, match="metrics"):
            self._make_snapshot(metrics={"val_loss": "0.06"})  # type: ignore[arg-type]

    def test_bool_metric_value_rejected(self):
        """bool 不应被当作数值 metric 接受."""
        with pytest.raises(ValueError, match="metrics"):
            self._make_snapshot(metrics={"flag": True})  # type: ignore[arg-type]

    def test_non_dict_environment_rejected(self):
        with pytest.raises(ValueError, match="environment"):
            self._make_snapshot(environment=["python:3.10"])  # type: ignore[arg-type]

    def test_optional_fields_default(self):
        snap = self._make_snapshot()
        assert snap.lineage_record_id is None
        assert snap.mlflow_run_id is None
        assert snap.notes == ""


@pytest.mark.unit
@pytest.mark.contracts
class TestAbstractInterfaces:
    """4 个 Sink ABC + IObservabilitySink 联合接口."""

    def test_trace_sink_abstract(self):
        with pytest.raises(TypeError):
            ITraceSink()  # type: ignore[abstract]

    def test_metric_sink_abstract(self):
        with pytest.raises(TypeError):
            IMetricSink()  # type: ignore[abstract]

    def test_log_sink_abstract(self):
        with pytest.raises(TypeError):
            ILogSink()  # type: ignore[abstract]

    def test_snapshot_store_abstract(self):
        with pytest.raises(TypeError):
            ISnapshotStore()  # type: ignore[abstract]

    def test_observability_sink_abstract(self):
        """IObservabilitySink 是 4 个接口的联合，也不可实例化."""
        with pytest.raises(TypeError):
            IObservabilitySink()  # type: ignore[abstract]

    def test_observability_sink_is_subclass_of_all_four(self):
        """IObservabilitySink 继承自 4 个子接口."""
        assert issubclass(IObservabilitySink, ITraceSink)
        assert issubclass(IObservabilitySink, IMetricSink)
        assert issubclass(IObservabilitySink, ILogSink)
        assert issubclass(IObservabilitySink, ISnapshotStore)

    def test_trace_sink_can_be_subclassed(self):
        class DummyTraceSink(ITraceSink):
            def start_span(self, name, parent=None):
                return "span-1"

            def end_span(self, span_id, status="ok"):
                return None

            def add_attribute(self, span_id, key, value):
                return None

            def add_event(self, span_id, name, payload):
                return None

        sink = DummyTraceSink()
        assert sink.start_span("x") == "span-1"

    def test_metric_sink_can_be_subclassed(self):
        class DummyMetricSink(IMetricSink):
            def counter(self, name, value=1, labels=None):
                return None

            def gauge(self, name, value, labels=None):
                return None

            def histogram(self, name, value, labels=None):
                return None

        sink = DummyMetricSink()
        assert sink is not None

    def test_log_sink_can_be_subclassed(self):
        class DummyLogSink(ILogSink):
            def log(self, entry):
                return None

        sink = DummyLogSink()
        assert sink is not None

    def test_snapshot_store_can_be_subclassed(self):
        class DummySnapshotStore(ISnapshotStore):
            async def create(self, *, config, dataset_versions, model_uri, metrics,
                             created_by, notes=""):
                return ExperimentSnapshot(
                    snapshot_id="snap-1",
                    created_at=datetime.utcnow(),
                    created_by=created_by,
                    git_sha="abc",
                    code_dirty=False,
                    config=config,
                    dataset_versions=dataset_versions,
                    model_uri=model_uri,
                    metrics=metrics,
                    environment={},
                )

            async def get(self, snapshot_id):
                raise KeyError(snapshot_id)

            async def list(self, *, filters=None):
                return []

            async def reproduce(self, snapshot_id):
                return "wf-run-1"

        store = DummySnapshotStore()
        assert store is not None

    def test_observability_sink_can_be_subclassed(self):
        """IObservabilitySink 可被具体实现子类化（需实现全部 4 个接口方法）."""

        class CompositeSink(IObservabilitySink):
            # ITraceSink
            def start_span(self, name, parent=None):
                return "span-1"

            def end_span(self, span_id, status="ok"):
                return None

            def add_attribute(self, span_id, key, value):
                return None

            def add_event(self, span_id, name, payload):
                return None

            # IMetricSink
            def counter(self, name, value=1, labels=None):
                return None

            def gauge(self, name, value, labels=None):
                return None

            def histogram(self, name, value, labels=None):
                return None

            # ILogSink
            def log(self, entry):
                return None

            # ISnapshotStore
            async def create(self, *, config, dataset_versions, model_uri, metrics,
                             created_by, notes=""):
                return ExperimentSnapshot(
                    snapshot_id="snap-1",
                    created_at=datetime.utcnow(),
                    created_by=created_by,
                    git_sha="abc",
                    code_dirty=False,
                    config=config,
                    dataset_versions=dataset_versions,
                    model_uri=model_uri,
                    metrics=metrics,
                    environment={},
                )

            async def get(self, snapshot_id):
                raise KeyError(snapshot_id)

            async def list(self, *, filters=None):
                return []

            async def reproduce(self, snapshot_id):
                return "wf-run-1"

        sink = CompositeSink()
        assert sink is not None
        assert sink.start_span("x") == "span-1"
