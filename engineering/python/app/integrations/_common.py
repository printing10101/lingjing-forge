"""OPC UA / MTConnect 适配器共享的工具函数。

此前 `parse_tds_url` 在 `opcua/adapter.py` 与 `mtconnect/adapter.py`
中各有一份完全相同的实现；`format_sample` / `_fmt` / `build_tdengine_client`
在 `opcua/cli.py` 与 `mtconnect/cli.py` 中也各有一份。本模块集中存放这些
公共函数，避免后续维护时出现两份不一致的实现。
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.opcua.parser import Sample


# TDS URL 解析

# Regex used by the CLI to translate ``tds://host:port/db`` shorthand
# into the (host, port, database) tuple the TDengine client expects.
_TDS_URL_RE = re.compile(r"^tds://([^:/]+):(\d+)/(.+)$")


def parse_tds_url(url: str) -> tuple[str, int, str]:
    """Parse a ``tds://host:port/database`` URL.

    Args:
        url: Connection string of the form ``tds://localhost:6030/test``.

    Returns:
        ``(host, port, database)`` tuple.

    Raises:
        ValueError: if the URL does not match the expected format.
    """
    match = _TDS_URL_RE.match(url)
    if not match:
        raise ValueError(f"Invalid TDS URL: {url!r}. Expected tds://host:port/database")
    host, port, database = match.group(1), int(match.group(2)), match.group(3)
    return host, port, database


# CLI 输出格式化


def format_sample(sample: "Sample") -> str:
    """Render a sample as a single line of text for CLI display.

    The format is intentionally compact and human-friendly so it can
    be eyeballed during a smoke test::

        [2026-06-15 10:23:45.123] speed=12000.0 load=42.5 feed=1500.0 exec=ACTIVE

    Missing values are rendered as ``-`` so columns stay aligned.
    """
    ts = sample.observed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if sample.observed_at else "-"
    speed = _fmt(sample.spindle_speed)
    load = _fmt(sample.spindle_load)
    feed = _fmt(sample.feedrate)
    exec_ = sample.execution if sample.execution is not None else "-"
    return f"[{ts}] speed={speed} load={load} feed={feed} exec={exec_}"


def _fmt(value: float | None) -> str:
    """Format a float compactly; return ``-`` for ``None``."""
    if value is None:
        return "-"
    return f"{value:.2f}"


# TDengine wiring


def build_tdengine_client(output_url: str):
    """Construct a TDengine client from a ``tds://`` URL.

    The function is lazy: it imports :mod:`app.services.tdengine_client`
    only when actually needed so unit tests that don't care about
    storage can run in isolation.
    """
    host, port, database = parse_tds_url(output_url)
    # The TDengine client reads its connection parameters from
    # environment variables. Set them on the fly so the same client
    # can be reused for any URL passed via the CLI.
    logger = logging.getLogger(__name__)
    password = os.environ.get("TDENGINE_PASSWORD", "")
    if not password:
        logger.warning("TDENGINE_PASSWORD not set. Please configure it in .env file.")
    os.environ["TDENGINE_URL"] = f"taos://root:{password}@{host}:{port}"
    os.environ["TDENGINE_DB"] = database

    try:
        from app.services import tdengine_client as tdc
    except ImportError as exc:  # pragma: no cover - import-time guard
        raise SystemExit(
            f"Cannot import TDengine client: {exc}\n"
            "Install with: pip install tdengine\n"
            "Or set TDENGINE_PASSWORD in your .env file."
        )
    client = tdc.get_tdengine()
    return client, database, tdc


__all__ = [
    "parse_tds_url",
    "format_sample",
    "build_tdengine_client",
]
