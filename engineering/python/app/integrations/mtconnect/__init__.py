"""MTConnect protocol adapter for CNC machine data acquisition.

This package implements a standards-compliant (MTConnect v1.5+) HTTP/XML
client that periodically polls a MTConnect Agent (such as the public
demo at ``http://demo.mtconnect.org``) and persists the collected samples
into the project's TDengine time-series database.

The public surface is intentionally small:

* :class:`MTConnectAdapter` – the high-level polling / storage orchestrator.
* :func:`parse_sample_response` – pure-function XML → ``Sample`` parser,
  exposed for unit tests and external reuse.
* :class:`Sample` – typed view over a single polling cycle's data items.

The CLI entry point is available via::

    python -m app.integrations.mtconnect.cli --agent http://demo.mtconnect.org
"""

from app.integrations.mtconnect.adapter import MTConnectAdapter
from app.integrations.mtconnect.parser import Sample, parse_sample_response

__all__ = [
    "MTConnectAdapter",
    "Sample",
    "parse_sample_response",
    "MTConnectStreamServer",
    "StreamEvent",
    "AlertEvent",
    "StreamConsumer",
    "WebSocketAlertHandler",
    "ConditionChecker",
    "ChatterDetector",
    "Alert",
    "AlertCondition",
    "AlertPriority",
    "AlertType",
    "MTConnectExperienceBridge",
]

from app.integrations.mtconnect.streaming import (
    MTConnectStreamServer,
    StreamEvent,
    AlertEvent,
    StreamConsumer,
    WebSocketAlertHandler,
)
from app.integrations.mtconnect.conditions import (
    ConditionChecker,
    ChatterDetector,
    Alert,
    AlertCondition,
    AlertPriority,
    AlertType,
)
from app.integrations.mtconnect.experience_bridge import MTConnectExperienceBridge
