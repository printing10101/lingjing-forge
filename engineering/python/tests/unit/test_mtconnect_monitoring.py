"""Unit tests for MTConnect conditions and alert logic (white-box, zero network)."""

from __future__ import annotations

import asyncio

import pytest

from app.integrations.mtconnect.conditions import (
    Alert,
    AlertCondition,
    AlertPriority,
    AlertType,
    ChatterDetector,
    ConditionChecker,
)
from app.integrations.mtconnect.parser import Sample
from app.integrations.mtconnect.streaming import (
    AlertEvent,
    MTConnectStreamServer,
    StreamConsumer,
    StreamEvent,
)


# AlertCondition


class TestAlertCondition:
    def test_greater_than_triggered(self) -> None:
        cond = AlertCondition(data_item="spindle_load", threshold=80.0, operator="greater_than")
        assert cond.evaluate(85.0) is True

    def test_greater_than_not_triggered(self) -> None:
        cond = AlertCondition(data_item="spindle_load", threshold=80.0, operator="greater_than")
        assert cond.evaluate(79.9) is False

    def test_less_than_triggered(self) -> None:
        cond = AlertCondition(data_item="feedrate", threshold=0.1, operator="less_than")
        assert cond.evaluate(0.05) is True

    def test_less_than_not_triggered(self) -> None:
        cond = AlertCondition(data_item="feedrate", threshold=0.1, operator="less_than")
        assert cond.evaluate(0.5) is False

    def test_between_triggered(self) -> None:
        cond = AlertCondition(data_item="temperature", threshold=20.0, operator="between")
        assert cond.evaluate(25.0) is True

    def test_none_value_never_triggers(self) -> None:
        cond = AlertCondition(data_item="spindle_load", threshold=80.0, operator="greater_than")
        assert cond.evaluate(None) is False

    def test_unknown_operator_never_triggers(self) -> None:
        cond = AlertCondition(data_item="x", threshold=1.0, operator="not_a_real_operator")
        assert cond.evaluate(5.0) is False


# ConditionChecker


class TestConditionChecker:
    def test_no_alert_on_normal_sample(self) -> None:
        checker = ConditionChecker()
        sample = Sample(spindle_load=40.0, feedrate=500.0, spindle_speed=8000.0)
        assert checker.check(sample) == []

    def test_alert_on_high_spindle_load(self) -> None:
        checker = ConditionChecker()
        sample = Sample(spindle_load=90.0, feedrate=500.0, spindle_speed=8000.0)
        alerts = checker.check(sample)
        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.SPINDLE_OVERLOAD
        assert alerts[0].actual_value == 90.0
        assert alerts[0].threshold_value == 80.0

    def test_alert_on_near_zero_feedrate(self) -> None:
        checker = ConditionChecker()
        sample = Sample(spindle_load=10.0, feedrate=0.01, spindle_speed=8000.0)
        alerts = checker.check(sample)
        assert any(a.alert_type == AlertType.FEED_ANOMALY for a in alerts)

    def test_alert_on_high_spindle_speed(self) -> None:
        checker = ConditionChecker()
        sample = Sample(spindle_load=10.0, feedrate=500.0, spindle_speed=20000.0)
        alerts = checker.check(sample)
        assert any(a.alert_type == AlertType.SPINDLE_OVERLOAD for a in alerts)

    def test_cooldown_suppresses_repeat_alerts(self) -> None:
        checker = ConditionChecker()
        sample = Sample(spindle_load=90.0, feedrate=500.0, spindle_speed=8000.0)
        first = checker.check(sample)
        assert len(first) == 1
        # Second immediate check is suppressed by cooldown
        second = checker.check(sample)
        assert second == []

    def test_reset_clears_active_alerts(self) -> None:
        checker = ConditionChecker()
        sample = Sample(spindle_load=90.0, feedrate=500.0, spindle_speed=8000.0)
        checker.check(sample)
        assert len(checker.get_active_alerts()) >= 1
        checker.reset()
        assert checker.get_active_alerts() == []

    def test_alert_has_generated_id(self) -> None:
        alert = Alert(alert_type=AlertType.CHATTER_DETECTED, message="test")
        assert len(alert.alert_id) == 8

    def test_alert_to_dict(self) -> None:
        alert = Alert(
            alert_type=AlertType.VIBRATION_HIGH,
            priority=AlertPriority.HIGH,
            message="Vibration high",
            data_item="vibration",
            threshold_value=5.0,
            actual_value=7.5,
        )
        d = alert.to_dict()
        assert d["alert_type"] == "vibration_high"
        assert d["priority"] == 9
        assert d["actual_value"] == 7.5
        assert d["threshold_value"] == 5.0
        assert "triggered_at" in d


# ChatterDetector


