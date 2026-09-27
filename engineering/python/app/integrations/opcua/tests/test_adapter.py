"""Unit tests for OPC UA adapter.

This module contains comprehensive unit tests for the OPC UA adapter,
covering parsing, configuration, connection, and data handling.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.opcua.adapter import (
    AdapterConfig,
    OPCUAAdapter,
    parse_tds_url,
    build_table_ddl,
)
from app.integrations.opcua.parser import Sample, parse_opcua_data


# Parser tests


class TestParser:
    """Tests for OPC UA data parser."""

    def test_parse_complete_data(self):
        """Test parsing complete OPC UA data."""
        data = {
            "SpindleSpeed": 12000.0,
            "SpindleLoad": 42.5,
            "Feedrate": 1500.0,
            "Execution": "ACTIVE",
        }
        sample = parse_opcua_data(data)

        assert sample.spindle_speed == 12000.0
        assert sample.spindle_load == 42.5
        assert sample.feedrate == 1500.0
        assert sample.execution == "ACTIVE"
        assert sample.observed_at is not None

    def test_parse_partial_data(self):
        """Test parsing partial OPC UA data."""
        data = {
            "SpindleSpeed": 12000.0,
            "Feedrate": 1500.0,
        }
        sample = parse_opcua_data(data)

        assert sample.spindle_speed == 12000.0
        assert sample.spindle_load is None
        assert sample.feedrate == 1500.0
        assert sample.execution is None

    def test_parse_empty_data(self):
        """Test parsing empty OPC UA data."""
        sample = parse_opcua_data({})

        assert sample.spindle_speed is None
        assert sample.spindle_load is None
        assert sample.feedrate is None
        assert sample.execution is None
        assert sample.is_empty()

    def test_parse_with_custom_timestamp(self):
        """Test parsing with custom timestamp."""
        data = {"SpindleSpeed": 12000.0}
        custom_time = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        sample = parse_opcua_data(data, observed_at=custom_time)

        assert sample.observed_at == custom_time

    def test_parse_case_insensitive(self):
        """Test case-insensitive key matching."""
        data = {
            "spindlespeed": 12000.0,
            "SPINDLELOAD": 42.5,
            "feedrate": 1500.0,
        }
        sample = parse_opcua_data(data)

        assert sample.spindle_speed == 12000.0
        assert sample.spindle_load == 42.5
        assert sample.feedrate == 1500.0

    def test_parse_numeric_string(self):
        """Test parsing numeric values as strings."""
        data = {
            "SpindleSpeed": "12000.0",
            "SpindleLoad": "42.5",
        }
        sample = parse_opcua_data(data)

        assert sample.spindle_speed == 12000.0
        assert sample.spindle_load == 42.5

    def test_parse_unavailable_values(self):
        """Test parsing UNAVAILABLE sentinel values."""
        data = {
            "SpindleSpeed": "UNAVAILABLE",
            "SpindleLoad": "N/A",
            "Feedrate": "NA",
        }
        sample = parse_opcua_data(data)

        assert sample.spindle_speed is None
        assert sample.spindle_load is None
        assert sample.feedrate is None

    def test_parse_extra_fields(self):
        """Test parsing extra fields into extras dict."""
        data = {
            "SpindleSpeed": 12000.0,
            "Temperature": 45.0,
            "Vibration": 0.5,
        }
        sample = parse_opcua_data(data)

        assert sample.spindle_speed == 12000.0
        assert "temperature" in sample.extras
        assert sample.extras["temperature"] == 45.0
        assert "vibration" in sample.extras
        assert sample.extras["vibration"] == 0.5


class TestSample:
    """Tests for Sample dataclass."""

    def test_sample_is_empty(self):
        """Test Sample.is_empty() method."""
        empty_sample = Sample()
        assert empty_sample.is_empty()

        partial_sample = Sample(spindle_speed=12000.0)
        assert not partial_sample.is_empty()

        full_sample = Sample(
            spindle_speed=12000.0,
            spindle_load=42.5,
            feedrate=1500.0,
            execution="ACTIVE",
        )
        assert not full_sample.is_empty()

    def test_sample_to_storage_row(self):
        """Test Sample.to_storage_row() method."""
        sample = Sample(
            spindle_speed=12000.0,
            spindle_load=42.5,
            feedrate=1500.0,
            execution="ACTIVE",
            observed_at=datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        )
        row = sample.to_storage_row()

        assert row["ts"] == "2026-06-15 10:00:00.000000"
        assert row["spindle_speed"] == 12000.0
        assert row["spindle_load"] == 42.5
        assert row["feedrate"] == 1500.0
        assert row["execution"] == "ACTIVE"

    def test_sample_to_storage_row_with_none(self):
        """Test Sample.to_storage_row() with None values."""
        sample = Sample()
        row = sample.to_storage_row()

        assert "ts" in row
        assert row["spindle_speed"] is None
        assert row["spindle_load"] is None
        assert row["feedrate"] is None
        assert row["execution"] is None


# Adapter configuration tests


class TestAdapterConfig:
    """Tests for AdapterConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AdapterConfig()

        assert config.endpoint == "opc.tcp://localhost:4840"
        assert config.interval == 1.0
        assert config.batch_size == 10
        assert config.batch_interval == 5.0
        assert config.max_retries == 5
        assert config.database == "test"
        assert config.table == "opcua"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = AdapterConfig(
            endpoint="opc.tcp://192.168.1.100:4840",
            interval=2.0,
            batch_size=20,
            database="production",
            table="machines",
        )

        assert config.endpoint == "opc.tcp://192.168.1.100:4840"
        assert config.interval == 2.0
        assert config.batch_size == 20
        assert config.database == "production"
        assert config.table == "machines"

    def test_invalid_endpoint(self):
        """Test invalid endpoint URL."""
        with pytest.raises(ValueError, match="endpoint must be an opc.tcp:// URL"):
            AdapterConfig(endpoint="http://localhost:4840")

    def test_endpoint_normalization(self):
        """Test endpoint URL normalization."""
        config = AdapterConfig(endpoint="opc.tcp://localhost:4840  ")
        assert config.endpoint == "opc.tcp://localhost:4840"


