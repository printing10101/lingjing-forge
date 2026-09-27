"""编排器 / NL2CAD 失败入册（自进化 M0：执行留痕）。

把 AgentOrchestrator 与 NL2CADService 的 LLM 提案位失败、安全升级人工、
一次通过成功结构化写入 failure_case_store（Phase 0 自进化第一性管道），
与 gcode_generation 管线的既有写入共用同一案例库与统计口径。

口径约定（重要，写入方必须遵守）：
- **只记录「模型质量信号」**：LLM 应答了但输出非法、安全校验升级人工、
  修复失败。LLM 不可用/超时属环境信号，已在 planning_source /
  decision_source 等 trace 字段可观测，不入册——避免环境抖动污染
  一次通过率（one_pass_rate）。
- **成败成对记录**：编排器侧记录 failure 的同时也记录 repair_count==0
  的一次通过 success，否则 failure-only 会让统计口径失真。
- **写入永不抛异常**：入册是旁路观测，任何异常只 debug 级记录，
  不允许击穿主流程（与 gcode_generation/pipeline 的钩子同一硬约束）。

新增 source 白名单值（见 failure_case_store.VALID_SOURCES）：
- ``llm_planning``：条件规划提案输出非法
- ``llm_param_aug``：参数知识增强提案输出非法
- ``gcode_repair``：G 代码 LLM 诊断修复输出非法
- ``nl2cad_extract``：NL2CAD 参数提取/精炼输出解析失败
"""

from __future__ import annotations

import logging
from typing import Any

from app.gcode_generation.failure_case_store import (
    FailureCase,
    get_failure_case_store,
)

logger = logging.getLogger(__name__)

__all__ = [
    "record_agent_failure",
    "record_agent_success",
    "record_llm_invalid_output",
]

#: LLM 提案位失败统一错误码（细分场景靠 source 区分）
LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"


def record_agent_failure(
    *,
    task_id: str,
    source: str,
    error_codes: list[str],
    error_messages: list[str],
    gcode_text: str = "",
    controller_type: str = "",
    material_name: str = "",
) -> str | None:
    """记录一条失败案例。返回 case_id；任何失败返回 None（不抛出）。"""
    try:
        case = FailureCase(
            task_id=task_id or "agent",
            outcome="failure",
            source=source,
            controller_type=controller_type or "",
            material_name=material_name or "",
            error_codes=[str(c) for c in error_codes if c],
            error_messages=[str(m) for m in error_messages if m],
            gcode_text=gcode_text or "",
        )
        return get_failure_case_store().record(case)
    except Exception as e:  # noqa: BLE001 - 旁路观测不允许击穿主流程
        logger.debug("失败案例入册跳过: %s", e)
        return None


def record_agent_success(
    *,
    task_id: str,
    controller_type: str = "",
    material_name: str = "",
) -> str | None:
    """记录一条一次通过成功案例（仅计数要素，不存 G 代码）。"""
    try:
        case = FailureCase(
            task_id=task_id or "agent",
            outcome="success",
            controller_type=controller_type or "",
            material_name=material_name or "",
        )
        return get_failure_case_store().record(case)
    except Exception as e:  # noqa: BLE001
        logger.debug("成功案例入册跳过: %s", e)
        return None


def record_llm_invalid_output(
    *,
    task_id: str,
    source: str,
    raw_output: Any,
    gcode_text: str = "",
    controller_type: str = "",
    material_name: str = "",
) -> str | None:
    """记录「LLM 应答了但输出非法」的提案位失败（统一错误码 + 原始输出截断留存）。"""
    return record_agent_failure(
        task_id=task_id,
        source=source,
        error_codes=[LLM_INVALID_OUTPUT],
        error_messages=[f"LLM 原始输出(截断): {str(raw_output)[:400]}"],
        gcode_text=gcode_text,
        controller_type=controller_type,
        material_name=material_name,
    )
