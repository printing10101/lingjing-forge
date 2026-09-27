"""仿真工厂闭环沙盒 单元测试（Phase 3b：① SUPCON Factory Agent 思路）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_factory_sandbox.py -v --no-cov
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

import pytest

from mcp_server.device_registry import SimulatedDevice
from mcp_server.factory_agent import ClosedLoopAgent
from mcp_server.factory_sandbox import SimulatedFactory


class TestSimulatedFactory:
    def setup_method(self) -> None:
        self.factory = SimulatedFactory(seed=7)

    def test_tick_advances_clock(self) -> None:
        self.factory.step()
        assert self.factory.get_status()["tick"] == 1

    def test_production_cycle_completes_parts(self) -> None:
        self.factory.enqueue_parts(3)
        self.factory.command_machine(True)
        for _ in range(30):  # 3 件 × 3 tick 加工 + 余量
            self.factory.step()
        kpis = self.factory.get_kpis()
        assert kpis["parts_completed"] == 3
        assert kpis["ticks"] == 30
        assert 0.0 <= kpis["score"]["total"] <= 100.0

    def test_quality_rate(self) -> None:
        # 确定性种子下运行一段生产，quality_rate ∈ [0,1]
        self.factory.enqueue_parts(5)
        self.factory.command_machine(True)
        for _ in range(60):
            self.factory.step()
        kpis = self.factory.get_kpis()
        assert 0.0 <= kpis["quality_rate"] <= 1.0
        assert kpis["parts_completed"] == 5

    def test_chatter_event_published(self) -> None:
        events: list[str] = []
        self.factory.subscribe("factory/chatter/high", lambda topic, payload: events.append(topic))
        # 启动主轴（spindle_on=True 才可能触发颤振告警）
        self.factory.execute("cnc_mill_01", "start_spindle", {"spindle_rpm": 12000.0})
        self.factory.enqueue_parts(20)
        self.factory.command_machine(True)
        for _ in range(200):
            self.factory.step()
        assert events  # 高转速下应出现至少一次颤振告警

    def test_execute_unknown_device(self) -> None:
        with pytest.raises(ValueError, match="未知设备"):
            self.factory.execute("ghost", "start_spindle")

    def test_set_signal_validation(self) -> None:
        with pytest.raises(ValueError, match="无信号"):
            SimulatedDevice(self.factory.cnc.descriptor).set_signal("nope", 1.0)


class TestClosedLoopAgent:
    def test_production_cycle_terminates(self) -> None:
        agent = ClosedLoopAgent(seed=42)
        report = agent.run_production_cycle(n_parts=3, max_ticks=500)
        assert report["parts_completed"] == 3
        assert report["parts_requested"] == 3
        assert report["score"]["total"] > 0
        assert "start_production" in report["actions"]

    def test_chatter_suppression_reduces_rpm(self) -> None:
        """闭环抑颤：颤振等级 high → 转速下降（ASC 风格自适应调速）。"""
        agent = ClosedLoopAgent(seed=42)
        agent.factory.execute("cnc_mill_01", "start_spindle", {"spindle_rpm": 8000.0})
        # 模拟传感器报高振动
        agent.factory.sensor.set_signal("chatter_level", "high")

        status = agent.perceive()
        action = agent.decide(status, parts_remaining=0)
        assert action == "suppress_chatter"
        agent.act(action)
        new_rpm = agent.factory.cnc.read_signal("spindle_rpm")
        assert new_rpm < 8000.0  # 已降速
        assert new_rpm >= 500.0  # 不低于下限

    def test_deterministic_with_seed(self) -> None:
        r1 = ClosedLoopAgent(seed=1).run_production_cycle(n_parts=2, max_ticks=200)
        r2 = ClosedLoopAgent(seed=1).run_production_cycle(n_parts=2, max_ticks=200)
        assert r1["score"] == r2["score"]
