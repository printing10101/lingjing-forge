"""提示词注册表（自进化 M0 · Prompt Registry v0）。

v0 已收编（自进化关键链路，trace 已带版本号）：
- ``orchestrator.conditional_planning.system`` / ``.user``
  （条件规划提示词，原内联于 app/agent/orchestrator.py）
- ``orchestrator.gcode_repair.system``
  （G 代码 LLM 诊断修复提示词，同上）
- ``knowledge_augmenter.param_proposal.system``
  （参数知识增强提案提示词，原内联于 app/agent/knowledge_augmenter.py）

待收编（不在 T1 提示词迭代面上，保持原位，后续按模块迁移）：
app/ai/process_understanding/_prompts.py、app/rag/query_rewriter.py、
app/sharp/react/prompt_templates.py、app/sharp/tools/llm_tools.py、
app/ai/process_explainer/prompts.py、app/api/v1/nl2cad/prompts.py、
app/cad/nl2cad_llm.py、app/knowledge_graph/extractor/prompts.py

版本迭代纪律：修改提示词语义必须升 version（新条目），不得原地改写
旧版本——旧版本是历史 trace / 案例的可追溯锚点。
"""

import logging
import threading

from app.ai.prompts.registry import PromptRegistry, PromptTemplate

__all__ = [
    "PromptRegistry",
    "PromptTemplate",
    "get_prompt_registry",
    "reset_prompt_registry",
    "ORCHESTRATOR_PLANNING_SYSTEM_ID",
    "ORCHESTRATOR_PLANNING_USER_ID",
    "ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID",
    "AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID",
    "EVOLUTION_PROPOSE_SYSTEM_ID",
    "EVOLUTION_PROPOSE_USER_ID",
    "AGENT_RUNTIME_REACT_SYSTEM_ID",
]

# ---------------------------------------------------------------------------
# prompt_id 常量（调用方与 trace / 案例库共用，避免魔法字符串漂移）
# ---------------------------------------------------------------------------

ORCHESTRATOR_PLANNING_SYSTEM_ID = "orchestrator.conditional_planning.system"
ORCHESTRATOR_PLANNING_USER_ID = "orchestrator.conditional_planning.user"
ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID = "orchestrator.gcode_repair.system"
AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID = "knowledge_augmenter.param_proposal.system"
EVOLUTION_PROPOSE_SYSTEM_ID = "evolution.propose_patch.system"
EVOLUTION_PROPOSE_USER_ID = "evolution.propose_patch.user"
AGENT_RUNTIME_REACT_SYSTEM_ID = "agent_runtime.react.system"

# ---------------------------------------------------------------------------
# v0 默认条目（正文与 2026-09 迁移前内联版本逐字一致，行为不变）
# ---------------------------------------------------------------------------

_PLANNING_SYSTEM_V1 = "你是严格的 JSON 输出规划器，无 markdown 围栏。"

_PLANNING_USER_V1 = (
    "你是数控编程管线的规划器。根据任务输入，从候选步骤中选择本次需要执行的"
    "步骤子集。约束：必需步骤（droppable=false）必须保留。"
    '输出严格 JSON：{"include": ["步骤名", ...], "rationale": "一句话理由"}。\n'
    "候选步骤: {catalog_json}\n"
    "任务输入摘要: {summary_json}"
)

_GCODE_REPAIR_SYSTEM_V1 = (
    "你是数控安全修复助手。给出的 G 代码未通过安全校验，"
    "请只修复报告列出的问题，严禁改动任何其他行、严禁增删功能。"
    "直接输出修复后的完整 G 代码纯文本（无 markdown 围栏、无解释）。"
    "若无法在不改动其他内容的前提下修复，输出原样代码。"
)

_PARAM_PROPOSAL_SYSTEM_V1 = (
    "你是资深数控工艺工程师助手。系统已用规则引擎产出初始切削参数，"
    "并检索到知识库中同材料同特征的历史工艺方案。请判断规则参数是否需要调整："
    "只在有明确工艺理由时调整，避免无依据的改动。"
    "必须输出严格 JSON（无 markdown 围栏），格式："
    '{"adjustments": {"参数名": 数值, ...}, "rationale": "简要理由"}。'
    "adjustments 为空对象表示认可规则参数。可调参数：spindle_rpm（主轴转速）、"
    "feed_rate_mm_per_min（进给）、depth_of_cut_mm（切深）、width_of_cut_mm（切宽）、"
    "stepover_pct（行距百分比）。"
)

_EVOLUTION_PROPOSE_SYSTEM_V1 = (
    "你是提示词迭代工程师。系统按失败类别统计了 LLM 环节的失败案例，"
    "请针对目标提示词产出一个新版本，修复高频失败模式。"
    "必须输出严格 JSON（无 markdown 围栏），格式："
    '{"template": "完整的新版提示词文本", "rationale": "修改要点（一句话）"}。'
    "纪律：只做针对性最小修改，保留原提示词的全部输出格式约束与安全红线；"
    "template 必须是完整可用的提示词全文，不得输出 diff 或片段。"
)

