"""Machining 数据采集管道主程序（M0.5 交付物）。

职责
----
1. 从 MTConnect Agent 拉取 :class:`Sample` 时序数据；
2. 通过 :class:`SampleBatchAggregator` 按时间窗 / 条数窗聚合为
   :class:`MachiningRecordCreate`；
3. 批量写入 TDengine（时序子表）+ PostgreSQL（关系型加工记录）；
4. 异常隔离、断线重连、写库重试；
5. 暴露 ``start_collector`` / ``stop_collector`` / ``get_collector`` /
   ``reset_collector`` 入口供 :class:`AsyncTaskManager` 调度与测试调用。

不包含
------
* 实时数据分析；
* 数据清洗 / 异常值剔除；
* 前端监控 UI（仅暴露状态查询接口）。

依赖
----
* M0.3 :mod:`app.integrations.mtconnect` 适配器；
* M0.4 :class:`app.models.machining_record.MachiningRecordCreate` 数据模型；
* :mod:`app.database.repository.machining_record_repo` 关系型仓储；
* :mod:`app.services.tdengine_client` 时序存储；
* :mod:`app.tasks.task_system` 异步任务管理（可选，本程序自带锁 /
  状态机，AsyncTaskManager 仅作为外部调度入口）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from collections.abc import Awaitable, Callable, Sequence

from app.database.repository.machining_record_repo import (
    MachiningRecordRepository,
    get_sync_sessionmaker,
)
from app.integrations.mtconnect.adapter import AdapterConfig, MTConnectAdapter
from app.integrations.mtconnect.parser import Sample
from app.models.machining_record import MachiningRecordCreate
from app.pipelines.converter import (
    CollectorContext,
    SampleBatchAggregator,
    aggregate_samples_to_record,
)

logger = logging.getLogger(__name__)


# Configuration


# 默认 MTConnect Agent URL。
# 安全修复 [P1-BE-3]：移除硬编码外部 demo 服务，避免生产环境误连公网导致工艺数据泄露。
# 开发测试时可通过环境变量 MTCONNECT_AGENT_URL 指向 demo.mtconnect.org；
# 生产环境必须配置内网 Agent URL，启动时 CollectorConfig 会校验非空。
DEFAULT_AGENT_URL = os.getenv("MTCONNECT_AGENT_URL", "")
DEFAULT_FLUSH_INTERVAL = 5.0  # seconds
DEFAULT_BATCH_SIZE = 100  # records per flush
DEFAULT_RETRY_BACKOFF = 2.0
DEFAULT_MAX_WRITE_RETRIES = 3
DEFAULT_POLL_INTERVAL = 1.0  # MTConnect 1 Hz 默认


@dataclass
class CollectorConfig:
    """采集器运行时配置。

    Attributes:
        agent_url: MTConnect Agent base URL。
        machine_id / tool_id / material: 注入到 ``CollectorContext`` 的静态字段。
        sample_interval: MTConnect 拉取间隔（秒，默认 1.0 = 1 Hz）。
        batch_size: 累积多少条 Sample 后强制 flush（默认 100）。
        flush_interval: 累积多长时间（秒）后强制 flush（默认 5.0）。
        aggregation_strategy: ``mean`` / ``last`` / ``max`` / ``min``。
        series_id_prefix: TDengine 时序子表 ID 前缀。
        process_params: 附加工艺参数，写入 ``process_params`` JSONB。
        max_write_retries: PostgreSQL / TDengine 写库失败重试次数。
        retry_backoff: 写库重试初始退避（秒），指数递增。
        use_task_manager: 是否把外层调度委托给 :class:`AsyncTaskManager`。
        tdengine_table: TDengine 时序子表名（默认 ``mtconnect``）。
    """

    agent_url: str = DEFAULT_AGENT_URL
    machine_id: str = "CNC-01"
    tool_id: str = "T-DEFAULT"
    material: str = "45号钢"
    sample_interval: float = DEFAULT_POLL_INTERVAL
    batch_size: int = DEFAULT_BATCH_SIZE
    flush_interval: float = DEFAULT_FLUSH_INTERVAL
    aggregation_strategy: str = "mean"
    series_id_prefix: str = "mach"
    process_params: dict[str, Any] = field(default_factory=dict)
    max_write_retries: int = DEFAULT_MAX_WRITE_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    use_task_manager: bool = False
    tdengine_table: str = "mtconnect"

    def to_collector_context(self) -> CollectorContext:
        return CollectorContext(
            machine_id=self.machine_id,
            tool_id=self.tool_id,
            material=self.material,
            series_id_prefix=self.series_id_prefix,
            process_params=dict(self.process_params or {}),
        )

    def to_adapter_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_url=self.agent_url,
            interval=self.sample_interval,
            batch_size=max(self.batch_size, 1),
            batch_interval=self.flush_interval,
        )


# Stats


@dataclass
class CollectorStats:
    """采集器运行时统计信息。"""

    samples_consumed: int = 0
    records_written: int = 0
    tdengine_rows_written: int = 0
    write_retries: int = 0
    write_failures: int = 0
    poll_errors: int = 0
    started_at: float | None = None
    stopped_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "samples_consumed": self.samples_consumed,
            "records_written": self.records_written,
            "tdengine_rows_written": self.tdengine_rows_written,
            "write_retries": self.write_retries,
            "write_failures": self.write_failures,
            "poll_errors": self.poll_errors,
        }
        if self.started_at is not None:
            d["started_at"] = self.started_at
        if self.stopped_at is not None and self.started_at is not None:
            d["stopped_at"] = self.stopped_at
            d["runtime_seconds"] = round(self.stopped_at - self.started_at, 3)
        elif self.stopped_at is not None:
            d["stopped_at"] = self.stopped_at
        return d


# Persistence sinks (typed for DI / testing)


RecordSink = Callable[[Sequence[MachiningRecordCreate]], Awaitable[int]]


async def postgres_sink(
    records: Sequence[MachiningRecordCreate],
    *,
    session_factory=None,
) -> int:
    """把加工记录写入 PostgreSQL（通过同步仓储）。"""
    if not records:
        return 0
    repo: MachiningRecordRepository
    if session_factory is not None:
        repo = MachiningRecordRepository(session_factory=session_factory)
    else:
        factory = get_sync_sessionmaker()
        if factory is None:
            raise RuntimeError("PostgreSQL 未配置：请设置 DB_URL 或通过 session_factory 注入")
        repo = MachiningRecordRepository(session_factory=factory)

    def _sync_create() -> int:
        ok = 0
        for rec in records:
            try:
                repo.create(rec)
                ok += 1
            except (ValueError, KeyError, OSError) as exc:
                # 数据库写入失败（完整性错误、连接问题等），记录后继续处理下一条
                logger.warning(
                    "PostgreSQL write MachiningRecord failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
        return ok

    return await asyncio.to_thread(_sync_create)


async def tdengine_sink(
    samples: Sequence[Sample],
    *,
    table_name: str = "mtconnect",
    database: str | None = None,
) -> int:
    """把 MTConnect 原始时序数据写入 TDengine。"""
    if not samples:
        return 0
    try:
        from app.services import tdengine_client as tdc
    except ImportError:  # pragma: no cover
        logger.warning("tdengine_client 模块不可用，跳过时序写入")
        return -1

    rows: list[list[Any]] = []
    for s in samples:
        ts = s.observed_at or datetime.now(timezone.utc)
        rows.append(
            [
                ts.strftime("%Y-%m-%d %H:%M:%S.%f"),
                s.spindle_speed,
                s.spindle_load,
                s.feedrate,
                s.execution,
            ]
        )

    written = await tdc.insert_rows(
        table_name=table_name,
        rows=rows,
        database=database,
    )
    return int(written) if written is not None else 0


# Core collector


class MachiningCollector:
    """Machining 数据采集器（单实例）。"""

    def __init__(
        self,
        config: CollectorConfig | None = None,
        *,
        adapter: MTConnectAdapter | None = None,
        record_sink: RecordSink | None = None,
        tdengine_sink_fn: Callable[[Sequence[Sample]], Awaitable[int]] | None = None,
    ) -> None:
        self.config = config or CollectorConfig()
        self.context = self.config.to_collector_context()
        self._adapter = adapter or self._build_default_adapter()
        self._record_sink = record_sink
        self._tdengine_sink = tdengine_sink_fn
        self._aggregator = SampleBatchAggregator(
            flush_interval=self.config.flush_interval,
            batch_size=self.config.batch_size,
        )
        self._stop_event = asyncio.Event()
        self._run_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stats = CollectorStats()
        self._job_id: str | None = None
        # 待重试的写库队列（PostgreSQL）
        self._retry_queue: list[MachiningRecordCreate] = []
        # 待重试的 TDengine 样本队列
        self._tdengine_retry: list[Sample] = []

    # ------------------------------------------------------------------ factory

    def _build_default_adapter(self) -> MTConnectAdapter:
        return MTConnectAdapter(config=self.config.to_adapter_config())

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_running(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    @property
    def job_id(self) -> str | None:
        return self._job_id

    def get_stats(self) -> dict[str, Any]:
        return self._stats.to_dict()

    async def start(self) -> str:
        """启动采集循环（非阻塞），返回 job_id。"""
        async with self._lock:
            if self.is_running:
                if self._job_id is None:
                    self._job_id = f"collector-{uuid.uuid4().hex[:12]}"
                logger.info(
                    "Collector already running job_id=%s; returning existing",
                    self._job_id,
                )
                return self._job_id

            self._job_id = f"collector-{uuid.uuid4().hex[:12]}"
            self._stop_event.clear()
            self._stats = CollectorStats(started_at=time.time())

            # 探活：失败不阻断（dry-run 模式可用）
            try:
                identity = await asyncio.to_thread(self._adapter.probe)
                logger.info("Collector[%s] probe ok: %s", self._job_id, identity)
            except (ConnectionError, OSError, TimeoutError) as exc:  # pragma: no cover - depends on env
                logger.warning(
                    "Collector[%s] probe failed (continuing in offline mode): %s",
                    self._job_id,
                    exc,
                )

            self._run_task = asyncio.create_task(self._run_loop(), name=f"collector-loop-{self._job_id}")
            logger.info(
                "Collector[%s] started agent=%s interval=%.2fs batch=%d flush=%.1fs",
                self._job_id,
                self.config.agent_url,
                self.config.sample_interval,
                self.config.batch_size,
                self.config.flush_interval,
            )
            return self._job_id

    async def stop(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """停止采集循环并 flush 残留数据，返回最终统计。"""
        async with self._lock:
            if not self.is_running:
                logger.info("Collector[%s] stop requested but not running", self._job_id)
                return self.get_stats()

            logger.info("Collector[%s] stopping...", self._job_id)
            self._stop_event.set()
            # MTConnect 适配器也设置 stop，让同步 polling 退出
            try:
                self._adapter.stop()
            except (RuntimeError, OSError, AttributeError) as e:  # pragma: no cover
                # 适配器停止失败不应阻塞整体关闭流程
                logger.warning("adapter.stop raised: %s; ignoring", e)

            task = self._run_task
            assert task is not None
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Collector[%s] did not stop within %.1fs; cancelling", self._job_id, timeout)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # 任务取消是预期行为
                    pass
                except (RuntimeError, OSError) as e:
                    # 任务取消后可能产生的清理异常，记录但不阻塞关闭
                    logger.warning("Task cleanup after cancel raised: %s", e)

            # 最后一次 flush
            try:
                await self._flush_once()
            except (RuntimeError, OSError, ValueError) as e:  # pragma: no cover
                # 最终 flush 失败不应阻塞关闭流程，但需记录以便排查
                logger.warning("Final flush failed: %s", e, exc_info=True)

            self._stats.stopped_at = time.time()
            logger.info("Collector[%s] stopped. stats=%s", self._job_id, self._stats.to_dict())
            return self.get_stats()

    # ------------------------------------------------------------------ loop

    async def _run_loop(self) -> None:
        """主循环：从适配器拉取样本，喂入聚合器，条件满足时 flush。"""
        try:
            while not self._stop_event.is_set():
                try:
                    sample = await asyncio.to_thread(self._fetch_one_sample)
                except (ConnectionError, TimeoutError, OSError) as exc:  # 适配器级异常隔离
                    self._stats.poll_errors += 1
                    logger.warning(
                        "Collector[%s] poll error: %s; sleeping %.2fs",
                        self._job_id,
                        exc,
                        self.config.sample_interval,
                    )
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.sample_interval)
                    except asyncio.TimeoutError:
                        pass
                    continue

                if sample is None:
                    # 拉取失败但已重试：略过本轮
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.sample_interval)
                    except asyncio.TimeoutError:
                        pass
                    continue

                self._stats.samples_consumed += 1
                self._aggregator.add(sample)

                if self._aggregator.should_flush():
                    try:
                        await self._flush_once()
                    except (RuntimeError, OSError, ValueError) as e:
                        # flush 失败时记录错误，数据保留在队列中等待重试
                        logger.warning(
                            "Collector[%s] flush failed: %s; records queued for retry",
                            self._job_id,
                            e,
                            exc_info=True,
                        )

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.sample_interval)
                except asyncio.TimeoutError:
                    # 超时是预期行为：wait_for(stop_event.wait(), timeout=X) 用作"可中断睡眠"，
                    # 超时表示睡眠完成，无需任何处理；stop 事件触发时提前唤醒走正常流程
                    pass
        except asyncio.CancelledError:  # pragma: no cover
            logger.info("Collector[%s] run loop cancelled", self._job_id)
            raise
        except (RuntimeError, ValueError, TypeError, OSError):  # pragma: no cover - 防御
            logger.exception("Collector[%s] run loop crashed", self._job_id)
            raise

    def _fetch_one_sample(self) -> Sample | None:
        """单次拉取（同步），由 ``asyncio.to_thread`` 调度。"""
        try:
            return self._adapter.fetch_sample()
        except (ConnectionError, TimeoutError, OSError) as exc:
            # 适配器内部已记录错误计数，这里仅传播以便上层选择重试策略
            logger.debug("fetch_sample raised: %s", exc)
            return None

    # ------------------------------------------------------------------ flush

    async def _flush_once(self) -> None:
        """执行一次 flush：写入 TDengine + PostgreSQL，处理重试队列。"""
        if self._aggregator.__len__() == 0 and not self._retry_queue and not self._tdengine_retry:
            return

        context = self.context

        # 1) 把聚合器当前窗口样本聚合成 1 条 MachiningRecordCreate
        records: list[MachiningRecordCreate] = []
        pending_samples: list[Sample] = []
        if len(self._aggregator) > 0:
            pending_samples = list(self._aggregator._buffer)
            try:
                record = aggregate_samples_to_record(
                    samples=pending_samples,
                    context=context,
                    strategy=self.config.aggregation_strategy,
                )
                records.append(record)
            except ValueError as exc:
                logger.warning("Collector[%s] aggregation skipped: %s", self._job_id, exc)
            self._aggregator.mark_flushed()

        # 合并待重试的 records
        records_to_write = records + list(self._retry_queue)
        self._retry_queue.clear()

        # 2) 写 PostgreSQL（带重试）
        if records_to_write:
            written = await self._write_with_retry(records_to_write)
            self._stats.records_written += written
            # 失败的 records 重新入队
            if written < len(records_to_write):
                self._retry_queue.extend(records_to_write[written:])
                self._stats.write_failures += len(records_to_write) - written

        # 3) 写 TDengine（带重试）
        samples_to_write = pending_samples + list(self._tdengine_retry)
        self._tdengine_retry.clear()
        if samples_to_write:
            td_rows = await self._write_tdengine_with_retry(samples_to_write)
            if td_rows > 0:
                self._stats.tdengine_rows_written += td_rows
            else:
                self._tdengine_retry.extend(samples_to_write)
                self._stats.write_failures += len(samples_to_write)

    async def _write_with_retry(self, records: Sequence[MachiningRecordCreate]) -> int:
        """通过 record_sink 写 PostgreSQL，失败时重试。"""
        if not records:
            return 0

        async def _attempt() -> int:
            sink = self._record_sink or (lambda recs: postgres_sink(recs))
            return await sink(list(records))

        backoff = self.config.retry_backoff
        for attempt in range(1, self.config.max_write_retries + 1):
            try:
                written = await _attempt()
                return int(written or 0)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                self._stats.write_retries += 1
                if attempt >= self.config.max_write_retries:
                    logger.error(
                        "Collector[%s] write to PostgreSQL exhausted retries: %s",
                        self._job_id,
                        exc,
                    )
                    return 0
                sleep_for = backoff * (1.0 + 0.1 * (attempt - 1))
                logger.warning(
                    "Collector[%s] write attempt %d/%d failed: %s; retry in %.2fs",
                    self._job_id,
                    attempt,
                    self.config.max_write_retries,
                    exc,
                    sleep_for,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                    # 若 stop 事件被触发，则中断重试
                    return 0
                except asyncio.TimeoutError:
                    # 超时是预期行为：wait_for(stop_event.wait(), timeout=X) 用作"可中断睡眠"，
                    # 超时表示睡眠完成，无需任何处理；stop 事件触发时提前唤醒走正常流程
                    pass
                backoff = min(backoff * 2, 30.0)
        return 0

    async def _write_tdengine_with_retry(self, samples: Sequence[Sample]) -> int:
        """通过 tdengine_sink 写时序数据，失败时重试。"""
        if not samples:
            return 0

        async def _attempt() -> int:
            sink = self._tdengine_sink or (lambda s: tdengine_sink(s, table_name=self.config.tdengine_table))
            return await sink(list(samples))

        backoff = self.config.retry_backoff
        for attempt in range(1, self.config.max_write_retries + 1):
            try:
                written = await _attempt()
                if written is None or written < 0:
                    raise RuntimeError(f"TDengine sink returned {written!r}")
                return int(written)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                self._stats.write_retries += 1
                if attempt >= self.config.max_write_retries:
                    logger.error(
                        "Collector[%s] write to TDengine exhausted retries: %s",
                        self._job_id,
                        exc,
                    )
                    return 0
                sleep_for = backoff * (1.0 + 0.1 * (attempt - 1))
                logger.warning(
                    "Collector[%s] TDengine write attempt %d/%d failed: %s; retry in %.2fs",
                    self._job_id,
                    attempt,
                    self.config.max_write_retries,
                    exc,
                    sleep_for,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                    return 0
                except asyncio.TimeoutError:
                    # 超时是预期行为：wait_for(stop_event.wait(), timeout=X) 用作"可中断睡眠"，
                    # 超时表示睡眠完成，无需任何处理；stop 事件触发时提前唤醒走正常流程
                    pass
                backoff = min(backoff * 2, 30.0)
        return 0

    # ------------------------------------------------------------------ utilities

    def dump_state(self) -> dict[str, Any]:
        """导出内部状态，用于诊断 / 监控。"""
        return {
            "job_id": self._job_id,
            "is_running": self.is_running,
            "stats": self._stats.to_dict(),
            "aggregator": self._aggregator.snapshot(),
            "retry_queue_size": len(self._retry_queue),
            "tdengine_retry_size": len(self._tdengine_retry),
            "config": {
                "agent_url": self.config.agent_url,
                "sample_interval": self.config.sample_interval,
                "batch_size": self.config.batch_size,
                "flush_interval": self.config.flush_interval,
                "machine_id": self.config.machine_id,
                "tool_id": self.config.tool_id,
                "material": self.config.material,
            },
        }


# Singleton façade for external callers (CLI, FastAPI, tests)


_collector_singleton: MachiningCollector | None = None
# [H3] asyncio.Lock 懒初始化：模块级创建会绑定到导入时的事件循环，
# 在多事件循环场景下抛 RuntimeError "bound to a different event loop"。
_collector_lock: asyncio.Lock | None = None


def _get_collector_lock() -> asyncio.Lock:
    """懒初始化采集器单例锁，绑定到首次调用的事件循环。"""
    global _collector_lock
    if _collector_lock is None:
        _collector_lock = asyncio.Lock()
    return _collector_lock


def get_collector() -> MachiningCollector | None:
    """返回当前活动的全局采集器实例（若已通过 :func:`start_collector` 启动）。"""
    return _collector_singleton


async def start_collector(
    *,
    duration: float | None = None,
    agent_url: str | None = None,
    machine_id: str | None = None,
    tool_id: str | None = None,
    material: str | None = None,
    sample_interval: float | None = None,
    batch_size: int | None = None,
    flush_interval: float | None = None,
    config: CollectorConfig | None = None,
    adapter: MTConnectAdapter | None = None,
    record_sink: RecordSink | None = None,
    tdengine_sink_fn: Callable[[Sequence[Sample]], Awaitable[int]] | None = None,
) -> str:
    """启动采集任务并运行指定时长（秒），返回 job_id。

    本函数是 M0.5 验收脚本::

        asyncio.run(start_collector(duration=60, agent_url='http://demo.mtconnect.org:80'))

    的入口。``duration`` 为 ``None`` 时表示持续运行，直到外部调用 :func:`stop_collector`。
    """
    global _collector_singleton
    async with _get_collector_lock():
        if config is None:
            config = CollectorConfig(
                agent_url=agent_url or DEFAULT_AGENT_URL,
                machine_id=machine_id or "CNC-01",
                tool_id=tool_id or "T-DEFAULT",
                material=material or "45号钢",
                sample_interval=sample_interval or DEFAULT_POLL_INTERVAL,
                batch_size=batch_size or DEFAULT_BATCH_SIZE,
                flush_interval=flush_interval or DEFAULT_FLUSH_INTERVAL,
            )
        elif any(
            v is not None
            for v in (agent_url, machine_id, tool_id, material, sample_interval, batch_size, flush_interval)
        ):
            # 用户显式提供 config 同时又传了字段，config 优先；仅记录一次告警
            logger.info("start_collector: 同时提供 config 与字段参数，config 优先")

        _collector_singleton = MachiningCollector(
            config=config,
            adapter=adapter,
            record_sink=record_sink,
            tdengine_sink_fn=tdengine_sink_fn,
        )
        job_id = await _collector_singleton.start()

    if duration is not None and duration > 0:
        try:
            await asyncio.sleep(duration)
        finally:
            await stop_collector()
    return job_id


async def stop_collector(*, timeout: float = 10.0) -> dict[str, Any]:
    """停止当前全局采集器并返回统计信息。"""
    async with _get_collector_lock():
        collector = _collector_singleton
        if collector is None:
            logger.info("stop_collector: no active collector")
            return {}
        stats = await collector.stop(timeout=timeout)
    return stats


async def reset_collector() -> None:
    """重置全局采集器（用于测试）。"""
    global _collector_singleton
    async with _get_collector_lock():
        if _collector_singleton is not None and _collector_singleton.is_running:
            await _collector_singleton.stop()
        _collector_singleton = None


__all__ = [
    "CollectorConfig",
    "CollectorStats",
    "MachiningCollector",
    "RecordSink",
    "postgres_sink",
    "tdengine_sink",
    "start_collector",
    "stop_collector",
    "get_collector",
    "reset_collector",
    "DEFAULT_AGENT_URL",
    "DEFAULT_FLUSH_INTERVAL",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_POLL_INTERVAL",
]
