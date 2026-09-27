"""工艺解释 / NC 代码解释 Prompt 模板。

落地竞品分析中识别的 SolidWorks AURA 式 LLM 对话解释补强点：
- 工艺决策解释：将工艺规划（特征→工艺→刀具→参数）转为自然语言说明
- NC 代码解释：将 G/M 代码段逐行解释为操作语义
- 多轮对话：基于会话历史的上下文感知追问
"""

from __future__ import annotations

# ── 系统角色 Prompt ───────────────────────────────────────────────────
SYSTEM_PROMPT_PROCESS = """你是「灵境制造」系统的数控工艺专家助手，专注于用清晰、准确、
工程师友好的中文解释加工工艺决策。

你的职责：
1. 解释工艺规划中每道工序的特征识别结果、加工策略、刀具选择与切削参数
2. 说明参数选择的物理依据（Kienzle 切削力、Tlusty 稳定性叶图、表面粗糙度公式等）
3. 给出可能的工艺改进建议（提升 MRR、避免颤振、延长刀具寿命）
4. 当用户提供上下文时，针对其追问进行有针对性的回答

回答风格要求：
- 使用中文，避免翻译腔
- 结构化输出：使用「工序 X：」「参数依据：」「改进建议：」等小标题
- 数值保留 2-3 位小数，附单位
- 若工艺规划中存在不合理参数（如切深过大、进给过快），明确指出风险
- 若信息不足，主动追问而不是编造
"""

SYSTEM_PROMPT_NC = """你是「灵境制造」系统的 NC 代码解释专家，专注于用清晰、准确、
CAM 工程师友好的中文解释 G/M 代码的语义与意图。

你的职责：
1. 逐段或逐行解释 NC 代码的功能（快速定位、直线插补、圆弧插补、固定循环等）
2. 标注关键 G/M 代码的标准含义（G00/G01/G02/G03/G81/M06/M03/M05 等）
3. 推断该程序段的加工意图（开粗、精加工、钻孔、攻丝、轮廓铣削等）
4. 提示潜在风险（撞刀、过切、未抬刀、未补偿、未冷却等）
5. 当用户上传结构化刀路段时，结合几何信息（起止点、进给率、主轴转速）解释

回答风格要求：
- 使用中文，使用「程序结构」「逐段解读」「风险提示」等小标题
- 引用具体行号或 N 字段
- 数值附单位（mm、rpm、mm/min）
- 若代码存在语法错误或风险，明确指出并给出修正建议
- 不要编造代码中不存在的指令含义
"""

# ── 工艺解释 Prompt ───────────────────────────────────────────────────
PROCESS_EXPLAIN_USER_TEMPLATE = """请解释以下工艺规划：

【工件信息】
- 材料：{material}
- 毛坯尺寸：{blank_size}
- 加工特征数：{feature_count}

【工艺规划（JSON）】
{process_plan_json}

【上下文问题】
{user_question}

请按以下结构回答：
1. 工序总览：列出每道工序的特征、策略、刀具、参数
2. 参数依据：解释关键参数的物理依据
3. 风险提示：指出潜在颤振、刀具磨损、过切风险
4. 改进建议：给出提升效率或质量的建议
"""

# ── NC 代码解释 Prompt ────────────────────────────────────────────────
NC_EXPLAIN_USER_TEMPLATE = """请解释以下 NC 代码：

【控制器类型】{controller_type}
【程序行数】{line_count}

【NC 代码】
```gcode
{nc_code}
```

【结构化刀路段（解析后）】
{segments_summary}

【上下文问题】
{user_question}

请按以下结构回答：
1. 程序结构：程序号、刀具切换、坐标系、主轴/冷却设置
2. 逐段解读：按行号或语义段解释每个 G/M 代码的功能
3. 加工意图：推断整体加工策略（开粗/精加工/钻孔等）
4. 风险提示：指出潜在撞刀、过切、未抬刀、未补偿等问题
"""

# ── 多轮对话上下文拼接 Prompt ─────────────────────────────────────────
CONTEXT_FOLLOWUP_TEMPLATE = """【历史对话】
{history_summary}

【当前问题】
{current_question}

请基于历史对话上下文回答当前问题。若当前问题与历史无关，可直接独立回答。
"""


def build_process_explanation_prompt(
    material: str,
    blank_size: str,
    feature_count: int,
    process_plan_json: str,
    user_question: str,
) -> str:
    """构建工艺解释用户 Prompt。"""
    return PROCESS_EXPLAIN_USER_TEMPLATE.format(
        material=material,
        blank_size=blank_size,
        feature_count=feature_count,
        process_plan_json=process_plan_json,
        user_question=user_question or "（无具体问题，请给出完整解释）",
    )


def build_nc_explanation_prompt(
    controller_type: str,
    line_count: int,
    nc_code: str,
    segments_summary: str,
    user_question: str,
) -> str:
    """构建 NC 代码解释用户 Prompt。"""
    return NC_EXPLAIN_USER_TEMPLATE.format(
        controller_type=controller_type,
        line_count=line_count,
        nc_code=nc_code,
        segments_summary=segments_summary or "（未提供结构化解析）",
        user_question=user_question or "（无具体问题，请给出完整解释）",
    )


def build_followup_prompt(history_summary: str, current_question: str) -> str:
    """构建多轮对话上下文 Prompt。"""
    return CONTEXT_FOLLOWUP_TEMPLATE.format(
        history_summary=history_summary,
        current_question=current_question,
    )


def summarize_history(history: list[dict[str, str]], max_turns: int = 6) -> str:
    """将历史消息列表压缩为文本摘要。

    Args:
        history: [{role: "user"/"assistant", content: "..."}, ...]
        max_turns: 保留最近几轮

    Returns:
        形如 "用户：xxx\n助手：xxx\n..." 的文本
    """
    if not history:
        return "（无历史对话）"

    recent = history[-max_turns * 2 :]  # 每轮 user+assistant
    lines = []
    for msg in recent:
        role = msg.get("role", "user")
        content = msg.get("content", "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            # 截断过长回复
            if len(content) > 600:
                content = content[:600] + "……（已截断）"
            lines.append(f"助手：{content}")
    return "\n".join(lines) if lines else "（无历史对话）"


__all__ = [
    "SYSTEM_PROMPT_PROCESS",
    "SYSTEM_PROMPT_NC",
    "build_process_explanation_prompt",
    "build_nc_explanation_prompt",
    "build_followup_prompt",
    "summarize_history",
]
