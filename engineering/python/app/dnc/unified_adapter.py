"""DNC 双协议统一适配器。

落地竞品分析中 MachineMetrics Universal Connectivity 思路：
1. 定义统一 ``MachineAdapter`` 抽象接口，屏蔽 MTConnect/OPC UA 差异
2. ``UnifiedDNCAdapter`` 支持协议自动探测：先试 MTConnect /probe，失败再试 OPC UA
3. 故障切换：主协议断开时自动尝试备用协议
4. 统一 ``UnifiedMachineStatus`` schema：所有协议返回一致字段
5. 统一订阅接口：``subscribe_status(callback, interval)`` 屏蔽底层差异
6. 资产发现：扫描子网内 MTConnect Agent (5000) 与 OPC UA (4840) 端口

设计目标：
    上层业务代码（切削力监控、颤振预警、刀具磨损反馈）只依赖
    ``UnifiedMachineStatus``，不需要关心机床用的是哪种协议。
"""

from __future__ import annotations

import abc
import asyncio
import logging
import socket
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from collections.abc import Callable

from .mtconnect_client import MTConnectClient
from .opcu_client import OPCUAClient

logger = logging.getLogger(__name__)


class ProtocolType(str, Enum):
    OPC_UA = "opcua"
    MTCONNECT = "mtconnect"
    AUTO = "auto"  # 自动探测


@dataclass
class UnifiedMachineStatus:
    """统一机床状态 schema（屏蔽协议差异）。

    所有字段均为 Optional，因为不同协议能采集到的字段不完全一致。
    缺失字段统一返回 None，上层代码通过 ``status.is_available`` 判断有效性。
    """

    machine_id: str
    protocol: str  # "mtconnect" / "opcua"
    timestamp: str  # ISO 8601
    connected: bool = False
    # 加工状态
    spindle_speed_rpm: float | None = None
    feed_rate_mm_per_min: float | None = None
    # 位置（mm）
    x_position: float | None = None
    y_position: float | None = None
    z_position: float | None = None
    # 运行状态
    execution_mode: str | None = None  # ACTIVE / FEED_HOLD / STOPPED
    controller_mode: str | None = None  # AUTOMATIC / MANUAL / MDI
    machine_mode: str | None = None
    alarm_active: bool | None = None
    availability: str | None = None  # AVAILABLE / UNAVAILABLE
    # 协议特有原始数据
    raw: dict = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        """机床是否处于可用且加工中状态。"""
        return (
            self.connected
            and self.availability in (None, "AVAILABLE", True)
            and self.execution_mode in (None, "ACTIVE", "READY")
        )

    def to_dict(self) -> dict:
        return asdict(self)


class MachineAdapter(abc.ABC):
    """机床协议适配器抽象基类。"""

    # 底层协议客户端（MTConnectAdapter MTConnectClient，OPCUAAdapter OPCUAClient）。
    # 声明为 Any：各具体子类类型不同，统一在 ABC 层暴露只读访问。
    client: Any

    @abc.abstractmethod
    async def connect(self, timeout: float = 10.0) -> bool: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    async def get_status(self) -> UnifiedMachineStatus: ...

    @abc.abstractmethod
    async def send_nc_program(self, program_path: str, program_name: str) -> bool: ...

    @abc.abstractmethod
    def is_connected(self) -> bool: ...


class MTConnectAdapter(MachineAdapter):
    """MTConnect 协议适配器。"""

    def __init__(self, agent_url: str, device_name: str = "Device", machine_id: str = ""):
        self.machine_id = machine_id or device_name
        self.client = MTConnectClient(agent_url=agent_url, device_name=device_name)

    async def connect(self, timeout: float = 10.0) -> bool:
        return await self.client.connect()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def get_status(self) -> UnifiedMachineStatus:
        raw = await self.client.get_current_status()
        return UnifiedMachineStatus(
            machine_id=self.machine_id,
            protocol="mtconnect",
            timestamp=raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
            connected=raw.get("connected", False),
            spindle_speed_rpm=_safe_float(raw.get("spindle_speed")),
            feed_rate_mm_per_min=_safe_float(raw.get("feed_rate")),
            x_position=_safe_float(raw.get("x_position")),
            y_position=_safe_float(raw.get("y_position")),
            z_position=_safe_float(raw.get("z_position")),
            execution_mode=raw.get("execution_mode"),
            controller_mode=raw.get("controller_mode"),
            availability=raw.get("availability"),
            raw=raw,
        )

    async def send_nc_program(self, program_path: str, program_name: str) -> bool:
        # MTConnect 标准不支持 NC 传输
        logger.warning("MTConnect 协议不支持 NC 程序传输，请配置 OPC UA 通道或厂商扩展")
        return False

    def is_connected(self) -> bool:
        return self.client.is_connected()


