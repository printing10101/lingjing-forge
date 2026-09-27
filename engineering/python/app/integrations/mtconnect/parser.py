"""MTConnect XML response parser.

This module is responsible for turning a raw ``MTConnectStreams`` XML
response (returned by an MTConnect Agent) into a strongly-typed
:class:`Sample` object that the rest of the adapter can consume.

Design notes
------------
* Uses :mod:`xml.etree.ElementTree` (the Python standard library) as the
  XML parser.  This is **mandatory** per the project rules – we never
  parse XML with regular expressions.
* The parser is a **pure function** with no I/O so it can be unit
  tested in isolation.  All side effects (network, storage) live in
  :mod:`app.integrations.mtconnect.adapter`.
* Numeric data items (``SAMPLE`` / ``EVENT`` with a numeric ``value``)
  are coerced to ``float``; status-style items such as ``execution``
  keep their string representation.
* Missing data items are surfaced as ``None`` rather than raised as
  exceptions, because a partial response from the agent is the rule,
  not the exception, in real-world deployments.

A typical agent response looks like::

    <MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5"
                      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Header ... />
      <Streams>
        <DeviceStream name="M12345">
          <ComponentStream component="Spindle" name="spindle">
            <Samples>
              <SpindleSpeed dataItemId="s1" timestamp="..." sequence="...">12000</SpindleSpeed>
              <SpindleLoad dataItemId="s2" timestamp="..." sequence="...">42.5</SpindleLoad>
            </Samples>
            <Events>
              <Execution dataItemId="s3" timestamp="..." sequence="...">ACTIVE</Execution>
            </Events>
          </ComponentStream>
          <ComponentStream component="Axes" name="axes">
            <Samples>
              <Feedrate dataItemId="a1" timestamp="...">1500.0</Feedrate>
            </Samples>
          </ComponentStream>
        </DeviceStream>
      </Streams>
    </MTConnectStreams>
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from xml.etree import ElementTree as ET

from app.utils.xml_guard import parse_untrusted_xml

# Namespaces used by the MTConnect spec; we still walk the tree by tag
# suffix to stay forward-compatible with newer spec versions.
_MT_NS = "{urn:mtconnect.org:MTConnectStreams:1.5}"


# Data model


@dataclass
class Sample:
    """A single polling cycle's worth of normalized MTConnect data items.

    The four fields below are the **canonical** data items requested by
    the M0.3 task.  Additional fields can be added in later milestones
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
    # agent. Stored alongside the values for time-series correlation.
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


def _local_tag(tag: str) -> str:
    """Strip the MTConnect XML namespace from a tag name.

    The MTConnect Agent returns every element in the
    ``urn:mtconnect.org:MTConnectStreams:1.5`` namespace, which would
    otherwise leak implementation details into our code.  We strip the
    namespace once and reason about the local name everywhere else.
    """
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _coerce_float(value: str | None) -> float | None:
    """Convert an MTConnect numeric text node to ``float`` when possible.

    Returns ``None`` for empty strings, ``UNAVAILABLE`` (MTConnect's
    sentinel for missing data) and unparseable values.  This is **not**
    a validation step – callers can decide whether the absence is an
    error in their context.
    """
    if value is None:
        return None
    s = value.strip()
    if not s or s.upper() in {"UNAVAILABLE", "NA", "N/A"}:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _find_first_numeric(root: ET.Element, tag: str) -> float | None:
    """Return the first numeric value found for a given element ``tag``.

    The MTConnect schema allows the same data item to be reported under
    multiple ``DeviceStream`` / ``ComponentStream`` combinations.  We
    pick the first one and treat later occurrences as duplicates; this
    matches the behaviour of the official MTConnect reference client.
    """
    for elem in root.iter():
        if _local_tag(elem.tag) != tag:
            continue
        parsed = _coerce_float(elem.text)
        if parsed is not None:
            return parsed
    return None


def _find_first_text(root: ET.Element, tag: str) -> str | None:
    """Return the first non-empty text content for a given element ``tag``."""
    for elem in root.iter():
        if _local_tag(elem.tag) != tag:
            continue
        if elem.text is None:
            continue
        text = elem.text.strip()
        if text:
            return text
    return None


def parse_sample_response(xml_text: str) -> Sample:
    """Parse a ``MTConnectStreams`` XML document into a :class:`Sample`.

    Args:
        xml_text: Raw response body returned by an MTConnect Agent when
            polling ``/sample`` or ``/current`` endpoints.

    Returns:
        A populated :class:`Sample`.  Fields that are not present in
        the response are set to ``None``.

    Raises:
        UnsafeXmlError: If ``xml_text`` carries a DOCTYPE/ENTITY
            declaration (entity-expansion vector); the guard in
            :mod:`app.utils.xml_guard` rejects it outright.
        ET.ParseError: If ``xml_text`` is not well-formed XML.  This
            deliberately propagates to the caller – a malformed
            response almost always indicates a protocol-level problem
            that the retry logic should see.
    """
    if not xml_text or not xml_text.strip():
        # Empty body return an empty sample so the caller can decide.
        return Sample(observed_at=datetime.now(timezone.utc))

    root = parse_untrusted_xml(xml_text)

    sample = Sample(
        spindle_speed=_find_first_numeric(root, "SpindleSpeed"),
        spindle_load=_find_first_numeric(root, "SpindleLoad"),
        feedrate=_find_first_numeric(root, "Feedrate"),
        execution=_find_first_text(root, "Execution"),
        observed_at=datetime.now(timezone.utc),
    )

    # Capture a couple of optional fields the demo agent exposes. These
    # are not persisted by default, but exposing them on the Sample
    # object makes them available to tests and future iterations.
    for tag, key in (
        ("ControllerMode", "controller_mode"),
        ("Program", "program"),
        ("PowerState", "power_state"),
    ):
        value = _find_first_text(root, tag)
        if value is not None:
            sample.extras[key] = value

    return sample


__all__ = ["Sample", "parse_sample_response"]
