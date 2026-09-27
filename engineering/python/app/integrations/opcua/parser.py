"""OPC UA data parser.

This module is responsible for converting raw OPC UA data values
into a strongly-typed :class:`Sample` object that the rest of the
adapter can consume.

Design notes
------------
* The parser is a **pure function** with no I/O so it can be unit
  tested in isolation.  All side effects (network, storage) live in
  :mod:`app.integrations.opcua.adapter`.
* Numeric data items are coerced to ``float``; status-style items such
  as ``execution`` keep their string representation.
* Missing data items are surfaced as ``None`` rather than raised as
  exceptions, because a partial response from the server is the rule,
  not the exception, in real-world deployments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Data model


@dataclass
class Sample:
    """A single polling cycle's worth of normalized OPC UA data items.

    The four fields below are the **canonical** data items requested by
    the M5.4 task.  Additional fields can be added in later milestones
    (vibration, temperature, …) without breaking callers that consume
    only the four primary items.
    """

    spindle_speed: float | None = None
    spindle_load: float | None = None
    feedrate: float | None = None
    execution: str | None = None

    # Free-form extras so future requirements don't require a data model
    # change. The adapter does not persist ``extras`` by default.
    extras: dict[str, Any] = field(default_factory=dict)

    # The wall-clock time at which the sample was received from the
    # server. Stored alongside the values for time-series correlation.
    observed_at: datetime | None = None

    def is_empty(self) -> bool:
        """Return ``True`` when every canonical data item is missing."""
        return all(
            getattr(self, field_name) is None
            for field_name in ("spindle_speed", "spindle_load", "feedrate", "execution")
        )

    def to_storage_row(self) -> dict[str, Any]:
        """Return a dict ready to be fed to :class:`TDengineClient.insert_rows`.

        The timestamp is converted to an ISO-8601 string; ``None`` values
        are preserved (TDengine will store ``NULL`` for those columns).
        """
        ts = self.observed_at or datetime.now(timezone.utc)
        return {
            "ts": ts.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "spindle_speed": self.spindle_speed,
            "spindle_load": self.spindle_load,
            "feedrate": self.feedrate,
            "execution": self.execution,
        }


# Parser


def _coerce_float(value: Any | None) -> float | None:
    """Convert an OPC UA numeric value to ``float`` when possible.

    Returns ``None`` for empty strings, ``None`` values and unparseable
    values.  This is **not** a validation step – callers can decide
    whether the absence is an error in their context.
    """
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s.upper() in {"UNAVAILABLE", "NA", "N/A"}:
            return None
        try:
            return float(s)
        except (TypeError, ValueError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any | None) -> str | None:
    """Convert an OPC UA value to string when possible."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() in {"UNAVAILABLE", "NA", "N/A"}:
        return None
    return s


def parse_opcua_data(
    data: dict[str, Any],
    *,
    observed_at: datetime | None = None,
) -> Sample:
    """Parse OPC UA data values into a :class:`Sample`.

    Args:
        data: Dictionary mapping node names to their values.
            Expected keys include:
            - ``spindle_speed`` or ``SpindleSpeed``
            - ``spindle_load`` or ``SpindleLoad``
            - ``feedrate`` or ``Feedrate``
            - ``execution`` or ``Execution``
        observed_at: Optional timestamp when the data was observed.
            If not provided, the current UTC time is used.

    Returns:
        A populated :class:`Sample`.  Fields that are not present in
        the data are set to ``None``.
    """
    # Normalize keys to lowercase for case-insensitive lookup
    normalized = {k.lower(): v for k, v in data.items()}

    sample = Sample(
        spindle_speed=_coerce_float(normalized.get("spindlespeed")),
        spindle_load=_coerce_float(normalized.get("spindleload")),
        feedrate=_coerce_float(normalized.get("feedrate")),
        execution=_coerce_str(normalized.get("execution")),
        observed_at=observed_at or datetime.now(timezone.utc),
    )

    # Capture any extra fields that don't map to canonical data items
    canonical_keys = {"spindlespeed", "spindleload", "feedrate", "execution"}
    for key, value in normalized.items():
        if key not in canonical_keys:
            sample.extras[key] = value

    return sample


__all__ = ["Sample", "parse_opcua_data"]
