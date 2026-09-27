"""MTConnect → MachiningRecord 数据转换层。

本模块是 M0.5 数据采集管道的"纯函数"组件，**不**执行任何 IO，仅负责
把 :class:`app.integrations.mtconnect.parser.Sample` 时序样本（高频）
转换为 :class:`app.models.machining_record.MachiningRecordCreate`
（关系型加工记录）。

设计要点
--------

1. **单向数据流** —— 单条 :class:`Sample` 通过 :func:`convert_sample_to_record`
   直接转换为一条 ``MachiningRecordCreate``；一组 :class:`Sample` 通过
   :class:`SampleBatchAggregator` 聚合成 1 条加工记录（典型策略：均值 /
   末值 / 最大值）。
2. **可注入默认值** —— 实际生产中 ``machine_id`` / ``tool_id`` / ``material``
   等静态字段需要从外部（采集任务参数 / 配置中心）注入。本模块通过
   :class:`CollectorContext` 把这些上下文以不可变方式传入。
3. **TDengine 引用** —— 聚合后保留 ``tdengine_series_id`` 字段，将
   ``observed_at`` 列表 / 数值序列写入 TDengine 子表，主记录仅保留
   引用 ID。
4. **Pydantic 校验在边界完成** —— 转换函数不直接捕获 Pydantic
   ValidationError，调用方负责重试 / 入队。
"""

from __future__ import annotations

import logging
import statistics
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from collections.abc import Sequence

from app.integrations.mtconnect.parser import Sample
from app.models.machining_record import (
    MachiningRecordCreate,
)

logger = logging.getLogger(__name__)


# Collector context


@dataclass(frozen=True)
class CollectorContext:
    """采集上下文：注入静态字段供转换器使用。

    Attributes:
        machine_id: 机床标识（对应 ``machines.json`` 中的 ``machine.id``）。
        tool_id: 刀具标识（对应 ``tools.json`` 中的 ``tool.id``）。
        material: 工件材料名称。
        series_id_prefix: TDengine 时序子表命名空间前缀，避免不同机床 /
            刀具 / 加工任务之间的 series 冲突。生成规则：
            ``{prefix}_{machine_id}_{tool_id}_{start_ts}``。
        process_params: 附加工艺参数（depth_of_cut / coolant / operation 等）。
    """

    machine_id: str
    tool_id: str
    material: str
    series_id_prefix: str = "mach"
    process_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.machine_id or not self.tool_id or not self.material:
            raise ValueError("CollectorContext.machine_id / tool_id / material are required")


# Single sample MachiningRecordCreate


def _safe_float(value: float | None, default: float = 0.0) -> float:
    """将 ``None`` 替换为 ``default``，保证 Pydantic 字段非空。"""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN guard
        return default
    return f


def convert_sample_to_record(
    sample: Sample,
    context: CollectorContext,
    *,
    record_id: str | None = None,
    timestamp: datetime | None = None,
) -> MachiningRecordCreate:
    """把一条 MTConnect 样本转成 MachiningRecordCreate。

    注意：本函数对缺失字段采用"零值兜底"策略（spindle_speed=0, feed_rate=0），
    数据清洗在后续阶段实现。``observed_at`` 不存在时使用 ``timestamp`` 形参或
    当前 UTC 时间。
    """
    ts = timestamp or sample.observed_at or datetime.now(timezone.utc)
    series_id = _build_series_id(context, ts, suffix=str(uuid.uuid4().hex[:8]))
    return MachiningRecordCreate(
        record_id=record_id,
        machine_id=context.machine_id,
        tool_id=context.tool_id,
        material=context.material,
        timestamp=ts,
        spindle_speed=_safe_float(sample.spindle_speed),
        feed_rate=_safe_float(sample.feedrate),
        tdengine_series_id=series_id,
        process_params={
            **context.process_params,
            "spindle_load": sample.spindle_load,
            "execution": sample.execution,
            "extras": dict(sample.extras or {}),
        },
    )


# N samples 1 MachiningRecordCreate (batch aggregation)


def _build_series_id(context: CollectorContext, start_ts: datetime, suffix: str = "") -> str:
    """构造 TDengine 时序子表 ID。

    Format: ``{prefix}_{machine_id}_{tool_id}_{utc_ts}_{suffix}``
    例: ``mach_CNC-01_T-EM-10_20260611T102345Z_ab12cd34``
    """
    ts_compact = start_ts.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = [context.series_id_prefix, context.machine_id, context.tool_id, ts_compact]
    if suffix:
        parts.append(suffix)
    return "_".join(parts)


