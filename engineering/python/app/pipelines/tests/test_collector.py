"""Unit tests for the data acquisition pipeline V1 (M0.5).

测试分层
--------

1. **转换层 (converter)** – :class:`Sample` → :class:`MachiningRecordCreate` /
   :class:`SampleBatchAggregator` 的纯函数测试，无 IO。
2. **采集器 (collector)** – :class:`MachiningCollector` 的生命周期 / 异常隔离 /
   重试 / 单例外观（``start_collector`` / ``stop_collector``）测试，
   通过注入 mock 适配器和 sink 函数避免任何网络与数据库依赖。

测试运行方式::

    cd python && pytest app/pipelines/tests/test_collector.py -v

本文件**不**依赖真实 MTConnect Agent / PostgreSQL / TDengine，
仅在内存中验证数据流的正确性。M0.5 验收步骤 7.1 / 7.2 仍然需要
真实的采集管道；本测试只保证代码逻辑正确。
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections.abc import Sequence
from unittest.mock import MagicMock

import pytest

# 兼容直接 ``pytest app/pipelines/tests/test_collector.py`` 调用的场景：
# 将 ``python/`` 目录加入 sys.path，保证 ``from app.xxx`` 可解析。
_PYTHON_DIR = Path(__file__).resolve().parents[3]
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from app.integrations.mtconnect.parser import Sample
from app.models.machining_record import MachiningRecordCreate
from app.pipelines import (
    CollectorConfig,
    CollectorContext,
    MachiningCollector,
    SampleBatchAggregator,
    aggregate_samples_to_record,
    convert_sample_to_record,
    get_collector,
    reset_collector,
    start_collector,
    stop_collector,
)
from app.pipelines.machining_collector import CollectorStats


# Helpers / Fixtures


def _make_sample(
    *,
    spindle_speed: float | None = 5000.0,
    spindle_load: float | None = 30.0,
    feedrate: float | None = 800.0,
    execution: str | None = "ACTIVE",
    observed_at: datetime | None = None,
    extras: dict[str, Any] | None = None,
) -> Sample:
    """构造一条 :class:`Sample` 便于测试。"""
    return Sample(
        spindle_speed=spindle_speed,
        spindle_load=spindle_load,
        feedrate=feedrate,
        execution=execution,
        observed_at=observed_at or datetime.now(timezone.utc),
        extras=extras or {},
    )


def _make_context(
    *,
    machine_id: str = "CNC-TEST",
    tool_id: str = "T-TEST",
    material: str = "45号钢",
    series_id_prefix: str = "test",
    process_params: dict[str, Any] | None = None,
) -> CollectorContext:
    return CollectorContext(
        machine_id=machine_id,
        tool_id=tool_id,
        material=material,
        series_id_prefix=series_id_prefix,
        process_params=process_params or {},
    )


@pytest.fixture
def context() -> CollectorContext:
    return _make_context()


@pytest.fixture
def base_config() -> CollectorConfig:
    """构造一个离线、可重现的 :class:`CollectorConfig`（不访问网络）。"""
    return CollectorConfig(
        agent_url="http://test.invalid:80",
        machine_id="CNC-TEST",
        tool_id="T-TEST",
        material="45号钢",
        sample_interval=0.05,
        batch_size=5,
        flush_interval=0.5,
        aggregation_strategy="mean",
        max_write_retries=2,
        retry_backoff=0.01,
        use_task_manager=False,
    )


# CollectorContext


class TestCollectorContext:
    def test_construction_minimal(self) -> None:
        ctx = _make_context()
        assert ctx.machine_id == "CNC-TEST"
        assert ctx.tool_id == "T-TEST"
        assert ctx.material == "45号钢"
        assert ctx.series_id_prefix == "mach"

    def test_frozen_dataclass(self) -> None:
        ctx = _make_context()
        with pytest.raises((AttributeError, Exception)):
            ctx.machine_id = "new"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "kw",
        [
            {"machine_id": ""},
            {"tool_id": ""},
            {"material": ""},
        ],
    )
    def test_required_fields_validation(self, kw: dict[str, str]) -> None:
        base = dict(machine_id="X", tool_id="Y", material="Z")
        base.update(kw)
        with pytest.raises(ValueError):
            CollectorContext(**base)  # type: ignore[arg-type]

    def test_process_params_default(self) -> None:
        ctx = _make_context()
        assert ctx.process_params == {}


# convert_sample_to_record


class TestConvertSampleToRecord:
    def test_basic_conversion(self, context: CollectorContext) -> None:
        ts = datetime(2026, 6, 11, 10, 23, 45, tzinfo=timezone.utc)
        sample = _make_sample(observed_at=ts)
        record = convert_sample_to_record(sample, context, record_id="rec-001")
        assert isinstance(record, MachiningRecordCreate)
        assert record.machine_id == context.machine_id
        assert record.tool_id == context.tool_id
        assert record.material == context.material
        assert record.spindle_speed == 5000.0
        assert record.feed_rate == 800.0
        assert record.tdengine_series_id is not None
        assert record.tdengine_series_id.startswith("test_CNC-TEST_T-TEST_")
        assert record.record_id == "rec-001"

    def test_none_values_become_zero(self, context: CollectorContext) -> None:
        sample = _make_sample(spindle_speed=None, feedrate=None)
        record = convert_sample_to_record(sample, context)
        assert record.spindle_speed == 0.0
        assert record.feed_rate == 0.0

    def test_nan_becomes_zero(self, context: CollectorContext) -> None:
        sample = _make_sample(spindle_speed=float("nan"), feedrate=float("nan"))
        record = convert_sample_to_record(sample, context)
        assert record.spindle_speed == 0.0
        assert record.feed_rate == 0.0

    def test_invalid_string_becomes_zero(self, context: CollectorContext) -> None:
        # Sample 字段在解析时已是 float，但仍要保证转换层对异常值兜底。
        sample = _make_sample(spindle_speed="not-a-number")  # type: ignore[arg-type]
        record = convert_sample_to_record(sample, context)
        assert record.spindle_speed == 0.0

    def test_timestamp_override(self, context: CollectorContext) -> None:
        ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        sample = _make_sample(observed_at=None)
        record = convert_sample_to_record(sample, context, timestamp=ts)
        assert record.timestamp == ts

    def test_process_params_includes_extras(self, context: CollectorContext) -> None:
        sample = _make_sample(extras={"controller_mode": "AUTO", "program": "O1234"})
        record = convert_sample_to_record(sample, context)
        assert record.process_params["extras"] == {
            "controller_mode": "AUTO",
            "program": "O1234",
        }
        assert record.process_params["execution"] == "ACTIVE"
        assert record.process_params["spindle_load"] == 30.0


# aggregate_samples_to_record


class TestAggregateSamples:
    def test_mean_strategy(self, context: CollectorContext) -> None:
        samples = [
            _make_sample(spindle_speed=1000.0, feedrate=100.0),
            _make_sample(spindle_speed=2000.0, feedrate=200.0),
            _make_sample(spindle_speed=3000.0, feedrate=300.0),
        ]
        record = aggregate_samples_to_record(samples, context)
        assert record.spindle_speed == 2000.0
        assert record.feed_rate == 200.0
        assert record.process_params["aggregation_strategy"] == "mean"
        assert record.process_params["window_size"] == 3

    def test_last_strategy(self, context: CollectorContext) -> None:
        samples = [
            _make_sample(spindle_speed=1000.0, feedrate=100.0),
            _make_sample(spindle_speed=5000.0, feedrate=500.0),
        ]
        record = aggregate_samples_to_record(samples, context, strategy="last")
        assert record.spindle_speed == 5000.0
        assert record.feed_rate == 500.0

    def test_max_strategy(self, context: CollectorContext) -> None:
        samples = [
            _make_sample(spindle_speed=2000.0, feedrate=200.0),
            _make_sample(spindle_speed=5000.0, feedrate=100.0),
            _make_sample(spindle_speed=3000.0, feedrate=400.0),
        ]
        record = aggregate_samples_to_record(samples, context, strategy="max")
        assert record.spindle_speed == 5000.0
        assert record.feed_rate == 400.0

    def test_min_strategy(self, context: CollectorContext) -> None:
        samples = [
            _make_sample(spindle_speed=2000.0, feedrate=200.0),
            _make_sample(spindle_speed=5000.0, feedrate=100.0),
        ]
        record = aggregate_samples_to_record(samples, context, strategy="min")
        assert record.spindle_speed == 2000.0
        assert record.feed_rate == 100.0

    def test_empty_samples_raises(self, context: CollectorContext) -> None:
        with pytest.raises(ValueError):
            aggregate_samples_to_record([], context)

    def test_unknown_strategy_raises(self, context: CollectorContext) -> None:
        with pytest.raises(ValueError):
            aggregate_samples_to_record([_make_sample()], context, strategy="bogus")

    def test_window_duration(self, context: CollectorContext) -> None:
        start = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(seconds=10)
        samples = [
            _make_sample(observed_at=start),
            _make_sample(observed_at=end),
        ]
        record = aggregate_samples_to_record(samples, context)
        assert record.process_params["window_duration_s"] == 10.0

    def test_process_params_propagation(self) -> None:
        ctx = _make_context(process_params={"operation": "face_milling", "depth_of_cut": 1.5})
        record = aggregate_samples_to_record([_make_sample()], ctx)
        assert record.process_params["operation"] == "face_milling"
        assert record.process_params["depth_of_cut"] == 1.5


# SampleBatchAggregator


class TestSampleBatchAggregator:
    def test_invalid_flush_interval(self) -> None:
        with pytest.raises(ValueError):
            SampleBatchAggregator(flush_interval=0, batch_size=10)

    def test_invalid_batch_size(self) -> None:
        with pytest.raises(ValueError):
            SampleBatchAggregator(flush_interval=1.0, batch_size=0)

    def test_invalid_max_samples(self) -> None:
        with pytest.raises(ValueError):
            SampleBatchAggregator(flush_interval=1.0, batch_size=10, max_samples_per_record=0)

    def test_add_and_len(self) -> None:
        agg = SampleBatchAggregator(flush_interval=5.0, batch_size=10)
        assert len(agg) == 0
        agg.add(_make_sample())
        assert len(agg) == 1
        agg.extend([_make_sample(), _make_sample()])
        assert len(agg) == 3

    def test_should_flush_on_batch_size(self) -> None:
        agg = SampleBatchAggregator(flush_interval=10.0, batch_size=3)
        agg.add(_make_sample())
        assert not agg.should_flush()
        agg.add(_make_sample())
        assert not agg.should_flush()
        agg.add(_make_sample())
        assert agg.should_flush()

    def test_should_flush_on_window_age(self) -> None:
        agg = SampleBatchAggregator(flush_interval=0.1, batch_size=1000)
        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        agg.add(_make_sample(observed_at=start))
        assert agg.should_flush()

    def test_should_flush_false_when_empty(self) -> None:
        agg = SampleBatchAggregator(flush_interval=0.1, batch_size=1)
        assert not agg.should_flush()

    def test_flush_records_returns_aggregated(self, context: CollectorContext) -> None:
        agg = SampleBatchAggregator(flush_interval=5.0, batch_size=10)
        agg.extend(
            [
                _make_sample(spindle_speed=1000.0, feedrate=100.0),
                _make_sample(spindle_speed=2000.0, feedrate=200.0),
                _make_sample(spindle_speed=3000.0, feedrate=300.0),
            ]
        )
        records = agg.flush_records(context)
        assert len(records) == 1
        assert records[0].spindle_speed == 2000.0
        assert records[0].feed_rate == 200.0

    def test_flush_records_empty_buffer(self, context: CollectorContext) -> None:
        agg = SampleBatchAggregator(flush_interval=5.0, batch_size=10)
        assert agg.flush_records(context) == []

    def test_mark_flushed_resets_state(self) -> None:
        agg = SampleBatchAggregator(flush_interval=5.0, batch_size=10)
        agg.add(_make_sample())
        agg.mark_flushed()
        assert len(agg) == 0
        assert agg._last_flush_at is not None

    def test_max_samples_cap(self) -> None:
        agg = SampleBatchAggregator(flush_interval=5.0, batch_size=1000, max_samples_per_record=3)
        for _ in range(10):
            agg.add(_make_sample())
        assert len(agg) == 3  # 超出容量后从队头丢弃

    def test_snapshot(self) -> None:
        agg = SampleBatchAggregator(flush_interval=2.0, batch_size=50)
        agg.add(_make_sample())
        snap = agg.snapshot()
        assert snap["buffer_size"] == 1
        assert snap["batch_size"] == 50
        assert snap["flush_interval"] == 2.0


# MachiningCollector


class _StubAdapter:
    """同步适配器桩：返回预定义的样本序列，并支持 stop 事件。"""

    def __init__(self, samples: list[Sample]) -> None:
        self._samples = list(samples)
        self._idx = 0
        self._stopped = False
        self.probe_calls = 0
        self.fetch_calls = 0

    def probe(self) -> dict[str, str]:
        self.probe_calls += 1
        return {"instance_id": "test", "sender": "stub"}

    def fetch_sample(self) -> Sample:
        self.fetch_calls += 1
        if self._idx >= len(self._samples):
            self._idx = 0  # 循环复用样本
        s = self._samples[self._idx]
        self._idx += 1
        return s

    def stop(self) -> None:
        self._stopped = True


def _build_collector(
    config: CollectorConfig,
    *,
    samples: list[Sample] | None = None,
    record_sink=None,
    tdengine_sink_fn=None,
) -> MachiningCollector:
    adapter = _StubAdapter(samples or [_make_sample()])
    return MachiningCollector(
        config=config,
        adapter=adapter,  # type: ignore[arg-type]
        record_sink=record_sink,
        tdengine_sink_fn=tdengine_sink_fn,
    )


class TestCollectorLifecycle:
    @pytest.mark.asyncio
    async def test_start_returns_job_id_and_runs(self, base_config: CollectorConfig) -> None:
        records_written: list[MachiningRecordCreate] = []

        async def sink(recs: Sequence[MachiningRecordCreate]) -> int:
            records_written.extend(recs)
            return len(recs)

        c = _build_collector(base_config, record_sink=sink, samples=[_make_sample()] * 20)
        job_id = await c.start()
        assert job_id.startswith("collector-")
        assert c.is_running
        assert c.job_id == job_id

        # 等到一定时间后停止
        await asyncio.sleep(0.3)
        stats = await c.stop(timeout=2.0)
        assert not c.is_running
        assert stats["samples_consumed"] >= 1
        assert stats["records_written"] >= 1

    @pytest.mark.asyncio
    async def test_double_start_returns_existing_job_id(self, base_config: CollectorConfig) -> None:
        async def sink(recs: Sequence[MachiningRecordCreate]) -> int:
            return len(recs)

        c = _build_collector(base_config, record_sink=sink)
        first = await c.start()
        second = await c.start()
        assert first == second
        await c.stop(timeout=2.0)

    @pytest.mark.asyncio
    async def test_stop_when_not_running_returns_stats(self, base_config: CollectorConfig) -> None:
        c = _build_collector(base_config)
        stats = await c.stop(timeout=0.1)
        assert isinstance(stats, dict)
        assert stats["samples_consumed"] == 0

    @pytest.mark.asyncio
    async def test_probe_failure_does_not_block(self) -> None:
        cfg = CollectorConfig(agent_url="http://x", sample_interval=0.05)
        bad_adapter = MagicMock()
        bad_adapter.probe.side_effect = RuntimeError("offline")
        bad_adapter.fetch_sample.return_value = _make_sample()
        bad_adapter.stop = MagicMock()
        c = MachiningCollector(config=cfg, adapter=bad_adapter)
        # start 不应抛出
        job_id = await c.start()
        assert job_id is not None
        await c.stop(timeout=1.0)


class TestCollectorStats:
    @pytest.mark.asyncio
    async def test_get_stats(self, base_config: CollectorConfig) -> None:
        async def sink(recs: Sequence[MachiningRecordCreate]) -> int:
            return len(recs)

        c = _build_collector(base_config, record_sink=sink)
        await c.start()
        await asyncio.sleep(0.1)
        stats = c.get_stats()
        assert "samples_consumed" in stats
        assert "records_written" in stats
        await c.stop(timeout=1.0)

    def test_dump_state(self, base_config: CollectorConfig) -> None:
        c = _build_collector(base_config)
        state = c.dump_state()
        assert state["config"]["machine_id"] == "CNC-TEST"
        assert "aggregator" in state
        assert "stats" in state


class TestCollectorWriteRetry:
    @pytest.mark.asyncio
    async def test_postgres_sink_retry_exhausted(self, base_config: CollectorConfig) -> None:
        attempts = {"n": 0}

        async def failing_sink(recs: Sequence[MachiningRecordCreate]) -> int:
            attempts["n"] += 1
            raise RuntimeError("PostgreSQL down")

        c = _build_collector(
            base_config,
            record_sink=failing_sink,
            samples=[_make_sample()] * 10,
        )
        await c.start()
        await asyncio.sleep(0.5)
        await c.stop(timeout=2.0)
        # max_write_retries=2 每个 flush 最多调用 2 次
        assert attempts["n"] >= 1
        assert c._stats.write_retries >= 1
        assert c._stats.write_failures >= 1
        assert c._retry_queue  # 失败入队等待下次重试

    @pytest.mark.asyncio
    async def test_tdengine_sink_retry(self, base_config: CollectorConfig) -> None:
        async def ok_sink(recs: Sequence[MachiningRecordCreate]) -> int:
            return len(recs)

        td_attempts = {"n": 0}

        async def failing_td(samples: Sequence[Sample]) -> int:
            td_attempts["n"] += 1
            raise RuntimeError("TDengine down")

        c = _build_collector(
            base_config,
            record_sink=ok_sink,
            tdengine_sink_fn=failing_td,
            samples=[_make_sample()] * 10,
        )
        await c.start()
        await asyncio.sleep(0.5)
        await c.stop(timeout=2.0)
        assert td_attempts["n"] >= 1
        assert c._tdengine_retry

    @pytest.mark.asyncio
    async def test_sink_succeeds_after_retries(self, base_config: CollectorConfig) -> None:
        attempts = {"n": 0}

        async def flaky_sink(recs: Sequence[MachiningRecordCreate]) -> int:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient")
            return len(recs)

        c = _build_collector(
            base_config,
            record_sink=flaky_sink,
            samples=[_make_sample()] * 10,
        )
        await c.start()
        await asyncio.sleep(0.5)
        await c.stop(timeout=2.0)
        assert attempts["n"] >= 2


class TestCollectorExceptionIsolation:
    @pytest.mark.asyncio
    async def test_adapter_exception_does_not_crash(self, base_config: CollectorConfig) -> None:
        """适配器持续抛错时，采集器应当记录 ``poll_errors`` 而不崩溃。"""

        class _BoomAdapter(_StubAdapter):
            def __init__(self) -> None:
                super().__init__(samples=[])

            def fetch_sample(self) -> Sample:
                raise ConnectionError("network down")

        async def sink(recs: Sequence[MachiningRecordCreate]) -> int:
            return len(recs)

        c = MachiningCollector(
            config=base_config,
            adapter=_BoomAdapter(),  # type: ignore[arg-type]
            record_sink=sink,
        )
        await c.start()
        await asyncio.sleep(0.3)
        await c.stop(timeout=1.0)
        assert c._stats.poll_errors >= 1


class TestCollectorSingleton:
    @pytest.mark.asyncio
    async def test_start_and_stop_singleton(self, base_config: CollectorConfig) -> None:
        async def sink(recs: Sequence[MachiningRecordCreate]) -> int:
            return len(recs)

        await reset_collector()
        # 注入 sink 是不可能的（start_collector 签名未暴露 record_sink），
        # 因此这里只验证生命周期与查询接口；写入失败不影响测试通过。
        try:
            job_id = await start_collector(duration=0.2, config=base_config)
            assert job_id.startswith("collector-")
            c = get_collector()
            assert c is not None
            assert c.is_running
            stats = await stop_collector()
            assert "samples_consumed" in stats
        finally:
            await reset_collector()

    @pytest.mark.asyncio
    async def test_stop_when_no_collector(self) -> None:
        await reset_collector()
        stats = await stop_collector()
        assert stats == {}

    @pytest.mark.asyncio
    async def test_reset_clears_singleton(self) -> None:
        await reset_collector()
        assert get_collector() is None


# Module exports


def test_module_exports() -> None:
    """确保 ``app.pipelines`` 顶层导出完整。"""
    import app.pipelines as pl

    expected = {
        "SampleBatchAggregator",
        "convert_sample_to_record",
        "aggregate_samples_to_record",
        "CollectorContext",
        "CollectorConfig",
        "MachiningCollector",
        "start_collector",
        "stop_collector",
        "get_collector",
        "reset_collector",
    }
    for name in expected:
        assert hasattr(pl, name), f"app.pipelines 缺少 {name}"


# Stats dataclass


def test_collector_stats_to_dict() -> None:
    s = CollectorStats()
    d = s.to_dict()
    assert d["samples_consumed"] == 0
    assert d["records_written"] == 0
    assert "started_at" not in d
    assert "stopped_at" not in d

    s.started_at = time.time() - 5
    s.stopped_at = time.time()
    d2 = s.to_dict()
    assert d2["runtime_seconds"] >= 5
