"""G 代码安全校验修复闭环（从 orchestrator.py 拆出）.

编排器在 validate_safety 失败后的自动修复能力：
白名单内错误做确定性修复（文本/参数级），白名单外先走 LLM 诊断
修复（提案位），仍失败转人工。**LLM 无权绕过校验**——修复产物会
回到 validate_safety 重验，重验失败仍走升级人工。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agent.failure_recorder import record_agent_failure
from app.ai.prompts import (
    ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID,
    get_prompt_registry,
)

logger = logging.getLogger(__name__)

# 可自动修复的安全错误码 → 修复动作（其余错误码先走 LLM 诊断修复，
# 仍失败才转人工，不做半吊子修复）
REPAIRABLE_CODES: dict[str, str] = {
    "NO_PROGRAM_END": "append_program_end",
    "NEGATIVE_FEED": "clamp_negative_feed",
    # FEED_OUT_OF_RANGE：validate_gcode_text 文本级进给越界（G94 模态），
    # recommended 为 clamp 建议值（2026-09 接线，此前该错误码从未产出）
    "FEED_OUT_OF_RANGE": "clamp_feed_range",
    # 以下两类当前 validate 步骤不产出，但保留映射：一旦 L1/L2 参数级校验
    # 接入编排校验（带 recommended clamp 值），修复闭环无需改动即生效。
    "SPINDLE_OUT_OF_RANGE": "clamp_parameter",
    "AXIS_TRAVEL_EXCEEDED": "clamp_parameter",
}


def plan_repairs(report: dict[str, Any]) -> list[dict[str, Any]]:
    """根据安全报告规划修复动作；存在任何不可修复错误时返回空（转 LLM/人工）。"""
    actions: list[dict[str, Any]] = []
    for issue in report.get("issues", []):
        if issue.get("severity") != "error":
            continue
        code = issue.get("code", "")
        mapped = REPAIRABLE_CODES.get(code)
        if mapped is None:
            return []
        action: dict[str, Any] = {
            "action": mapped,
            "code": code,
            "message": issue.get("message", ""),
        }
        if mapped == "clamp_negative_feed":
            action["line"] = (issue.get("context") or {}).get("line")
        elif mapped in ("clamp_parameter", "clamp_feed_range"):
            if issue.get("recommended") is None:
                return []
            action["value"] = issue["recommended"]
            if mapped == "clamp_feed_range":
                action["line"] = (issue.get("context") or {}).get("line")
        if not any(a["code"] == code for a in actions):
            actions.append(action)
    return actions


def apply_parameter_repairs(
    context: dict[str, Any],
    actions: list[dict[str, Any]],
) -> list[str]:
    """参数级修复：在重新生成之前，把 clamp 建议值注入工艺参数上下文。"""
    applied: list[str] = []
    plan_output = context.get("parameter_recommend")
    if not isinstance(plan_output, dict) or not isinstance(plan_output.get("parameters"), dict):
        return applied
    for action in actions:
        key = "spindle_rpm" if action.get("code") == "SPINDLE_OUT_OF_RANGE" else "safe_z"
        plan_output["parameters"][key] = action.get("value")
        applied.append(f"参数 {key} clamp 至 {action.get('value')}")
    return applied


def apply_text_repairs(
    gen_output: dict[str, Any],
    actions: list[dict[str, Any]],
) -> list[str]:
    """文本级修复：直接修正重新生成后的 G 代码文本（追加 M30 / 负进给取绝对值）。"""
    applied: list[str] = []
    gcode = gen_output.get("gcode", "") if isinstance(gen_output, dict) else ""

    for action in actions:
        kind = action.get("action")
        if kind == "append_program_end":
            if not re.search(r"\bM(30|02)\b", gcode, re.IGNORECASE):
                gcode = (gcode.rstrip() + "\nM30\n") if gcode else "M30\n"
                applied.append("追加程序结束指令 M30")
        elif kind == "clamp_negative_feed":
            gcode, desc = clamp_negative_feed_line(gcode, action.get("line"))
            if desc:
                applied.append(desc)
        elif kind == "clamp_feed_range":
            gcode, desc = clamp_feed_range_line(gcode, action.get("line"), action.get("value"))
            if desc:
                applied.append(desc)

    if isinstance(gen_output, dict) and applied:
        gen_output["gcode"] = gcode
        warnings = gen_output.setdefault("repair_warnings", [])
        warnings.append("自动修复：" + "；".join(applied))
    return applied


def clamp_negative_feed_line(gcode: str, target_line: int | None) -> tuple[str, str]:
    """把第 target_line 个有效行（跳过空行/注释）中的负进给取绝对值。

    Returns:
        (新文本, 描述)；未定位到目标行时原样返回。
    """
    if not gcode:
        return gcode, ""
    lines = gcode.split("\n")
    effective = 0
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped or stripped.startswith(";"):
            continue
        effective += 1
        if target_line is not None and effective != target_line:
            continue
        new_ln, n = re.subn(
            r"F\s*(-\d+(?:\.\d+)?)",
            lambda m: f"F{abs(float(m.group(1))):g}",
            ln,
            flags=re.IGNORECASE,
        )
        if n:
            lines[i] = new_ln
            return "\n".join(lines), f"第 {effective} 行负进给已取绝对值"
        if target_line is not None:
            break
    return gcode, ""


def clamp_feed_range_line(gcode: str, target_line: int | None, recommended: float | None) -> tuple[str, str]:
    """把第 target_line 个有效行（跳过空行/注释）中的进给 clamp 至建议值。

    Returns:
        (新文本, 描述)；未定位到目标行或无建议值时原样返回。
    """
    if not gcode or recommended is None:
        return gcode, ""
    lines = gcode.split("\n")
    effective = 0
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped or stripped.startswith(";"):
            continue
        effective += 1
        if target_line is not None and effective != target_line:
            continue
        new_ln, n = re.subn(
            r"F\s*\d+(?:\.\d+)?",
            f"F{float(recommended):g}",
            ln,
            count=1,
            flags=re.IGNORECASE,
        )
        if n:
            lines[i] = new_ln
            return "\n".join(lines), f"第 {effective} 行进给已 clamp 至 {float(recommended):g}"
        if target_line is not None:
            break
    return gcode, ""


async def llm_repair_gcode(
    report: dict[str, Any],
    context: dict[str, Any],
    *,
    enabled: bool,
) -> str | None:
    """白名单外安全错误的 LLM 诊断修复（提案位）。

    把结构化诊断 + 当前 G 代码交 LLM 产出修复版本；返回 None 表示
    LLM 不可用/输出不合法（调用方转人工）。

    开关由调用方传入（编排器读 ``LNN_ORCHESTRATOR_LLM_REPAIR``，默认开）。
    """
    if not enabled:
        return None
    gen_output = context.get("gcode_generate")
    gcode = gen_output.get("gcode", "") if isinstance(gen_output, dict) else ""
    if not gcode:
        return None
    try:
        from app.ai.llm_client import get_llm_client

        # 提示词走注册表（自进化 M0）：版本随 repair_history 入 trace
        repair_system, _ = get_prompt_registry().render(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID)
        client = await get_llm_client()
        payload = {
            "issues": report.get("issues", []),
            "gcode": gcode,
        }
        response = await client.chat_completion(
            [
                {
                    "role": "system",
                    "content": repair_system,
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=2048,
            temperature=0.1,
        )
    except Exception as e:
        logger.info("LLM 诊断修复不可用（转人工）: %s", type(e).__name__)
        return None
    repaired = re.sub(r"```[a-z]*", "", response.get("content", "")).strip()
    # 基本合法性守卫：非空、仍是多行 G 代码形态、未膨胀超过 1.5 倍（防幻觉重写）
    if not repaired or "\n" not in repaired or len(repaired) > len(gcode) * 1.5 + 64:
        # LLM 应答了但输出非法：模型质量信号，入册（口径见 failure_recorder）
        record_agent_failure(
            task_id=str(context.get("pipeline_id", "") or "orchestrator"),
            source="gcode_repair",
            error_codes=["LLM_INVALID_OUTPUT"],
            error_messages=[f"修复提案原始输出(截断): {repaired[:400]}"],
            gcode_text=gcode,
        )
        logger.info("LLM 诊断修复输出不合法（转人工）")
        return None
    return repaired
