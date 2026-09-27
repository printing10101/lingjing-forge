"""FastAPI router for DNC machine communication endpoints.

This module provides REST API endpoints for managing CNC machine connections,
querying real-time status, transferring NC programs, and retrieving alarm
information. It supports both OPC UA and MTConnect protocols.

Example::

    from fastapi import FastAPI
    from app.api.v1.dnc import router

    app = FastAPI()
    app.include_router(router)
"""

import asyncio
import logging

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

from app.dnc.dnc_manager import dnc_manager, ProtocolType
from app.dnc.nc_gate import get_dispatch_block_reason
from app.dnc.unified_adapter import (
    UnifiedDNCAdapter,
    discover_machines,
)
from app.core.response import success, error, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/dnc",
    tags=["DNC 机床通信"],
    dependencies=[Depends(require_permission("dnc:read"))],
    responses={
        500: {"description": "Internal server error"},
    },
)


# ── 请求/响应模型 ──────────────────────────────────────────────


class MachineConnectRequest(BaseModel):
    """机床连接请求模型。

    Attributes:
        machine_id: 机床唯一标识
        protocol: 通信协议（opcua / mtconnect）
        endpoint: 连接端点
        username: 认证用户名（可选）
        password: 认证密码（可选）
        device_name: MTConnect 设备名称（可选，默认 Device）
    """

    machine_id: str = Field(..., description="机床唯一标识", examples=["CNC-001"])
    protocol: ProtocolType = Field(..., description="通信协议", examples=["opcua"])
    endpoint: str = Field(..., description="连接端点", examples=["opc.tcp://192.168.1.100:4840"])
    username: str | None = Field(None, description="认证用户名")
    password: str | None = Field(None, description="认证密码")
    device_name: str | None = Field("Device", description="MTConnect 设备名称")


class NCSendRequest(BaseModel):
    """NC 程序发送请求模型。

    Attributes:
        machine_id: 目标机床 ID
        program_path: 本地 NC 程序文件路径
        program_name: 机床端存储的程序名（可选，默认使用文件名）
    """

    machine_id: str = Field(..., description="目标机床 ID", examples=["CNC-001"])
    program_path: str = Field(..., description="本地 NC 程序文件路径", examples=["/path/to/program.nc"])
    program_name: str | None = Field(None, description="机床端存储的程序名（默认使用文件名）")


# ── API 路由 ──────────────────────────────────────────────────


@router.post("/machines", summary="添加机床连接")
async def connect_machine(req: MachineConnectRequest):
    """
    连接一台数控机床到 DNC 系统。

    支持 OPC UA 和 MTConnect 两种协议。
    """
    ok = await dnc_manager.add_machine(
        machine_id=req.machine_id,
        protocol=req.protocol,
        endpoint=req.endpoint,
        username=req.username,
        password=req.password,
        device_name=req.device_name,
    )
    if ok:
        return success(data={"machine_id": req.machine_id, "status": "connected"})
    return error(code=ErrorCode.INTERNAL_ERROR, message=f"无法连接到机床 {req.machine_id}")


@router.delete("/machines/{machine_id}", summary="移除机床连接")
async def disconnect_machine(machine_id: str):
    """断开并移除指定机床连接。"""
    await dnc_manager.remove_machine(machine_id)
    return success(data={"machine_id": machine_id, "status": "disconnected"})


@router.get("/machines", summary="列出已连接机床")
async def list_machines():
    """列出所有已连接的机床及其状态。"""
    machines = dnc_manager.list_machines()
    return success(data=machines)


@router.get("/machines/{machine_id}/status", summary="获取机床状态")
async def get_machine_status(machine_id: str):
    """获取指定机床的实时状态信息。"""
    status = await dnc_manager.get_machine_status(machine_id)
    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])
    return success(data=status)


@router.get("/status", summary="获取所有机床状态")
async def get_all_status():
    """获取所有已连接机床的实时状态。"""
    all_status = await dnc_manager.get_all_machines_status()
    return success(data=all_status)