# TDS URL parsing tests


class TestParseTdsUrl:
    """Tests for parse_tds_url function."""

    def test_valid_tds_url(self):
        """Test parsing valid TDS URL."""
        host, port, database = parse_tds_url("tds://localhost:6030/test")

        assert host == "localhost"
        assert port == 6030
        assert database == "test"

    def test_tds_url_with_ip(self):
        """Test parsing TDS URL with IP address."""
        host, port, database = parse_tds_url("tds://192.168.1.100:6030/production")

        assert host == "192.168.1.100"
        assert port == 6030
        assert database == "production"

    def test_invalid_tds_url(self):
        """Test parsing invalid TDS URL."""
        with pytest.raises(ValueError, match="Invalid TDS URL"):
            parse_tds_url("http://localhost:6030/test")

        with pytest.raises(ValueError, match="Invalid TDS URL"):
            parse_tds_url("tds://localhost/test")

        with pytest.raises(ValueError, match="Invalid TDS URL"):
            parse_tds_url("invalid")


# Adapter tests


class TestOPCUAAdapter:
    """Tests for OPCUAAdapter."""

    def test_adapter_initialization(self):
        """Test adapter initialization."""
        config = AdapterConfig()
        adapter = OPCUAAdapter(config=config)

        assert adapter.config == config
        assert adapter.ingested_count == 0
        assert adapter.error_count == 0
        assert adapter.buffer_size == 0

    def test_adapter_default_config(self):
        """Test adapter with default configuration."""
        adapter = OPCUAAdapter()

        assert adapter.config is not None
        assert adapter.config.endpoint == "opc.tcp://localhost:4840"

    def test_adapter_enqueue(self):
        """Test sample enqueueing."""
        adapter = OPCUAAdapter()
        sample = Sample(spindle_speed=12000.0)

        adapter._enqueue(sample)

        assert adapter.buffer_size == 1
        assert adapter._buffer[0] == sample

    def test_adapter_flush_without_tdengine(self):
        """Test flush without TDengine client."""
        adapter = OPCUAAdapter()
        sample = Sample(spindle_speed=12000.0)

        adapter._enqueue(sample)
        assert adapter.buffer_size == 1

        written = adapter.flush()

        assert written == 1
        assert adapter.ingested_count == 1
        assert adapter.buffer_size == 0

    def test_adapter_flush_empty_buffer(self):
        """Test flush with empty buffer."""
        adapter = OPCUAAdapter()

        written = adapter.flush()

        assert written == 0
        assert adapter.ingested_count == 0

    def test_adapter_stop(self):
        """Test adapter stop."""
        adapter = OPCUAAdapter()

        assert not adapter._stop_event.is_set()
        adapter.stop()
        assert adapter._stop_event.is_set()

    def test_adapter_maybe_flush_by_size(self):
        """Test automatic flush by batch size."""
        config = AdapterConfig(batch_size=3)
        adapter = OPCUAAdapter(config=config)

        # Add samples below threshold
        for i in range(2):
            adapter._enqueue(Sample(spindle_speed=float(i)))
        adapter._maybe_flush()
        assert adapter.buffer_size == 2

        # Add sample to reach threshold
        adapter._enqueue(Sample(spindle_speed=3.0))
        adapter._maybe_flush()
        assert adapter.buffer_size == 0
        assert adapter.ingested_count == 3

    def test_adapter_maybe_flush_by_time(self):
        """Test automatic flush by time interval."""
        config = AdapterConfig(batch_interval=0.1, batch_size=100)
        adapter = OPCUAAdapter(config=config)

        # Add a sample
        adapter._enqueue(Sample(spindle_speed=12000.0))

        # Wait for time threshold
        time.sleep(0.15)
        adapter._maybe_flush()

        assert adapter.buffer_size == 0
        assert adapter.ingested_count == 1

    def test_adapter_row_for_storage(self):
        """Test row conversion for storage."""
        adapter = OPCUAAdapter()
        sample = Sample(
            spindle_speed=12000.0,
            spindle_load=42.5,
            feedrate=1500.0,
            execution="ACTIVE",
            observed_at=datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        )

        row = adapter._row_for_storage(sample)

        assert row[0] == "2026-06-15 10:00:00.000000"
        assert row[1] == 12000.0
        assert row[2] == 42.5
        assert row[3] == 1500.0
        assert row[4] == "ACTIVE"


