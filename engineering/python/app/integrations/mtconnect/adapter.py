"""MTConnect Adapter V1 – HTTP polling + TDengine persistence.

The :class:`MTConnectAdapter` is the entry point used by both the CLI
and any future background workers.  Its responsibilities are deliberately
narrow:

1. **Probe the agent** – verify it speaks MTConnect and surface a
   useful error if it does not.
2. **Poll the ``/sample`` endpoint** at a configurable rate (default
   1 Hz, per the M0.3 spec).
3. **Parse the response** with :func:`app.integrations.mtconnect.parser
   .parse_sample_response`.
4. **Buffer samples** until either a batch-size threshold or a flush
   interval is reached, then persist them with the project's TDengine
   client (M0.2 deliverable).
5. **Exponential back-off retry** on transient network errors so a
   momentary agent hiccup does not derail the whole stream.

The class is intentionally **synchronous** in its public API so it can
be driven from both the CLI (``python -m``) and the FastAPI side
through ``run_in_executor`` – the underlying ``requests`` library is
synchronous and there is no need to pay the async tax in M0.3.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable
from xml.etree import ElementTree as ET

import requests
from requests import Session

from app.integrations.mtconnect.parser import Sample, parse_sample_response

from app.integrations._common import parse_tds_url
from app.utils.xml_guard import parse_untrusted_xml

logger = logging.getLogger(__name__)

# MTConnect 异步 future 的统一等待超时（秒）。用于跨线程提交的协程结果回收，
# 避免因事件循环阻塞导致采集线程长时间挂起。
DEFAULT_MTCONNECT_FUTURE_TIMEOUT_SEC: float = 10.0


def _local_attr_name(name: str) -> str:
    """剥掉 XML Clark notation 命名空间前缀，返回本地属性名。

    MTConnect 流数据属性形如 ``{urn:mtconnect.org:MTConnectStreams:1.8}timestamp``，
    只取 ``}`` 之后的本地名（F821 修复：历史实现引用但从未定义）。
    """
    return name.split("}")[-1]


# Configuration


# Table schema for TDengine. Kept here (rather than in the TDengine
# client) because the column set is a property of the *MTConnect*
# contract, not of the storage backend.
DEFAULT_TABLE_DDL: tuple[str, ...] = (
    "(ts TIMESTAMP, ",
    "spindle_speed DOUBLE, ",
    "spindle_load DOUBLE, ",
    "feedrate DOUBLE, ",
    "execution BINARY(32))",
)


@dataclass
class AdapterConfig:
    """Runtime configuration for :class:`MTConnectAdapter`."""

    # Network
    agent_url: str = "http://demo.mtconnect.org:80"
    timeout: float = 10.0
    sample_path: str = "/sample"

    # Polling
    interval: float = 1.0  # seconds between samples (1 Hz default)

    # Batching
    batch_size: int = 10  # flush after this many samples
    batch_interval: float = 5.0  # ...or after this many seconds
    # 持久化失败时批次会回灌缓冲区等待重试；该上限防止 TDengine 长时间
    # 不可用时缓冲无界增长。超出上限时丢弃最旧的样本并计入 dropped_samples。
    max_buffer: int = 1000

    # Retry
    max_retries: int = 5  # bounded – never spin forever
    initial_backoff: float = 0.5  # seconds for the first retry
    max_backoff: float = 16.0  # ...but capped so we don't sleep forever

    # Storage
    database: str = "test"  # TDengine DB (override per env)
    table: str = "mtconnect"  # TDengine super/sub-table name

    def __post_init__(self) -> None:
        # Normalise the agent URL so the rest of the code can rely on
        # ``urljoin`` semantics. Trailing slashes are removed to
        # avoid double-slash URLs after path joining.
        self.agent_url = self.agent_url.rstrip("/")
        if not self.agent_url.lower().startswith(("http://", "https://")):
            raise ValueError(f"agent_url must be an http(s) URL, got: {self.agent_url!r}")
        if self.max_buffer < 1:
            raise ValueError(f"max_buffer must be >= 1, got: {self.max_buffer!r}")


# Adapter


# Callback type used by the adapter to publish "live" samples to the
# CLI or any other observer. Receives a fully populated ``Sample``.
SampleCallback = Callable[[Sample], None]


class MTConnectAdapter:
    """Polling + persistence orchestrator for a single MTConnect Agent.

    Typical usage::

        cfg = AdapterConfig(agent_url="http://demo.mtconnect.org", interval=1.0)
        adapter = MTConnectAdapter(cfg)
        adapter.probe()                              # fail fast on misconfig
        adapter.run(duration=20.0, on_sample=print)  # blocks for 20 s

    Or, in a long-running service, drain the batch buffer on a timer.
    """

    # Construction

    def __init__(
        self,
        config: AdapterConfig | None = None,
        session: Session | None = None,
        tdengine_client: Any | None = None,
    ) -> None:
        self.config = config or AdapterConfig()
        # Allow tests to inject a mocked ``requests.Session``; in
        # production we just create a real one with a sensible UA.
        self._session = session or self._build_default_session()
        # Same trick for the TDengine client – the dependency is
        # imported lazily so unit tests don't need a running TDengine.
        self._tdengine = tdengine_client
        # Internal state
        self._buffer: list[Sample] = []
        self._buffer_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_flush = time.monotonic()
        self._ingested_count = 0
        self._error_count = 0
        self._dropped_count = 0

    def close(self) -> None:
        """Close the HTTP session and release connection pool resources."""
        if self._session is not None:
            self._session.close()
            logger.debug("MTConnect adapter session closed")

    def __enter__(self) -> "MTConnectAdapter":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()

    # Public API

    def probe(self) -> dict[str, str]:
        """Verify the agent responds to ``/probe``.

        Returns a dict with the agent's identity information:

        * ``instance_id``
        * ``sender``
        * ``version``
        * ``mtconnect_version``

        Raises:
            requests.RequestException: on transport errors
            RuntimeError: if the response is not a valid MTConnect probe
        """
        url = f"{self.config.agent_url}/probe"
        logger.info("Probing MTConnect agent at %s", url)
        response = self._session.get(url, timeout=self.config.timeout)
        response.raise_for_status()

        root = parse_untrusted_xml(response.text)
        # The MTConnect namespace is declared on the root element, which
        # means ElementTree exposes all attributes (including ``sender``
        # / ``instanceId``) with the Clark notation prefix
        # ``{urn:mtconnect.org:MTConnectDevices:1.5}attr``. We build a
        # lookup that transparently handles both namespaced and
        # namespace-free payloads so the parser works for v1.5 as well
        # as for any future spec revision that may drop the namespace.
        #
        # ``sender`` / ``instanceId`` / ``mtconnectVersion`` live on the
        # ``<Header>`` child element (per the MTConnect spec), *not* on
        # the document root. We therefore merge the root attributes
        # with those of the first ``<Header>`` we find in the tree so
        # the lookup below works for both the strict v1.5 layout and
        # for any conformant agent that places the identity header on
        # the root element.
        namespace_attrs: dict[str, str] = {}
        for attr_name, attr_value in root.attrib.items():
            namespace_attrs[_local_attr_name(attr_name)] = attr_value
        for header in root.iter():
            if _local_attr_name(header.tag) == "Header":
                for attr_name, attr_value in header.attrib.items():
                    namespace_attrs.setdefault(_local_attr_name(attr_name), attr_value)
                break

        # Map the spec attribute names (which may be CamelCase) onto the
        # dict keys we expose to callers (snake_case for ergonomics).
        attrs = {
            "instance_id": "instanceId",
            "sender": "sender",
            "version": "version",
            "mtconnect_version": "mtconnectVersion",
        }
        result: dict[str, str] = {}
        for out_key, attr_name in attrs.items():
            value = namespace_attrs.get(attr_name)
            if value is not None:
                result[out_key] = value
        if not result:
            raise RuntimeError(f"Agent at {url} did not return a valid MTConnect probe document")
        logger.info("Probe OK: %s", result)
        return result

    def fetch_sample(self) -> Sample:
        """Pull exactly one ``/sample`` response and parse it.

        Network errors propagate to the caller; the polling loop is
        responsible for retry/back-off.  XML parse errors are also
        propagated because they usually indicate a contract break
        with the agent.
        """
        url = f"{self.config.agent_url}{self.config.sample_path}"
        logger.debug("GET %s", url)
        response = self._session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        return parse_sample_response(response.text)

    def run(
        self,
        duration: float | None = None,
        on_sample: SampleCallback | None = None,
    ) -> int:
        """Poll the agent until ``duration`` seconds elapse or :meth:`stop`.

        Args:
            duration: Maximum wall-clock time to run, in seconds.
                ``None`` means run until :meth:`stop` is called.
            on_sample: Optional callback invoked synchronously for every
                successfully parsed sample.  Used by the CLI to print
                the live data stream.

        Returns:
            Number of samples ingested (i.e. successfully written to
            TDengine or accepted by the on_sample callback).
        """
        deadline = (time.monotonic() + duration) if duration else None
        logger.info(
            "Starting MTConnect polling (agent=%s, interval=%.2fs, duration=%s)",
            self.config.agent_url,
            self.config.interval,
            f"{duration}s" if duration else "indefinite",
        )

        while not self._stop_event.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                logger.info("Reached duration deadline, stopping")
                break

            sample = self._fetch_with_retry()
            if sample is None:
                # Persistent failure – sleep one full interval so we
                # don't hot-loop, then try again.
                self._sleep_interval()
                continue

            # 1) Hand the sample to any observer (CLI / metrics).
            if on_sample is not None:
                try:
                    on_sample(sample)
                except (RuntimeError, ValueError, TypeError, OSError):  # pragma: no cover - defensive
                    logger.exception("on_sample callback raised")

            # 2) Buffer it for batched storage.
            self._enqueue(sample)
            self._maybe_flush()

            # 3) Sleep until the next tick, but remain responsive to
            # stop requests.
            self._sleep_interval()

        # Always flush at the end so the last partial batch is not lost.
        self.flush()
        logger.info("Polling stopped. ingested=%d, errors=%d", self.ingested_count, self.error_count)
        return self.ingested_count

    def stop(self) -> None:
        """Request the polling loop to exit at the next opportunity."""
        self._stop_event.set()

    def flush(self) -> int:
        """Persist any samples currently buffered in TDengine.

        Returns the number of rows written.  Safe to call from a
        background thread – the buffer is guarded by a lock.

        持久化失败时批次不会被丢弃，而是回灌到缓冲区头部等待下一次
        flush 重试（有界，见 :attr:`AdapterConfig.max_buffer`）。
        """
        with self._buffer_lock:
            if not self._buffer:
                return 0
            batch = self._buffer
            self._buffer = []
            self._last_flush = time.monotonic()

        if self._tdengine is None:
            # No storage configured – drop the batch but count it.
            # The CLI uses this mode for "dry-run" / smoke testing.
            logger.debug("No TDengine client wired; dropping %d samples", len(batch))
            self._ingested_count += len(batch)
            return len(batch)

        rows = [self._row_for_storage(s) for s in batch]
        written = self._persist_rows(rows)
        if written > 0:
            self._ingested_count += written
            logger.info("Flushed batch of %d samples to TDengine", written)
        if written < len(batch):
            # 失败回灌：TDengine 故障时不丢采样数据，等下次 flush 重试。
            # 注意：若 future 超时后协程实际落库成功，可能产生重复行——
            # TDengine 同时间戳写入会覆盖，语义上可接受（宁重复勿丢失）。
            self._requeue_failed(batch)
        return written

    def _requeue_failed(self, batch: list[Sample]) -> None:
        """把持久化失败的批次放回缓冲区头部（保持时序），超限则丢最旧。

        flush() 已把缓冲清空，因此裁剪必须针对 ``批次 + 现存缓冲`` 的
        合并结果进行，否则上限不生效。
        """
        with self._buffer_lock:
            combined = batch + self._buffer
            overflow = len(combined) - self.config.max_buffer
            if overflow > 0:
                self._dropped_count += overflow
                logger.error(
                    "MTConnect retry buffer overflow; dropping %d oldest samples (total dropped=%d)",
                    overflow,
                    self._dropped_count,
                )
                del combined[:overflow]
            self._buffer = combined
            logger.warning("MTConnect flush failed; %d samples re-queued for retry", len(batch))

    # Public, read-only state

    @property
    def ingested_count(self) -> int:
        return self._ingested_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def dropped_samples(self) -> int:
        """因重试缓冲溢出而被丢弃的样本总数。"""
        return self._dropped_count

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    # Internal helpers

    def _build_default_session(self) -> Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "APT-MTConnect-Adapter/1.0",
                "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.5",
            }
        )
        return session

    def _fetch_with_retry(self) -> Sample | None:
        """Fetch + parse a single sample, with exponential back-off.

        Returns ``None`` if every retry attempt failed; the caller
        should sleep one full interval and try again later.
        """
        backoff = self.config.initial_backoff
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                return self.fetch_sample()
            except requests.RequestException as exc:
                last_error = exc
                self._error_count += 1
                if attempt >= self.config.max_retries:
                    logger.warning(
                        "MTConnect fetch failed after %d attempts: %s",
                        attempt,
                        exc,
                    )
                    break
                # Add a small jitter to avoid thundering-herd retries
                # if multiple adapters restart simultaneously.
                sleep_for = backoff * (0.5 + random.random())
                logger.info(
                    "MTConnect fetch attempt %d/%d failed (%s); retrying in %.2fs",
                    attempt,
                    self.config.max_retries,
                    exc,
                    sleep_for,
                )
                if self._stop_event.wait(sleep_for):
                    # Stop was requested mid-backoff.
                    return None
                backoff = min(backoff * 2, self.config.max_backoff)
            except ET.ParseError as exc:
                # Malformed XML almost certainly won't fix itself with
                # another immediate attempt. Log and bail.
                self._error_count += 1
                logger.error("MTConnect returned malformed XML: %s", exc)
                last_error = exc
                break

        logger.debug("Giving up on this polling cycle: %s", last_error)
        return None

    def _sleep_interval(self) -> None:
        """Sleep ``interval`` seconds while remaining responsive to :meth:`stop`."""
        self._stop_event.wait(self.config.interval)

    def _enqueue(self, sample: Sample) -> None:
        with self._buffer_lock:
            self._buffer.append(sample)

    def _maybe_flush(self) -> None:
        """Flush if either the size or the age threshold is met."""
        with self._buffer_lock:
            too_many = len(self._buffer) >= self.config.batch_size
            too_old = (time.monotonic() - self._last_flush) >= self.config.batch_interval and bool(self._buffer)
        if too_many or too_old:
            self.flush()

    def _row_for_storage(self, sample: Sample) -> list[Any]:
        """Convert a :class:`Sample` into a row matching ``DEFAULT_TABLE_DDL``.

        Column order must stay in lockstep with the DDL constant
        declared at module top.
        """
        ts = sample.observed_at or datetime.now(timezone.utc)
        return [
            ts.strftime("%Y-%m-%d %H:%M:%S.%f"),
            sample.spindle_speed,
            sample.spindle_load,
            sample.feedrate,
            sample.execution,
        ]

    def _persist_rows(self, rows: list[list[Any]]) -> int:
        """Persist a batch of rows through the TDengine client.

        The TDengine client API exposed by M0.2 is async; we run it
        through the adapter's own event loop so callers can drive the
        adapter synchronously from a CLI / script.
        """
        if not rows:
            return 0
        client = self._tdengine
        if client is None:
            return 0

        # If the injected client exposes a ``insert_rows`` coroutine
        # we drive it from an event loop; otherwise we look for a
        # synchronous ``insert_rows_sync`` for ergonomic test stubs.
        insert = getattr(client, "insert_rows", None)
        if insert is None:
            logger.error("TDengine client does not expose insert_rows; aborting flush")
            return 0

        import asyncio  # local import – keeps top-level cost low

        # Check if we're in an async context (FastAPI) or sync context (CLI)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside a running event loop (e.g., FastAPI async context).
            # Cannot use run_until_complete() or asyncio.run() here.
            # Use run_coroutine_threadsafe() to schedule on the loop from this thread.
            # 注意：不要在事件循环线程内直接调用 flush()——那会先阻塞本循环
            # 再等待调度回同一循环的协程，必然超时（样本会经 _requeue_failed 保住，
            # 但白等 DEFAULT_MTCONNECT_FUTURE_TIMEOUT_SEC 秒）。
            try:
                future = asyncio.run_coroutine_threadsafe(self._insert_async(client, insert, rows), loop)
                return future.result(timeout=DEFAULT_MTCONNECT_FUTURE_TIMEOUT_SEC)
            except Exception:
                # 宽捕是有意的：调用方（轮询线程）必须存活，失败样本由
                # flush() 的回灌机制兜底，这里只负责日志。
                logger.exception("Failed to persist rows in async context")
                return 0
        else:
            # No running loop - typical CLI scenario or sync context.
            # Use asyncio.run() to create a new event loop.
            try:
                return asyncio.run(self._insert_async(client, insert, rows))
            except Exception as exc:
                logger.error("Failed to persist rows in sync context: %s", exc, exc_info=True)
                return 0

    async def _insert_async(self, client: Any, insert: Callable, rows: list[Any]) -> int:
        result = await insert(
            table_name=self.config.table,
            rows=rows,
            database=self.config.database,
        )
        if result is None or result < 0:
            logger.error("TDengine insert returned %r (rows=%d)", result, len(rows))
            return 0
        return int(result)


# Convenience: CLI / one-shot helper


def build_table_ddl() -> tuple[str, ...]:
    """Return the canonical TDengine DDL column list for the table."""
    return DEFAULT_TABLE_DDL


__all__ = [
    "AdapterConfig",
    "MTConnectAdapter",
    "build_table_ddl",
    "parse_tds_url",
]
