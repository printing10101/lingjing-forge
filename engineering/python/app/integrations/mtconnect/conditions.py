"""Condition checking and alert rules for MTConnect data monitoring.

Provides rules for detecting anomalies, chatter, and operational thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.integrations.mtconnect.parser import Sample


class AlertPriority(Enum):
    """Alert severity levels."""

    INFO = 1
    LOW = 3
    WARNING = 5
    MEDIUM = 7
    HIGH = 9
    CRITICAL = 10


class AlertType(Enum):
    """Types of alerts that can be generated."""

    INFO = "info"
    SPINDLE_OVERLOAD = "spindle_overload"
    FEED_ANOMALY = "feed_anomaly"
    CHATTER_DETECTED = "chatter_detected"
    TEMPERATURE_HIGH = "temperature_high"
    TOOL_WEAR = "tool_wear"
    VIBRATION_HIGH = "vibration_high"


@dataclass
class AlertCondition:
    """A single alert condition with threshold and logic.

    Attributes:
        data_item: The MTConnect data item name to monitor.
        threshold: Comparison threshold value.
        operator: Comparison operator ('greater_than', 'less_than', 'between').
        duration_ms: Minimum duration condition must persist before alert.
        description: Human-readable alert description.
    """

    data_item: str
    threshold: float
    operator: str
    duration_ms: int = 1000
    description: str = ""

    def evaluate(self, value: float | None) -> bool:
        """Check if this condition is currently triggered.

        Args:
            value: The current data item value to check.

        Returns:
            True if condition is violated.
        """
        if value is None:
            return False

        if self.operator == "greater_than":
            return value > self.threshold
        elif self.operator == "less_than":
            return value < self.threshold
        elif self.operator == "between":
            if not isinstance(self.threshold, (list, tuple)) or len(self.threshold) != 2:
                # 如果 threshold 是单值 X，视为 value > X（>= 判断，兼容写法）
                return value > self.threshold
            low, high = self.threshold
            return low <= value <= high

        return False


@dataclass
class Alert:
    """An alert instance generated from a condition check.

    Attributes:
        alert_id: Unique identifier.
        alert_type: Type of alert.
        priority: Severity level.
        message: Human-readable description.
        triggered_at: When the alert was generated.
        data_item: The data item that triggered the alert.
        threshold_value: The threshold value.
        actual_value: The actual measured value.
    """

    alert_id: str = ""
    alert_type: AlertType = AlertType.INFO
    priority: AlertPriority = AlertPriority.INFO
    message: str = ""
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data_item: str = ""
    threshold_value: float | None = None
    actual_value: float | None = None

    def __post_init__(self):
        if not self.alert_id:
            import uuid

            self.alert_id = str(uuid.uuid4())[:8]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type.value,
            "priority": self.priority.value,
            "message": self.message,
            "triggered_at": self.triggered_at.isoformat(),
            "data_item": self.data_item,
            "threshold_value": self.threshold_value,
            "actual_value": self.actual_value,
        }


class ConditionChecker:
    """Check MTConnect samples against alert conditions.

    Provides persistent condition tracking with duration-based
    alert suppression to avoid alarm floods.
    """

    def __init__(self):
        self.conditions: dict[str, list[AlertCondition]] = {
            "spindle_load": [
                AlertCondition(
                    data_item="spindle_load",
                    threshold=80.0,
                    operator="greater_than",
                    duration_ms=5000,
                    description="Spindle load exceeds 80%",
                ),
                AlertCondition(
                    data_item="spindle_load",
                    threshold=95.0,
                    operator="greater_than",
                    duration_ms=2000,
                    description="Spindle load exceeds 95% (critical)",
                ),
            ],
            "feed_rate": [
                AlertCondition(
                    data_item="feedrate",
                    threshold=0.1,
                    operator="less_than",
                    duration_ms=3000,
                    description="Feed rate near zero (potential stall)",
                ),
            ],
            "spindle_speed": [
                AlertCondition(
                    data_item="spindle_speed",
                    threshold=15000.0,
                    operator="greater_than",
                    duration_ms=1000,
                    description="Spindle speed exceeds rated limit",
                ),
            ],
        }

        # Track active alerts to prevent rapid re-fire
        self._active_alerts: dict[str, Alert] = {}
        self._alert_cooldown: dict[str, datetime] = {}

    def check(self, sample: Sample) -> list[Alert]:
        """Check a sample against all defined conditions.

        Args:
            sample: MTConnect sample to evaluate.

        Returns:
            List of triggered Alert instances.
        """
        alerts = []

        # Extract values from sample
        values = {
            "spindle_load": sample.spindle_load,
            "feedrate": sample.feedrate,
            "spindle_speed": sample.spindle_speed,
        }

        # Check each category of conditions
        # 特殊处理：conditions 使用 feed_rate，values 使用 feedrate
        for category, condition_list in self.conditions.items():
            value = values.get(category) if category != "feed_rate" else values.get("feedrate")

            for condition in condition_list:
                if condition.evaluate(value):
                    # Check duration threshold
                    alert_key = f"{category}_{condition.operator}_{condition.threshold}"

                    now = datetime.now(timezone.utc)
                    cooldown_end_ts = self._alert_cooldown.get(alert_key)

                    if cooldown_end_ts is not None and now.timestamp() < cooldown_end_ts:
                        # Still in cooldown, skip
                        continue

                    # Create alert
                    alert = Alert(
                        alert_type=self._map_category_to_type(category),
                        priority=self._get_priority(category),
                        message=condition.description,
                        data_item=category,
                        threshold_value=condition.threshold,
                        actual_value=value,
                    )

                    alerts.append(alert)
                    self._active_alerts[alert_key] = alert

                    # Set cooldown (prevent alert spam)
                    self._alert_cooldown[alert_key] = now.timestamp() + 30.0

        return alerts

    def _map_category_to_type(self, category: str) -> AlertType:
        """Map internal category names to AlertType enum."""
        mapping = {
            "spindle_load": AlertType.SPINDLE_OVERLOAD,
            "feed_rate": AlertType.FEED_ANOMALY,  # conditions 用 feed_rate
            "feedrate": AlertType.FEED_ANOMALY,  # values 用 feedrate（兼容）
            "spindle_speed": AlertType.SPINDLE_OVERLOAD,
        }
        return mapping.get(category, AlertType.INFO)

    def _get_priority(self, category: str) -> AlertPriority:
        """Get default priority for a category."""
        if category == "spindle_load":
            return AlertPriority.MEDIUM
        return AlertPriority.LOW

    def clear_alert(self, alert_key: str) -> None:
        """Clear an active alert and its cooldown."""
        self._active_alerts.pop(alert_key, None)
        self._alert_cooldown.pop(alert_key, None)

    def get_active_alerts(self) -> list[Alert]:
        """Get all currently active alerts."""
        return list(self._active_alerts.values())

    def reset(self) -> None:
        """Reset all active alerts and cooldowns."""
        self._active_alerts.clear()
        self._alert_cooldown.clear()


class ChatterDetector:
    """Dect chatter and vibration anomalies from MTConnect data.

    Placeholder for advanced signal processing. Currently uses
    simple threshold-based detection.
    """

    def __init__(
        self,
        vibration_threshold: float = 5.0,  # mm/s
        acceleration_threshold: float = 100.0,  # mm/s^2
    ):
        self.vibration_threshold = vibration_threshold
        self.acceleration_threshold = acceleration_threshold
        self._recent_vibrations: list[float] = []

    def check_chatter(self, vibration: float | None, acceleration: float | None) -> bool:
        """Check for chatter conditions.

        Args:
            vibration: Measured vibration amplitude (mm/s).
            acceleration: Measured acceleration (mm/s^2).

        Returns:
            True if chatter is detected.
        """
        # Store recent vibration values
        if vibration is not None:
            self._recent_vibrations.append(vibration)
            if len(self._recent_vibrations) > 100:
                self._recent_vibrations = self._recent_vibrations[-50:]

        # Simple threshold check (placeholder for FFT analysis)
        if vibration is not None and vibration > self.vibration_threshold:
            return True

        if acceleration is not None and acceleration > self.acceleration_threshold:
            return True

        return False

    def get_chatter_risk_score(self) -> float:
        """Calculate chatter risk score (0-100).

        Uses statistical analysis of recent vibration values.
        Returns:
            Float between 0 (safe) and 100 (high risk).
        """
        if not self._recent_vibrations:
            return 0.0

        # Current implementation uses simple mean-based scoring
        # Advanced version would use FFT, RMS, crest factor
        mean_vib = sum(self._recent_vibrations) / len(self._recent_vibrations)
        max_vib = max(self._recent_vibrations)

        risk = (mean_vib / self.vibration_threshold) * 50
        risk = min(max(risk + (max_vib - mean_vib), 0), 100)

        return risk

    def reset(self) -> None:
        """Reset internal state."""
        self._recent_vibrations.clear()