def aggregate_samples_to_record(
    samples: Sequence[Sample],
    context: CollectorContext,
    *,
    record_id: str | None = None,
    strategy: str = "mean",
) -> MachiningRecordCreate:
    """把 N 条 Sample 聚合为 1 条 MachiningRecordCreate（关系型入库）。

    聚合策略：
        * ``mean``     - 主轴转速 / 进给速度取算术平均（默认）
        * ``last``     - 取最后一条样本的值
        * ``max``      - 主轴转速 / 进给速度取最大值
        * ``min``      - 主轴转速 / 进给速度取最小值

    Args:
        samples: 一组有序 MTConnect 样本（按时间升序）。
        context: 采集上下文。
        record_id: 可选记录 ID；为空则由仓储层自动生成。
        strategy: 聚合策略。

    Returns:
        聚合后的 ``MachiningRecordCreate`` 实例。

    Raises:
        ValueError: 当 ``samples`` 为空或 ``strategy`` 未知时。
    """
    if not samples:
        raise ValueError("aggregate_samples_to_record requires at least one sample")
    if strategy not in {"mean", "last", "max", "min"}:
        raise ValueError(f"Unknown aggregation strategy: {strategy!r}; expected one of: mean, last, max, min")

    start_ts = samples[0].observed_at or datetime.now(timezone.utc)
    end_ts = samples[-1].observed_at or start_ts

    speed_values = [s.spindle_speed for s in samples if s.spindle_speed is not None]
    feed_values = [s.feedrate for s in samples if s.feedrate is not None]
    load_values = [s.spindle_load for s in samples if s.spindle_load is not None]
    executions = [s.execution for s in samples if s.execution is not None]

    def _reduce(values: list[float], strat: str) -> float:
        if not values:
            return 0.0
        if strat == "mean":
            return float(statistics.fmean(values))
        if strat == "max":
            return float(max(values))
        if strat == "min":
            return float(min(values))
        # last 取最后一条样本
        return float(values[-1])

    if strategy == "last":
        spindle_speed = _safe_float(samples[-1].spindle_speed)
        feed_rate = _safe_float(samples[-1].feedrate)
    else:
        spindle_speed = _reduce(speed_values, strategy)
        feed_rate = _reduce(feed_values, strategy)

    spindle_load = _reduce(load_values, "mean") if load_values else None
    execution = executions[-1] if executions else None

    series_id = _build_series_id(context, start_ts, suffix=f"agg{uuid.uuid4().hex[:6]}")

    duration = (end_ts - start_ts).total_seconds() if end_ts >= start_ts else 0.0

    return MachiningRecordCreate(
        record_id=record_id,
        machine_id=context.machine_id,
        tool_id=context.tool_id,
        material=context.material,
        timestamp=start_ts,
        spindle_speed=spindle_speed,
        feed_rate=feed_rate,
        tdengine_series_id=series_id,
        process_params={
            **context.process_params,
            "window_size": len(samples),
            "aggregation_strategy": strategy,
            "window_duration_s": round(duration, 3),
            "spindle_load": spindle_load,
            "execution": execution,
            "extras": dict(samples[-1].extras or {}),
        },
    )


# Rolling aggregator state


class SampleBatchAggregator:
    """按时间窗或条数窗累积 :class:`Sample` 并触发聚合。

    典型用法（在 :mod:`machining_collector` 中）::

        agg = SampleBatchAggregator(flush_interval=5.0, batch_size=100)
        while running:
            sample = await fetch_one_sample()
            agg.add(sample)
            if agg.should_flush(now=time.monotonic()):
                records = agg.flush_records(context, strategy="mean")
                await write_to_postgres(records)
                agg.mark_flushed()
    """

    def __init__(
        self,
        *,
        flush_interval: float = 5.0,
        batch_size: int = 100,
        max_samples_per_record: int = 1000,
    ) -> None:
        if flush_interval <= 0:
            raise ValueError("flush_interval must be > 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if max_samples_per_record <= 0:
            raise ValueError("max_samples_per_record must be > 0")
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.max_samples_per_record = max_samples_per_record
        self._buffer: deque[Sample] = deque()
        self._last_flush_at: float | None = None
        self._current_window_start: datetime | None = None

    # ----------------------------------------------------------------- add

    def add(self, sample: Sample) -> None:
        """向缓冲区追加一个样本。"""
        if self._current_window_start is None and sample.observed_at is not None:
            self._current_window_start = sample.observed_at
        self._buffer.append(sample)
        # 防止异常长尾场景下内存爆掉
        while len(self._buffer) > self.max_samples_per_record:
            self._buffer.popleft()

    def extend(self, samples: Sequence[Sample]) -> None:
        """批量追加样本。"""
        for s in samples:
            self.add(s)

    # ----------------------------------------------------------------- stats

    def __len__(self) -> int:
        return len(self._buffer)

    @property
    def window_age_seconds(self) -> float:
        """当前窗口第一条样本距今的秒数。"""
        if self._current_window_start is None:
            return 0.0
        now = datetime.now(timezone.utc)
        return (now - self._current_window_start).total_seconds()

    # ----------------------------------------------------------------- flush

    def should_flush(self, now_monotonic: float | None = None) -> bool:
        """判断是否到达 flush 条件（条数阈值或时间窗阈值）。"""
        if not self._buffer:
            return False
        if len(self._buffer) >= self.batch_size:
            return True
        if self._last_flush_at is None:
            # 第一次 flush 用窗口起始时间作为基线
            baseline = self._current_window_start.timestamp() if self._current_window_start else None
            if baseline is None:
                return False

            if now_monotonic is None:
                now_monotonic = time.monotonic()
            return (now_monotonic - baseline) >= self.flush_interval
        if self.window_age_seconds >= self.flush_interval:
            return True
        return False

    def flush_records(
        self,
        context: CollectorContext,
        *,
        strategy: str = "mean",
    ) -> list[MachiningRecordCreate]:
        """把缓冲区的所有样本聚合成 1 条 MachiningRecordCreate。"""
        if not self._buffer:
            return []
        samples = list(self._buffer)
        record = aggregate_samples_to_record(
            samples=samples,
            context=context,
            strategy=strategy,
        )
        return [record]

    def mark_flushed(self) -> None:
        """清空缓冲区并重置窗口计时。"""
        self._buffer.clear()
        self._last_flush_at = time.monotonic()
        self._current_window_start = None

    # ----------------------------------------------------------------- introspection

    def snapshot(self) -> dict[str, Any]:
        """导出当前聚合器状态（用于调试 / 指标）。"""
        return {
            "buffer_size": len(self._buffer),
            "batch_size": self.batch_size,
            "flush_interval": self.flush_interval,
            "window_age_seconds": self.window_age_seconds,
            "last_flush_at": self._last_flush_at,
        }


__all__ = [
    "CollectorContext",
    "convert_sample_to_record",
    "aggregate_samples_to_record",
    "SampleBatchAggregator",
]
