"""仿真工厂 → MCP 工具（升级①：语言驱动仿真工厂 + 传输抽象）。

让 LLM Agent 通过 MCP 直接驱动仿真工厂（SUPCON「语言驱动工厂」落点）：

- ``factory_run_cycle``：闭环生产 n 件并返回 NLDF 风格 KPI 评分；
- ``factory_get_status``：工厂/设备/传感器全量状态（感知层）；
- ``factory_get_kpis``：KPI 快照；
- ``factory_step``：手动推进一个仿真 tick。

传输抽象：工厂事件总线为 MQTT 风格 topic 的进程内 pub/sub；
``MqttEventBridge`` 在配置 ``LNN_MQTT_URL`` 且装有 paho-mqtt 时把事件镜像到
真实 broker，未配置/未安装则 no-op（进程内总线兜底）——接真实产线时零改动换桥。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from mcp_server.factory_sandbox import SimulatedFactory

logger = logging.getLogger("lingjing-mcp")

_MQTT_TOPICS = ("factory/chatter/high", "factory/part/complete")


def _fmt_success(data: dict[str, Any]) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _fmt_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


# 单例（供 MCP 工具共享状态）
_factory: SimulatedFactory | None = None
_agent: Any | None = None


def _get_singletons(seed: int = 42) -> tuple[SimulatedFactory, Any]:
    global _factory, _agent
    if _factory is None:
        from mcp_server.factory_agent import ClosedLoopAgent

        _factory = SimulatedFactory(seed=seed)
        _agent = ClosedLoopAgent(factory=_factory, seed=seed)
    return _factory, _agent


# 工具处理器（与 mcp_server/device_tools.py 格式一致：错误不抛，结构化文本）
async def _handle_run_cycle(n_parts: int, max_ticks: int = 500) -> str:
    if n_parts <= 0 or n_parts > 10000:
        return _fmt_error(f"n_parts 必须为 1~10000，收到 {n_parts}")
    factory, agent = _get_singletons()
    report = agent.run_production_cycle(n_parts=n_parts, max_ticks=max_ticks)
    return _fmt_success(report)


async def _handle_get_status() -> str:
    factory, _ = _get_singletons()
    return _fmt_success(factory.get_status())


async def _handle_get_kpis() -> str:
    factory, _ = _get_singletons()
    return _fmt_success(factory.get_kpis())


async def _handle_step() -> str:
    factory, _ = _get_singletons()
    factory.step()
    return _fmt_success(factory.get_kpis())


def register_factory_tools(
    server: Any,
    factory: SimulatedFactory | None = None,
    agent: Any | None = None,
) -> list[str]:
    """在 FastMCP server 上注册仿真工厂工具。

    Args:
        server: FastMCP 实例。
        factory: 工厂实例（缺省用模块单例，便于测试注入）。
        agent: 闭环 Agent（缺省用模块单例）。

    Returns:
        注册的工具名列表。
    """
    global _factory, _agent
    if factory is not None:
        _factory = factory
    if agent is not None:
        _agent = agent

    registered: list[str] = []
    specs = [
        (
            "factory_run_cycle",
            "闭环生产 n 件零件并返回 NLDF 风格 KPI 评分（生产效率/质量/可用性，满分 100）",
            _handle_run_cycle,
        ),
        ("factory_get_status", "读取仿真工厂全量状态（机床/传感器/KPI 快照）", _handle_get_status),
        ("factory_get_kpis", "读取工厂 KPI 与评分", _handle_get_kpis),
        ("factory_step", "手动推进一个仿真 tick（感知层调试用）", _handle_step),
    ]
    for name, description, handler in specs:
        server.tool(name=name, description=description)(handler)
        registered.append(name)
    logger.info("仿真工厂已注册 %d 个 MCP 工具: %s", len(registered), registered)
    return registered


# MQTT 事件桥（传输抽象：接真实 broker 时零改动）
class MqttEventBridge:
    """把工厂事件镜像到真实 MQTT broker。

    - 配置 ``LNN_MQTT_URL`` 且装有 paho-mqtt 时：订阅工厂事件并发布到 broker；
    - 否则 no-op（进程内总线兜底，仿真默认形态）。
    """

    def __init__(self, factory: SimulatedFactory, broker_url: str | None = None, topic_prefix: str = "factory") -> None:
        self.factory = factory
        self.broker_url = broker_url or os.environ.get("LNN_MQTT_URL", "")
        self.topic_prefix = topic_prefix
        self._client: Any = None
        self._publishes: list[tuple[str, str]] = []  # 便于测试/调试记录
        try:
            import paho.mqtt.client as mqtt  # type: ignore[import-not-found]

            self._mqtt = mqtt
        except ImportError:
            self._mqtt = None
        if self._mqtt and self.broker_url:
            logger.info("MQTT 事件桥已启用: %s（topic 前缀 %s）", self.broker_url, topic_prefix)

    @property
    def enabled(self) -> bool:
        return self._mqtt is not None and bool(self.broker_url)

    def attach(self) -> None:
        """把桥挂到工厂事件总线（订阅已知 topic）。"""

        def _make_handler(topic: str) -> Callable[[str, dict[str, Any]], None]:
            def handler(_topic: str, payload: dict[str, Any]) -> None:
                self._publish(topic, payload)

            return handler

        for topic in _MQTT_TOPICS:
            self.factory.subscribe(topic, _make_handler(topic))

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False)
        self._publishes.append((topic, body))
        if self.enabled and self._client is not None:
            try:
                self._client.publish(f"{self.topic_prefix}/{topic.split('/', 1)[-1]}", body)
            except Exception as e:  # noqa: BLE001 - 桥异常不阻断仿真
                logger.warning("MQTT 发布失败 %s: %s", topic, e)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["MqttEventBridge", "register_factory_tools"]
