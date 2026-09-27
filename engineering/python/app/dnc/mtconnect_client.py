"""
MTConnect 客户端实现

用于连接 MTConnect Agent，实现：
- 机床状态数据采集
- 主轴转速/进给速度监控
- 加工状态跟踪
- 报警信息获取

工业安全合规依据：
- IEC 62443-3-3 SR 7.2 网络可用性：通过指数退避重试与心跳探测保障通信可用性，
  所有重试参数通过构造函数注入，禁止硬编码。
- ISO 10218 安全联锁：NC 程序传输失败必须显式抛异常，禁止静默返回 False，
  避免调用方误判传输成功而启动机床加工导致人员伤害。
"""

import logging
import xml.etree.ElementTree as ET
from typing import Any
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from app.audit.audit_log import (
    AuditLog,
    AIModule,
    UserDecision,
    OperationStatus,
)
from app.core.safe_errors import safe_error_message
from app.utils.retry import retry_with_backoff
from app.utils.xml_guard import parse_untrusted_xml

logger = logging.getLogger(__name__)

# MTConnect HTTP 默认超时（秒）：与 opcua_client.py 的 DEFAULT_OPCUA_TIMEOUT_SEC 对齐
DEFAULT_MTCONNECT_HTTP_TIMEOUT_SEC: float = 10.0


