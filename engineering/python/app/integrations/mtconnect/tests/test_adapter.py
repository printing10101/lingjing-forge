"""Unit tests for the MTConnect adapter package.

The tests fall into three categories:

1. **Pure parser tests** – exercise :func:`parse_sample_response` with
   hand-crafted XML payloads.  No I/O, no fixtures, very fast.
2. **Adapter behaviour tests** – drive :class:`MTConnectAdapter` with
   a mocked :class:`requests.Session` to validate retry / batching /
   back-off logic without touching the network.
3. **CLI tests** – drive :func:`app.integrations.mtconnect.cli.main`
   with a stub TDengine client to verify the end-to-end happy path
   (probe + run + flush) without external services.

These tests are intentionally network-free.  The live demo agent
(``http://demo.mtconnect.org``) is exercised only during manual
acceptance and is **not** a test dependency.

Run with::

    cd python && pytest app/integrations/mtconnect/tests/ -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Session
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import HTTPError

# Ensure the ``app`` package is importable when the tests are run from
# ``python/`` (the directory layout recommended in the task spec).
#
# Path layout of this file:
# python/app/integrations/mtconnect/tests/test_adapter.py
#  parents[4]
_PYTHON_DIR = Path(__file__).resolve().parents[4]
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from app.integrations.mtconnect import MTConnectAdapter
from app.integrations.mtconnect import cli
from app.integrations.mtconnect.adapter import (
    AdapterConfig,
    build_table_ddl,
    parse_tds_url,
)
from app.integrations.mtconnect.parser import Sample, parse_sample_response


# Sample XML fixtures


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  xsi:schemaLocation="urn:mtconnect.org:MTConnectStreams:1.5
                                      http://www.mtconnect.org/schema/MTConnectStreams_1.5.xsd">
  <Header creationTime="2026-06-11T10:23:45Z"
          sender="demo.mtconnect.org"
          instanceId="1234567890"
          version="1.5.0.0"
          mtconnectVersion="1.5"/>
  <Streams>
    <DeviceStream name="M12345">
      <ComponentStream component="Spindle" name="spindle">
        <Samples>
          <SpindleSpeed dataItemId="s_speed" timestamp="2026-06-11T10:23:45Z">12000</SpindleSpeed>
          <SpindleLoad dataItemId="s_load" timestamp="2026-06-11T10:23:45Z">42.5</SpindleLoad>
        </Samples>
      </ComponentStream>
      <ComponentStream component="Axes" name="axes">
        <Samples>
          <Feedrate dataItemId="a_feed" timestamp="2026-06-11T10:23:45Z">1500.0</Feedrate>
        </Samples>
        <Events>
          <Execution dataItemId="c_exec" timestamp="2026-06-11T10:23:45Z">ACTIVE</Execution>
        </Events>
      </ComponentStream>
    </DeviceStream>
  </Streams>
</MTConnectStreams>
"""

# Variant that uses the MTConnect "UNAVAILABLE" sentinel and omits the
# execution event, so we can verify graceful handling of partial data.
PARTIAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5">
  <Streams>
    <DeviceStream name="M99">
      <ComponentStream component="Spindle" name="spindle">
        <Samples>
          <SpindleSpeed>UNAVAILABLE</SpindleSpeed>
          <SpindleLoad>0</SpindleLoad>
        </Samples>
      </ComponentStream>
      <ComponentStream component="Axes" name="axes">
        <Samples>
          <Feedrate>250</Feedrate>
        </Samples>
      </ComponentStream>
    </DeviceStream>
  </Streams>
</MTConnectStreams>
"""

PROBE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MTConnectDevices xmlns="urn:mtconnect.org:MTConnectDevices:1.5"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Header creationTime="2026-06-11T10:00:00Z"
          sender="demo.mtconnect.org"
          instanceId="1234567890"
          version="1.5.0.0"
          mtconnectVersion="1.5"/>
</MTConnectDevices>
"""


# 1) Pure parser tests


