"""仿真工厂沙盒（Phase 3b：① SUPCON Factory Agent 思路）。

借鉴中控 SUPCON「会思考的工厂」Factory Agent / NLDF 仿真基准：
- **事件总线**：MQTT 风格 topic 的进程内 pub/sub（接入真实产线时替换为 paho-mqtt）；
- **虚拟设备**：复用 Phase 2 的 SimulatedDevice（cnc_mill_01 + vib_sensor_01）；
- **物理耦合**：工厂 tick 依据机床状态合成传感器信号（振动峰值/颤振等级），
  模拟真实物理链路（转速↑→振动↑；切深/磨损↑→颤振风险↑）；
- **KPI 追踪**：产量/缺陷/停机，输出 NLDF 风格加权评分
  （生产效率 40 + 质量成本 30 + 停机可用性 30，满分 100）。

闭环（SUPCON 五层）在 factory_agent.py 实现：感知 → 推理 → 执行 → 反馈。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Callable

from mcp_server.device_registry import (
    SimulatedDevice,
    build_cnc_milling_descriptor,
    build_vibration_sensor_descriptor,
)

logger = logging.getLogger("lingjing-mcp")

# NLDF 风格权重（满分 100）
W_EFFICIENCY = 40.0  # 生产效率
W_QUALITY = 30.0  # 质量成本
W_AVAILABILITY = 30.0  # 停机可用性


@dataclass
class FactoryKpis:
    """工厂 KPI 快照（NLDF 风格）。"""

    ticks: int = 0
    parts_requested: int = 0
    parts_completed: int = 0
    defect_count: int = 0
    downtime_ticks: int = 0
    chatter_events: int = 0

    @property
    def quality_rate(self) -> float:
        return 1.0 - (self.defect_count / self.parts_completed) if self.parts_completed else 1.0

    @property
    def availability(self) -> float:
        return 1.0 - (self.downtime_ticks / self.ticks) if self.ticks else 1.0

    @property
    def throughput(self) -> float:
        return self.parts_completed / self.ticks if self.ticks else 0.0

    def score(self) -> dict[str, float]:
        """NLDF 风格 100 分制评分。"""
        # 生产效率：单位 tick 产量归一化（基准 0.05 件/tick = 满分）
        eff = min(self.throughput / 0.05, 1.0)
        quality = max(self.quality_rate, 0.0)
        avail = max(self.availability, 0.0)
        return {
            "efficiency": round(eff * W_EFFICIENCY, 2),
            "quality": round(quality * W_QUALITY, 2),
            "availability": round(avail * W_AVAILABILITY, 2),
            "total": round(eff * W_EFFICIENCY + quality * W_QUALITY + avail * W_AVAILABILITY, 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticks": self.ticks,
            "parts_requested": self.parts_requested,
            "parts_completed": self.parts_completed,
            "defect_count": self.defect_count,
            "downtime_ticks": self.downtime_ticks,
            "chatter_events": self.chatter_events,
            "quality_rate": round(self.quality_rate, 4),
            "availability": round(self.availability, 4),
            "throughput": round(self.throughput, 4),
            "score": self.score(),
        }


class SimulatedFactory:
    """仿真工厂：多设备 + 事件总线 + 时序推进 + KPI 追踪。

    Args:
        seed: 随机种子（确定性可测）。
        process_ticks: 单件加工耗时（tick 数）。
        defect_rate: 无颤振时的基础缺陷率。
    """

    def __init__(self, seed: int = 42, process_ticks: int = 3, defect_rate: float = 0.05) -> None:
        self._rng = random.Random(seed)
        self._clock = 0
        self._process_ticks = process_ticks
        self._defect_rate = defect_rate
        self._bus: dict[str, list[Callable[[str, dict[str, Any]], None]]] = {}
        self._event_queue: list[tuple[str, dict[str, Any]]] = []

        self.cnc = SimulatedDevice(build_cnc_milling_descriptor())
        self.sensor = SimulatedDevice(build_vibration_sensor_descriptor())
        self.devices = {"cnc_mill_01": self.cnc, "vib_sensor_01": self.sensor}

        # 生产状态机
        self._busy_ticks = 0
        self._queued_parts = 0
        self._machine_commanded = False  # agent 是否已下达加工指令
        self.kpis = FactoryKpis()

    # 事件总线（MQTT 风格 topic）
    def subscribe(self, topic: str, handler: Callable[[str, dict[str, Any]], None]) -> None:
        self._bus.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self._event_queue.append((topic, payload))
        for handler in self._bus.get(topic, []):
            try:
                handler(topic, payload)
            except Exception as e:  # noqa: BLE001 - 订阅者异常不应阻断仿真
                logger.warning("事件订阅者异常 %s: %s", topic, e)

    def drain_events(self) -> list[tuple[str, dict[str, Any]]]:
        events = list(self._event_queue)
        self._event_queue.clear()
        return events

    # 物理耦合：依据机床状态合成传感器信号
    def _synthesize_sensor(self) -> None:
        spindle_rpm = float(self.cnc.read_signal("spindle_rpm"))
        # 物理语义：转速 > 0 即主轴旋转（spindle_on 布尔信号由 execute 的 stop/start 维护）
        spindle_on = spindle_rpm > 0
        base = 0.05 + (spindle_rpm / 24000.0) * 0.3 * (1.0 if spindle_on else 0.2)
        # 颤振风险：随机波动，约 12% tick 出现高振动
        noise = self._rng.random()
        if noise < 0.12 and spindle_on:
            peak = base + self._rng.uniform(0.5, 0.9)
            level = "high"
        else:
            peak = base + self._rng.uniform(0.0, 0.15)
            level = "low"
        self.sensor.set_signal("vibration_peak", round(peak, 4))
        self.sensor.set_signal("chatter_level", level)
        if level == "high":
            self.kpis.chatter_events += 1
            self.publish("factory/chatter/high", {"vibration_peak": peak, "tick": self._clock})

    # 生产推进
    def enqueue_parts(self, n: int) -> None:
        self._queued_parts += n
        self.kpis.parts_requested += n

    def command_machine(self, on: bool) -> None:
        """agent 下达加工指令（开始/停止连续生产）。"""
        self._machine_commanded = on

    @property
    def machine_busy(self) -> bool:
        return self._busy_ticks > 0

    def step(self) -> None:
        """推进一个 tick：处理生产 + 合成传感器 + 发事件。"""
        self._clock += 1
        self.kpis.ticks += 1

        # 生产：有排队件且（已在下达加工态或正在加工） 推进加工
        if self.machine_busy:
            self._busy_ticks -= 1
            if self._busy_ticks == 0:
                self._complete_part()
        elif self._machine_commanded and self._queued_parts > 0:
            self._busy_ticks = self._process_ticks
        elif self._machine_commanded and self._queued_parts == 0:
            # 无料 停机（待料）
            self.kpis.downtime_ticks += 1

        self._synthesize_sensor()

    def _complete_part(self) -> None:
        self._queued_parts -= 1
        self.kpis.parts_completed += 1
        defect = self._rng.random() < self._defect_rate
        if defect:
            self.kpis.defect_count += 1
        self.publish(
            "factory/part/complete", {"part": self.kpis.parts_completed, "defect": defect, "tick": self._clock}
        )

    def execute(self, device_id: str, op_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """向设备执行能力（agent 动作通道）。"""
        device = self.devices.get(device_id)
        if device is None:
            raise ValueError(f"未知设备: {device_id}")
        result = device.execute(op_name, params or {})
        self.publish(f"factory/{device_id}/{op_name}", {"params": params or {}, "result": result})
        return result

    def get_status(self) -> dict[str, Any]:
        """感知层：工厂 + 设备 + 传感器全量状态。"""
        return {
            "tick": self._clock,
            "machine_busy": self.machine_busy,
            "queued_parts": self._queued_parts,
            "cnc": self.cnc.status(),
            "sensor": self.sensor.status(),
            "kpis": self.kpis.to_dict(),
        }

    def get_kpis(self) -> dict[str, Any]:
        return self.kpis.to_dict()


__all__ = ["FactoryKpis", "SimulatedFactory", "W_EFFICIENCY", "W_QUALITY", "W_AVAILABILITY"]
