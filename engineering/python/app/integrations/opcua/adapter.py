"""OPC UA Adapter V1 – Subscription-based data acquisition + TDengine persistence.

The :class:`OPCUAAdapter` is the entry point used by both the CLI
and any future background workers.  Its responsibilities are deliberately
narrow:

1. **Connect to the OPC UA server** – establish a secure channel and session.
2. **Subscribe to data nodes** – use OPC UA subscription mechanism for
   real-time data updates.
3. **Parse the data** with :func:`app.integrations.opcua.parser.parse_opcua_data`.
4. **Buffer samples** until either a batch-size threshold or a flush
   interval is reached, then persist them with the project's TDengine
   client (M0.2 deliverable).
5. **Exponential back-off retry** on transient network errors so a
   momentary server hiccup does not derail the whole stream.

The class is intentionally **synchronous** in its public API so it can
be driven from both the CLI (``python -m``) and the FastAPI side
through ``run_in_executor`` – the underlying ``asyncua`` library is
asynchronous but we wrap it for ergonomic usage.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from app.config.limits import DEFAULT_THREAD_JOIN_TIMEOUT_SEC
from app.integrations._common import parse_tds_url

logger = logging.getLogger(__name__)

# 后台事件循环线程的统一 join 超时（秒）。
# 已迁移至 app.config.limits 集中管理，与 app.data.pipeline.loader 共享同一基准值。

# Common network/IO exception types for OPC UA communication
_NETWORK_EXCEPTIONS = (ConnectionError, OSError, TimeoutError)


# Configuration


# Table schema for TDengine. Kept here (rather than in the TDengine
# client) because the column set is a property of the *OPC UA* contract,
# not of the storage backend.
DEFAULT_TABLE_DDL: tuple[str, ...] = (
    "(ts TIMESTAMP, ",
    "spindle_speed DOUBLE, ",
    "spindle_load DOUBLE, ",
    "feedrate DOUBLE, ",
    "execution BINARY(32))",
)


@dataclass
class AdapterConfig:
    """Runtime configuration for :class:`OPCUAAdapter`."""

    # Network
    endpoint: str = "opc.tcp://localhost:4840"
    timeout: float = 10.0

    # Subscription
    interval: float = 1.0  # seconds between samples (1 Hz default)

    # Batching
    batch_size: int = 10  # flush after this many samples
    batch_interval: float = 5.0  # ...or after this many seconds

    # Retry
    max_retries: int = 5  # bounded – never spin forever
    initial_backoff: float = 0.5  # seconds for the first retry
    max_backoff: float = 16.0  # ...but capped so we don't sleep forever

    # Storage
    database: str = "test"  # TDengine DB (override per env)
    table: str = "opcua"  # TDengine super/sub-table name

    # Node configuration
    node_ids: list[str] | None = None  # OPC UA node IDs to subscribe

    # 安全配置（S1 修复）
    # 安全策略：默认强制 Basic256Sha256（工业生产最低安全基线）。
    # 设为 "None" 显式降级为明文（仅开发/仿真环境，会记录 WARNING）。
    security_policy: str = "Basic256Sha256"
    # 客户端证书/私钥路径（PEM 格式，None 表示使用 asyncua 默认自签名）
    cert_path: str | None = None
    key_path: str | None = None
    # 用户名/密码凭据（None 表示匿名；生产环境必须配置）
    username: str | None = None
    password: str | None = None

    def __post_init__(self) -> None:
        # Normalize the endpoint URL
        self.endpoint = self.endpoint.strip()
        if not self.endpoint.lower().startswith("opc.tcp://"):
            raise ValueError(f"endpoint must be an opc.tcp:// URL, got: {self.endpoint!r}")
        # 凭据支持从环境变量读取（未显式传入时），便于生产环境经 env 注入。
        if self.username is None:
            self.username = os.environ.get("LNN_OPCUA_USERNAME")
        if self.password is None:
            self.password = os.environ.get("LNN_OPCUA_PASSWORD")
        # S1 修复：生产环境拒绝 NoSecurity
        if self.security_policy.lower() == "none":
            # 仅在显式声明 LNN_OPCUA_ALLOW_NOSECURITY=1 时允许降级
            if os.environ.get("LNN_OPCUA_ALLOW_NOSECURITY", "") != "1":
                raise ValueError(
                    "OPC UA security_policy='None' (NoSecurity) is forbidden in "
                    "production. Set LNN_OPCUA_ALLOW_NOSECURITY=1 only for "
                    "development/simulation environments."
                )
            logger.warning(
                "OPC UA using NoSecurity (plaintext) - DEVELOPMENT ONLY. "
                "Set security_policy='Basic256Sha256' for production."
            )


# Adapter


# Callback type used by the adapter to publish "live" samples to the
# CLI or any other observer. Receives a fully populated ``Sample``.
SampleCallback = Callable[[Any], None]


class OPCUAAdapter:
    """Subscription + persistence orchestrator for a single OPC UA server.

    Typical usage::

        cfg = AdapterConfig(endpoint="opc.tcp://localhost:4840", interval=1.0)
        adapter = OPCUAAdapter(cfg)
        adapter.connect()                              # fail fast on misconfig
        adapter.run(duration=20.0, on_sample=print)  # blocks for 20 s

    Or, in a long-running service, drain the batch buffer on a timer.
    """

    # Construction

    def __init__(
        self,
        config: AdapterConfig | None = None,
        tdengine_client: Any | None = None,
    ) -> None:
        self.config = config or AdapterConfig()
        # Same trick for the TDengine client – the dependency is
        # imported lazily so unit tests don't need a running TDengine.
        self._tdengine = tdengine_client
        # Internal state
        self._buffer: list[Any] = []
        self._buffer_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_flush = time.monotonic()
        self._ingested_count = 0
        self._error_count = 0
        self._client: Any = None
        self._subscription: Any = None
        self._connected = False
        self._loop: Any = None  # Persistent event loop for asyncua operations

    # Public API

    def connect(self) -> dict[str, str]:
        """Connect to the OPC UA server and create a subscription.

        Returns a dict with the server's identity information:

        * ``endpoint``
        * ``server_uri``
        * ``server_name``

        Raises:
            RuntimeError: on connection errors
        """
        try:
            # Import asyncua lazily to avoid hard dependency
            from asyncua import ua, Client

            logger.info("Connecting to OPC UA server at %s", self.config.endpoint)

            # Create event loop and run it in a background thread so that
            # asyncua's internal publish_loop and other coroutines keep
            # processing while the main thread is blocked in run().
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._loop_thread = threading.Thread(
                    target=self._run_loop,
                    daemon=True,
                    name="opcua-event-loop",
                )
                self._loop_thread.start()

            try:
                # Connect to server with retry logic
                self._client = Client(self.config.endpoint)
                self._client.session_timeout = int(self.config.timeout * 1000)

                # S1 修复：强制设置安全策略与凭据
                # 默认 Basic256Sha256，仅当显式 security_policy="None" 且
                # LNN_OPCUA_ALLOW_NOSECURITY=1 时降级（__post_init__ 已校验）
                if self.config.security_policy and self.config.security_policy.lower() != "none":
                    # asyncua SecurityPolicy 需要证书/私钥；未配置时使用默认自签名
                    from asyncua.crypto.security_policies import (
                        SecurityPolicyBasic256Sha256,
                    )

                    self._client.set_security(
                        SecurityPolicyBasic256Sha256,
                        certificate=self.config.cert_path,
                        private_key=self.config.key_path,
                    )

                # S1 加固：设置用户名/密码凭据（生产环境必须配置）。
                # 缺省拒绝匿名连接（同 JWT secret 守卫的 fail-closed 思路）：
                # 未配置凭据时抛出，除非显式 LNN_OPCUA_ALLOW_ANON=1（仅开发/仿真）。
                if self.config.username and self.config.password:
                    self._client.set_user(self.config.username)
                    self._client.set_password(self.config.password)
                else:
                    if os.environ.get("LNN_OPCUA_ALLOW_ANON", "") != "1":
                        raise RuntimeError(
                            "OPC UA 未配置用户名/密码凭据，缺省拒绝匿名连接(anonymous)。"
                            "生产环境请在 AdapterConfig 中配置 username/password，"
                            "或设置 LNN_OPCUA_USERNAME / LNN_OPCUA_PASSWORD 环境变量；"
                            "仅开发/仿真环境可设 LNN_OPCUA_ALLOW_ANON=1 显式允许匿名。"
                        )
                    logger.warning(
                        "OPC UA connecting without credentials (anonymous) - "
                        "DEVELOPMENT ONLY. Configure username/password in production."
                    )

                # Schedule connection on the background loop
                self._run_coro(self._client.connect())

                # Get server info
                server_uri = self._run_coro(self._client.get_node(ua.ObjectIds.Server_ServerArray).get_value())
                server_name = self._run_coro(self._client.get_node(ua.ObjectIds.Server_ServerStatus).get_value())

                # Create subscription
                handler = SubHandler(self)
                self._subscription = self._run_coro(
                    self._client.create_subscription(self.config.interval * 1000, handler)
                )

                # Subscribe to nodes
                nodes_to_subscribe = self.config.node_ids
                if not nodes_to_subscribe:
                    # Auto-discover nodes: look for common CNC data items
                    nodes_to_subscribe = self._discover_cnc_nodes()
                    logger.info("Auto-discovered %d CNC nodes: %s", len(nodes_to_subscribe), nodes_to_subscribe)

                if nodes_to_subscribe:
                    nodes = [self._client.get_node(node_id) for node_id in nodes_to_subscribe]

                    # Cache node names for use in the sync callback
                    for node in nodes:
                        try:
                            browse_name = self._run_coro(node.read_browse_name())
                            handler._node_names[node.nodeid.to_string()] = browse_name.Name
                        except (asyncio.TimeoutError, TimeoutError, OSError) as exc:
                            logger.warning("Failed to cache node name for %s: %s", node.nodeid.to_string(), exc)

                    self._run_coro(self._subscription.subscribe_data_change(nodes))
                    logger.info("Subscribed to %d nodes", len(nodes))

                self._connected = True
                result = {
                    "endpoint": self.config.endpoint,
                    "server_uri": str(server_uri[0] if server_uri else "unknown"),
                    "server_name": str(server_name),
                }
                logger.info("Connection established: %s", result)
                return result

            except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError):
                # Clean up on connection failure
                if self._client:
                    try:
                        self._run_coro(self._client.disconnect())
                    except (ConnectionError, OSError, asyncio.TimeoutError) as cleanup_exc:
                        logger.warning("断开连接清理失败: %s", cleanup_exc, exc_info=True)
                raise

        except ImportError as exc:
            raise RuntimeError(
                f"OPC UA client library 'asyncua' not installed: {exc}\nInstall with: pip install asyncua"
            ) from exc
        except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
            logger.error("Failed to connect to OPC UA server: %s", exc)
            raise RuntimeError(f"Connection failed: {exc}") from exc

    def _run_loop(self) -> None:
        """Run the event loop in a background thread."""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # Clean up any remaining tasks
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def _run_coro(self, coro: Any) -> Any:
        """Submit a coroutine to the background event loop and wait for result."""
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("Event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=self.config.timeout)

    def _discover_cnc_nodes(self) -> list[str]:
        """Auto-discover common CNC data nodes from the OPC UA server.

        Looks for nodes with browse names matching common CNC data items:
        SpindleSpeed, SpindleLoad, FeedRate, Execution, etc.

        Returns a list of node IDs (as strings) that can be subscribed to.
        """
        if not self._client or not self._loop:
            return []

        discovered_nodes = []
        target_names = {
            "spindlespeed",
            "spindleload",
            "feedrate",
            "execution",
            "spindle_speed",
            "spindle_load",
            "feed_rate",
        }

        try:
            # Browse the objects node to find our data nodes
            objects_node = self._client.nodes.objects
            children = self._run_coro(objects_node.get_children())

            for child in children:
                try:
                    # Get browse name (asyncua 2.0 uses read_browse_name)
                    browse_name = self._run_coro(child.read_browse_name())
                    name_lower = browse_name.Name.lower()

                    # Check if this is a CNC-related object
                    if any(keyword in name_lower for keyword in ["cnc", "machine", "spindle", "feed"]):
                        # Get children of this object
                        sub_children = self._run_coro(child.get_children())
                        for sub_child in sub_children:
                            try:
                                sub_browse = self._run_coro(sub_child.read_browse_name())
                                if sub_browse.Name.lower() in target_names:
                                    node_id = sub_child.nodeid.to_string()
                                    discovered_nodes.append(node_id)
                            except (asyncio.TimeoutError, TimeoutError, OSError) as browse_exc:
                                logger.warning("浏览子节点失败: %s", browse_exc, exc_info=True)
                                continue

                    # Also check if the child itself matches
                    if name_lower in target_names:
                        node_id = child.nodeid.to_string()
                        discovered_nodes.append(node_id)

                except (asyncio.TimeoutError, TimeoutError, OSError) as child_exc:
                    logger.warning("处理子节点失败: %s", child_exc, exc_info=True)
                    continue

        except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning("Node auto-discovery failed: %s", exc)

        return discovered_nodes

    def disconnect(self) -> None:
        """Disconnect from the OPC UA server."""
        if self._client and self._connected and self._loop:
            try:
                # Cancel all pending tasks before disconnecting
                pending_tasks = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                if pending_tasks:
                    logger.debug("Cancelling %d pending async tasks", len(pending_tasks))
                    for task in pending_tasks:
                        task.cancel()
                    # Wait briefly for tasks to cancel
                    self._run_coro(asyncio.gather(*pending_tasks, return_exceptions=True))

                if self._subscription:
                    self._run_coro(self._subscription.delete())
                self._run_coro(self._client.disconnect())
            except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
                logger.warning("Error during disconnect: %s", exc)
            finally:
                self._connected = False
                self._client = None
                self._subscription = None
                # Stop the background event loop
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._loop.stop)
                if hasattr(self, "_loop_thread") and self._loop_thread.is_alive():
                    self._loop_thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT_SEC)
                self._loop = None

    def run(
        self,
        duration: float | None = None,
        on_sample: SampleCallback | None = None,
    ) -> int:
        """Subscribe to data until ``duration`` seconds elapse or :meth:`stop`.

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
        if not self._connected:
            raise RuntimeError("Adapter not connected. Call connect() first.")

        deadline = (time.monotonic() + duration) if duration else None
        logger.info(
            "Starting OPC UA subscription (endpoint=%s, interval=%.2fs, duration=%s)",
            self.config.endpoint,
            self.config.interval,
            f"{duration}s" if duration else "indefinite",
        )

        # Store callback for use in subscription handler
        self._on_sample_callback = on_sample

        # Main loop - just wait and flush periodically
        consecutive_errors = 0
        max_consecutive_errors = 10  # 连续错误次数上限

        while not self._stop_event.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                logger.info("Reached duration deadline, stopping")
                break

            try:
                # Check connection health
                if not self._check_connection_health():
                    logger.warning("Connection health check failed, attempting reconnection...")
                    self._attempt_reconnect()
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error("Max reconnection attempts reached, stopping")
                        break
                    continue

                # Reset error counter on successful health check
                consecutive_errors = 0

                # Sleep for a short interval, checking for stop event
                if self._stop_event.wait(self.config.interval):
                    break

                # Flush buffer if needed
                self._maybe_flush()

            except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
                logger.error("Runtime error in subscription loop: %s", exc, exc_info=True)
                self._error_count += 1
                consecutive_errors += 1

                if consecutive_errors >= max_consecutive_errors:
                    logger.error("Max consecutive errors reached, stopping")
                    break

                # Wait before retry
                backoff_time = min(
                    self.config.initial_backoff * (2 ** (consecutive_errors - 1)), self.config.max_backoff
                )
                logger.info("Waiting %.2f seconds before retry", backoff_time)
                if self._stop_event.wait(backoff_time):
                    break

        # Always flush at the end so the last partial batch is not lost.
        try:
            self.flush()
        finally:
            # P1-1 修复：run() 退出后必须 disconnect，否则 OPC UA 连接、订阅、
            # 后台 event loop 线程将持续泄漏。stop() 仅请求退出，资源清理由
            # 此处 finally 保证——无论循环因 stop()、deadline 还是异常退出。
            try:
                self.disconnect()
            except Exception as exc:
                logger.warning("disconnect failed during run() cleanup: %s", exc, exc_info=True)
        logger.info(
            "Subscription stopped. ingested=%d, errors=%d",
            self.ingested_count,
            self.error_count,
        )
        return self.ingested_count

    def _check_connection_health(self) -> bool:
        """Check if the OPC UA connection is still healthy.

        Returns:
            bool: True if connection is healthy, False otherwise
        """
        if not self._client or not self._connected:
            return False

        try:
            # Try to read a simple node to verify connection
            from asyncua import ua

            self._run_coro(self._client.get_node(ua.ObjectIds.Server_ServerStatus).get_value())
            return True
        except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
            logger.debug("Connection health check failed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Unexpected error in health check: %s", exc)
            return False

    def _attempt_reconnect(self) -> bool:
        """Attempt to reconnect to the OPC UA server.

        .. note::
            仅同步上下文使用：本方法使用 ``time.sleep`` 阻塞等待，
            不应在 async 上下文中直接调用。订阅循环运行在同步线程中，
            故此处使用同步 sleep 是安全的。

        Returns:
            bool: True if reconnection succeeded, False otherwise
        """
        logger.info("Attempting to reconnect to OPC UA server...")

        try:
            # Clean up old connection
            if self._client:
                try:
                    self._run_coro(self._client.disconnect())
                except Exception as exc:
                    logger.debug("Error during disconnect: %s", exc)

            self._connected = False
            self._client = None
            self._subscription = None

            # Wait before reconnect
            time.sleep(self.config.initial_backoff)

            # Try to reconnect
            self.connect()
            logger.info("Reconnection successful")
            return True

        except (ConnectionError, OSError, TimeoutError, RuntimeError) as exc:
            logger.error("Reconnection failed: %s", exc)
            return False
        except (ValueError, TypeError, AttributeError) as exc:
            logger.error("Unexpected error during reconnection: %s", exc, exc_info=True)
            return False

    def stop(self) -> None:
        """Request the subscription loop to exit at the next opportunity."""
        self._stop_event.set()

    def flush(self) -> int:
        """Persist any samples currently buffered in TDengine.

        Returns the number of rows written.  Safe to call from a
        background thread – the buffer is guarded by a lock.
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
            logger.warning("No TDengine client wired; dropping %d samples", len(batch))
            self._ingested_count += len(batch)
            return len(batch)

        rows = [self._row_for_storage(s) for s in batch]
        written = self._persist_rows(rows)
        if written > 0:
            self._ingested_count += written
            logger.info("Flushed batch of %d samples to TDengine", written)
        return written

    # Public, read-only state

    @property
    def ingested_count(self) -> int:
        return self._ingested_count

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    # Internal helpers

    def _enqueue(self, sample: Any) -> None:
        """Add a sample to the buffer (called from subscription handler)."""
        with self._buffer_lock:
            self._buffer.append(sample)

    def _maybe_flush(self) -> None:
        """Flush if either the size or the age threshold is met."""
        with self._buffer_lock:
            too_many = len(self._buffer) >= self.config.batch_size
            too_old = (time.monotonic() - self._last_flush) >= self.config.batch_interval and bool(self._buffer)
        if too_many or too_old:
            self.flush()

    def _row_for_storage(self, sample: Any) -> list[Any]:
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
        """Persist a batch of rows through the TDengine client."""
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

        # Check if we're in an async context (FastAPI) or sync context (CLI)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside a running event loop (e.g., FastAPI async context).
            # Cannot use run_until_complete() or asyncio.run() here.
            # Use run_coroutine_threadsafe() to schedule on the loop from this thread.
            try:
                future = asyncio.run_coroutine_threadsafe(self._insert_async(client, insert, rows), loop)
                # Wait for the result with a timeout
                return future.result(timeout=self.config.timeout)
            except (RuntimeError, TimeoutError, Exception) as exc:
                logger.error("Failed to persist rows in async context: %s", exc, exc_info=True)
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


# Subscription handler


class SubHandler:
    """OPC UA subscription handler that receives data change notifications."""

    def __init__(self, adapter: OPCUAAdapter):
        self.adapter = adapter
        self._node_names: dict[str, str] = {}  # Cache: node_id_str -> browse_name

    def datachange_notification(self, node, val, data):
        """Called when a subscribed node's value changes."""
        try:
            from app.integrations.opcua.parser import parse_opcua_data

            # Get cached node name (avoid async call in sync callback)
            node_id_str = node.nodeid.to_string()
            node_name = self._node_names.get(node_id_str)

            if node_name is None:
                # Fallback: try to get from node (shouldn't happen normally)
                logger.warning("Node name not cached for %s, using node_id", node_id_str)
                node_name = node_id_str

            # Build data dict
            data_dict = {node_name: val}

            # Parse into Sample
            sample = parse_opcua_data(data_dict)

            # Enqueue sample
            self.adapter._enqueue(sample)

            # Call callback if registered
            if hasattr(self.adapter, "_on_sample_callback") and self.adapter._on_sample_callback:
                try:
                    self.adapter._on_sample_callback(sample)
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as callback_exc:
                    logger.error("on_sample callback raised: %s", callback_exc, exc_info=True)

        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.error("Error processing data change: %s", exc, exc_info=True)
            self.adapter._error_count += 1
        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.error("Network error processing data change: %s", exc, exc_info=True)
            self.adapter._error_count += 1
        except (UnicodeDecodeError, IndexError, ArithmeticError) as exc:
            logger.error("Data processing error: %s", exc, exc_info=True)
            self.adapter._error_count += 1


# Convenience: CLI / one-shot helper


def build_table_ddl() -> tuple[str, ...]:
    """Return the canonical TDengine DDL column list for the table."""
    return DEFAULT_TABLE_DDL


__all__ = [
    "AdapterConfig",
    "OPCUAAdapter",
    "build_table_ddl",
    "parse_tds_url",
]
