"""Real-time streaming for MTConnect data via WebSocket + event queue.

This module provides real-time data streaming capabilities for MTConnect,
complementing the polling-based Adapter with low-latency event-driven
delivery suitable for visualization and alerting.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeVar

from app.integrations.mtconnect.adapter import MTConnectAdapter
from app.integrations.mtconnect.parser import Sample

logger = logging.getLogger(__name__)

# Type variables for the event stream
T = TypeVar("T")


@dataclass
class StreamEvent:
    """A single event emitted from the MTConnect stream.

    Attributes:
        event_id: Unique identifier for this event.
        timestamp: When the event was generated.
        data: The underlying Sample data.
        event_type: Type of event ('data', 'alarm', 'status').
        priority: Event priority (1-10, higher = more urgent).
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: Sample | None = None
    event_type: str = "data"
    priority: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for WebSocket serialization."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "data": {
                "spindle_speed": self.data.spindle_speed if self.data else None,
                "spindle_load": self.data.spindle_load if self.data else None,
                "feedrate": self.data.feedrate if self.data else None,
                "execution": self.data.execution if self.data else None,
            }
            if self.data
            else None,
            "event_type": self.event_type,
            "priority": self.priority,
        }


@dataclass
class AlertEvent(StreamEvent):
    """Specialized event for alerts and warnings.

    Attributes:
        alert_type: Type of alert ('chatter', 'overload', 'temperature').
        message: Human-readable alert description.
        threshold_value: Threshold that triggered the alert.
        actual_value: Actual measured value.
    """

    alert_type: str = ""
    message: str = ""
    threshold_value: float | None = None
    actual_value: float | None = None
    event_type: str = "alert"
    priority: int = 5

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with alert-specific fields."""
        base = super().to_dict()
        base.update(
            {
                "alert_type": self.alert_type,
                "message": self.message,
                "threshold_value": self.threshold_value,
                "actual_value": self.actual_value,
            }
        )
        return base


class StreamConsumer:
    """Event consumer for MTConnect stream events.

    Can be used to subscribe to raw data, process alerts, or emit metrics.
    """

    def __init__(self, name: str):
        self.name = name
        self.event_count = 0
        self.alert_count = 0
        self.last_event: datetime | None = None

    async def on_event(self, event: StreamEvent) -> None:
        """Handle incoming event. Override in subclasses for custom logic."""
        self.event_count += 1
        self.last_event = datetime.now(timezone.utc)
        if event.event_type == "alert":
            self.alert_count += 1
            logger.warning("[%s] Alert: %s (priority=%d)", self.name, event.message, event.priority)

    def status(self) -> dict[str, Any]:
        """Return consumer status metrics."""
        return {
            "name": self.name,
            "event_count": self.event_count,
            "alert_count": self.alert_count,
            "last_event": self.last_event.isoformat() if self.last_event else None,
        }


class MTConnectStreamServer:
    """Real-time MTConnect data stream server with WebSocket support.

    Provides low-latency data delivery suitable for visualization,
    monitoring, and alerting use cases.

    Usage:
        server = MTConnectStreamServer(agent_url="http://machine:80")
        consumer = StreamConsumer("dashboard")
        server.add_consumer(consumer)

        async with server:
            async for event in server.stream():
                # Process events or let consumers handle them
                pass
    """

    def __init__(
        self,
        agent_url: str,
        poll_interval: float = 1.0,
        max_backlog: int = 1000,
        enable_alerts: bool = True,
    ):
        self.agent_url = agent_url
        self.poll_interval = poll_interval
        self.max_backlog = max_backlog
        self.enable_alerts = enable_alerts

        self.adapter: MTConnectAdapter | None = None
        self._consumers: list[StreamConsumer] = []
        self._stop_event = asyncio.Event()
        self._event_queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=max_backlog)
        self._subscriber_count = 0
        self._stats = {
            "events_emitted": 0,
            "alerts_emitted": 0,
            "queue_depth": 0,
        }

    def add_consumer(self, consumer: StreamConsumer) -> None:
        """Add a consumer to receive stream events."""
        self._consumers.append(consumer)
        logger.info("[%s] Added consumer: %s", self.agent_url, consumer.name)

    def remove_consumer(self, consumer: StreamConsumer) -> bool:
        """Remove a consumer from the stream."""
        if consumer in self._consumers:
            self._consumers.remove(consumer)
            logger.info("[%s] Removed consumer: %s", self.agent_url, consumer.name)
            return True
        return False

    async def stream(self) -> AsyncIterator[StreamEvent]:
        """Yield StreamEvents in real-time."""
        self._subscriber_count += 1
        try:
            while not self._stop_event.is_set():
                try:
                    event = await asyncio.wait_for(self._event_queue.get(), timeout=self.poll_interval)
                    self._stats["queue_depth"] = self._event_queue.qsize()
                    yield event
                except asyncio.TimeoutError:
                    # Periodic timeout, check if we should stop
                    continue
        finally:
            self._subscriber_count -= 1
            logger.info("[%s] Subscriber removed, active=%d", self.agent_url, self._subscriber_count)

    async def start(self) -> None:
        """Start the streaming loop."""
        if self.adapter is not None:
            logger.warning("Streaming already started")
            return

        logger.info("Starting MTConnect stream from %s", self.agent_url)

        # Initialize adapter for polling
        from app.integrations.mtconnect.adapter import AdapterConfig

        config = AdapterConfig(
            agent_url=self.agent_url,
            interval=self.poll_interval,
            batch_size=1,  # Immediate flush for streaming
            batch_interval=0.0,
            max_retries=3,
        )
        self.adapter = MTConnectAdapter(config)
        self.adapter.probe()

        # Start streaming
        self._stop_event.clear()
        await self._stream_loop()

    async def stop(self) -> None:
        """Stop the streaming loop."""
        self._stop_event.set()
        if self.adapter is not None:
            self.adapter.close()
            self.adapter = None
        logger.info("Stopped MTConnect stream from %s", self.agent_url)

    async def _stream_loop(self) -> None:
        """Main streaming loop - poll adapter and emit events."""
        while not self._stop_event.is_set():
            try:
                # Fetch sample from adapter
                sample = self.adapter.fetch_sample()

                # Check for alerts
                alert_events = []
                if self.enable_alerts:
                    alert_events = self._check_alerts(sample)

                # Create data event
                data_event = StreamEvent(data=sample, priority=1, event_type="data")

                # Add alert events if any
                events = alert_events + [data_event]

                # Distribute events to consumers
                for event in events:
                    await self._emit_event(event)

            except Exception as exc:
                logger.error("Stream iteration error: %s", exc, exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _emit_event(self, event: StreamEvent) -> None:
        """Emit event to queue and all consumers."""
        # Add to queue
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event")

        # Notify consumers
        for consumer in self._consumers[:]:
            try:
                await consumer.on_event(event)
            except Exception as exc:
                logger.error("Consumer error %s: %s", consumer.name, exc)

        # Update stats
        self._stats["events_emitted"] += 1
        if event.event_type == "alert":
            self._stats["alerts_emitted"] += 1

    def _check_alerts(self, sample: Sample) -> list[AlertEvent]:
        """Check sample for alert conditions.

        Returns list of AlertEvents if any thresholds are exceeded.
        """
        return check_alerts(sample)

    async def __aenter__(self) -> "MTConnectStreamServer":
        """Context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        await self.stop()

    def get_stats(self) -> dict[str, Any]:
        """Return stream server statistics."""
        return {
            **self._stats,
            "queue_depth": self._event_queue.qsize(),
            "active_consumers": len(self._consumers),
            "active_subscribers": self._subscriber_count,
        }