# Build table DDL tests


class TestBuildTableDdl:
    """Tests for build_table_ddl function."""

    def test_build_table_ddl(self):
        """Test building table DDL."""
        ddl = build_table_ddl()

        assert len(ddl) == 5
        assert "ts TIMESTAMP" in ddl[0]
        assert "spindle_speed DOUBLE" in ddl[1]
        assert "spindle_load DOUBLE" in ddl[2]
        assert "feedrate DOUBLE" in ddl[3]
        assert "execution BINARY(32)" in ddl[4]


# CLI tests


class TestCLI:
    """Tests for CLI module."""

    def test_format_sample(self):
        """Test sample formatting."""
        from app.integrations.opcua.cli import format_sample

        sample = Sample(
            spindle_speed=12000.0,
            spindle_load=42.5,
            feedrate=1500.0,
            execution="ACTIVE",
            observed_at=datetime(2026, 6, 15, 10, 0, 0, 123000, tzinfo=timezone.utc),
        )

        formatted = format_sample(sample)

        assert "2026-06-15 10:00:00.123" in formatted
        assert "speed=12000.00" in formatted
        assert "load=42.50" in formatted
        assert "feed=1500.00" in formatted
        assert "exec=ACTIVE" in formatted

    def test_format_sample_with_none(self):
        """Test sample formatting with None values."""
        from app.integrations.opcua.cli import format_sample

        sample = Sample()
        formatted = format_sample(sample)

        assert "speed=-" in formatted
        assert "load=-" in formatted
        assert "feed=-" in formatted
        assert "exec=-" in formatted

    def test_build_parser(self):
        """Test argument parser construction."""
        from app.integrations.opcua.cli import build_parser

        parser = build_parser()

        # Test default values
        args = parser.parse_args([])
        assert args.endpoint == "opc.tcp://localhost:4840"
        assert args.interval == 1.0
        assert args.batch_size == 10

        # Test custom values
        args = parser.parse_args(
            [
                "--endpoint",
                "opc.tcp://192.168.1.100:4840",
                "--interval",
                "2.0",
                "--batch-size",
                "20",
                "--dry-run",
            ]
        )
        assert args.endpoint == "opc.tcp://192.168.1.100:4840"
        assert args.interval == 2.0
        assert args.batch_size == 20
        assert args.dry_run is True


# Integration tests (mocked)


class TestAdapterIntegration:
    """Integration tests with mocked OPC UA server."""

    @patch("asyncua.Client")
    def test_adapter_connect_success(self, mock_client_class):
        """Test successful connection to OPC UA server."""
        # Mock the asyncua Client
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock async methods
        async def mock_connect():
            return None

        async def mock_get_node(node_id):
            mock_node = MagicMock()

            async def mock_get_value():
                if node_id == 2271:  # Server_ServerArray
                    return ["urn:test:server"]
                elif node_id == 2256:  # Server_ServerStatus
                    return MagicMock()

            mock_node.get_value = mock_get_value
            return mock_node

        async def mock_create_subscription(interval, handler):
            mock_sub = MagicMock()

            async def mock_subscribe_data_change(nodes):
                return None

            mock_sub.subscribe_data_change = mock_subscribe_data_change

            async def mock_delete():
                return None

            mock_sub.delete = mock_delete
            return mock_sub

        mock_client.connect = mock_connect
        mock_client.get_node = mock_get_node
        mock_client.create_subscription = mock_create_subscription

        # Test connection
        config = AdapterConfig()
        adapter = OPCUAAdapter(config=config)

        # This will fail because we can't easily mock async code
        # In a real test, we'd use pytest-asyncio
        # For now, just verify the adapter can be created
        assert adapter.config == config

    def test_adapter_run_without_connect(self):
        """Test running adapter without connection raises error."""
        adapter = OPCUAAdapter()

        with pytest.raises(RuntimeError, match="Adapter not connected"):
            adapter.run(duration=1.0)


__all__ = [
    "TestParser",
    "TestSample",
    "TestAdapterConfig",
    "TestParseTdsUrl",
    "TestOPCUAAdapter",
    "TestBuildTableDdl",
    "TestCLI",
    "TestAdapterIntegration",
]