class MTConnectClient:
    """
    MTConnect 客户端

    用于连接 MTConnect Agent 并采集机床数据。

    工业安全合规：
        - 所有网络操作支持指数退避重试（IEC 62443-3-3 SR 7.2 网络可用性）
        - NC 程序传输失败必须显式抛异常（ISO 10218 安全联锁）
        - 重试参数通过构造函数注入，禁止硬编码
    """

    def __init__(
        self,
        agent_url: str,
        device_name: str = "Device",
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
        retry_backoff_max: float = 60.0,
        failure_callback: Callable | None = None,
        audit_log: AuditLog | None = None,
    ):
        """
        初始化 MTConnect 客户端

        Args:
            agent_url: MTConnect Agent URL，如 "http://192.168.1.100:5000"
            device_name: 设备名称
            max_retries: 最大重试次数（默认 3）
            retry_backoff_base: 指数退避基数（秒，默认 1.0）
            retry_backoff_max: 最大退避时间（秒，默认 60.0）
            failure_callback: 失败告警回调，签名 callback(operation_name, error, attempt)
            audit_log: 审计日志记录器，为 None 时仅使用 logger
        """
        self.agent_url = agent_url.rstrip("/")
        self.device_name = device_name
        self.client: httpx.AsyncClient | None = None
        self.connected = False
        self.sequence = 0
        # 重试参数（IEC 62443-3-3 SR 7.2 网络可用性，禁止硬编码）
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_max = retry_backoff_max
        self.failure_callback = failure_callback
        self.audit_log = audit_log

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
            max_retries=self.max_retries,
            backoff_base=self.retry_backoff_base,
            backoff_max=self.retry_backoff_max,
            failure_callback=self.failure_callback,
        )

    def _log_audit(
        self,
        operation: str,
        status: OperationStatus,
        program_name: str | None = None,
        error: str | None = None,
    ) -> None:
        """记录审计日志（NC 程序传输尝试、失败、成功）"""
        if not self.audit_log:
            return
        try:
            self.audit_log.log_decision(
                ai_module=AIModule.PROCESS_OPTIMIZE,
                ai_recommendation={
                    "operation": operation,
                    "protocol": "mtconnect",
                    "agent_url": self.agent_url,
                    "program_name": program_name,
                },
                user_decision=UserDecision.AUTO_EXECUTED,
                final_execution={
                    "status": status.value,
                    "error": error,
                },
                operation_status=status,
                metadata={
                    "component": "dnc.mtconnect_client",
                    "operation": operation,
                },
            )
        except Exception as audit_err:
            logger.error("审计日志记录失败: %s", audit_err)

    async def connect(self) -> bool:
        """
        连接到 MTConnect Agent（带指数退避重试）

        Returns:
            连接成功返回 True

        Raises:
            RuntimeError: 达到最大重试次数后仍失败（不静默返回 False）
        """

        async def _do_connect():
            # 连接池复用：仅在 client 为空或已关闭时才新建 AsyncClient，避免每次重试重建连接池
            if self.client is not None and not getattr(self.client, "is_closed", False):
                # 已有可用 client，直接复用做 probe
                pass
            else:
                # 清理旧连接
                if self.client:
                    try:
                        await self.client.aclose()
                    except Exception as close_err:
                        # 工业连接重建期清理异常：记录 debug 日志便于排查资源泄漏，不阻塞重连
                        logger.debug("MTConnect 旧连接 aclose 失败（重连清理）: %s", close_err)
                    self.client = None
                self.client = httpx.AsyncClient(timeout=DEFAULT_MTCONNECT_HTTP_TIMEOUT_SEC)
            response = await self.client.get(f"{self.agent_url}/probe")
            if response.status_code != 200:
                raise RuntimeError(f"MTConnect 连接失败: HTTP {response.status_code}")
            self.connected = True
            logger.info("MTConnect 连接成功: %s", self.agent_url)
            return True

        try:
            return await self._retry_with_backoff(_do_connect, "MTConnect connect")
        except RuntimeError:
            # 清理资源
            self.connected = False
            if self.client:
                try:
                    await self.client.aclose()
                except Exception as close_err:
                    logger.debug("MTConnect client aclose failed during cleanup: %s", close_err)
                self.client = None
            raise

    async def disconnect(self):
        """断开连接"""
        if self.client:
            await self.client.aclose()
            self.connected = False
            logger.info("MTConnect 连接已断开")

    async def get_current_status(self) -> dict[str, Any]:
        """
        获取当前机床状态（网络异常时自动重试）

        Returns:
            包含机床状态信息的字典
        """
        if not self.connected:
            return {"connected": False, "timestamp": datetime.now(timezone.utc).isoformat()}

        async def _fetch_status():
            if not self.client:
                raise RuntimeError("MTConnect 客户端未初始化")
            response = await self.client.get(
                f"{self.agent_url}/current", params={"path": f"//Device[@name='{self.device_name}']"}
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            return self._parse_current_response(response.text)

        try:
            return await self._retry_with_backoff(_fetch_status, "MTConnect get_current_status")
        except RuntimeError as e:
            logger.error("获取 MTConnect 状态失败: %s", e)
            return safe_error_message(e, fallback="MTConnect 状态查询失败", context="mtconnect.get_current_status")

    def _parse_current_response(self, xml_content: str) -> dict[str, Any]:
        """解析 MTConnect Current 响应"""
        status = {
            "connected": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            root = parse_untrusted_xml(xml_content)
            namespaces = {"mt": "urn:mtconnect.org:MTConnect:2.0", "m": "urn:mtconnect.org:MTConnect:2.0"}

            # 提取关键数据项
            data_items = {
                "spindle_speed": ("SpindleSpeed", "ACTUAL"),
                "feed_rate": ("PathFeedrate", "ACTUAL"),
                "x_position": ("Xabs", "ACTUAL"),
                "y_position": ("Yabs", "ACTUAL"),
                "z_position": ("Zabs", "ACTUAL"),
                "execution_mode": ("Execution", None),
                "controller_mode": ("ControllerMode", None),
                "availability": ("Availability", None),
            }

            for key, (item_type, sub_type) in data_items.items():
                value = self._find_data_item(root, item_type, sub_type, namespaces)
                if value is not None:
                    status[key] = value

        except Exception as e:
            logger.error("解析 MTConnect 响应失败: %s", e)

        return status

    def _find_data_item(self, root: ET.Element, item_type: str, sub_type: str | None, namespaces: dict) -> Any | None:
        """查找特定类型的数据项"""
        # 简化实现，实际需要根据 MTConnect 标准解析
        for elem in root.iter():
            if item_type in elem.tag:
                if sub_type is None or elem.get("subType") == sub_type:
                    return elem.text
        return None

    async def get_alarms(self) -> list[dict[str, Any]]:
        """
        获取机床报警信息

        Returns:
            报警信息列表
        """
        if not self.connected:
            return []
        if not self.client:
            return []

        try:
            response = await self.client.get(
                f"{self.agent_url}/current", params={"path": f"//Device[@name='{self.device_name}']//Condition"}
            )

            if response.status_code != 200:
                return []

            return self._parse_alarms(response.text)

        except Exception as e:
            logger.error("获取报警信息失败: %s", e)
            return []

    def _parse_alarms(self, xml_content: str) -> list[dict[str, Any]]:
        """解析报警信息"""
        alarms = []
        try:
            root = parse_untrusted_xml(xml_content)
            for elem in root.iter():
                if "Fault" in elem.tag or "Warning" in elem.tag:
                    alarms.append(
                        {
                            "type": "Fault" if "Fault" in elem.tag else "Warning",
                            "code": elem.get("code", ""),
                            "message": elem.text or "",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
        except Exception as e:
            logger.error("解析报警信息失败: %s", e)

        return alarms

    async def send_nc_program(self, program_content: str, program_name: str) -> bool:
        """
        发送 NC 程序到机床（通过 MTConnect 命令）

        工业安全合规（ISO 10218 安全联锁）：
            MTConnect 标准不支持 NC 程序传输。禁止静默返回 False，
            否则调用方可能误判传输成功而启动机床加工，导致人员伤害或设备损坏。
            必须显式抛出 RuntimeError 让调用方感知失败。

        Args:
            program_content: NC 程序内容
            program_name: 程序名称

        Raises:
            RuntimeError: MTConnect 标准不支持 NC 程序传输
        """
        # 审计日志：记录传输尝试（最终为失败）
        self._log_audit(
            operation="send_nc_program",
            status=OperationStatus.FAILED,
            program_name=program_name,
            error="MTConnect standard does not support NC program transfer",
        )

        logger.error(
            "MTConnect 标准不支持 NC 程序传输: %s (agent=%s)",
            program_name,
            self.agent_url,
        )
        raise RuntimeError(
            "MTConnect standard does not support NC program transfer; use OPC UA or vendor-specific interface"
        )

    async def health_check(self) -> bool:
        """
        轻量级健康检查（探测 /probe 端点）

        用于验证 MTConnect Agent 是否在线可用，不触发重试机制，
        适合作为心跳/探活调用。

        Returns:
            连接可用返回 True，否则返回 False
        """
        try:
            if not self.client:
                return False
            response = await self.client.get(f"{self.agent_url}/probe")
            return response.status_code == 200
        except Exception as e:
            logger.debug("MTConnect 健康检查失败: %s", e)
            return False

    def is_connected(self) -> bool:
        """返回连接状态"""
        return self.connected
