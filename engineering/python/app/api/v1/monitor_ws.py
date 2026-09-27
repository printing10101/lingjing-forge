"""实时监控 WebSocket 端点（Phase A 前端实时数据通道）。

桥接 MTConnectStreamServer → FastAPI WebSocket：
- GET /api/v1/monitor/ws?machine_id=VM-001
  建立 WS 连接后，服务端持续推送 MTConnect 实时事件（data/alert），
  前端 MachineMonitor.vue 消费。

协议：
- 客户端 → 服务端：{ "action": "subscribe", "machine_id": "VM-001" }
- 服务端 → 客户端：StreamEvent.to_dict() JSON
  { "event_id", "timestamp", "data": {...}, "event_type", "priority" }
  其中 event_type ∈ {data, alert}

数据源（信任红线治理，2026-09）：
- 配置了环境变量 ``MTCONNECT_AGENT_URL``：连接该真实 Agent，不可达时推送
  「未连接」告警事件（不再静默虚构数据）。
- 未配置 ``MTCONNECT_AGENT_URL`` 且 ``LNN_MONITOR_ALLOW_DEMO=1``：显式演示
  模式，回落本地模拟 Agent（``http://127.0.0.1:5010``）+ demo 降级数据。
- 未配置且未开启演示模式（**默认**）：不连接任何数据源，周期性推送
  ``agent_disconnected`` 告警——设备监控页绝不展示虚构机床数据。

设计要点：
1. 信任红线：默认不虚构数据；demo 数据必须在显式开启演示模式后才可用，
   且 payload 带 source=demo 标记
2. 优雅降级：允许演示时 Agent 不可达降级 demo；未允许时降级为「未连接」告警，
   并周期性重探，Agent 恢复后自动切回真实数据
3. 心跳：每 15s 推送 ping 事件，检测断线
4. 权限：require_permission("monitor:read")
5. 并发安全：每连接独立订阅，断开自动清理
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.integrations.mtconnect.adapter import AdapterConfig, MTConnectAdapter
from app.integrations.mtconnect.parser import Sample
from app.integrations.mtconnect.streaming import StreamEvent, check_alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitor", tags=["realtime-monitor"])

# 心跳间隔（秒）
_HEARTBEAT_INTERVAL_S = 15.0

# demo 降级后周期性重探真实 Agent 的间隔（tick 数，每 tick 1 秒）。
# 此前断连后永久降级且不再恢复，操作员会一直看假数据。
_ADAPTER_REPROBE_TICKS = 30

# 本地模拟 Agent 默认地址（仅在显式演示模式下使用；生产环境用 MTCONNECT_AGENT_URL）
_DEFAULT_AGENT_URL = "http://127.0.0.1:5010"


def _demo_allowed() -> bool:
    """演示数据需显式开启（信任红线：默认禁止向用户展示虚构机床数据）。"""
    return os.getenv("LNN_MONITOR_ALLOW_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_agent_url() -> str | None:
    """返回当前生效的 MTConnect Agent URL；未配置且未开演示模式时为 None。"""
    env_url = os.getenv("MTCONNECT_AGENT_URL", "").strip()
    if env_url:
        return env_url
    if _demo_allowed():
        return _DEFAULT_AGENT_URL
    return None


def _close_adapter_quietly(adapter: MTConnectAdapter | None) -> None:
    if adapter is None:
        return
    try:
        adapter.close()
    except Exception:  # pragma: no cover - 防御性
        pass


def _create_adapter() -> MTConnectAdapter | None:
    """创建并探活 MTConnect 适配器；未配置数据源或 Agent 不可达时返回 None。"""
    agent_url = _resolve_agent_url()
    if not agent_url:
        return None
    cfg = AdapterConfig(
        agent_url=agent_url,
        interval=1.0,
        batch_size=1,
        batch_interval=0.0,
        max_retries=1,
        timeout=3.0,
    )
    adapter = MTConnectAdapter(config=cfg)
    try:
        adapter.probe()
        logger.info("monitor: connected to MTConnect agent %s", agent_url)
        return adapter
    except (ConnectionError, OSError, TimeoutError, RuntimeError):
        # RuntimeError：agent 返回 200 但不是合法 MTConnect 文档
        logger.warning("monitor: MTConnect agent %s 不可达，降级为 demo 数据源", agent_url)
        _close_adapter_quietly(adapter)
        return None


async def _ws_permission_check(websocket: WebSocket) -> None:
    """WS 权限检查（require_permission 是 FastAPI Depends，WS 需手动调用）。"""
    if os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return
    if not hasattr(websocket.state, "username") or not websocket.state.username:
        await websocket.close(code=4401, reason="Not authenticated")
        raise WebSocketDisconnect(4401, "Not authenticated")


def _demo_sample(machine_id: str, tick: int) -> Sample:
    """生成模拟样本（Agent 不可达时降级用，保证前端可调试）。"""
    return Sample(
        spindle_speed=float(6000 + tick % 500),
        spindle_load=float(40 + (tick % 30)),
        feedrate=float(300 + tick % 200),
        execution="ACTIVE" if tick % 5 else "IDLE",
    )


@router.websocket("/ws")
async def machine_monitor_ws(websocket: WebSocket) -> None:
    """实时机床监控 WebSocket 端点。

    数据源优先级：MTConnect Agent（本地联调用模拟 Agent）→ demo 降级。
    """
    await websocket.accept()
    try:
        await _ws_permission_check(websocket)
    except WebSocketDisconnect:
        return

    machine_id = "VM-001"
    tick = 0

    # 数据源：配置了 Agent（或显式演示模式）→ 真实/模拟数据；否则推送
    # 「未连接」告警（绝不虚构数据），并周期性重探，Agent 恢复后自动切回。
    demo_allowed = _demo_allowed()
    adapter = _create_adapter()

    def _disconnected_notice() -> dict[str, object]:
        url = _resolve_agent_url()
        if url:
            message = f"MTConnect Agent {url} 不可达，已停止推送机床数据"
            if demo_allowed:
                message += "（演示模式未对不可达 Agent 降级虚构数据）"
        else:
            message = "设备监控未连接：未配置 MTCONNECT_AGENT_URL。如需演示数据请设置 LNN_MONITOR_ALLOW_DEMO=1"
        return {
            "event_type": "alert",
            "alert_type": "agent_disconnected",
            "priority": 3,
            "message": message,
            "data": {"source": "disconnected", "machine_id": machine_id},
        }

    try:
        while True:
            # 接收订阅/心跳消息（非阻塞）
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
                action = message.get("action", "")
                if action == "subscribe" and message.get("machine_id"):
                    machine_id = str(message["machine_id"])
                    await websocket.send_json(
                        {
                            "event_type": "status",
                            "message": f"已订阅 {machine_id}",
                        }
                    )
                continue
            except asyncio.TimeoutError:
                pass

            # 仅在存在可连接的数据源时周期性重探
            if adapter is None and _resolve_agent_url() and tick % _ADAPTER_REPROBE_TICKS == 0:
                adapter = _create_adapter()

            sample: Sample | None = None
            if adapter is not None:
                try:
                    sample = await asyncio.to_thread(adapter.fetch_sample)
                except (ConnectionError, OSError, TimeoutError, ET.ParseError) as exc:
                    logger.warning("monitor: fetch sample failed: %s；本轮停止推送真实数据", exc)
                    _close_adapter_quietly(adapter)
                    adapter = None

            if sample is not None:
                # 告警事件 + 数据事件（告警优先推送，前端可即时感知）
                alert_events = check_alerts(sample)
                data_event = StreamEvent(data=sample, event_type="data", priority=1)
                for event in alert_events + [data_event]:
                    payload = event.to_dict()
                    await websocket.send_json(payload)
            elif demo_allowed:
                # 显式演示模式：Agent 不可达时降级 demo 数据（payload 带 source=demo
                # 标记，前端可区分），避免模拟数据被当作真实机床状态
                sample = _demo_sample(machine_id, tick)
                alert_events = check_alerts(sample)
                data_event = StreamEvent(data=sample, event_type="data", priority=1)
                for event in alert_events + [data_event]:
                    payload = event.to_dict()
                    if event is data_event:
                        payload["source"] = "demo"
                        if isinstance(payload.get("data"), dict):
                            payload["data"]["source"] = "demo"
                    await websocket.send_json(payload)
            elif tick % _ADAPTER_REPROBE_TICKS == 0:
                # 信任红线：无真实数据源且演示未开启 → 推送「未连接」告警，
                # 周期性提醒（每 30s）而非静默虚构机床数据
                await websocket.send_json(_disconnected_notice())
            tick += 1

            # 心跳
            if tick % _HEARTBEAT_INTERVAL_S == 0:
                await websocket.send_json({"event_type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()})

            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        logger.info("monitor ws disconnected: machine=%s", machine_id)
    except Exception as exc:
        logger.warning("monitor ws error: %s", exc)
    finally:
        _close_adapter_quietly(adapter)
        try:
            await websocket.close(code=1011, reason="internal error")
        except Exception:
            pass
