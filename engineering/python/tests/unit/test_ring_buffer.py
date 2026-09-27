"""Unit tests for Ring Log Buffer module."""

from __future__ import annotations

from app.utils.ring_buffer import (
    LogEntry,
    RingBuffer,
    BUFFER_TYPES,
    DEFAULT_CAPACITY,
)


class TestLogEntry:
    def test_create_log_entry(self):
        entry = LogEntry(
            type="system_event",
            level="INFO",
            source="test",
            message="Test message",
        )
        assert entry.type == "system_event"
        assert entry.level == "INFO"
        assert entry.source == "test"
        assert entry.message == "Test message"
        assert isinstance(entry.timestamp, str)
        assert len(entry.timestamp) > 0

    def test_log_entry_defaults(self):
        entry = LogEntry()
        assert entry.type == "system_event"
        assert entry.level == "INFO"
        assert entry.source == ""
        assert entry.message == ""
        assert entry.data == {}

    def test_log_entry_to_json(self):
        entry = LogEntry(message="Hello")
        json_str = entry.to_json()
        assert "Hello" in json_str
        assert isinstance(json_str, str)

    def test_log_entry_with_data(self):
        entry = LogEntry(
            type="request",
            level="WARN",
            source="/api/test",
            message="Request processed",
            data={"method": "GET", "status": 200},
        )
        assert entry.data["method"] == "GET"
        assert entry.data["status"] == 200


class TestRingBuffer:
    def test_create_ring_buffer_with_default_capacity(self):
        buf = RingBuffer()
        assert buf.capacity == DEFAULT_CAPACITY
        assert buf.size == 0

    def test_create_ring_buffer_with_custom_capacity(self):
        buf = RingBuffer(capacity=100)
        assert buf.capacity == 100
        assert buf.size == 0

    def test_append_single_entry(self):
        buf = RingBuffer(capacity=10)
        entry = LogEntry(message="test")
        buf.append(entry)
        assert buf.size == 1
        assert buf.total_appended == 1
        assert buf.total_dropped == 0

    def test_append_multiple_entries(self):
        buf = RingBuffer(capacity=10)
        for i in range(5):
            buf.append(LogEntry(message=f"msg_{i}"))
        assert buf.size == 5
        assert buf.total_appended == 5

    def test_total_appended_increments(self):
        buf = RingBuffer(capacity=5)
        for i in range(10):
            buf.append(LogEntry(message=f"msg_{i}"))
        assert buf.total_appended == 10
        assert buf.size == 5
        assert buf.total_dropped == 5

    def test_snapshot_returns_copy(self):
        buf = RingBuffer(capacity=5)
        for i in range(3):
            buf.append(LogEntry(message=f"msg_{i}"))
        entries = buf.snapshot()
        assert len(entries) == 3
        assert isinstance(entries, list)
        assert all(isinstance(e, LogEntry) for e in entries)

    def test_snapshot_does_not_mutate_buffer(self):
        buf = RingBuffer(capacity=5)
        buf.append(LogEntry(message="a"))
        entries = buf.snapshot()
        entries.clear()
        assert buf.size == 1

    def test_append_with_data(self):
        buf = RingBuffer(capacity=10)
        entry = LogEntry(
            type="ai_inference",
            level="INFO",
            source="inference",
            message="Done",
            data={"model": "cfc", "time_ms": 12.5},
        )
        buf.append(entry)
        assert buf.size == 1

    def test_empty_buffer_snapshot(self):
        buf = RingBuffer(capacity=10)
        entries = buf.snapshot()
        assert entries == []


class TestBufferTypes:
    def test_buffer_types_is_tuple(self):
        assert isinstance(BUFFER_TYPES, tuple)
        assert len(BUFFER_TYPES) == 3

    def test_buffer_types_contains_expected(self):
        assert "request" in BUFFER_TYPES
        assert "ai_inference" in BUFFER_TYPES
        assert "system_event" in BUFFER_TYPES


class TestRingLogBuffer:
    def test_get_ring_log_buffer_creates_singleton(self):
        from app.utils.ring_buffer import get_ring_log_buffer

        buf1 = get_ring_log_buffer()
        buf2 = get_ring_log_buffer()
        assert buf1 is buf2

    def test_ring_log_buffer_append(self):
        from app.utils.ring_buffer import get_ring_log_buffer

        buf = get_ring_log_buffer()
        buf.append(
            "system_event",
            level="INFO",
            source="test",
            message="Integration test message",
        )
        assert buf is not None

    def test_ring_log_buffer_stats(self):
        from app.utils.ring_buffer import get_ring_log_buffer

        buf = get_ring_log_buffer()
        stats = buf.stats()
        assert "buffers" in stats
        assert "uptime_seconds" in stats
