"""
OPC UA 客户端实现

用于连接支持 OPC UA 协议的数控机床，实现：
- 机床状态实时监控
- NC 程序远程传输
- 加工参数下发
- 报警信息采集

工业安全合规依据：
- IEC 62443-3-3 SR 7.2 网络可用性：通过心跳探测（heartbeat_node_id）验证真实
  连接状态，配合指数退避重连保障通信可用性；所有重连参数通过构造函数注入，
  禁止硬编码。
- ISO 10218 安全联锁：NC 程序传输失败必须显式抛异常；program_name 必须严格
  转义（移除 / ; .. 等特殊字符），防止路径穿越导致程序写入非目标节点；
  节点 ID 通过可配置模板构造，禁止硬编码命名空间。
"""

import asyncio
import logging
from typing import Any
from collections.abc import Callable
from datetime import datetime, timezone

from app.utils.retry import retry_with_backoff

try:
    from asyncua import Client

    OPCUA_AVAILABLE = True
except ImportError:
    OPCUA_AVAILABLE = False
    logging.warning("OPC UA 依赖未安装，请运行: pip install asyncua")

logger = logging.getLogger(__name__)

# OPC UA 客户端的统一超时（秒）。用于 client.timeout 与 connect() wait_for 两个位置，
# 必须保持一致以避免出现"连接已超时但客户端仍认为可用"的状态不一致。
DEFAULT_OPCUA_TIMEOUT_SEC: float = 10.0


class OPCUASubscriptionHandler:
    """OPC UA 数据变更订阅处理器"""

    def __init__(self, callback: Callable | None = None):
        self.callback = callback

    def datachange_notification(self, node, val, data):
        """数据变更通知回调"""
        if self.callback:
            self.callback(node.nodeid.to_string(), val, datetime.now(timezone.utc))
        # P2-批次2 修复：改用 %s 懒求值，避免 debug 级别关闭时仍执行字符串插值。
        # OPC UA 订阅回调每秒触发数十~数百次，是性能热路径。
        logger.debug("OPC UA 数据变更: %s = %s", node.nodeid.to_string(), val)