@router.post("/nc-program/send", summary="发送 NC 程序到机床")
async def send_nc_program(req: NCSendRequest):
    """
    将 NC 程序远程传输到指定机床。

    目前仅 OPC UA 协议支持程序传输。

    仿真强制闭环（硬约束）：程序必须能追溯到一个 SUCCEEDED 且体素仿真
    通过的阶段 7 校验任务，否则下发被拦截（错误码 8013）。
    外部/历史程序可显式设置 LNN_DNC_ALLOW_UNVALIDATED_NC=1 绕过
    （每次下发写入哈希链审计日志；审计失败则放行失败，fail-closed）。
    """
    # 验证文件存在
    program_path = Path(req.program_path)
    if not program_path.exists():
        # 修复：避免向客户端回显服务器文件路径（可能泄露目录结构），改为通用提示，路径详情仅记日志
        logger.warning("NC 程序文件不存在: machine_id=%s path=%s", req.machine_id, req.program_path)
        raise HTTPException(status_code=400, detail="文件不存在，请检查路径后重试")

    # 仿真强制闭环闸门（fail-closed：未通过阶段 7 校验（含体素仿真）的程序不允许上机）
    block_reason = get_dispatch_block_reason(str(program_path), machine_id=req.machine_id)
    if block_reason:
        logger.warning(
            "NC 程序下发被闸门拦截: machine_id=%s path=%s reason=%s",
            req.machine_id,
            req.program_path,
            block_reason,
        )
        return JSONResponse(
            status_code=403,
            content=error(
                code=ErrorCode.NC_NOT_VALIDATED,
                message=block_reason,
                suggestion="请先在阶段 7 完成 CAM 校验（含体素仿真）与工程师确认，再下发该程序。",
            ),
        )

    program_name = req.program_name or program_path.stem
    ok = await dnc_manager.send_nc_program(req.machine_id, str(program_path), program_name)
    if ok:
        return success(data={"machine_id": req.machine_id, "program_name": program_name, "status": "sent"})
    return error(code=ErrorCode.INTERNAL_ERROR, message="NC 程序发送失败")


@router.get("/machines/{machine_id}/alarms", summary="获取机床报警")
async def get_machine_alarms(machine_id: str):
    """获取指定机床的当前报警信息。"""
    # 目前仅 MTConnect 支持报警查询
    if machine_id not in dnc_manager.connections:
        logger.info("机床未连接: %s", machine_id)
        raise HTTPException(status_code=404, detail="机床未连接")

    conn = dnc_manager.connections[machine_id]
    if conn["protocol"] == ProtocolType.MTCONNECT:
        # [H12] 防御 client 为 None：连接字典中可能存在占位条目（连接中/断开中）
        client = conn.get("client")
        if client is None:
            raise HTTPException(status_code=503, detail="机床连接尚未建立完成")
        alarms = await client.get_alarms()
        return success(data=alarms)
    else:
        # 诚实失败：OPC UA 报警能力未接入（app/integrations/opcua 仅有数据
        # 读取适配器），此前返回空列表冒充"无报警"会误导操作员。
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="OPC UA 机床报警查询暂未实现",
            detail={"machine_id": machine_id, "protocol": "opc_ua"},
            suggestion="请改用 MTConnect 协议连接该机床，或接入支持 AlarmAndCondition 模型的 OPC UA 适配器。",
        )


# ── 统一双协议适配器端点（落地 MachineMetrics Universal Connectivity） ─────

# 全局统一适配器注册表（machine_id -> UnifiedDNCAdapter）
_unified_adapters: dict[str, UnifiedDNCAdapter] = {}
# [A-H2] 懒初始化的 asyncio.Lock，避免在模块导入时绑定事件循环
_unified_adapters_lock: asyncio.Lock | None = None


def _get_unified_adapters_lock() -> asyncio.Lock:
    """[A-H2] 懒初始化 asyncio.Lock，避免模块导入时绑定错误的事件循环。"""
    global _unified_adapters_lock
    if _unified_adapters_lock is None:
        _unified_adapters_lock = asyncio.Lock()
    return _unified_adapters_lock