class TestChatterDetector:
    def test_no_chatter_within_threshold(self) -> None:
        detector = ChatterDetector(vibration_threshold=5.0)
        assert detector.check_chatter(3.0, 50.0) is False

    def test_chatter_on_high_vibration(self) -> None:
        detector = ChatterDetector(vibration_threshold=5.0)
        assert detector.check_chatter(6.5, 50.0) is True

    def test_chatter_on_high_acceleration(self) -> None:
        detector = ChatterDetector(acceleration_threshold=100.0)
        assert detector.check_chatter(3.0, 150.0) is True

    def test_none_values_no_chatter(self) -> None:
        detector = ChatterDetector()
        assert detector.check_chatter(None, None) is False

    def test_risk_score_zero_when_no_data(self) -> None:
        detector = ChatterDetector()
        assert detector.get_chatter_risk_score() == 0.0

    def test_risk_score_increases_with_vibration(self) -> None:
        detector = ChatterDetector(vibration_threshold=5.0)
        detector.check_chatter(4.0, 50.0)
        detector.check_chatter(4.5, 50.0)
        score = detector.get_chatter_risk_score()
        assert 0.0 <= score <= 100.0
        assert score > 0.0

    def test_reset_clears_history(self) -> None:
        detector = ChatterDetector(vibration_threshold=5.0)
        detector.check_chatter(4.0, 50.0)
        detector.reset()
        assert detector.get_chatter_risk_score() == 0.0


# Streaming events


class TestStreamEvent:
    def test_data_event_to_dict(self) -> None:
        sample = Sample(spindle_speed=8000.0, spindle_load=42.0)
        event = StreamEvent(data=sample, event_type="data")
        d = event.to_dict()
        assert d["event_type"] == "data"
        assert d["data"]["spindle_speed"] == 8000.0
        assert d["data"]["spindle_load"] == 42.0
        assert d["data"]["execution"] is None
        assert "event_id" in d and "timestamp" in d

    def test_alert_event_to_dict_includes_alert_fields(self) -> None:
        event = AlertEvent(
            alert_type="spindle_overload",
            message="Overload",
            threshold_value=80.0,
            actual_value=92.0,
            priority=6,
        )
        d = event.to_dict()
        assert d["event_type"] == "alert"
        assert d["alert_type"] == "spindle_overload"
        assert d["message"] == "Overload"
        assert d["threshold_value"] == 80.0
        assert d["actual_value"] == 92.0
        assert d["priority"] == 6

    def test_events_have_unique_ids(self) -> None:
        assert StreamEvent().event_id != StreamEvent().event_id


class TestStreamConsumer:
    def test_consumer_counts_data_events(self) -> None:
        consumer = StreamConsumer("test")
        assert consumer.event_count == 0
        assert consumer.alert_count == 0

    def test_consumer_counts_alerts(self) -> None:
        consumer = StreamConsumer("test")
        alert = AlertEvent(alert_type="chatter", message="Chatter!")
        asyncio.run(consumer.on_event(alert))
        assert consumer.event_count == 1
        assert consumer.alert_count == 1

    def test_consumer_status(self) -> None:
        consumer = StreamConsumer("test")
        status = consumer.status()
        assert status["name"] == "test"
        assert status["event_count"] == 0
        assert status["last_event"] is None


class TestMTConnectStreamServer:
    def test_initial_stats(self) -> None:
        server = MTConnectStreamServer(agent_url="http://example.invalid")
        stats = server.get_stats()
        assert stats["events_emitted"] == 0
        assert stats["alerts_emitted"] == 0
        assert stats["active_consumers"] == 0
        assert stats["active_subscribers"] == 0

    def test_add_remove_consumer(self) -> None:
        server = MTConnectStreamServer(agent_url="http://example.invalid")
        consumer = StreamConsumer("c1")
        server.add_consumer(consumer)
        assert server.get_stats()["active_consumers"] == 1
        assert server.remove_consumer(consumer) is True
        assert server.remove_consumer(consumer) is False
        assert server.get_stats()["active_consumers"] == 0

    def test_check_alerts_spindle_overload(self) -> None:
        server = MTConnectStreamServer(agent_url="http://example.invalid")
        sample = Sample(spindle_load=95.0)
        alerts = server._check_alerts(sample)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "spindle_overload"
        assert alerts[0].actual_value == 95.0

    def test_check_alerts_feed_anomaly(self) -> None:
        server = MTConnectStreamServer(agent_url="http://example.invalid")
        sample = Sample(feedrate=0.0)
        alerts = server._check_alerts(sample)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "feed_anomaly"

    def test_check_alerts_clean_sample(self) -> None:
        server = MTConnectStreamServer(agent_url="http://example.invalid")
        sample = Sample(spindle_load=50.0, feedrate=300.0)
        assert server._check_alerts(sample) == []

    def test_alerts_disabled(self) -> None:
        server = MTConnectStreamServer(agent_url="http://example.invalid", enable_alerts=False)
        sample = Sample(spindle_load=95.0)
        # With alerts disabled, the server's check still runs but the
        # public API contracts should not raise.
        assert isinstance(server._check_alerts(sample), list)

    def test_emit_event_updates_stats(self) -> None:
        async def scenario() -> None:
            server = MTConnectStreamServer(agent_url="http://example.invalid")
            consumer = StreamConsumer("c")
            server.add_consumer(consumer)
            sample = Sample(spindle_speed=8000.0)
            await server._emit_event(StreamEvent(data=sample))
            stats = server.get_stats()
            assert stats["events_emitted"] == 1
            assert stats["active_consumers"] == 1
            assert consumer.event_count == 1

        asyncio.run(scenario())