class OPCUAAdapter(MachineAdapter):
    """OPC UA 协议适配器。"""

    def __init__(
        self,
        endpoint: str,
        username: str | None = None,
        password: str | None = None,
        machine_id: str = "",
    ):
        self.machine_id = machine_id or endpoint
        self.client = OPCUAClient(endpoint=endpoint, username=username, password=password)

    async def connect(self, timeout: float = 10.0) -> bool:
        return await self.client.connect(timeout=timeout)

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def get_status(self) -> UnifiedMachineStatus:
        raw = await self.client.get_machine_status()
        return UnifiedMachineStatus(
            machine_id=self.machine_id,
            protocol="opcua",
            timestamp=raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
            connected=raw.get("connected", False),
            spindle_speed_rpm=_safe_float(raw.get("spindle_speed")),
            feed_rate_mm_per_min=_safe_float(raw.get("feed_rate")),
            x_position=_safe_float(raw.get("x_position")),
            y_position=_safe_float(raw.get("y_position")),
            z_position=_safe_float(raw.get("z_position")),
            machine_mode=raw.get("machine_mode"),
            alarm_active=raw.get("alarm_active"),
            raw=raw,
        )

    async def send_nc_program(self, program_path: str, program_name: str) -> bool:
        return await self.client.send_nc_program(program_path, program_name)

    def is_connected(self) -> bool:
        return self.client.is_connected()