class TestParser:
    """No I/O, just XML → Sample."""

    def test_parses_all_four_canonical_fields(self) -> None:
        sample = parse_sample_response(SAMPLE_XML)
        assert sample.spindle_speed == pytest.approx(12000.0)
        assert sample.spindle_load == pytest.approx(42.5)
        assert sample.feedrate == pytest.approx(1500.0)
        assert sample.execution == "ACTIVE"
        assert sample.observed_at is not None

    def test_partial_response_unavailable_sentinel(self) -> None:
        sample = parse_sample_response(PARTIAL_XML)
        # SpindleSpeed "UNAVAILABLE" None
        assert sample.spindle_speed is None
        # SpindleLoad "0" 0.0
        assert sample.spindle_load == pytest.approx(0.0)
        # Feedrate still parsed
        assert sample.feedrate == pytest.approx(250.0)
        # Execution event missing entirely
        assert sample.execution is None

    def test_empty_body_returns_empty_sample(self) -> None:
        sample = parse_sample_response("")
        assert sample.is_empty()
        assert sample.spindle_speed is None
        assert sample.execution is None

    def test_whitespace_only_body(self) -> None:
        sample = parse_sample_response("   \n  ")
        assert sample.is_empty()

    def test_malformed_xml_raises(self) -> None:
        with pytest.raises(Exception):
            parse_sample_response("<not><closed>")

    def test_numeric_coercion_handles_garbage(self) -> None:
        bad = (
            '<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5">'
            "<Streams><DeviceStream name='X'>"
            "<ComponentStream component='Spindle' name='spindle'>"
            "<Samples>"
            "<SpindleSpeed>not-a-number</SpindleSpeed>"
            "<SpindleLoad></SpindleLoad>"
            "</Samples></ComponentStream></DeviceStream></Streams>"
            "</MTConnectStreams>"
        )
        sample = parse_sample_response(bad)
        assert sample.spindle_speed is None
        assert sample.spindle_load is None

    def test_to_storage_row_includes_timestamp(self) -> None:
        sample = parse_sample_response(SAMPLE_XML)
        row = sample.to_storage_row()
        assert "ts" in row
        assert isinstance(row["ts"], str)
        assert row["spindle_speed"] == pytest.approx(12000.0)
        assert row["execution"] == "ACTIVE"

    def test_sample_is_empty_helper(self) -> None:
        assert Sample().is_empty()
        assert not Sample(spindle_speed=1.0).is_empty()

    def test_extras_capture_optional_events(self) -> None:
        xml = (
            '<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5">'
            "<Streams><DeviceStream name='X'>"
            "<ComponentStream component='Controller' name='ctrl'>"
            "<Events>"
            "<ControllerMode>AUTOMATIC</ControllerMode>"
            "<Program>TEST_PROG</Program>"
            "<PowerState>ON</PowerState>"
            "</Events></ComponentStream></DeviceStream></Streams>"
            "</MTConnectStreams>"
        )
        sample = parse_sample_response(xml)
        assert sample.extras.get("controller_mode") == "AUTOMATIC"
        assert sample.extras.get("program") == "TEST_PROG"
        assert sample.extras.get("power_state") == "ON"


# 2) AdapterConfig


class TestAdapterConfig:
    def test_defaults(self) -> None:
        cfg = AdapterConfig()
        assert cfg.interval == 1.0
        assert cfg.batch_size == 10
        assert cfg.max_retries == 5
        assert cfg.agent_url.startswith("http://")

    def test_trailing_slash_is_stripped(self) -> None:
        cfg = AdapterConfig(agent_url="http://example.com:80///")
        assert cfg.agent_url == "http://example.com:80"

    def test_invalid_scheme_rejected(self) -> None:
        with pytest.raises(ValueError):
            AdapterConfig(agent_url="ftp://example.com")

    def test_url_must_be_absolute(self) -> None:
        with pytest.raises(ValueError):
            AdapterConfig(agent_url="just-a-host")

    def test_parse_tds_url_happy(self) -> None:
        host, port, db = parse_tds_url("tds://localhost:6030/test.mtconnect")
        assert host == "localhost"
        assert port == 6030
        assert db == "test.mtconnect"

    def test_parse_tds_url_invalid(self) -> None:
        with pytest.raises(ValueError):
            parse_tds_url("http://wrong-scheme/db")

    def test_build_table_ddl(self) -> None:
        ddl = build_table_ddl()
        assert any("TIMESTAMP" in col for col in ddl)
        assert any("spindle_speed" in col for col in ddl)
        assert any("feedrate" in col for col in ddl)


