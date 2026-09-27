"""统一 AgentRuntime 的制造工具集（自进化 M2）。

工具 = 制造后端能力的最小封装，与 mcp_server 的 26 个工具**同源后端
实现**（mcp_server 是外部智能体的壳，本模块是产品内 Agent 的手）。
观测统一返回紧凑 JSON 字符串（ensure_ascii=False），供 LLM 消费。

协议说明：工具方法名为 ``run``（而非 execute）——与安全扫描器对
``async def execute`` 签名的已确认误报保持距离，语义无差别。
所有 run 方法**同步**（CPU 工具为主），由 ReAct 循环统一放线程调度；
异常一律捕获为 ``{"error": ...}`` 观测，不允许击穿循环。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["Tool", "ToolRegistry", "create_default_registry"]

#: 观测文本截断上限（与 SHARP TrajectoryRecorder 的 500 字符口径一致）
MAX_OBSERVATION_CHARS = 2000


def _to_observation(data: Any) -> str:
    """dict → 紧凑 JSON 观测文本（超限截断）。"""
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        text = json.dumps({"error": f"观测序列化失败: {e}"}, ensure_ascii=False)
    if len(text) > MAX_OBSERVATION_CHARS:
        text = text[:MAX_OBSERVATION_CHARS] + f"…（截断，原长 {len(text)}）"
    return text


@dataclass
class Tool:
    """一个可被 ReAct 循环调用的工具（协议：name/description/params/run）。

    Args:
        name: 工具名（LLM 的 Action 目标，snake_case）。
        description: 一句话功能描述（进系统提示词）。
        params: 参数名 → 中文说明（与 SHARP ToolRegistry 的轻量 schema
            风格一致，非 JSON Schema——面向文本协议足够）。
        fn: 同步实现函数，接收与 params 同名的关键字参数，返回
            dict（序列化为观测文本）。
    """

    name: str
    description: str
    params: dict[str, str]
    fn: Callable[..., dict[str, Any]]
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def run(self, **kwargs: Any) -> str:
        """调用工具实现；任何异常捕获为 error 观测（永不抛出）。"""
        try:
            return _to_observation(self.fn(**kwargs))
        except Exception as e:  # noqa: BLE001 - 工具失败是观测不是异常
            logger.warning("工具 %s 执行失败: %s", self.name, e, exc_info=True)
            return _to_observation({"error": f"{type(e).__name__}: {e}"})

    def signature(self) -> str:
        """进系统提示词的签名行。"""
        params = "；".join(f"{k}={v}" for k, v in self.params.items()) or "无参数"
        return f"- {self.name}: {self.description}（参数: {params}）"


class ToolRegistry:
    """工具注册表（借鉴 SHARP ToolRegistry 的已验证形态）。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name or not tool.name.strip():
            raise ValueError("工具名不能为空")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def to_prompt_text(self) -> str:
        """全部工具的签名文本（注入 ReAct 系统提示词的 {tools_text}）。"""
        return "\n".join(t.signature() for t in sorted(self._tools.values(), key=lambda x: x.name))


# ---------------------------------------------------------------------------
# 具体工具（与 mcp_server 工具同源的后端实现）
# ---------------------------------------------------------------------------


def _tool_process_plan_run(part_description: str = "", **_: Any) -> dict[str, Any]:
    """零件描述 → 工艺规划 + G 代码（ProcessPlanningPipeline，规则流水线）。"""
    from app.process_planning.pipeline import ProcessPlanningPipeline

    try:
        desc = json.loads(part_description) if isinstance(part_description, str) else dict(part_description)
    except json.JSONDecodeError as e:
        return {"error": f"part_description 必须是合法 JSON: {e}"}
    if not isinstance(desc, dict):
        return {"error": "part_description 必须是 JSON 对象（material/holes/planes）"}

    plan_result = ProcessPlanningPipeline().run(desc)
    stages_ok = all(getattr(s, "error", "") in (None, "") for s in getattr(plan_result, "stages", []))
    op_plan = getattr(plan_result, "operation_plan", None)
    operations = getattr(op_plan, "operations", []) or []
    return {
        "success": bool(getattr(plan_result, "success", False)),
        "stages_ok": stages_ok,
        "operation_count": len(operations),
        "operation_names": [getattr(op, "name", "") for op in operations][:10],
        "summary": str(getattr(plan_result, "summary", ""))[:300],
    }