class UnifiedDNCAdapter:
    """DNC 双协议统一适配器（带自动探测与故障切换）。

    使用方式：
        adapter = UnifiedDNCAdapter(machine_id="vmc_01")
        await adapter.connect_auto(
            endpoints=["http://192.168.1.100:5000", "opc.tcp://192.168.1.100:4840"]
        )
        status = await adapter.get_status()
        await adapter.subscribe_status(callback, interval=1.0)
    """

    def __init__(self, machine_id: str):
        self.machine_id = machine_id
        self.primary: MachineAdapter | None = None
        self.fallback: MachineAdapter | None = None
        self._active: MachineAdapter | None = None
        self._subscription_task: asyncio.Task | None = None
        self._failover_count = 0

    async def connect_auto(
        self,
        endpoints: list[str],
        credentials: dict | None = None,
        timeout: float = 5.0,
    ) -> dict:
        """自动探测并连接可用协议。

        Args:
            endpoints: 候选端点列表，按优先级排序。
                       http:// → MTConnect, opc.tcp:// → OPC UA
            credentials: OPC UA 凭据 {"username": ..., "password": ...}
            timeout: 单个端点连接超时

        Returns:
            连接结果摘要 {"primary_protocol": ..., "fallback_protocol": ...}
        """
        credentials = credentials or {}
        candidates: list[MachineAdapter] = []
        for ep in endpoints:
            ep_lower = ep.lower()
            if ep_lower.startswith("http://") or ep_lower.startswith("https://"):
                candidates.append(MTConnectAdapter(agent_url=ep, machine_id=self.machine_id))
            elif ep_lower.startswith("opc.tcp://"):
                candidates.append(
                    OPCUAAdapter(
                        endpoint=ep,
                        username=credentials.get("username"),
                        password=credentials.get("password"),
                        machine_id=self.machine_id,
                    )
                )

        # 尝试连接候选，第一个成功的作为 primary，第二个作为 fallback
        connected_adapters: list[MachineAdapter] = []
        for adapter in candidates:
            try:
                ok = await adapter.connect(timeout=timeout)
                if ok:
                    connected_adapters.append(adapter)
                    if len(connected_adapters) >= 2:
                        break
            except Exception as e:
                logger.warning(
                    "连接 %s 失败: %s",
                    adapter.__class__.__name__,
                    e,
                )

        if not connected_adapters:
            return {
                "primary_protocol": None,
                "fallback_protocol": None,
                "error": "所有候选端点均无法连接",
            }

        self.primary = connected_adapters[0]
        self._active = self.primary
        if len(connected_adapters) > 1:
            self.fallback = connected_adapters[1]

        return {
            "primary_protocol": self._protocol_name(self.primary),
            "fallback_protocol": (self._protocol_name(self.fallback) if self.fallback else None),
        }

    async def connect_single(
        self,
        protocol: ProtocolType,
        endpoint: str,
        credentials: dict | None = None,
        timeout: float = 10.0,
    ) -> bool:
        """单协议连接（兼容旧 DNCManager 用法）。"""
        credentials = credentials or {}
        if protocol == ProtocolType.MTCONNECT:
            self.primary = MTConnectAdapter(agent_url=endpoint, machine_id=self.machine_id)
        elif protocol == ProtocolType.OPC_UA:
            self.primary = OPCUAAdapter(
                endpoint=endpoint,
                username=credentials.get("username"),
                password=credentials.get("password"),
                machine_id=self.machine_id,
            )
        else:
            # connect_auto 返回 dict 摘要；成功标准是 primary 已建立
            await self.connect_auto([endpoint], credentials, timeout)
            return self.primary is not None

        ok = await self.primary.connect(timeout=timeout)
        if ok:
            self._active = self.primary
            return True
        return False

    async def get_status(self) -> UnifiedMachineStatus:
        """获取机床状态（带故障切换）。"""
        if self._active is None:
            return UnifiedMachineStatus(
                machine_id=self.machine_id,
                protocol="none",
                timestamp=datetime.now(timezone.utc).isoformat(),
                connected=False,
            )

        try:
            return await self._active.get_status()
        except Exception as e:
            logger.warning(
                "主协议 %s 获取状态失败: %s，尝试故障切换",
                self._protocol_name(self._active),
                e,
            )
            if await self._failover():
                return await self._active.get_status()
            # 故障切换失败，返回未连接状态
            return UnifiedMachineStatus(
                machine_id=self.machine_id,
                protocol=self._protocol_name(self._active),
                timestamp=datetime.now(timezone.utc).isoformat(),
                connected=False,
            )

    async def send_nc_program(self, program_path: str, program_name: str) -> bool:
        """发送 NC 程序（自动选择支持该操作的协议）。"""
        # 优先使用 OPC UA（MTConnect 标准不支持 NC 传输）
        if isinstance(self._active, OPCUAAdapter):
            return await self._active.send_nc_program(program_path, program_name)
        if self.fallback and isinstance(self.fallback, OPCUAAdapter):
            if not self.fallback.is_connected():
                await self.fallback.connect()
            return await self.fallback.send_nc_program(program_path, program_name)
        if self._active is not None:
            return await self._active.send_nc_program(program_path, program_name)
        return False

    async def subscribe_status(
        self,
        callback: Callable[[UnifiedMachineStatus], None],
        interval: float = 1.0,
    ) -> None:
        """订阅机床状态（轮询封装）。

        Args:
            callback: 状态回调函数
            interval: 轮询间隔（秒）
        """
        if self._subscription_task is not None:
            await self.unsubscribe_status()

        async def _poll():
            while True:
                try:
                    status = await self.get_status()
                    callback(status)
                except Exception as e:
                    logger.exception("状态订阅轮询失败: %s", e)
                await asyncio.sleep(interval)

        self._subscription_task = asyncio.create_task(_poll())

    async def unsubscribe_status(self) -> None:
        if self._subscription_task is not None:
            self._subscription_task.cancel()
            try:
                await self._subscription_task
            except asyncio.CancelledError:
                # 取消订阅任务时 CancelledError 是预期行为，无需处理
                pass
            self._subscription_task = None

    async def _failover(self) -> bool:
        """故障切换到备用协议。"""
        if self.fallback is None:
            return False
        try:
            if not self.fallback.is_connected():
                ok = await self.fallback.connect()
                if not ok:
                    return False
            self._active = self.fallback
            self._failover_count += 1
            logger.warning(
                "已故障切换到 %s（累计切换 %d 次）",
                self._protocol_name(self.fallback),
                self._failover_count,
            )
            return True
        except Exception as e:
            logger.error("故障切换失败: %s", e)
            return False

    async def disconnect(self) -> None:
        await self.unsubscribe_status()
        if self.primary is not None:
            try:
                await self.primary.disconnect()
            except Exception as e:
                logger.warning("断开 primary 失败: %s", e)
        if self.fallback is not None:
            try:
                await self.fallback.disconnect()
            except Exception as e:
                logger.warning("断开 fallback 失败: %s", e)

    def is_connected(self) -> bool:
        return self._active is not None and self._active.is_connected()

    @property
    def active_protocol(self) -> str | None:
        return self._protocol_name(self._active) if self._active else None

    @property
    def failover_count(self) -> int:
        return self._failover_count

    @staticmethod
    def _protocol_name(adapter: MachineAdapter | None) -> str:
        if isinstance(adapter, MTConnectAdapter):
            return "mtconnect"
        if isinstance(adapter, OPCUAAdapter):
            return "opcua"
        return "none"


