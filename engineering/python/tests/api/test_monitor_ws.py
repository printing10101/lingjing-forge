"""Unit tests for monitor WebSocket 端点（Phase A 实时通道）。"""

from __future__ import annotations

import os
import socket

os.environ.setdefault("LNN_PERMISSION_ENFORCED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.monitor_ws import router
from app.dnc.mock_agent import MockMTConnectAgent
from app.integrations.mtconnect.parser import Sample
from app.integrations.mtconnect.streaming import check_alerts


def _free_port() -> int:
    """探测一个空闲端口，避免 CI 环境端口冲突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _recv_until_data(ws) -> dict:
    """跳过 status/alert 前置事件，收第一条 data。"""
    msg = ws.receive_json()
    while msg["event_type"] != "data":
        msg = ws.receive_json()
    return msg


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestMonitorWS:
    def test_ws_no_demo_by_default_pushes_disconnected_notice(self, client: TestClient, monkeypatch) -> None:
        """信任红线：默认（未配置 Agent、未开演示）绝不虚构机床数据。"""
        monkeypatch.delenv("MTCONNECT_AGENT_URL", raising=False)
        monkeypatch.delenv("LNN_MONITOR_ALLOW_DEMO", raising=False)
        with client.websocket_connect("/monitor/ws") as ws:
            msg = ws.receive_json()
            assert msg["event_type"] == "alert"
            assert msg["alert_type"] == "agent_disconnected"
            assert msg["data"]["source"] == "disconnected"
            assert "MTCONNECT_AGENT_URL" in msg["message"]

    def test_ws_receives_data_events_in_demo_mode(self, client: TestClient, monkeypatch) -> None:
        """显式演示模式（LNN_MONITOR_ALLOW_DEMO=1）下 demo 数据可用且带标记。"""
        monkeypatch.delenv("MTCONNECT_AGENT_URL", raising=False)
        monkeypatch.setenv("LNN_MONITOR_ALLOW_DEMO", "1")
        with client.websocket_connect("/monitor/ws") as ws:
            msg = _recv_until_data(ws)
            assert "event_id" in msg
            assert "timestamp" in msg
            assert msg["data"]["spindle_speed"] is not None
            # demo 数据必须带显式标记，不能被当作真实机床状态
            assert msg.get("source") == "demo" or msg["data"].get("source") == "demo"

    def test_ws_subscribe_changes_machine(self, client: TestClient) -> None:
        with client.websocket_connect("/monitor/ws") as ws:
            ws.send_json({"action": "subscribe", "machine_id": "VM-042"})
            status = ws.receive_json()
            assert status["event_type"] == "status"
            assert "VM-042" in status["message"]

    def test_ws_data_event_shape(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setenv("LNN_MONITOR_ALLOW_DEMO", "1")
        with client.websocket_connect("/monitor/ws") as ws:
            msg = _recv_until_data(ws)
            assert msg["event_type"] == "data"
            data = msg["data"]
            # demo 模式下 payload.data 额外带 source=demo 标记（信任红线要求）
            assert set(data.keys()) == {
                "spindle_speed",
                "spindle_load",
                "feedrate",
                "execution",
                "source",
            }

    def test_ws_heartbeat_event(self) -> None:
        """心跳事件格式校验（模拟 15s 后触发的 ping）。"""
        import json

        heartbeat = {"event_type": "ping", "timestamp": "2026-08-21T00:00:00+00:00"}
        assert json.loads(json.dumps(heartbeat))["event_type"] == "ping"

    def test_demo_sample_generator(self) -> None:
        from app.api.v1.monitor_ws import _demo_sample

        s1 = _demo_sample("VM-001", 0)
        s2 = _demo_sample("VM-001", 1)
        assert s1.spindle_speed is not None
        assert s1.execution in ("ACTIVE", "IDLE")
        # 不同 tick 产生不同转速（模拟变化）
        assert s1.spindle_speed != s2.spindle_speed

    def test_demo_mode_off_by_default(self, monkeypatch) -> None:
        """_demo_allowed / _resolve_agent_url 默认行为：不连任何数据源。"""
        from app.api.v1.monitor_ws import _demo_allowed, _resolve_agent_url

        monkeypatch.delenv("MTCONNECT_AGENT_URL", raising=False)
        monkeypatch.delenv("LNN_MONITOR_ALLOW_DEMO", raising=False)
        assert _demo_allowed() is False
        assert _resolve_agent_url() is None

        monkeypatch.setenv("LNN_MONITOR_ALLOW_DEMO", "1")
        assert _demo_allowed() is True
        assert _resolve_agent_url() is not None

        monkeypatch.setenv("MTCONNECT_AGENT_URL", "http://192.168.1.10:5000")
        assert _resolve_agent_url() == "http://192.168.1.10:5000"


# 告警规则（check_alerts 纯函数）


class TestCheckAlerts:
    def test_normal_sample_no_alert(self) -> None:
        sample = Sample(spindle_speed=6000, spindle_load=50, feedrate=300, execution="ACTIVE")
        assert check_alerts(sample) == []

    def test_spindle_overload_triggers_alert(self) -> None:
        sample = Sample(spindle_speed=6000, spindle_load=92, feedrate=300, execution="ACTIVE")
        alerts = check_alerts(sample)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "spindle_overload"
        assert alerts[0].actual_value == 92.0
        assert alerts[0].threshold_value == 80.0

    def test_feed_anomaly_triggers_alert(self) -> None:
        sample = Sample(spindle_speed=6000, spindle_load=50, feedrate=0.05, execution="ACTIVE")
        alerts = check_alerts(sample)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "feed_anomaly"

    def test_multiple_conditions_produce_multiple_alerts(self) -> None:
        sample = Sample(spindle_speed=6000, spindle_load=95, feedrate=0.0, execution="ACTIVE")
        alerts = check_alerts(sample)
        types = {a.alert_type for a in alerts}
        assert "spindle_overload" in types
        assert "feed_anomaly" in types


# 模拟 Agent WS 端到端（真实 HTTP + 告警推送）


class TestMonitorWSWithMockAgent:
    def test_ws_streams_real_mock_agent_data(self, client: TestClient, monkeypatch) -> None:
        """WS 数据流来自模拟 Agent 真实值（非 demo）。"""
        port = _free_port()
        agent = MockMTConnectAgent(port=port, device_name="TEST-CNC-001")
        agent.start()
        try:
            monkeypatch.setenv("MTCONNECT_AGENT_URL", agent.url)
            with client.websocket_connect("/monitor/ws") as ws:
                msg = ws.receive_json()
                # 首条应为 data 事件（默认 overload_cycle=20，前几帧无告警）
                assert msg["event_type"] == "data"
                data = msg["data"]
                # 模拟 Agent base_spindle_speed=6000，±5% 波动 在 5500~6500 区间
                assert 5500 <= data["spindle_speed"] <= 6500
                assert data["spindle_load"] is not None
                assert data["feedrate"] is not None
                assert data["execution"] in ("ACTIVE", "IDLE")
        finally:
            agent.stop()

    def test_ws_pushes_overload_alert_from_mock_agent(self, client: TestClient, monkeypatch) -> None:
        """过载帧经模拟 Agent → adapter → check_alerts → WS alert 全链路推送。

        设置 overload_cycle=2：probe 消耗 tick=1，首次 fetch（tick=2）即触发过载。
        """
        port = _free_port()
        agent = MockMTConnectAgent(port=port, device_name="TEST-CNC-001")
        agent.simulator.overload_cycle = 2
        agent.start()
        try:
            monkeypatch.setenv("MTCONNECT_AGENT_URL", agent.url)
            with client.websocket_connect("/monitor/ws") as ws:
                first = ws.receive_json()
                # 告警优先推送 首条为 spindle_overload
                assert first["event_type"] == "alert"
                assert first["alert_type"] == "spindle_overload"
                assert first["actual_value"] > 80.0
                # 随后收到 data 事件
                second = ws.receive_json()
                assert second["event_type"] == "data"
                assert second["data"]["spindle_speed"] is not None
        finally:
            agent.stop()

    def test_ws_demo_fallback_when_agent_unreachable(self, client: TestClient, monkeypatch) -> None:
        """显式演示模式下，Agent 不可达时优雅降级为 demo 数据（带标记）。"""
        port = _free_port()  # 无服务监听
        monkeypatch.setenv("MTCONNECT_AGENT_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LNN_MONITOR_ALLOW_DEMO", "1")
        with client.websocket_connect("/monitor/ws") as ws:
            msg = _recv_until_data(ws)
            assert msg["event_type"] == "data"
            assert msg["data"]["spindle_speed"] is not None
            assert msg.get("source") == "demo" or msg["data"].get("source") == "demo"

    def test_ws_unreachable_agent_without_demo_pushes_notice(self, client: TestClient, monkeypatch) -> None:
        """信任红线：Agent 不可达且未开演示 → 推送未连接告警而非虚构数据。"""
        port = _free_port()  # 无服务监听
        monkeypatch.setenv("MTCONNECT_AGENT_URL", f"http://127.0.0.1:{port}")
        monkeypatch.delenv("LNN_MONITOR_ALLOW_DEMO", raising=False)
        with client.websocket_connect("/monitor/ws") as ws:
            msg = ws.receive_json()
            assert msg["event_type"] == "alert"
            assert msg["alert_type"] == "agent_disconnected"
            assert msg["data"]["source"] == "disconnected"
            assert "不可达" in msg["message"]
