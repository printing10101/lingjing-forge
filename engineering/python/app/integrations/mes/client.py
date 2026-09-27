"""MES/ERP client implementation for manufacturing system integration.

This module provides an asynchronous HTTP client for communicating with
external MES/ERP systems. It handles work order synchronization, production
data reporting, material queries, and quality data upload.

The client uses httpx for async HTTP communication and includes comprehensive
error handling and logging.

Example::

    client = MESClient(base_url="https://mes.example.com", api_key="<your-api-key>")  # 请替换为实际 API Key
    result = await client.sync_work_order(work_order_data)
    if result.success:
        # Synced with ID: {result.data_id}
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Data Structures


@dataclass
class WorkOrderData:
    """工单数据，用于同步到 MES 系统。

    Attributes:
        work_order_no: 工单编号
        product_code: 产品编码
        quantity: 计划数量
        priority: 优先级 (1-10, 10 最高)
        planned_start: 计划开始时间
        planned_end: 计划结束时间
        customer_order_no: 客户订单号（可选）
        remarks: 备注信息（可选）
    """

    work_order_no: str
    product_code: str
    quantity: int
    priority: int = 5
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    customer_order_no: str | None = None
    remarks: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于 JSON 序列化。"""
        data = {
            "work_order_no": self.work_order_no,
            "product_code": self.product_code,
            "quantity": self.quantity,
            "priority": self.priority,
        }
        if self.planned_start:
            data["planned_start"] = self.planned_start.isoformat()
        if self.planned_end:
            data["planned_end"] = self.planned_end.isoformat()
        if self.customer_order_no:
            data["customer_order_no"] = self.customer_order_no
        if self.remarks:
            data["remarks"] = self.remarks
        return data


@dataclass
class SyncResult:
    """同步操作结果。

    Attributes:
        success: 是否成功
        message: 结果消息
        data_id: MES 系统中的数据 ID（如果有）
        error_code: 错误代码（如果失败）
        timestamp: 结果时间戳
    """

    success: bool
    message: str
    data_id: str | None = None
    error_code: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于 JSON 序列化。"""
        return {
            "success": self.success,
            "message": self.message,
            "data_id": self.data_id,
            "error_code": self.error_code,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MaterialInfo:
    """物料信息。

    Attributes:
        material_code: 物料编码
        name: 物料名称
        specification: 规格型号
        unit: 单位
        stock_quantity: 库存数量
        warehouse_location: 仓库位置
        batch_no: 批次号（可选）
        expiry_date: 有效期（可选）
    """

    material_code: str
    name: str
    specification: str
    unit: str
    stock_quantity: float
    warehouse_location: str
    batch_no: str | None = None
    expiry_date: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于 JSON 序列化。"""
        data = {
            "material_code": self.material_code,
            "name": self.name,
            "specification": self.specification,
            "unit": self.unit,
            "stock_quantity": self.stock_quantity,
            "warehouse_location": self.warehouse_location,
        }
        if self.batch_no:
            data["batch_no"] = self.batch_no
        if self.expiry_date:
            data["expiry_date"] = self.expiry_date.isoformat()
        return data


