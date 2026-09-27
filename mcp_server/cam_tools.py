"""CAM 主链路 MCP 工具组（W11 扩面 · 只读）。

把 Agent Gateway 新增的 CAM/G-code 只读端点暴露为 MCP 工具，
决策智能体由此能感知 CAM 状态与工艺知识（华为 ICT 赛道三）：

- ``gcode_get_failure_stats``: 失败案例库基线报表（一次通过率/错误码分布）
- ``gcode_list_failure_cases``: 最近运行案例列表（含错误分类与失败 G 代码）
- ``cam_recommend_process``: 工艺四元组推荐（feature+material → 工艺/刀具/参数）
- ``cam_get_quadruple_stats``: 工艺知识库统计

全部 R 类只读，经 Agent Gateway API（Bearer Token）访问 FastAPI 后端。
handler 提取为模块级函数便于测试（与 factory_tools 同模式）。
开关：``LINGJING_MCP_CAM_TOOLS=0`` 关闭；注册失败仅告警，不影响既有工具。
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

# feature/material 标识白名单（字母数字下划线连字符，防注入）
_IDENTIFIER_PATTERN = _re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _sanitize_identifier(value: str, name: str) -> str:
    if not value or not _IDENTIFIER_PATTERN.match(value):
        raise ValueError(
            f"{name} 必须匹配 ^[a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}}$，当前: {value!r}"
        )
    return value


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    # trust_env=False：目标为 localhost 网关，禁用系统代理防劫持
    # （实测系统代理会劫持 127.0.0.1 请求，见 Llama 本地推理同款修复）
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, trust_env=False) as client:
        resp = await client.get(f"{BASE_URL}{path}", headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------
# 模块级 handler（可单测）
# ----------------------------------------------------------------------


async def _handle_get_failure_stats() -> str:
    try:
        result = await _get("/api/agent/v1/gcode/failure-stats")
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001 - MCP 工具统一兜底
        logger.exception("gcode_get_failure_stats failed")
        return _format_error(str(exc))


async def _handle_list_failure_cases(
    limit: int = 20, offset: int = 0, outcome: str = "", source: str = ""
) -> str:
    if not 1 <= limit <= 100:
        return _format_error("limit 须在 [1, 100]")
    if offset < 0:
        return _format_error("offset 不能为负")
    if outcome and outcome not in ("success", "failure"):
        return _format_error("outcome 只能为 success/failure")
    try:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if outcome:
            params["outcome"] = outcome
        if source:
            params["source"] = source
        result = await _get("/api/agent/v1/gcode/failure-cases", params=params)
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("gcode_list_failure_cases failed")
        return _format_error(str(exc))


async def _handle_recommend_process(
    feature: str, material: str = "general", top_k: int = 5
) -> str:
    try:
        feature = _sanitize_identifier(feature, "feature")
        material = _sanitize_identifier(material, "material")
    except ValueError as exc:
        logger.warning("cam_recommend_process validation error: %s", exc)
        return _format_error(str(exc))
    if not 1 <= top_k <= 20:
        return _format_error("top_k 须在 [1, 20]")
    try:
        result = await _get(
            "/api/agent/v1/cam/process-recommend",
            params={"feature": feature, "material": material, "top_k": top_k},
        )
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cam_recommend_process failed")
        return _format_error(str(exc))


async def _handle_get_quadruple_stats() -> str:
    try:
        result = await _get("/api/agent/v1/cam/quadruple-stats")
        return _format_success(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cam_get_quadruple_stats failed")
        return _format_error(str(exc))


# ----------------------------------------------------------------------
# 注册
# ----------------------------------------------------------------------


def register_cam_tools(server) -> None:
    """注册 CAM 主链路只读工具组（4 个 R 类工具）。"""

    @server.tool(
        name="gcode_get_failure_stats",
        description=(
            "获取 G 代码生成失败案例库的基线报表：一次通过率(one_pass_rate)、"
            "按来源与错误码的失败分布。用于评估当前生成链路质量与迭代回归对比"
        ),
    )
    async def gcode_get_failure_stats() -> list[dict]:
        return [{"type": "text", "text": await _handle_get_failure_stats()}]

    @server.tool(
        name="gcode_list_failure_cases",
        description=(
            "列出最近的 G 代码生成运行案例（按时间倒序）。可用 outcome=success/failure "
            "与 source 过滤；failure 案例携带完整 G 代码文本与结构化错误分类（L1-L6 安全门禁码等），"
            "供分析与学习"
        ),
    )
    async def gcode_list_failure_cases(
        limit: int = 20,
        offset: int = 0,
        outcome: str = "",
        source: str = "",
    ) -> list[dict]:
        return [
            {
                "type": "text",
                "text": await _handle_list_failure_cases(limit, offset, outcome, source),
            }
        ]

    @server.tool(
        name="cam_recommend_process",
        description=(
            "工艺四元组推荐：给定加工特征(feature，如 face/hole/profile)与材料(material)，"
            "返回推荐的工艺方法、刀具与切削参数（含历史成功实证 generated_validated），"
            "按置信度降序"
        ),
    )
    async def cam_recommend_process(
        feature: str,
        material: str = "general",
        top_k: int = 5,
    ) -> list[dict]:
        return [
            {
                "type": "text",
                "text": await _handle_recommend_process(feature, material, top_k),
            }
        ]

    @server.tool(
        name="cam_get_quadruple_stats",
        description=(
            "获取工艺知识库统计：四元组总数与特征/工艺/材料/刀具覆盖面，"
            "反映当前工艺知识的规模与边界"
        ),
    )
    async def cam_get_quadruple_stats() -> list[dict]:
        return [{"type": "text", "text": await _handle_get_quadruple_stats()}]