def _tool_safety_validate_gcode(gcode_text: str = "", controller_type: str = "fanuc_0i", **_: Any) -> dict[str, Any]:
    """G 代码文本安全校验（SafetyValidator L5/L6，结构化错误码）。"""
    from app.gcode_generation.safety_validator import SafetyValidator

    report = SafetyValidator(controller_type=controller_type).validate_gcode_text(
        gcode_text or "", controller_type=controller_type
    )
    return {
        "is_valid": report.is_valid,
        "error_codes": report.error_codes,
        "warning_codes": report.warning_codes,
        "messages": [i.message for i in report.issues][:10],
    }


def _tool_evaluate_gcode(
    gcode_text: str = "",
    controller_type: str = "fanuc_0i",
    safe_z: float | None = None,
    stock_top_z: float | None = None,
    stock_length: float | None = None,
    stock_width: float | None = None,
    stock_height: float | None = None,
    **_: Any,
) -> dict[str, Any]:
    """G 代码统一评分（app.evaluation 唯一分数出口：passed/复合分/失败类别）。"""
    from app.evaluation import GcodeEvaluationRequest, evaluate_gcode

    report = evaluate_gcode(
        GcodeEvaluationRequest(
            gcode_text=gcode_text or "",
            controller_type=controller_type,
            safe_z=safe_z,
            stock_top_z=stock_top_z,
            stock_length=stock_length,
            stock_width=stock_width,
            stock_height=stock_height,
        )
    )
    return {
        "passed": report.passed,
        "composite_score": round(report.composite_score, 1),
        "failure_classes": report.failure_classes,
        "dimensions": {d.name: {"status": d.status, "score": round(d.score, 1)} for d in report.dimensions},
    }


def _tool_rag_recommend_process(
    feature: str = "", material: str = "general", top_k: int = 3, **_: Any
) -> dict[str, Any]:
    """工艺四元组知识检索（app.rag ProcessQuadruple 索引）。"""
    from app.rag.process_quadruple import get_process_quadruple_index

    index = get_process_quadruple_index()
    quads = index.recommend_process(feature or "", material or "general", top_k=max(1, min(int(top_k), 5)))
    return {"count": len(quads), "recommendations": quads}


def _tool_failure_stats_query(**_: Any) -> dict[str, Any]:
    """失败案例库统计（一次通过率 / 按来源与错误码分布）。"""
    from app.gcode_generation.failure_case_store import get_failure_case_store

    return get_failure_case_store().stats()


def create_default_registry() -> ToolRegistry:
    """默认制造工具集（五个，全部为确定性 CPU 工具，无 LLM 依赖）。"""
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="process_plan_run",
            description="按零件描述（JSON：material/holes/planes）运行工艺规划流水线，产出工序与参数",
            params={"part_description": '零件描述 JSON 字符串，如 {"material": "45钢", "holes": [...]}'},
            fn=_tool_process_plan_run,
        )
    )
    registry.register(
        Tool(
            name="safety_validate_gcode",
            description="对 G 代码文本做语法合规与结构完整性安全校验，返回结构化错误码",
            params={"gcode_text": "G 代码全文", "controller_type": "控制器类型，默认 fanuc_0i"},
            fn=_tool_safety_validate_gcode,
        )
    )
    registry.register(
        Tool(
            name="evaluate_gcode",
            description="统一评分：语法安全 + 体素几何 + 物理仿真复合分（passed/composite_score/failure_classes）",
            params={
                "gcode_text": "G 代码全文",
                "controller_type": "控制器类型，默认 fanuc_0i",
                "safe_z": "可选：安全 Z 平面（mm）",
                "stock_top_z": "可选：毛坯顶面 Z（mm）",
                "stock_length": "可选：毛坯长（mm）",
                "stock_width": "可选：毛坯宽（mm）",
                "stock_height": "可选：毛坯高（mm）",
            },
            fn=_tool_evaluate_gcode,
        )
    )
    registry.register(
        Tool(
            name="rag_recommend_process",
            description="按特征与材料检索工艺四元组知识库的历史方案",
            params={
                "feature": "特征类型（hole/plane/cylinder...）",
                "material": "材料（steel/aluminum...）",
                "top_k": "返回条数，默认 3",
            },
            fn=_tool_rag_recommend_process,
        )
    )
    registry.register(
        Tool(
            name="failure_stats_query",
            description="查询生成链路失败案例统计（一次通过率、按来源/错误码分布）",
            params={},
            fn=_tool_failure_stats_query,
        )
    )
    return registry
