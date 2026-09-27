"""仿真工厂闭环控制 Agent（Phase 3b：① SUPCON Factory Agent 思路）。

实现 SUPCON「感知 → 推理 → 执行 → 反馈」闭环：

- **感知（Perception）**：读取工厂/设备/传感器状态；
- **推理（Reasoning）**：规则策略——颤振等级 high 时降速抑颤（闭环反馈），
  空闲且有任务时启动加工；
- **执行（Action）**：向 SimulatedFactory 下发设备能力（start_spindle / move_axis /
  set_feed_rate / stop_spindle）；
- **反馈（Feedback）**：推进 tick 后读回新状态与 KPI，形成闭环。

`run_production_cycle(n_parts, max_ticks)` 自动生产 n 件并返回 NLDF 风格
KPI 评分。颤振降速逻辑与你的「AI 主动稳定性控制（ASC，自适应 SSV 调速抑颤）」
研究主线同构——仿真沙盒可作为 ASC 策略的验证场。
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_server.factory_sandbox import SimulatedFactory

logger = logging.getLogger("lingjing-mcp")

# 颤振降速参数（ASC 风格自适应调速）
_CHATTER_RPM_REDUCTION = 0.8  # 检测到颤振 → 转速 × 0.8
_MIN_RPM = 500.0  # 降速下限（防失速）


class ClosedLoopAgent:
    """仿真工厂闭环控制 Agent（规则版，LLM 版可在此之上扩展）。"""

    def __init__(self, factory: SimulatedFactory | None = None, seed: int = 42) -> None:
        self.factory = factory if factory is not None else SimulatedFactory(seed=seed)
        self._parts_done = 0
        self._actions_taken: list[str] = []

    # 闭环四步
    def perceive(self) -> dict[str, Any]:
        """感知层：读取全量状态。"""
        return self.factory.get_status()

    def decide(self, status: dict[str, Any], parts_remaining: int) -> str:
        """推理层：返回下一步动作标识。

        - "suppress_chatter"：颤振等级 high → 降速抑颤（闭环反馈）
        - "start_production"：空闲且有任务 → 启动加工
        - "stop_production"：任务完成 → 停机
        - "idle"：等待
        """
        sensor_state = status.get("sensor", {}).get("state", {})
        chatter_level = sensor_state.get("chatter_level", "low")
        machine_busy = status.get("machine_busy", False)

        # 闭环抑颤：无论忙闲，检测到高振动即降速（ASC 风格）
        if chatter_level == "high":
            return "suppress_chatter"
        if parts_remaining > 0 and not machine_busy:
            return "start_production"
        if parts_remaining == 0 and not machine_busy:
            return "stop_production"
        return "idle"

    def act(self, action: str) -> None:
        """执行层：把动作翻译为设备能力调用。"""
        f = self.factory
        if action == "start_production":
            f.execute("cnc_mill_01", "move_axis", {"x": 0.0, "y": 0.0, "z": 50.0})
            f.execute("cnc_mill_01", "set_feed_rate", {"feed_rate": 1200.0})
            f.execute("cnc_mill_01", "start_spindle", {"spindle_rpm": 8000.0})
            f.command_machine(True)
            self._actions_taken.append("start_production")
        elif action == "suppress_chatter":
            current = float(f.cnc.read_signal("spindle_rpm"))
            target = max(current * _CHATTER_RPM_REDUCTION, _MIN_RPM)
            f.execute("cnc_mill_01", "start_spindle", {"spindle_rpm": round(target, 1)})
            self._actions_taken.append(f"suppress_chatter->{target:.0f}rpm")
            logger.info("闭环抑颤：spindle %s → %s rpm", current, round(target, 1))
        elif action == "stop_production":
            f.execute("cnc_mill_01", "stop_spindle", {})
            f.command_machine(False)
            self._actions_taken.append("stop_production")

    def observe(self) -> dict[str, Any]:
        """反馈层：推进一个 tick 并读回状态与 KPI。"""
        self.factory.step()
        return self.factory.get_kpis()

    # 闭环主循环
    def run_production_cycle(self, n_parts: int, max_ticks: int = 500) -> dict[str, Any]:
        """闭环生产 n 件零件，返回 KPI 报告。

        Args:
            n_parts: 目标产量。
            max_ticks: 最大仿真 tick 数（防死循环）。

        Returns:
            KPI dict（含 NLDF 风格 score）。
        """
        self.factory.enqueue_parts(n_parts)
        for _ in range(max_ticks):
            status = self.perceive()
            parts_remaining = status.get("queued_parts", 0)
            # 从事件队列获取最新颤振告警（感知增强：事件驱动）
            for topic, _payload in self.factory.drain_events():
                if topic == "factory/chatter/high":
                    # 强制进入抑颤决策
                    status = self.perceive()
                    status["sensor"]["state"]["chatter_level"] = "high"
            action = self.decide(status, parts_remaining)
            if action == "stop_production" and self._parts_done >= n_parts:
                break
            if action != "idle":
                self.act(action)
            kpis = self.observe()
            self._parts_done = kpis.get("parts_completed", 0)
            if self._parts_done >= n_parts:
                # 目标达成，最后停机
                self.act("stop_production")
                break

        final = self.factory.get_kpis()
        final["actions"] = self._actions_taken
        return final


__all__ = ["ClosedLoopAgent"]