class OPCUAClient:
    """
    OPC UA 客户端

    用于连接数控机床的 OPC UA 服务器，实现数据读写和订阅。

    工业安全合规：
        - 通过心跳节点验证真实连接（IEC 62443-3-3 SR 7.2 网络可用性）
        - NC 程序节点 ID 严格转义（ISO 10218 安全联锁，防路径穿越）
        - 重连参数通过构造函数注入，禁止硬编码
    """

    def __init__(
        self,
        endpoint: str,
        username: str | None = None,
        password: str | None = None,
        max_reconnect_attempts: int = 5,
        reconnect_backoff_base: float = 1.0,
        reconnect_backoff_max: float = 60.0,
        node_namespace: int = 2,
        nc_program_node_template: str = "NCProgram/{program_name}",
        heartbeat_node_id: str | None = None,
        failure_callback: Callable | None = None,
    ):
        """
        初始化 OPC UA 客户端

        Args:
            endpoint: OPC UA 服务器端点，如 "opc.tcp://192.168.1.100:4840"
            username: 认证用户名（可选）
            password: 认证密码（可选）
            max_reconnect_attempts: 最大重连尝试次数（默认 5）
            reconnect_backoff_base: 重连指数退避基数（秒，默认 1.0）
            reconnect_backoff_max: 最大重连退避时间（秒，默认 60.0）
            node_namespace: NC 程序节点命名空间（默认 ns=2）
            nc_program_node_template: NC 程序节点模板，含 {program_name} 占位符
            heartbeat_node_id: 心跳探测节点 ID，用于真实连接检测（如 "ns=0;i=2258"）
            failure_callback: 失败告警回调，签名 callback(operation_name, error, attempt)
        """
        if not OPCUA_AVAILABLE:
            raise RuntimeError("OPC UA 依赖未安装，请运行: pip install asyncua")

        self.endpoint = endpoint
        self.username = username
        self.password = password
        self.client: Client | None = None
        self.subscription = None
        self.handler: OPCUASubscriptionHandler | None = None
        self.connected = False
        # 重连参数（IEC 62443-3-3 SR 7.2 网络可用性，禁止硬编码）
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_backoff_base = reconnect_backoff_base
        self.reconnect_backoff_max = reconnect_backoff_max
        # 节点配置（ISO 10218 安全联锁，防路径穿越）
        self.node_namespace = node_namespace
        self.nc_program_node_template = nc_program_node_template
        self.heartbeat_node_id = heartbeat_node_id
        self.failure_callback = failure_callback

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.disconnect()

    async def _retry_with_backoff(
        self,
        operation: Callable,
        operation_name: str,
    ) -> Any:
        """通用指数退避重试（委托给 ``app.utils.retry.retry_with_backoff``）。

        保留方法签名以兼容现有外部调用与子类 override。具体算法与退避策略
        见 ``app/utils/retry.py``。
        """
        return await retry_with_backoff(
            operation,
            operation_name,
            max_retries=self.max_reconnect_attempts,
            backoff_base=self.reconnect_backoff_base,
            backoff_max=self.reconnect_backoff_max,
            failure_callback=self.failure_callback,
        )

    async def connect(self, timeout: float = 10.0) -> bool:
        """
        连接到 OPC UA 服务器

        Args:
            timeout: 连接超时时间（秒），默认 10 秒

        Returns:
            连接成功返回 True

        Raises:
            RuntimeError: 连接失败（不静默返回 False）
        """

        async def _do_connect():
            # 清理旧 client
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception as close_err:
                    # 工业连接重建期清理异常：记录 debug 日志便于排查资源泄漏，不阻塞重连
                    logger.debug("OPC UA 旧连接 disconnect 失败（重连清理）: %s", close_err)
                self.client = None
            self.client = Client(url=self.endpoint)
            self.client.timeout = timeout
            if self.username and self.password:
                self.client.set_user(self.username)
                self.client.set_password(self.password)
            await asyncio.wait_for(self.client.connect(), timeout=timeout)
            self.connected = True
            logger.info("OPC UA 连接成功: %s", self.endpoint)
            return True

        return await self._retry_with_backoff(_do_connect, "OPC UA connect")

    async def _reconnect_if_needed(self) -> bool:
        """
        检查并维护连接（基于心跳探测，非布尔标志）

        工业安全合规（IEC 62443-3-3 SR 7.2 网络可用性）：
            - 如果 heartbeat_node_id 配置了，读取该节点验证真实连接状态，
              而非仅检查布尔标志 self.connected
            - 重连时先清理旧 client：if self.client: try: await self.client.disconnect() except: pass
            - 使用指数退避重试（与 MTConnect 模式一致）
            - 达到最大重试次数后抛出 RuntimeError

        Returns:
            连接可用返回 True

        Raises:
            RuntimeError: 重连达到最大次数仍失败
        """
        # 1. 如果配置了心跳节点，验证真实连接
        if self.heartbeat_node_id and self.client and self.connected:
            try:
                node = self.client.get_node(self.heartbeat_node_id)
                await node.read_value()
                return True
            except Exception as e:
                logger.warning("OPC UA 心跳探测失败，连接可能已断开: %s", e)
                self.connected = False
        elif self.connected and self.client:
            # 未配置心跳节点，回退到布尔标志检查
            return True

        # 2. 需要重连：先清理旧 client，避免资源泄漏
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as close_err:
                # 重连清理异常：记录 debug 日志便于排查，不阻塞重连流程
                logger.debug("OPC UA 重连清理旧连接失败: %s", close_err)
            self.client = None
        self.connected = False

        logger.warning("OPC UA 连接已断开，尝试重新连接...")

        async def _do_reconnect():
            self.client = Client(url=self.endpoint)
            self.client.timeout = DEFAULT_OPCUA_TIMEOUT_SEC
            if self.username and self.password:
                self.client.set_user(self.username)
                self.client.set_password(self.password)
            await asyncio.wait_for(self.client.connect(), timeout=DEFAULT_OPCUA_TIMEOUT_SEC)
            self.connected = True
            logger.info("OPC UA 重连成功: %s", self.endpoint)
            return True

        return await self._retry_with_backoff(_do_reconnect, "OPC UA reconnect")

    async def disconnect(self):
        """断开 OPC UA 连接"""
        if self.client:
            await self.client.disconnect()
            self.connected = False
            logger.info("OPC UA 连接已断开")

    async def read_node(self, node_id: str) -> Any:
        """
        读取节点值

        Args:
            node_id: OPC UA 节点 ID，如 "ns=2;s=SpindleSpeed"

        Returns:
            节点当前值

        Raises:
            RuntimeError: 未连接且无法重连
        """
        if not await self._reconnect_if_needed():
            raise RuntimeError("OPC UA 未连接且无法重新连接")

        # _reconnect_if_needed 成功后 client 必然非 None（不变量断言）
        assert self.client is not None
        node = self.client.get_node(node_id)
        value = await node.read_value()
        return value

    async def write_node(self, node_id: str, value: Any):
        """
        写入节点值

        Args:
            node_id: OPC UA 节点 ID
            value: 要写入的值

        Raises:
            RuntimeError: 未连接且无法重连
        """
        if not await self._reconnect_if_needed():
            raise RuntimeError("OPC UA 未连接且无法重新连接")

        assert self.client is not None
        node = self.client.get_node(node_id)
        await node.write_value(value)
        logger.info("OPC UA 写入: %s = %s", node_id, value)

    async def subscribe(self, node_ids: list[str], callback: Callable):
        """
        订阅节点数据变更（订阅失败时自动重试）

        Args:
            node_ids: 要订阅的节点 ID 列表
            callback: 数据变更回调函数，签名为 callback(node_id, value, timestamp)

        Raises:
            RuntimeError: 未连接且无法重连，或订阅失败达到最大重试次数
        """
        if not await self._reconnect_if_needed():
            raise RuntimeError("OPC UA 未连接且无法重新连接")

        async def _do_subscribe():
            # 清理旧订阅
            if self.subscription:
                try:
                    await self.subscription.delete()
                except Exception as del_err:
                    # 旧订阅清理异常：记录 debug 日志便于排查，不阻塞新订阅创建
                    logger.debug("OPC UA 旧订阅 delete 失败（重建清理）: %s", del_err)
                self.subscription = None
            assert self.client is not None  # subscribe 前已保证连接
            self.handler = OPCUASubscriptionHandler(callback)
            subscription = await self.client.create_subscription(500, self.handler)
            self.subscription = subscription
            nodes = [self.client.get_node(nid) for nid in node_ids]
            await subscription.subscribe_data_change(nodes)
            logger.info("OPC UA 已订阅 %s 个节点", len(node_ids))
            return True

        await self._retry_with_backoff(_do_subscribe, "OPC UA subscribe")

    async def unsubscribe(self):
        """取消订阅"""
        if self.subscription:
            await self.subscription.delete()
            self.subscription = None
            logger.info("OPC UA 订阅已取消")

    async def get_machine_status(self) -> dict[str, Any]:
        """
        获取机床状态（标准 OPC UA 机床信息模型）

        Returns:
            包含机床状态信息的字典
        """
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connected": self.connected,
        }

        if not self.connected:
            return status

        try:
            # 读取标准机床状态节点（根据实际机床 OPC UA 信息模型调整）
            status.update(
                {
                    "spindle_speed": await self.safe_read("ns=2;s=SpindleSpeed"),
                    "feed_rate": await self.safe_read("ns=2;s=FeedRate"),
                    "x_position": await self.safe_read("ns=2;s=PosX"),
                    "y_position": await self.safe_read("ns=2;s=PosY"),
                    "z_position": await self.safe_read("ns=2;s=PosZ"),
                    "machine_mode": await self.safe_read("ns=2;s=MachineMode"),
                    "alarm_active": await self.safe_read("ns=2;s=AlarmActive"),
                }
            )
        except Exception as e:
            logger.error("获取机床状态失败: %s", e)

        return status

    async def safe_read(self, node_id: str) -> Any:
        """安全读取节点，失败时返回 None"""
        try:
            return await self.read_node(node_id)
        except Exception as e:
            logger.warning("Failed to read OPC UA node %s: %s", node_id, e)
            return None

    def _sanitize_program_name(self, program_name: str) -> str:
        """
        转义 program_name，防止路径穿越（ISO 10218 安全联锁）

        移除 / \\ ; .. 等特殊字符，避免构造恶意节点 ID 写入非目标节点。
        同时移除控制字符（\\x00 \\n \\r）。

        Args:
            program_name: 原始程序名

        Returns:
            转义后的安全程序名

        Raises:
            ValueError: 转义后为空字符串
        """
        sanitized = program_name
        # 移除路径分隔符
        sanitized = sanitized.replace("/", "").replace("\\", "")
        # 移除 OPC UA 节点 ID 分隔符
        sanitized = sanitized.replace(";", "")
        # 移除点号序列（防止 .. 路径穿越）
        sanitized = sanitized.replace("..", "")
        # 移除控制字符
        sanitized = sanitized.replace("\x00", "").replace("\n", "").replace("\r", "")
        if not sanitized:
            raise ValueError(f"program_name 转义后为空，可能包含恶意字符: {program_name!r}")
        return sanitized

    async def send_nc_program(self, program_path: str, program_name: str) -> bool:
        """
        发送 NC 程序到机床（通过 OPC UA 文件传输）

        工业安全合规（ISO 10218 安全联锁）：
            - program_name 严格转义，防止路径穿越导致写入非目标节点
            - 节点 ID 通过可配置命名空间和模板构造，不硬编码 ns=2
            - 传输失败显式抛异常，不静默返回 False

        Args:
            program_path: 本地 NC 程序文件路径
            program_name: 机床端存储的程序名

        Returns:
            发送成功返回 True

        Raises:
            ValueError: program_name 包含非法字符
            RuntimeError: 传输失败
        """
        # 转义 program_name，防止路径穿越（ISO 10218 安全联锁）
        sanitized_name = self._sanitize_program_name(program_name)

        # 构造节点 ID（使用可配置命名空间和模板，不硬编码）
        node_id = f"ns={self.node_namespace};s={self.nc_program_node_template.format(program_name=sanitized_name)}"

        if not await self._reconnect_if_needed():
            raise RuntimeError("OPC UA 未连接且无法重新连接")

        try:
            # 读取本地文件
            with open(program_path, "r", encoding="utf-8") as f:
                program_content = f.read()

            # 写入机床 NC 程序存储节点
            assert self.client is not None  # _reconnect_if_needed 成功后必然已连接
            program_node = self.client.get_node(node_id)
            await program_node.write_value(program_content)

            logger.info("NC 程序已发送: %s -> %s", program_name, node_id)
            return True

        except Exception as e:
            logger.error("发送 NC 程序失败: %s (node_id=%s)", e, node_id)
            raise RuntimeError(f"发送 NC 程序失败: {e}") from e

    async def health_check(self) -> bool:
        """
        健康检查（通过读取 heartbeat_node_id 验证真实连接）

        用于验证 OPC UA 连接是否真正可用，不触发重连机制，
        适合作为心跳/探活调用。

        Returns:
            连接可用返回 True，否则返回 False
        """
        if not self.heartbeat_node_id:
            # 未配置心跳节点，回退到布尔标志检查
            return self.connected
        try:
            if not self.client or not self.connected:
                return False
            node = self.client.get_node(self.heartbeat_node_id)
            await node.read_value()
            return True
        except Exception as e:
            logger.debug("OPC UA 健康检查失败: %s", e)
            return False

    def is_connected(self) -> bool:
        """返回连接状态"""
        return self.connected