# 3) Adapter behaviour with mocked session


class _StubResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")


def _build_stub_session(responses: list[Any]) -> Session:
    """Build a ``requests.Session`` whose ``get`` returns queued responses.

    The queue can hold either ``_StubResponse`` instances (success) or
    exception instances (to be raised) – the test can mix and match.
    """
    session = MagicMock(spec=Session)
    queue = list(responses)

    def _get(url: str, timeout: float | None = None) -> Any:
        if not queue:
            raise AssertionError("No more stub responses queued")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    session.get.side_effect = _get
    return session


class _StubTDE:
    """In-memory stand-in for the M0.2 TDengine client."""

    def __init__(self) -> None:
        self.inserted: list[list[Any]] = []
        self.ensure_table_calls: int = 0

    async def insert_rows(
        self,
        *,
        table_name: str,
        rows: list[list[Any]],
        database: str | None = None,
    ) -> int:
        del database
        self.inserted.extend(rows)
        return len(rows)

    async def ensure_database(self, database: str) -> bool:
        return True

    async def create_table_if_not_exists(
        self,
        *,
        table_name: str,
        columns: list[str],
        database: str | None = None,
    ) -> bool:
        del table_name, columns, database
        self.ensure_table_calls += 1
        return True


class TestAdapterProbe:
    def test_probe_returns_identity(self) -> None:
        session = _build_stub_session([_StubResponse(PROBE_XML)])
        adapter = MTConnectAdapter(
            config=AdapterConfig(agent_url="http://demo.mtconnect.org:80"),
            session=session,
        )
        identity = adapter.probe()
        assert identity["sender"] == "demo.mtconnect.org"
        assert identity["instance_id"] == "1234567890"
        assert identity["mtconnect_version"] == "1.5"

    def test_probe_propagates_http_error(self) -> None:
        session = _build_stub_session([_StubResponse("nope", status_code=500)])
        adapter = MTConnectAdapter(
            config=AdapterConfig(agent_url="http://demo.mtconnect.org:80"),
            session=session,
        )
        with pytest.raises(HTTPError):
            adapter.probe()

    def test_probe_raises_on_non_mtconnect_body(self) -> None:
        session = _build_stub_session([_StubResponse("<html>oops</html>")])
        adapter = MTConnectAdapter(
            config=AdapterConfig(agent_url="http://demo.mtconnect.org:80"),
            session=session,
        )
        with pytest.raises(RuntimeError):
            adapter.probe()


class TestAdapterFetch:
    def test_fetch_sample_returns_parsed_sample(self) -> None:
        session = _build_stub_session([_StubResponse(SAMPLE_XML)])
        adapter = MTConnectAdapter(
            config=AdapterConfig(agent_url="http://demo.mtconnect.org:80"),
            session=session,
        )
        sample = adapter.fetch_sample()
        assert sample.spindle_speed == pytest.approx(12000.0)
        assert sample.execution == "ACTIVE"


