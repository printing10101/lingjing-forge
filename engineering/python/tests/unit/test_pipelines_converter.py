"""pipelines.converter 单元测试（上下文校验 / 数值兜底 / 聚合 / 批次缓冲）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.integrations.mtconnect.parser import Sample
from app.pipelines.converter import (
    CollectorContext,
    SampleBatchAggregator,
    _build_series_id,
    _safe_float,
    aggregate_samples_to_record,
)

pytestmark = pytest.mark.unit


def _ctx(**kw) -> CollectorContext:
    return CollectorContext(
        machine_id=kw.get('machine_id', 'm1'),
        tool_id=kw.get('tool_id', 't1'),
        material=kw.get('material', 'steel'),
    )


def _sample(speed=None, feed=None, ts=None) -> Sample:
    return Sample(spindle_speed=speed, feedrate=feed, observed_at=ts)


class TestCollectorContext:
    def test_valid(self):
        c = _ctx()
        assert c.machine_id == 'm1'
        assert c.series_id_prefix == 'mach'

    def test_missing_machine_id_raises(self):
        with pytest.raises(ValueError, match='required'):
            CollectorContext(machine_id='', tool_id='t1', material='steel')

    def test_missing_material_raises(self):
        with pytest.raises(ValueError, match='required'):
            CollectorContext(machine_id='m1', tool_id='t1', material='')


class TestSafeFloat:
    def test_none_returns_default(self):
        assert _safe_float(None) == 0.0
        assert _safe_float(None, default=5.0) == 5.0

    def test_invalid_returns_default(self):
        assert _safe_float('oops') == 0.0

    def test_nan_returns_default(self):
        assert _safe_float(float('nan')) == 0.0

    def test_normal(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float('42') == 42.0


class TestBuildSeriesId:
    def test_format(self):
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        sid = _build_series_id(_ctx(), ts, suffix='abc123')
        assert sid == 'mach_m1_t1_20260115T103000Z_abc123'

    def test_no_suffix(self):
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        sid = _build_series_id(_ctx(), ts)
        assert sid == 'mach_m1_t1_20260115T103000Z'


class TestAggregateSamples:
    def test_empty_samples_raises(self):
        with pytest.raises(ValueError, match='at least one'):
            aggregate_samples_to_record([], _ctx())

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match='Unknown aggregation'):
            aggregate_samples_to_record([_sample(1000.0)], _ctx(), strategy='bogus')

    def test_mean_strategy(self):
        ts = datetime.now(timezone.utc)
        samples = [_sample(1000.0, 200.0, ts), _sample(2000.0, 300.0, ts)]
        rec = aggregate_samples_to_record(samples, _ctx(), strategy='mean')
        assert rec.spindle_speed == 1500.0
        assert rec.feed_rate == 250.0
        assert rec.machine_id == 'm1'
        assert rec.process_params['aggregation_strategy'] == 'mean'

    def test_last_strategy(self):
        ts = datetime.now(timezone.utc)
        samples = [_sample(1000.0, None, ts), _sample(2000.0, 400.0, ts)]
        rec = aggregate_samples_to_record(samples, _ctx(), strategy='last')
        assert rec.spindle_speed == 2000.0
        assert rec.feed_rate == 400.0

    def test_max_strategy(self):
        ts = datetime.now(timezone.utc)
        samples = [_sample(1000.0, None, ts), _sample(3000.0, None, ts)]
        rec = aggregate_samples_to_record(samples, _ctx(), strategy='max')
        assert rec.spindle_speed == 3000.0


class TestSampleBatchAggregator:
    def test_invalid_params(self):
        with pytest.raises(ValueError):
            SampleBatchAggregator(flush_interval=0)
        with pytest.raises(ValueError):
            SampleBatchAggregator(batch_size=0)
        with pytest.raises(ValueError):
            SampleBatchAggregator(max_samples_per_record=0)

    def test_add_and_len(self):
        agg = SampleBatchAggregator(batch_size=10)
        agg.add(_sample(1000.0))
        agg.add(_sample(2000.0))
        assert len(agg) == 2

    def test_extend(self):
        agg = SampleBatchAggregator(batch_size=10)
        agg.extend([_sample(1.0), _sample(2.0)])
        assert len(agg) == 2

    def test_should_flush_empty(self):
        agg = SampleBatchAggregator(batch_size=2)
        assert agg.should_flush() is False

    def test_should_flush_by_batch_size(self):
        agg = SampleBatchAggregator(batch_size=2)
        agg.add(_sample(1.0))
        agg.add(_sample(2.0))
        assert agg.should_flush() is True

    def test_flush_records_empty(self):
        agg = SampleBatchAggregator(batch_size=2)
        assert agg.flush_records(_ctx()) == []

    def test_flush_and_mark(self):
        agg = SampleBatchAggregator(batch_size=10)
        ts = datetime.now(timezone.utc)
        agg.add(_sample(1000.0, 200.0, ts))
        records = agg.flush_records(_ctx(), strategy='mean')
        assert len(records) == 1
        assert records[0].spindle_speed == 1000.0
        agg.mark_flushed()
        assert len(agg) == 0

    def test_snapshot(self):
        agg = SampleBatchAggregator(batch_size=5, flush_interval=2.0)
        s = agg.snapshot()
        assert s['buffer_size'] == 0
        assert s['batch_size'] == 5
        assert s['flush_interval'] == 2.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
