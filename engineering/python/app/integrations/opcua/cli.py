"""Command-line entry point for the OPC UA adapter.

Designed to be invoked as a Python module so it picks up the right
``sys.path`` and package context automatically::

    python -m app.integrations.opcua.cli \\
        --endpoint opc.tcp://localhost:4840 \\
        --duration 20 \\
        --output tds://localhost:6030/test.opcua

Behaviour
---------
* **Connection probe** – the first thing the CLI does is call
  :meth:`OPCUAAdapter.connect` so a misconfigured endpoint fails
  fast and with a clear error message.
* **Live printing** – every successfully parsed sample is printed to
  stdout in a compact, fixed-width format so users can see the data
  stream flowing in real time.
* **TDengine persistence** – samples are buffered and flushed to the
  configured TDengine database in batches (see ``--batch-size``).
* **Graceful shutdown** – ``Ctrl-C`` cleanly stops the subscription loop
  and flushes the final batch.

The module is fully covered by the test suite via
:func:`main` (which returns the exit code) and the individual
helpers.
"""

from __future__ import annotations

import argparse
import logging
import sys

from collections.abc import Sequence

from app.integrations._common import (
    build_tdengine_client,
    format_sample,
    parse_tds_url,
)
from app.integrations.opcua.adapter import AdapterConfig, OPCUAAdapter
from app.integrations.opcua.parser import Sample


# Default OPC UA demo endpoint (per M5.4 task).
DEFAULT_ENDPOINT = "opc.tcp://localhost:4840"
DEFAULT_OUTPUT = "tds://localhost:6030/test.opcua"

# ``format_sample`` / ``build_tdengine_client`` / ``parse_tds_url`` are
# imported from :mod:`app.integrations._common` to avoid duplicating the
# implementations between the OPC UA and MTConnect CLIs.


async def ensure_table(database: str, table: str) -> bool:
    """Make sure the destination table exists; create it on first run.

    Best-effort: failure is logged but does **not** abort the CLI – we
    still want to print data even if the table can't be created right
    now (TDengine might come up a few seconds after the script).
    """
    try:
        from app.services import tdengine_client as tdc
    except ImportError:  # pragma: no cover
        return False
    try:
        ok = await tdc.ensure_database(database)
        if not ok:
            return False
        # Use the canonical DDL defined by the adapter.
        from app.integrations.opcua.adapter import build_table_ddl

        return await tdc.create_table_if_not_exists(
            table_name=table,
            columns=list(build_table_ddl()),
            database=database,
        )
    except (RuntimeError, OSError) as exc:  # pragma: no cover - defensive
        logging.getLogger(__name__).warning("ensure_table(%s.%s) failed: %s", database, table, exc)
        return False


# Entry point


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser.  Kept separate for testability."""
    parser = argparse.ArgumentParser(
        prog="python -m app.integrations.opcua.cli",
        description=(
            "Subscribe to an OPC UA server, parse spindle/feedrate/execution data items and persist them to TDengine."
        ),
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=(f"OPC UA server endpoint URL (default: {DEFAULT_ENDPOINT})"),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help=("How long to run the subscription loop, in seconds. Omit to run until Ctrl-C."),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Subscription interval in seconds (default: 1.0 → 1 Hz).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=(f"TDengine connection string of the form ``tds://host:port/database`` (default: {DEFAULT_OUTPUT})."),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Flush to TDengine after this many samples (default: 10).",
    )
    parser.add_argument(
        "--batch-interval",
        type=float,
        default=5.0,
        help=("Maximum age of buffered samples before forcing a flush, in seconds (default: 5.0)."),
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retries per subscription cycle (default: 5).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connection timeout in seconds (default: 10.0).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=("Skip the TDengine wiring – useful for smoke-testing the network/connection path without a database."),
    )
    parser.add_argument(
        "--node",
        action="append",
        dest="nodes",
        help=(
            "OPC UA node ID to subscribe (e.g. ns=2;i=1). "
            "Can be specified multiple times. If omitted, subscribes "
            "to default nodes."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI main entry point.  Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("opcua.cli")

    config = AdapterConfig(
        endpoint=args.endpoint,
        timeout=args.timeout,
        interval=args.interval,
        batch_size=args.batch_size,
        batch_interval=args.batch_interval,
        max_retries=args.max_retries,
        node_ids=args.nodes,
    )

    # Resolve database / table from the TDS URL. We do this *before*
    # wiring the TDengine client so the table-creation step uses the
    # right name.
    host, port, database = parse_tds_url(args.output)
    config.database = database

    tdengine = None
    if not args.dry_run:
        tdengine, _db, _tdc = build_tdengine_client(args.output)
        if tdengine is None:
            log.warning(
                "TDengine client could not be initialised; continuing in read-only mode (no rows will be persisted)."
            )
        else:
            # Ensure table exists. This is a best-effort step.
            import asyncio

            try:
                asyncio.run(ensure_table(database, config.table))
            except (RuntimeError, OSError) as exc:  # pragma: no cover
                log.warning("ensure_table raised: %s", exc)

    adapter = OPCUAAdapter(config=config, tdengine_client=tdengine)

    # 1) Connect – fail fast on server / network problems.
    try:
        identity = adapter.connect()
        log.info("Server identity: %s", identity)
    except (RuntimeError, ValueError, OSError) as exc:
        # We deliberately route the human-readable failure message to
        # ``sys.stderr`` (and *not* to ``logging``) so that callers
        # using ``capsys`` / ``subprocess`` can reliably capture the
        # failure reason without needing to plumb a logging handler.
        log.error("Connection failed: %s", exc)
        sys.stderr.write(f"Connection failed: {exc}\n")
        sys.stderr.flush()
        return 2

    # 2) Run the subscription loop. ``format_sample`` is the live printer.
    ingested: int = 0
    try:
        ingested = adapter.run(duration=args.duration, on_sample=_print_sample)
    except KeyboardInterrupt:
        log.info("Interrupted by user, flushing...")
        adapter.stop()
        ingested = adapter.flush()
    except (RuntimeError, OSError) as exc:  # pragma: no cover - defensive
        # We never want an unexpected runtime failure (e.g. a flaky
        # network that raises something we did not anticipate) to
        # leave the user staring at a stack trace with no summary.
        # Log it, count it on the adapter and fall through to the
        # normal summary print below.
        log.exception("Subscription loop raised: %s", exc)
        adapter.stop()
        try:
            ingested = adapter.flush()
        except (RuntimeError, OSError):  # pragma: no cover - defensive
            pass
    finally:
        # Always print the final summary, even after errors.
        _print_summary(adapter, ingested)
        # Disconnect from OPC UA server
        try:
            adapter.disconnect()
        except (RuntimeError, OSError) as exc:  # pragma: no cover - defensive
            log.warning("disconnect() raised: %s", exc)

    return 0


def _print_sample(sample: Sample) -> None:
    """Default CLI callback that prints the sample to stdout."""
    sys.stdout.write(format_sample(sample) + "\n")
    sys.stdout.flush()


def _print_summary(adapter: OPCUAAdapter, ingested: int) -> None:
    """Print the final tally so the user gets immediate feedback."""
    sys.stdout.write(f"已写入 {ingested} 条 (errors={adapter.error_count}, buffer_left={adapter.buffer_size})\n")
    sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
