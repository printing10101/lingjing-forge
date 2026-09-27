"""
DNC 管理器

统一管理 OPC UA 和 MTConnect 连接，提供高层 API。

设计说明（BE-F4 整合）：
    本模块早期直接实例化 ``OPCUAClient`` / ``MTConnectClient``，
    与 ``unified_adapter.py`` 中的 ``UnifiedDNCAdapter`` 形成重复实现。
    现已重构为 ``UnifiedDNCAdapter`` 的薄包装层：

    * 连接逻辑（协议适配、故障切换）统一委托给 ``UnifiedDNCAdapter``
    * ``connections`` 字典保留为只读视图，维持对外 API 兼容
      （``app/api/v1/dnc.py`` 直接读取 ``connections[mid]["protocol"]``
      与 ``["client"]`` 字段用于 MTConnect alarms 查询）
    * 底层客户端通过 ``adapter.primary.client`` 暴露，避免重复实例化
"""

import logging
import threading
from typing import Any
from datetime import datetime, timezone

from .unified_adapter import UnifiedDNCAdapter, ProtocolType as _UnifiedProtocolType
from app.core.safe_errors import safe_error_message

# 协议类型枚举：复用 unified_adapter 的定义（消除重复枚举，保证 connect_single
# 参数类型一致）。保留本模块导出名供外部兼容导入（api/v1/dnc.py、测试）。
ProtocolType = _UnifiedProtocolType

logger = logging.getLogger(__name__)


class DNCManager:
    """DNC 机床通信管理器（UnifiedDNCAdapter 的多机床包装层）。"""

    def __init__(self):
        # 内部主存储：machine_id -> UnifiedDNCAdapter
        self._adapters: dict[str, UnifiedDNCAdapter] = {}
        # 元数据存储：machine_id -> {endpoint, protocol, connected_at}
        self._meta: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # 对外兼容视图：connections 字典
    @property
    def connections(self) -> dict[str, dict[str, Any]]:
        """只读兼容视图，返回 machine_id -> 连接元信息字典。

        每个条目包含：
            - client: 底层 OPCUAClient / MTConnectClient 实例
            - protocol: ProtocolType 枚举
            - endpoint: 连接端点
            - connected_at: ISO 8601 时间字符串
        """
        view: dict[str, dict[str, Any]] = {}
        with self._lock:
            for mid, adapter in self._adapters.items():
                meta = self._meta.get(mid, {})
                # 底层客户端从 adapter 的 primary 取出（保持外部 client API 兼容）
                client = adapter.primary.client if adapter.primary is not None else None
                view[mid] = {
                    "client": client,
                    "protocol": meta.get("protocol"),
                    "endpoint": meta.get("endpoint", ""),
                    "connected_at": meta.get("connected_at", ""),
                }
        return view

    # 连接管理
    async def add_machine(self, machine_id: str, protocol: ProtocolType, endpoint: str, **kwargs) -> bool:
        """添加机床连接（委托给 UnifiedDNCAdapter.connect_single）。"""
        try:
            if protocol not in (ProtocolType.OPC_UA, ProtocolType.MTCONNECT):
                logger.error("不支持的协议: %s", protocol)
                return False

            adapter = UnifiedDNCAdapter(machine_id=machine_id)
            credentials = {
                "username": kwargs.get("username"),
                "password": kwargs.get("password"),
            }
            # MTConnect 需要 device_name 参数
            if protocol == ProtocolType.MTCONNECT:
                device_name = kwargs.get("device_name", "Device")
                # MTConnectAdapter 在 connect_single 内部用 agent_url 构造客户端，
                # device_name 通过 kwargs.device_name 传递需走 connect_single 之外的路径。
                # 这里直接构造 MTConnectAdapter 以保留 device_name 参数。
                from .unified_adapter import MTConnectAdapter

                adapter.primary = MTConnectAdapter(
                    agent_url=endpoint,
                    device_name=device_name,
                    machine_id=machine_id,
                )
                ok = await adapter.primary.connect()
                if ok:
                    adapter._active = adapter.primary
            else:
                ok = await adapter.connect_single(
                    protocol=protocol,
                    endpoint=endpoint,
                    credentials=credentials,
                )

            if ok:
                with self._lock:
                    self._adapters[machine_id] = adapter
                    self._meta[machine_id] = {
                        "protocol": protocol,
                        "endpoint": endpoint,
                        "connected_at": datetime.now(timezone.utc).isoformat(),
                    }
                logger.info("机床 %s 已添加 (%s)", machine_id, protocol.value)
            return ok

        except Exception as e:
            logger.error("添加机床失败: %s", e)
            raise

    async def remove_machine(self, machine_id: str):
        """移除机床连接（委托给 UnifiedDNCAdapter.disconnect）。"""
        with self._lock:
            adapter = self._adapters.pop(machine_id, None)
            self._meta.pop(machine_id, None)

        if adapter is None:
            return

        try:
            await adapter.disconnect()
            logger.info("机床 %s 已移除", machine_id)
        except Exception as e:
            logger.error("断开机床 %s 连接失败: %s", machine_id, e)
            raise

    # 状态查询
    async def get_machine_status(self, machine_id: str) -> dict[str, Any]:
        """获取机床状态（返回 UnifiedMachineStatus.to_dict 或 error 字典）。"""
        with self._lock:
            adapter = self._adapters.get(machine_id)

        if adapter is None:
            return {"error": f"机床 {machine_id} 未连接"}

        try:
            status = await adapter.get_status()
            return status.to_dict()
        except Exception as e:
            # P1 信息泄露修复：避免向调用方暴露原始异常字符串（可能含路径/SQL/凭证）
            # 保留 error 字段字符串契约，附加 error_id 便于前端报障关联服务端日志
            logger.error("获取机床 %s 状态失败: %s", machine_id, e)
            err = safe_error_message(
                e,
                fallback=f"获取机床 {machine_id} 状态失败",
                context="dnc_manager.get_machine_status",
            )
            return {"error": err["message"], "error_id": err.get("error_id")}

    async def get_all_machines_status(self) -> dict[str, dict[str, Any]]:
        """获取所有机床状态。"""
        result = {}
        # 复制 keys 避免迭代时锁竞争
        with self._lock:
            machine_ids = list(self._adapters.keys())
        for machine_id in machine_ids:
            result[machine_id] = await self.get_machine_status(machine_id)
        return result

    async def send_nc_program(self, machine_id: str, program_path: str, program_name: str) -> bool:
        """发送 NC 程序到机床（委托给 UnifiedDNCAdapter.send_nc_program）。"""
        with self._lock:
            adapter = self._adapters.get(machine_id)

        if adapter is None:
            logger.error("机床 %s 未连接", machine_id)
            return False

        return await adapter.send_nc_program(program_path, program_name)

    def list_machines(self) -> list[dict[str, str]]:
        """列出所有已连接的机床。"""
        with self._lock:
            return [
                {
                    "machine_id": mid,
                    "protocol": meta["protocol"].value,
                    "endpoint": meta["endpoint"],
                    "connected_at": meta["connected_at"],
                }
                for mid, meta in self._meta.items()
            ]

    async def disconnect_all(self):
        """断开所有连接。"""
        with self._lock:
            machine_ids = list(self._adapters.keys())
        for machine_id in machine_ids:
            await self.remove_machine(machine_id)


# 全局单例
dnc_manager = DNCManager()