def check_alerts(sample: Sample) -> list[AlertEvent]:
    """Check sample for alert conditions.

    模块级纯函数，供 :class:`MTConnectStreamServer` 与 WebSocket 监控端点
    （``monitor_ws``）复用同一套告警规则，避免规则漂移。

    Returns list of AlertEvents if any thresholds are exceeded.
    """
    alerts = []

    # Spindle overload detection (>80% load)
    if sample.spindle_load is not None and sample.spindle_load > 80.0:
        alerts.append(
            AlertEvent(
                event_type="alert",
                alert_type="spindle_overload",
                message=f"Spindle load {sample.spindle_load:.1f}% exceeds threshold",
                threshold_value=80.0,
                actual_value=sample.spindle_load,
                priority=6,
            )
        )

    # Feed rate anomaly (unusual patterns)
    if sample.feedrate is not None and sample.feedrate < 0.1:  # Near-zero feed rate
        alerts.append(
            AlertEvent(
                event_type="alert",
                alert_type="feed_anomaly",
                message=f"Feed rate {sample.feedrate:.2f} mm/min below operational range",
                threshold_value=0.1,
                actual_value=sample.feedrate,
                priority=4,
            )
        )

    return alerts


class WebSocketAlertHandler:
    """Handle WebSocket connections for streaming and alerts.

    Provides WebSocket protocol implementation for real-time data delivery.
    Designed to be integrated with FastAPI or ASGI frameworks.
    """

    def __init__(self, stream_server: MTConnectStreamServer):
        self.stream_server = stream_server
        self._connected_clients: set = set()

    async def handle_connection(self, websocket: Any) -> None:
        """Handle a WebSocket connection.

        Args:
            websocket: ASGI WebSocket application instance
        """
        self._connected_clients.add(websocket)
        logger.info("WebSocket client connected")

        try:
            async for event in self.stream_server.stream():
                if event.event_type == "alert":
                    await websocket.send_json(event.to_dict())
                else:
                    # Send data events at a controlled rate
                    await websocket.send_json(event.to_dict())

        except Exception as exc:
            logger.error("WebSocket error: %s", exc)
        finally:
            self._connected_clients.discard(websocket)
            logger.info("WebSocket client disconnected")

    def broadcast_alert(self, alert_type: str, message: str, severity: int) -> None:
        """Broadcast an alert to all connected clients."""
        alert = AlertEvent(
            alert_type=alert_type,
            message=message,
            priority=severity,
        )
        for client in self._connected_clients:
            try:
                asyncio.create_task(client.send_json(alert.to_dict()))
            except Exception as exc:
                logger.error("Failed to broadcast alert: %s", exc)
