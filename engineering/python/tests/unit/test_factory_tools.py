"""仿真工厂 MCP 工具 + MQTT 桥 单元测试（升级①）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_factory_tools.py -v --no-cov
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("LINGJING_MCP_DEV", "1")
os.environ.setdefault("LINGJING_AGENT_TOKEN", "x" * 40)

import json

import pytest

from mcp_server.factory_agent import ClosedLoopAgent
from mcp_server.factory_sandbox import SimulatedFactory
from mcp_server.factory_tools import (
    MqttEventBridge,
    _handle_get_kpis,
    _handle_get_status,
    _handle_run_cycle,
    register_factory_tools,
)


class _FakeServer:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []

    def tool(self, name: str = "", description: str = ""):
        def deco(fn):
            self.registered.append((name, description))
            return fn

        return deco


class TestFactoryTools:
    def setup_method(self) -> None:
        self.factory = SimulatedFactory(seed=11)
        self.agent = ClosedLoopAgent(factory=self.factory, seed=11)
        register_factory_tools(_FakeServer(), factory=self.factory, agent=self.agent)

    @pytest.mark.asyncio
    async def test_run_cycle_handler(self) -> None:
        out = await _handle_run_cycle(n_parts=2, max_ticks=300)
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["data"]["parts_completed"] == 2
        assert 0 <= payload["data"]["score"]["total"] <= 100

    @pytest.mark.asyncio
    async def test_run_cycle_rejects_bad_input(self) -> None:
        out = await _handle_run_cycle(n_parts=0)
        payload = json.loads(out)
        assert payload["ok"] is False

    @pytest.mark.asyncio
    async def test_status_and_kpis_handlers(self) -> None:
        s = json.loads(await _handle_get_status())
        assert s["ok"] is True
        assert "cnc" in s["data"]
        k = json.loads(await _handle_get_kpis())
        assert k["ok"] is True
        assert "score" in k["data"]

    def test_register_names(self) -> None:
        server = _FakeServer()
        names = register_factory_tools(server, factory=self.factory, agent=self.agent)
        assert set(names) == {"factory_run_cycle", "factory_get_status", "factory_get_kpis", "factory_step"}

    def test_register_tools_integration(self) -> None:
        import mcp_server.tools as tools

        fake = _FakeServer()
        tools.register_tools(fake)
        names = [n for n, _ in fake.registered]
        assert "factory_run_cycle" in names
        assert "cnc_mill_01_start_spindle" in names  # 设备工具仍在
        assert "lnn_list_models" in names  # LNN 工具仍在


class TestMqttBridge:
    def test_disabled_without_broker(self) -> None:
        factory = SimulatedFactory(seed=3)
        bridge = MqttEventBridge(factory, broker_url=None)
        assert bridge.enabled is False  # 未配置 broker（paho 未装）→ no-op

    def test_captures_events_in_process(self) -> None:
        factory = SimulatedFactory(seed=5)
        bridge = MqttEventBridge(factory, broker_url=None)
        bridge.attach()
        factory.execute("cnc_mill_01", "start_spindle", {"spindle_rpm": 15000.0})
        factory.enqueue_parts(10)
        factory.command_machine(True)
        for _ in range(100):
            factory.step()
        # 事件被进程内捕获（即使无真实 broker，publishes 记录可用于桥接/审计）
        assert any("factory/chatter/high" in t for t, _ in bridge._publishes) or any(
            "factory/part/complete" in t for t, _ in bridge._publishes
        )