# 资产发现：扫描局域网内 MTConnect/OPC UA 服务


async def discover_machines(
    subnet: str = "192.168.1",
    timeout: float = 0.3,
    ports: list[int] | None = None,
) -> list[dict]:
    """扫描子网内 MTConnect (5000) 与 OPC UA (4840) 端口。

    Args:
        subnet: 子网前缀，如 "192.168.1"
        timeout: 单端口扫描超时
        ports: 自定义端口列表，默认 [5000, 4840]

    Returns:
        发现的机床列表 [{"ip": ..., "port": ..., "protocol": ...}]
    """
    if ports is None:
        ports = [5000, 4840]

    port_protocol = {5000: "mtconnect", 4840: "opcua"}
    discovered: list[dict] = []

    async def _check(ip: str, port: int):
        try:
            # 使用 get_running_loop 替代 get_event_loop，避免在已有事件循环中
            # 触发 DeprecationWarning，并确保协程运行在正确的循环上。
            future = asyncio.get_running_loop().run_in_executor(None, _socket_check, ip, port, timeout)
            if await future:
                discovered.append(
                    {
                        "ip": ip,
                        "port": port,
                        "protocol": port_protocol.get(port, "unknown"),
                        "endpoint": (f"http://{ip}:{port}" if port == 5000 else f"opc.tcp://{ip}:{port}"),
                    }
                )
        except Exception as e:
            # 网络扫描中单点失败不阻断整体发现，但保留诊断能力以便排障。
            # 使用 debug 级别避免在 254×N 次扫描中产生噪声。
            logger.debug("DNC device probe failed for %s:%s — %s", ip, port, e)

    tasks = []
    for host in range(1, 255):
        ip = f"{subnet}.{host}"
        for port in ports:
            tasks.append(_check(ip, port))

    # 限制并发避免 fd 耗尽
    semaphore = asyncio.Semaphore(100)

    async def _bounded(t):
        async with semaphore:
            await t

    # [A-H14] 添加 return_exceptions=True，避免单个探测任务抛异常
    # 中断整个 gather（_check 内部已有 try/except，此处为防御性兜底）
    results = await asyncio.gather(*[_bounded(t) for t in tasks], return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.debug("DNC discovery task failed: %s", r)
    return discovered


def _socket_check(ip: str, port: int, timeout: float) -> bool:
    """同步 socket 连通性检查（在线程池中执行）。"""
    # [H10] 使用 with 上下文管理器确保异常路径下 fd 也被释放，
    # 避免 connect_ex 抛异常时 sock.close() 被跳过导致 fd 泄漏。
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            return result == 0
    except Exception as e:
        # 连接被拒/超时/主机不可达等不同原因全部归并为 False，
        # 保留 debug 日志便于区分"无服务"与"扫描出错"。
        logger.debug("Socket check failed for %s:%s — %s", ip, port, e)
        return False


# 辅助函数


def _safe_float(value: Any) -> float | None:
    """安全转换为 float，失败返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
