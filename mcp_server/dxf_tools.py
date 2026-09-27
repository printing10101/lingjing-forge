"""DXF 图纸解析 MCP 工具（W13 扩面 · 只读）。

让决策智能体读懂图纸输入（CAM 链路最上游）：

- ``dxf_describe_file`` (R): DXF 文件解析摘要——版本、实体统计、
  图形范围、警告。路径由后端白名单（LINGJING_AGENT_INPUT_ROOTS）校验，
  拒绝路径遍历。

开关：``LINGJING_MCP_DXF_TOOLS=0`` 关闭；注册失败仅告警。
"""

from __future__ import annotations

import logging
import re as _re
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

_PATH_PATTERN = _re.compile(r"^[^`\x00]{1,1024}$")


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    # trust_env=False：目标为 localhost 网关，禁用系统代理防劫持
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, trust_env=False) as client:
        resp = await client.get(f"{BASE_URL}{path}", headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


async def _handle_describe_file(path: str) -> str:
    if not path or not _PATH_PATTERN.match(path):
        return _format_error("path 格式非法（1-1024 字符，无控制字符）")
    try:
        result = await _get(
            "/api/agent/v1/dxf/summary", params={"path": path}
        )
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001 - MCP 工具统一兜底
        logger.exception("dxf_describe_file failed")
        return _format_error(str(exc))


def register_dxf_tools(server) -> None:
    """注册 DXF 图纸解析只读工具。"""

    @server.tool(
        name="dxf_describe_file",
        description=(
            "解析 DXF 图纸文件返回结构化摘要：DXF 版本、各类实体统计"
            "（线/圆/弧/多段线/尺寸标注/HATCH/图块/样条）、图形范围与解析警告。"
            "文件须位于后端允许的输入目录白名单内（通常为 data/ 下）；"
            "用于加工前理解图纸构成"
        ),
    )
    async def dxf_describe_file(path: str) -> list[dict]:
        return [{"type": "text", "text": await _handle_describe_file(path)}]