class AutoConnectRequest(BaseModel):
    """自动探测连接请求。"""

    machine_id: str = Field(..., description="机床唯一标识")
    endpoints: list[str] = Field(
        ...,
        description="候选端点列表，按优先级排序",
        examples=[["http://192.168.1.100:5000", "opc.tcp://192.168.1.100:4840"]],
    )
    username: str | None = Field(None, description="OPC UA 用户名")
    password: str | None = Field(None, description="OPC UA 密码")
    timeout: float = Field(5.0, gt=0, le=30, description="单端点连接超时")


class DiscoverRequest(BaseModel):
    """资产发现请求。"""

    subnet: str = Field("192.168.1", description="子网前缀")
    timeout: float = Field(0.3, gt=0, le=2, description="单端口扫描超时")


@router.post("/unified/connect", summary="自动探测连接（双协议）")
async def unified_auto_connect(req: AutoConnectRequest):
    """自动探测可用协议并连接，支持故障切换。

    候选端点按优先级尝试连接，第一个成功的作为主协议，
    第二个成功的作为备用协议（故障切换用）。
    """
    adapter = UnifiedDNCAdapter(machine_id=req.machine_id)
    result = await adapter.connect_auto(
        endpoints=req.endpoints,
        credentials={"username": req.username, "password": req.password},
        timeout=req.timeout,
    )
    if result["primary_protocol"] is None:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=result.get("error", "所有候选端点均无法连接"),
        )
    # [A-H2] 加锁保护并发写入，避免多请求同时注册导致字典状态不一致
    async with _get_unified_adapters_lock():
        _unified_adapters[req.machine_id] = adapter
    return success(
        data={
            "machine_id": req.machine_id,
            "primary_protocol": result["primary_protocol"],
            "fallback_protocol": result["fallback_protocol"],
            "connected": adapter.is_connected(),
        }
    )


@router.get("/unified/{machine_id}/status", summary="获取统一状态")
async def get_unified_status(machine_id: str):
    """获取统一 schema 的机床状态（屏蔽协议差异）。"""
    # [A-H2] 加锁保护并发读，避免与并发写/删除产生字典状态不一致
    async with _get_unified_adapters_lock():
        adapter = _unified_adapters.get(machine_id)
    if adapter is None:
        logger.info("机床未注册: %s", machine_id)
        raise HTTPException(
            status_code=404,
            detail="机床未注册",
        )
    status = await adapter.get_status()
    return success(data=status.to_dict())


@router.post("/unified/discover", summary="扫描局域网内机床")
async def discover_network_machines(req: DiscoverRequest):
    """扫描子网内 MTConnect (5000) 与 OPC UA (4840) 端口。"""
    discovered = await discover_machines(
        subnet=req.subnet,
        timeout=req.timeout,
    )
    return success(
        data={
            "subnet": req.subnet,
            "count": len(discovered),
            "machines": discovered,
        }
    )


@router.get("/unified/{machine_id}/info", summary="获取适配器运行信息")
async def get_adapter_info(machine_id: str):
    """获取统一适配器的运行信息（当前协议、故障切换次数等）。"""
    # [A-H2] 加锁保护并发读
    async with _get_unified_adapters_lock():
        adapter = _unified_adapters.get(machine_id)
    if adapter is None:
        logger.info("机床未注册: %s", machine_id)
        raise HTTPException(
            status_code=404,
            detail="机床未注册",
        )
    return success(
        data={
            "machine_id": machine_id,
            "active_protocol": adapter.active_protocol,
            "connected": adapter.is_connected(),
            "failover_count": adapter.failover_count,
        }
    )


@router.delete("/unified/{machine_id}", summary="断开统一适配器")
async def disconnect_unified(machine_id: str):
    """断开统一适配器并释放资源。"""
    # [A-H2] 加锁保护并发 pop，避免与并发读/写产生竞态
    async with _get_unified_adapters_lock():
        adapter = _unified_adapters.pop(machine_id, None)
    if adapter is None:
        logger.info("机床未注册: %s", machine_id)
        raise HTTPException(
            status_code=404,
            detail="机床未注册",
        )
    await adapter.disconnect()
    return success(data={"machine_id": machine_id, "status": "disconnected"})