@dataclass
class QualityData:
    """质量数据，用于上报到 MES 系统。

    Attributes:
        batch_no: 批次号
        product_code: 产品编码
        inspection_type: 检验类型 (e.g., "incoming", "in_process", "final")
        result: 检验结果 ("pass", "fail", "conditional")
        inspector: 检验员
        inspection_time: 检验时间
        sample_size: 抽样数量
        qualified_qty: 合格数量
        defective_qty: 不合格数量
        defect_code: 缺陷代码（如果有）
        remarks: 备注（可选）
    """

    batch_no: str
    product_code: str
    inspection_type: str
    result: str
    inspector: str
    inspection_time: datetime
    sample_size: int
    qualified_qty: int
    defective_qty: int = 0
    defect_code: str | None = None
    remarks: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于 JSON 序列化。"""
        data = {
            "batch_no": self.batch_no,
            "product_code": self.product_code,
            "inspection_type": self.inspection_type,
            "result": self.result,
            "inspector": self.inspector,
            "inspection_time": self.inspection_time.isoformat(),
            "sample_size": self.sample_size,
            "qualified_qty": self.qualified_qty,
            "defective_qty": self.defective_qty,
        }
        if self.defect_code:
            data["defect_code"] = self.defect_code
        if self.remarks:
            data["remarks"] = self.remarks
        return data


# MES Client


class MESClient:
    """MES/ERP 系统的异步 HTTP 客户端。

    提供与 MES 系统通信的接口，支持工单同步、生产数据上报、物料查询
    和质量数据上传。所有方法都是异步的，使用 httpx 进行 HTTP 通信。

    Example::

        async with MESClient("https://mes.example.com", "api-key") as client:
            result = await client.sync_work_order(work_order)
            if result.success:
                logger.info("工单同步成功")

    Args:
        base_url: MES 系统的基础 URL
        api_key: API 认证密钥
        timeout: HTTP 请求超时时间（秒）
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
    ) -> None:
        """初始化 MES 客户端。

        Args:
            base_url: MES 系统的基础 URL，例如 "https://mes.example.com"
            api_key: API 认证密钥
            timeout: HTTP 请求超时时间（秒），默认 30 秒

        Raises:
            ValueError: 如果 base_url 为空或格式不正确
        """
        if not base_url or not base_url.strip():
            raise ValueError("base_url 不能为空")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

        # 配置 httpx 客户端
        self._client: httpx.AsyncClient | None = None
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        logger.info(
            "MES 客户端初始化完成: base_url=%s, timeout=%.1fs",
            self.base_url,
            self.timeout,
        )

    async def __aenter__(self) -> MESClient:
        """异步上下文管理器入口。"""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口。"""
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """确保 httpx 客户端已初始化。

        Returns:
            httpx.AsyncClient 实例
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=self._headers,
            )
            logger.debug("httpx 客户端已初始化")
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端连接。"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("httpx 客户端已关闭")

    async def sync_work_order(self, work_order: WorkOrderData) -> SyncResult:
        """同步工单到 MES 系统。

        将工单数据发送到 MES 系统进行生产排程和跟踪。
        包含指数退避重试机制，最多重试3次。

        Args:
            work_order: 工单数据对象

        Returns:
            SyncResult: 同步结果，包含成功状态和 MES 返回的数据 ID

        Raises:
            httpx.HTTPError: HTTP 请求失败
            ValueError: 工单数据无效
        """
        max_retries = 3
        base_delay = 1.0  # 秒

        for attempt in range(max_retries):
            try:
                client = await self._ensure_client()
                payload = work_order.to_dict()

                logger.info(
                    "同步工单 (尝试 %d/%d): work_order_no=%s, product=%s, qty=%d",
                    attempt + 1,
                    max_retries,
                    work_order.work_order_no,
                    work_order.product_code,
                    work_order.quantity,
                )

                response = await client.post("/api/work-orders", json=payload)
                response.raise_for_status()

                data = response.json()
                data_id = data.get("id") or data.get("work_order_id")

                logger.info("工单同步成功: data_id=%s", data_id)

                return SyncResult(
                    success=True,
                    message="工单同步成功",
                    data_id=str(data_id) if data_id else None,
                )

            except httpx.HTTPStatusError as e:
                # 4xx 错误不重试（客户端错误）
                if 400 <= e.response.status_code < 500:
                    logger.error(
                        "工单同步失败: HTTP %d - %s",
                        e.response.status_code,
                        e.response.text,
                    )
                    return SyncResult(
                        success=False,
                        message=f"HTTP 错误: {e.response.status_code}",
                        error_code=str(e.response.status_code),
                    )
                # 5xx 错误可重试
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "工单同步失败 (HTTP %d)，%0.1f秒后重试...",
                        e.response.status_code,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        "工单同步失败: HTTP %d - %s (已重试%d次)",
                        e.response.status_code,
                        e.response.text,
                        max_retries,
                    )
                    return SyncResult(
                        success=False,
                        message=f"HTTP 错误: {e.response.status_code}",
                        error_code=str(e.response.status_code),
                    )

            except httpx.RequestError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "工单同步请求失败，%0.1f秒后重试: %s",
                        delay,
                        str(e),
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error("工单同步请求失败 (已重试%d次): %s", max_retries, str(e))
                    return SyncResult(
                        success=False,
                        message="请求失败: 网络错误，请联系管理员",
                        error_code="REQUEST_ERROR",
                    )

            except Exception as e:
                logger.error("工单同步异常: %s", str(e), exc_info=True)
                return SyncResult(
                    success=False,
                    message="同步异常: 服务内部错误",
                    error_code="UNKNOWN_ERROR",
                )

        # 理论上不会到达这里
        return SyncResult(
            success=False,
            message="同步失败: 未知原因",
            error_code="UNKNOWN_ERROR",
        )

    async def report_production(
        self,
        batch_no: str,
        qty: int,
        qualified: int,
    ) -> SyncResult:
        """上报生产数据到 MES 系统。

        报告某个批次的生产数量和合格数量。

        Args:
            batch_no: 批次号
            qty: 生产总数量
            qualified: 合格数量

        Returns:
            SyncResult: 上报结果

        Raises:
            httpx.HTTPError: HTTP 请求失败
            ValueError: 参数无效
        """
        try:
            client = await self._ensure_client()
            payload = {
                "batch_no": batch_no,
                "quantity": qty,
                "qualified": qualified,
            }

            logger.info(
                "上报生产数据: batch_no=%s, qty=%d, qualified=%d",
                batch_no,
                qty,
                qualified,
            )

            response = await client.post("/api/production/reports", json=payload)
            response.raise_for_status()

            data = response.json()
            data_id = data.get("id") or data.get("report_id")

            logger.info("生产数据上报成功: data_id=%s", data_id)

            return SyncResult(
                success=True,
                message="生产数据上报成功",
                data_id=str(data_id) if data_id else None,
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "生产数据上报失败: HTTP %d - %s",
                e.response.status_code,
                e.response.text,
            )
            return SyncResult(
                success=False,
                message=f"HTTP 错误: {e.response.status_code}",
                error_code=str(e.response.status_code),
            )

        except httpx.RequestError as e:
            logger.error("生产数据上报请求失败: %s", str(e))
            return SyncResult(
                success=False,
                message="请求失败: 网络错误，请联系管理员",
                error_code="REQUEST_ERROR",
            )

        except Exception as e:
            logger.error("生产数据上报异常: %s", str(e), exc_info=True)
            return SyncResult(
                success=False,
                message="上报异常: 服务内部错误",
                error_code="UNKNOWN_ERROR",
            )

    async def query_material(self, material_code: str) -> MaterialInfo | None:
        """从 MES 系统查询物料信息。

        Args:
            material_code: 物料编码

        Returns:
            MaterialInfo: 物料信息，如果未找到则返回 None

        Raises:
            httpx.HTTPError: HTTP 请求失败
            ValueError: 物料编码无效
        """
        try:
            client = await self._ensure_client()

            logger.info("查询物料: material_code=%s", material_code)

            response = await client.get(f"/api/materials/{material_code}")

            if response.status_code == 404:
                logger.warning("物料未找到: material_code=%s", material_code)
                return None

            response.raise_for_status()

            data = response.json()

            # 解析有效期
            expiry_date = None
            if data.get("expiry_date"):
                try:
                    expiry_date = datetime.fromisoformat(data["expiry_date"])
                except (ValueError, TypeError):
                    logger.warning("无法解析有效期: %s", data.get("expiry_date"))

            material = MaterialInfo(
                material_code=data["material_code"],
                name=data["name"],
                specification=data.get("specification", ""),
                unit=data.get("unit", ""),
                stock_quantity=float(data.get("stock_quantity", 0)),
                warehouse_location=data.get("warehouse_location", ""),
                batch_no=data.get("batch_no"),
                expiry_date=expiry_date,
            )

            logger.info(
                "物料查询成功: code=%s, name=%s, stock=%.2f",
                material.material_code,
                material.name,
                material.stock_quantity,
            )

            return material

        except httpx.HTTPStatusError as e:
            logger.error(
                "物料查询失败: HTTP %d - %s",
                e.response.status_code,
                e.response.text,
            )
            return None

        except httpx.RequestError as e:
            logger.error("物料查询请求失败: %s", str(e))
            return None

        except Exception as e:
            logger.error("物料查询异常: %s", str(e), exc_info=True)
            return None

    async def report_quality(self, record: QualityData) -> SyncResult:
        """上报质量数据到 MES 系统。

        Args:
            record: 质量数据对象

        Returns:
            SyncResult: 上报结果

        Raises:
            httpx.HTTPError: HTTP 请求失败
            ValueError: 质量数据无效
        """
        try:
            client = await self._ensure_client()
            payload = record.to_dict()

            logger.info(
                "上报质量数据: batch_no=%s, result=%s, qualified=%d/%d",
                record.batch_no,
                record.result,
                record.qualified_qty,
                record.sample_size,
            )

            response = await client.post("/api/quality/records", json=payload)
            response.raise_for_status()

            data = response.json()
            data_id = data.get("id") or data.get("record_id")

            logger.info("质量数据上报成功: data_id=%s", data_id)

            return SyncResult(
                success=True,
                message="质量数据上报成功",
                data_id=str(data_id) if data_id else None,
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "质量数据上报失败: HTTP %d - %s",
                e.response.status_code,
                e.response.text,
            )
            return SyncResult(
                success=False,
                message=f"HTTP 错误: {e.response.status_code}",
                error_code=str(e.response.status_code),
            )

        except httpx.RequestError as e:
            logger.error("质量数据上报请求失败: %s", str(e))
            return SyncResult(
                success=False,
                message="请求失败: 网络错误，请联系管理员",
                error_code="REQUEST_ERROR",
            )

        except Exception as e:
            logger.error("质量数据上报异常: %s", str(e), exc_info=True)
            return SyncResult(
                success=False,
                message="上报异常: 服务内部错误",
                error_code="UNKNOWN_ERROR",
            )

    async def health_check(self) -> bool:
        """检查 MES 系统连接状态。

        Returns:
            bool: 如果 MES 系统可访问返回 True，否则返回 False
        """
        try:
            client = await self._ensure_client()

            logger.debug("执行 MES 健康检查")

            response = await client.get("/health")
            response.raise_for_status()

            logger.info("MES 系统健康检查通过")
            return True

        except httpx.HTTPStatusError as e:
            logger.warning(
                "MES 系统健康检查失败: HTTP %d",
                e.response.status_code,
            )
            return False

        except httpx.RequestError as e:
            logger.warning("MES 系统连接失败: %s", str(e))
            return False

        except Exception as e:
            logger.error("MES 系统健康检查异常: %s", str(e), exc_info=True)
            return False


__all__ = [
    "MESClient",
    "WorkOrderData",
    "SyncResult",
    "MaterialInfo",
    "QualityData",
]
