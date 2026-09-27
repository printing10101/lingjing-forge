"""工艺规划 MCP 工具（W14 扩面 · 写类同步）。

让决策智能体发起端到端工艺规划：零件描述（材料+孔列表）→
孔识别 → 工艺知识库 → 工序排序 → G 代码，一次调用返回全部结果。

- ``process_plan_run`` (B): 同步执行（CPU 秒级，无 GPU/LLM 依赖）。
  part_description ≤ 64KB、holes ≤ 50（后端规模护栏 fail-closed）。

开关：``LINGJING_MCP_PROCESS_TOOLS=0`` 关闭；注册失败仅告警。
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
_MAX_DESC_BYTES = 64 * 1024
# 控制字符防注入（保留常见空白）
_DESC_PATTERN = _re.compile(r"^[\x20-\x7E\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\n\r\t]*$")


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


def _validate_part_description(desc: dict[str, Any]) -> str | None:
    """返回错误消息（None = 通过）。"""
    if not isinstance(desc, dict):
        return "part_description 必须为对象"
    material = desc.get("material")
    if not isinstance(material, str) or not material.strip() or len(material) > 64:
        return "part_description.material 必须为 1-64 字符字符串"
    holes = desc.get("holes", [])
    if not isinstance(holes, list):
        return "part_description.holes 必须为列表"
    if len(holes) > 50:
        return f"holes 数量 {len(holes)} 超过上限 50"
    try:
        size = len(_desc_json(desc).encode("utf-8"))
    except (TypeError, ValueError):
        return "part_description 无法序列化（含非法值）"
    if size > _MAX_DESC_BYTES:
        return "part_description 超过 64KB 上限"
    return None


def _desc_json(desc: dict[str, Any]) -> str:
    import json

    return json.dumps(desc, ensure_ascii=False)


async def _handle_run(
    part_description: dict[str, Any],
    controller_type: str = "fanuc_0i",
    safe_z: float = 50.0,
    program_number: int = 1000,
) -> str:
    if controller_type not in _VALID_CONTROLLERS:
        return _format_error(
            f"controller_type 须为 {list(_VALID_CONTROLLERS)} 之一，当前: {controller_type!r}"
        )
    err = _validate_part_description(part_description)
    if err:
        return _format_error(err)
    try:
        result = await _post(
            "/api/agent/v1/process-planning/run",
            {
                "part_description": part_description,
                "controller_type": controller_type,
                "safe_z": safe_z,
                "program_number": program_number,
            },
        )
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001 - MCP 工具统一兜底
        logger.exception("process_plan_run failed")
        return _format_error(str(exc))


def register_process_planning_tools(server) -> None:
    """注册工艺规划工具（1 个 B 类）。"""

    @server.tool(
        name="process_plan_run",
        description=(
            "发起端到端工艺规划：输入零件描述（material 必填，holes 孔列表可空），"
            "依次执行孔特征识别 → 工艺知识库参数匹配 → 工序排序 → G 代码生成，"
            "返回各阶段记录、工序方案与 G 代码。生成结果须经 CAM 软件二次校验，"
            "绝不直接接口 CNC 控制器"
        ),
    )
    async def process_plan_run(
        part_description: dict[str, Any],
        controller_type: str = "fanuc_0i",
        safe_z: float = 50.0,
        program_number: int = 1000,
    ) -> list[dict]:
        return [
            {
                "type": "text",
                "text": await _handle_run(
                    part_description, controller_type, safe_z, program_number
                ),
            }
        ]
