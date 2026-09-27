"""G 代码生成任务 MCP 工具组（W12 扩面 · 写类 job 化）。

决策智能体发起 G 代码生成任务并追踪进度：

- ``gcode_create_job`` (B): 创建生成任务（后台异步执行，返回 task_id）。
  输入文件路径由后端白名单校验（LINGJING_GCODE_INPUT_ROOTS），
  拒绝路径遍历；
- ``gcode_get_job_status`` (R): 任务详情（状态/错误/特征摘要，
  G 代码全文按需索取）；
- ``gcode_list_jobs`` (R): 最近任务列表。

安全约定：生成结果仅供 CAM 二次校验，绝不直接接口 CNC 控制器。
开关：``LINGJING_MCP_GCODE_TOOLS=0`` 关闭；注册失败仅告警。
"""

from __future__ import annotations

import logging
import re as _re
import uuid
from typing import Any

import httpx

from mcp_server.tools import (
    _DEFAULT_TIMEOUT,
    _format_error,
    _format_success,
    _headers,
    BASE_URL,
)

logger = logging.getLogger("lingjing-mcp")

_VALID_CONTROLLERS = ("fanuc_0i", "siemens_840d", "heidenhain_tnc", "xmachine_xm100")
# 文件路径：拒绝空串/超长/空字节/反引号注入面（后端再做白名单强校验）
_PATH_PATTERN = _re.compile(r"^[^`\x00]{1,1024}$")


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    # trust_env=False：目标为 localhost 网关，禁用系统代理防劫持
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, trust_env=False) as client:
        resp = await client.post(
            f"{BASE_URL}{path}",
            headers={**_headers(), "Idempotency-Key": str(uuid.uuid4())},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, trust_env=False) as client:
        resp = await client.get(f"{BASE_URL}{path}", headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------
# 模块级 handler（可单测）
# ----------------------------------------------------------------------


async def _handle_create_job(
    chatter_report_path: str,
    operation_plan_path: str,
    controller_type: str = "fanuc_0i",
    material_name: str = "45#钢",
) -> str:
    for field, value in (
        ("chatter_report_path", chatter_report_path),
        ("operation_plan_path", operation_plan_path),
    ):
        if not value or not _PATH_PATTERN.match(value):
            return _format_error(f"{field} 格式非法（1-1024 字符，无控制字符）")
    if controller_type not in _VALID_CONTROLLERS:
        return _format_error(
            f"controller_type 须为 {list(_VALID_CONTROLLERS)} 之一，当前: {controller_type!r}"
        )
    if not material_name or len(material_name) > 64:
        return _format_error("material_name 须为 1-64 字符")
    try:
        result = await _post(
            "/api/agent/v1/gcode/jobs",
            {
                "chatter_report_path": chatter_report_path,
                "operation_plan_path": operation_plan_path,
                "controller_type": controller_type,
                "material_name": material_name,
            },
        )
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001 - MCP 工具统一兜底
        logger.exception("gcode_create_job failed")
        return _format_error(str(exc))


async def _handle_get_job_status(task_id: str, include_gcode: bool = False) -> str:
    if not task_id or len(task_id) > 256 or "/" in task_id or ".." in task_id:
        return _format_error("task_id 格式非法")
    try:
        result = await _get(
            f"/api/agent/v1/gcode/jobs/{task_id}",
            params={"include_gcode": str(include_gcode).lower()},
        )
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("gcode_get_job_status failed for %s", task_id)
        return _format_error(str(exc))


async def _handle_list_jobs(limit: int = 20) -> str:
    if not 1 <= limit <= 100:
        return _format_error("limit 须在 [1, 100]")
    try:
        result = await _get("/api/agent/v1/gcode/jobs", params={"limit": limit})
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("gcode_list_jobs failed")
        return _format_error(str(exc))


# ----------------------------------------------------------------------
# 注册
# ----------------------------------------------------------------------


def register_gcode_job_tools(server) -> None:
    """注册 G 代码生成任务工具组（1 B + 2 R）。"""

    @server.tool(
        name="gcode_create_job",
        description=(
            "发起 G 代码生成任务（后台异步执行）。输入为阶段 5 ChatterReport 与阶段 3 "
            "OperationPlan 的 JSON 文件路径（必须位于后端允许的输入目录白名单内，"
            "通常为 data/ 或 output/ 下）；返回 task_id，用 gcode_get_job_status 轮询。"
            "生成结果须经 CAM 软件二次校验，绝不直接接口 CNC 控制器"
        ),
    )
    async def gcode_create_job(
        chatter_report_path: str,
        operation_plan_path: str,
        controller_type: str = "fanuc_0i",
        material_name: str = "45#钢",
    ) -> list[dict]:
        return [
            {
                "type": "text",
                "text": await _handle_create_job(
                    chatter_report_path, operation_plan_path, controller_type, material_name
                ),
            }
        ]

    @server.tool(
        name="gcode_get_job_status",
        description=(
            "查询 G 代码生成任务详情：状态(pending/running/generated/failed/succeeded)、"
            "错误码、警告、特征摘要；include_gcode=true 时返回 G 代码全文"
        ),
    )
    async def gcode_get_job_status(task_id: str, include_gcode: bool = False) -> list[dict]:
        return [
            {
                "type": "text",
                "text": await _handle_get_job_status(task_id, include_gcode),
            }
        ]

    @server.tool(
        name="gcode_list_jobs",
        description="列出最近的 G 代码生成任务（按创建时间倒序）",
    )
    async def gcode_list_jobs(limit: int = 20) -> list[dict]:
        return [{"type": "text", "text": await _handle_list_jobs(limit)}]