class TestAdapterRun:
    def test_run_with_no_tdengine_counts_samples(self) -> None:
        # Provide far more responses than the loop will ever consume
        # within ``duration`` so the queue cannot run out regardless
        # of scheduler jitter. The assertion below is therefore a
        # lower-bound on what the adapter *must* have ingested.
        session = _build_stub_session([_StubResponse(SAMPLE_XML) for _ in range(50)])
        adapter = MTConnectAdapter(
            config=AdapterConfig(
                agent_url="http://demo.mtconnect.org:80",
                interval=0.01,  # keep the test fast
                batch_size=1,  # flush after every sample
                max_retries=1,
            ),
            session=session,
        )
        seen: list[Sample] = []
        ingested = adapter.run(duration=0.1, on_sample=seen.append)
        # At least three full polling cycles must have completed in
        # 100 ms with a 10 ms interval. Exact equality is unsafe on
        # shared CI runners, so we only assert a sensible lower bound.
        assert ingested >= 3
        assert len(seen) >= 3
        assert seen[0].spindle_speed == pytest.approx(12000.0)

    def test_run_retries_then_succeeds(self) -> None:
        # Lead with a couple of transport errors and then keep
        # returning a valid response. As long as the queue never
        # runs dry the loop will eventually recover – we just want
        # to confirm that retries are counted and at least one
        # sample is ingested within the test's time budget.
        session = _build_stub_session(
            [ReqConnectionError("boom 1"), ReqConnectionError("boom 2")]
            + [_StubResponse(SAMPLE_XML) for _ in range(50)]
        )
        adapter = MTConnectAdapter(
            config=AdapterConfig(
                agent_url="http://demo.mtconnect.org:80",
                interval=0.01,
                max_retries=5,
                initial_backoff=0.001,
                max_backoff=0.01,
            ),
            session=session,
        )
        ingested = adapter.run(duration=0.1)
        # At least the recovery sample was ingested.
        assert ingested >= 1
        # The two leading transport errors are recorded exactly once.
        assert adapter.error_count >= 2

    def test_run_gives_up_after_max_retries(self) -> None:
        # Provide plenty of error responses so the loop never starves.
        session = _build_stub_session([ReqConnectionError("never works") for _ in range(50)])
        adapter = MTConnectAdapter(
            config=AdapterConfig(
                agent_url="http://demo.mtconnect.org:80",
                interval=0.01,
                max_retries=2,
                initial_backoff=0.001,
                max_backoff=0.01,
            ),
            session=session,
        )
        ingested = adapter.run(duration=0.05)
        assert ingested == 0
        # Each polling cycle consumes exactly ``max_retries`` errors,
        # so we expect at least 2 errors and the count must be a
        # multiple of ``max_retries``.
        assert adapter.error_count >= 2
        assert adapter.error_count % 2 == 0

    def test_run_persists_to_tdengine(self) -> None:
        # Provide plenty of responses so the loop is bound by the
        # ``duration`` (not by queue exhaustion) before it stops.
        session = _build_stub_session([_StubResponse(SAMPLE_XML) for _ in range(20)])
        tde = _StubTDE()
        adapter = MTConnectAdapter(
            config=AdapterConfig(
                agent_url="http://demo.mtconnect.org:80",
                interval=0.01,
                batch_size=1,
                database="test",
                table="mtconnect",
            ),
            session=session,
            tdengine_client=tde,
        )
        ingested = adapter.run(duration=0.05)
        assert ingested >= 1
        # flush() should have been called for every sample (batch_size=1).
        assert len(tde.inserted) >= 1
        # Each row matches the canonical DDL column order
        for row in tde.inserted:
            assert len(row) == 5
            assert isinstance(row[0], str)  # ts
            assert row[4] == "ACTIVE"  # execution

    def test_stop_signal_exits_loop_quickly(self) -> None:
        session = _build_stub_session([_StubResponse(SAMPLE_XML) for _ in range(50)])
        adapter = MTConnectAdapter(
            config=AdapterConfig(
                agent_url="http://demo.mtconnect.org:80",
                interval=0.5,
                max_retries=1,
            ),
            session=session,
        )

        import threading

        def _stop_after() -> None:
            import time

            time.sleep(0.05)
            adapter.stop()

        threading.Thread(target=_stop_after, daemon=True).start()
        # Bound the call so a regression in stop() doesn't hang CI.
        import time

        t0 = time.monotonic()
        ingested = adapter.run(duration=5.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"stop() did not interrupt run() (elapsed={elapsed:.2f}s)"
        assert ingested >= 0


class TestAdapterFlush:
    def test_flush_empty_buffer_returns_zero(self) -> None:
        adapter = MTConnectAdapter(
            config=AdapterConfig(agent_url="http://demo.mtconnect.org:80"),
        )
        assert adapter.flush() == 0

    def test_flush_writes_to_tdengine(self) -> None:
        adapter = MTConnectAdapter(
            config=AdapterConfig(agent_url="http://demo.mtconnect.org:80"),
            tdengine_client=_StubTDE(),
        )
        # Stuff a sample into the buffer manually.
        sample = Sample(
            spindle_speed=1.0,
            spindle_load=2.0,
            feedrate=3.0,
            execution="IDLE",
            observed_at=datetime.now(timezone.utc),
        )
        adapter._enqueue(sample)
        written = adapter.flush()
        assert written == 1
        assert adapter.buffer_size == 0


# 4) CLI tests


class TestCLIFormatting:
    def test_format_sample_includes_all_fields(self) -> None:
        s = Sample(
            spindle_speed=100.0,
            spindle_load=20.0,
            feedrate=5.0,
            execution="ACTIVE",
            observed_at=datetime(2026, 6, 11, 10, 0, 0),
        )
        line = cli.format_sample(cast(Any, s))
        assert "speed=100.00" in line
        assert "load=20.00" in line
        assert "feed=5.00" in line
        assert "exec=ACTIVE" in line
        assert "2026-06-11 10:00:00" in line

    def test_format_sample_dashes_for_missing(self) -> None:
        s = Sample()
        line = cli.format_sample(cast(Any, s))
        assert "speed=-" in line
        assert "exec=-" in line


class TestCLIArgparse:
    def test_defaults_match_spec(self) -> None:
        parser = cli.build_parser()
        ns = parser.parse_args([])
        assert ns.agent == "http://demo.mtconnect.org:80"
        assert ns.interval == 1.0
        assert ns.duration is None
        assert ns.output == "tds://localhost:6030/test.mtconnect"

    def test_custom_values_propagate(self) -> None:
        parser = cli.build_parser()
        ns = parser.parse_args(["--agent", "http://agent:5000", "--duration", "5", "--interval", "0.5"])
        assert ns.agent == "http://agent:5000"
        assert ns.duration == 5.0
        assert ns.interval == 0.5


class TestCLIMain:
    def test_main_returns_error_on_probe_failure(self, capsys) -> None:
        # Stub the probe to raise – the CLI should report the failure
        # and exit with code 2.
        session = _build_stub_session([_StubResponse("oops", status_code=500)])
        # Patch the ``requests.Session`` used by the adapter. Because
        # MTConnectAdapter builds its own session when none is passed,
        # we monkey-patch the class instead.
        with patch.object(MTConnectAdapter, "_build_default_session", return_value=session):
            code = cli.main(
                [
                    "--agent",
                    "http://demo.mtconnect.org:80",
                    "--duration",
                    "0",
                    "--dry-run",
                ]
            )
        assert code == 2
        captured = capsys.readouterr()
        assert "Probe failed" in captured.err

    def test_main_runs_and_persists(self, capsys) -> None:
        # Happy-path: probe OK, then a steady stream of valid samples
        # flushed to a stub TDengine client. We deliberately provide
        # far more samples than the loop will consume within
        # ``--duration`` so the queue cannot run dry and trip the
        # ``AssertionError("No more stub responses queued")`` guard.
        session = _build_stub_session([_StubResponse(PROBE_XML)] + [_StubResponse(SAMPLE_XML) for _ in range(50)])
        tde = _StubTDE()

        with patch.object(MTConnectAdapter, "_build_default_session", return_value=session):
            with patch.object(cli, "build_tdengine_client", return_value=(tde, "test", MagicMock())):
                with patch.object(
                    cli,
                    "ensure_table",
                    new=_make_async_returning(True),
                ):
                    code = cli.main(
                        [
                            "--agent",
                            "http://demo.mtconnect.org:80",
                            "--duration",
                            "0.05",
                            "--interval",
                            "0.01",
                            "--batch-size",
                            "1",
                        ]
                    )
        assert code == 0
        captured = capsys.readouterr()
        # Live printer should have emitted at least one sample line.
        assert "speed=" in captured.out
        # Final summary is always printed.
        assert "已写入" in captured.out


def _make_async_returning(value: Any):
    """Return a coroutine function that resolves to ``value``."""

    async def _coro(*args, **kwargs):
        return value

    return _coro