_EVOLUTION_PROPOSE_USER_V1 = (
    "目标提示词 ID: {prompt_id}\n"
    "当前版本全文：\n{current_template}\n\n"
    "近期失败统计（按错误码/来源分布）：\n{failure_summary}\n\n"
    "请产出新版提示词（严格 JSON）。"
)

_AGENT_RUNTIME_REACT_SYSTEM_V1 = (
    "你是数控加工领域的智能体，通过调用工具完成制造任务"
    "（工艺规划 / G 代码评估 / 工艺知识检索 / 失败统计查询）。\n"
    "可用工具：\n{tools_text}\n"
    "每轮回复必须严格遵循以下两种格式之一。\n"
    "格式一（需要调用工具）：\n"
    "Thought: <一句话分析>\n"
    "Action: <工具名>\n"
    'Action Input: <JSON 对象，形如 {"参数名": 值}>\n'
    "格式二（任务已完成）：\n"
    "Thought: <一句话结论>\n"
    "Final Answer: <面向用户的最终回答>\n"
    "纪律：Action Input 必须是合法 JSON 且不带 markdown 围栏；"
    "一次只调用一个工具；只能使用上面列出的工具；"
    "观测结果足够回答任务时立即给出 Final Answer，不要无谓地连续调用工具。"
)


def _register_defaults(registry: PromptRegistry) -> None:
    """注册 v0 默认条目（幂等：同键覆盖）。"""
    registry.register(ORCHESTRATOR_PLANNING_SYSTEM_ID, 1, _PLANNING_SYSTEM_V1, "CONDITIONAL 模式 LLM 规划器系统提示")
    registry.register(
        ORCHESTRATOR_PLANNING_USER_ID,
        1,
        _PLANNING_USER_V1,
        "CONDITIONAL 模式 LLM 规划器用户提示（占位符：catalog_json / summary_json）",
    )
    registry.register(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID, 1, _GCODE_REPAIR_SYSTEM_V1, "G 代码 LLM 诊断修复系统提示")
    registry.register(AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID, 1, _PARAM_PROPOSAL_SYSTEM_V1, "参数知识增强 LLM 提案系统提示")
    registry.register(
        EVOLUTION_PROPOSE_SYSTEM_ID,
        1,
        _EVOLUTION_PROPOSE_SYSTEM_V1,
        "演化循环提示词补丁提案系统提示（自进化 M1）",
    )
    registry.register(
        EVOLUTION_PROPOSE_USER_ID,
        1,
        _EVOLUTION_PROPOSE_USER_V1,
        "演化循环提示词补丁提案用户提示（占位符：prompt_id / current_template / failure_summary）",
    )
    registry.register(
        AGENT_RUNTIME_REACT_SYSTEM_ID,
        1,
        _AGENT_RUNTIME_REACT_SYSTEM_V1,
        "统一 AgentRuntime ReAct 系统提示（占位符：tools_text）",
    )


def _seed_registry() -> PromptRegistry:
    """构造注册表：先播种代码内置默认条目，再叠加 applied 演化版本。

    applied 版本（自进化 M1 持久化，见 persistence.py）覆盖同 id 默认条目，
    保证进程重启后线上提示词不回退。覆盖层任何异常只告警不阻断——
    默认条目始终可用。
    """
    instance = PromptRegistry()
    _register_defaults(instance)
    try:
        from app.ai.prompts import persistence as _persistence

        for rec in _persistence.list_applied():
            try:
                instance.register(
                    str(rec["prompt_id"]),
                    int(rec["version"]),
                    str(rec["template"]),
                    str(rec.get("description", "") or ""),
                )
            except (KeyError, TypeError, ValueError) as e:
                logging.getLogger(__name__).warning(
                    "跳过非法的 applied 提示词记录 prompt_id=%r version=%r: %s",
                    rec.get("prompt_id"),
                    rec.get("version"),
                    e,
                )
    except Exception as e:  # noqa: BLE001 - 覆盖层失败不阻断默认条目
        logging.getLogger(__name__).warning("加载 applied 提示词覆盖失败（使用默认条目）: %s", e)
    return instance


# 进程级单例（双重检查锁，与 failure_case_store / orchestrator 风格一致）

_registry: PromptRegistry | None = None
_registry_lock = threading.Lock()


def get_prompt_registry() -> PromptRegistry:
    """获取全局注册表（首次调用播种默认条目 + applied 演化版本）。"""
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = _seed_registry()
        return _registry


def reset_prompt_registry() -> None:
    """重置全局注册表（测试用；下次 get_prompt_registry 重新播种默认条目）。"""
    global _registry
    with _registry_lock:
        _registry = None
