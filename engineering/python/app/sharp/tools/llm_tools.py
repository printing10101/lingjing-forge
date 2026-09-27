"""SHARP LLM 工具集（M2.4）。

封装 `LLMRouter.chat_completion`，提供 2 个 LLM 工具供 ReAct 循环调用。

工具
----
- `llm.reason`   LLM 综合推理（基于三元组 + 已收集证据得出验证结论）
- `llm.extract`  LLM 实体/关系抽取（从自由文本中抽取结构化三元组）

设计原则
--------
- **结构化输出**：通过 prompt 约束 LLM 输出 JSON，并在工具层做容错解析
- **失败降级**：LLM 路由失败或 JSON 解析失败时返回结构化错误，不抛异常
- **token 控制**：reason 工具限制 max_tokens=512，extract 工具限制 768
- **temperature 策略**：reason 用 0.2（保守推理），extract 用 0.0（确定性抽取）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.sharp.tools.base import BaseTool

logger = logging.getLogger(__name__)


# Prompt 模板

REASON_SYSTEM_PROMPT = """你是知识图谱三元组验证专家。基于给定三元组与多源证据，给出验证结论。

输出严格遵循如下 JSON 格式（不要包含额外文本）：
{
  "verdict": "supported" | "refuted" | "uncertain",
  "confidence": 0.0-1.0,
  "reasoning": "结论推理过程（不超过 200 字）",
  "key_evidence": ["支撑结论的关键证据摘要 1", "..."]
}

判定标准：
- supported：证据充分支持三元组成立（confidence >= 0.7）
- refuted：证据明确反对三元组（confidence >= 0.7）
- uncertain：证据不足或矛盾（confidence < 0.7）"""

EXTRACT_SYSTEM_PROMPT = """你是制造领域知识图谱抽取专家。从给定文本中抽取 (head, relation, tail) 三元组。

实体类型：Material / Tool / Feature / Process
关系类型：SUITABLE_FOR_MATERIAL / SUITABLE_FOR_FEATURE / APPLIED_TO / USED

输出严格遵循如下 JSON 格式（不要包含额外文本）：
{
  "triples": [
    {
      "head": {"type": "Tool", "name": "..."},
      "relation": "SUITABLE_FOR_MATERIAL",
      "tail": {"type": "Material", "name": "..."},
      "confidence": 0.0-1.0,
      "evidence": "原文中支撑该三元组的片段"
    }
  ]
}

若文本中无可抽取三元组，返回 {"triples": []}。"""


# JSON 容错解析

_JSON_BLOCK_PATTERN = re.compile(r"\{[\s\S]*\}")


def _parse_llm_json(content: str) -> dict | None:
    """容错解析 LLM 输出的 JSON。

    LLM 经常在 JSON 前后添加说明文字或代码块标记，需要：
    1. 优先尝试整段解析
    2. 失败则提取第一个 {...} 块解析
    3. 仍失败返回 None
    """
    if not content:
        return None
    text = content.strip()
    # 去除 markdown 代码块标记
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 第一次：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_err:
        # 直接解析失败，记录后尝试提取 {...} 块再解析
        logger.debug("JSON direct parse failed, trying block extraction: %s", first_err)
    # 第二次：提取 {...} 块
    match = _JSON_BLOCK_PATTERN.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as second_err:
            # 两次解析均失败，记录最终结果便于排查 LLM 输出格式问题
            logger.debug("JSON block extraction parse also failed: %s", second_err)
    return None


# LLM 推理工具


class LLMReasonTool(BaseTool):
    """LLM 综合推理工具。

    工具名：`llm.reason`
    输入三元组 + 已收集证据摘要，LLM 综合推理得出验证结论
    （verdict / confidence / reasoning / key_evidence）。
    """

    def __init__(self, llm_router) -> None:
        """Args:
        llm_router: `LLMRouter` 实例（含 `chat_completion` 异步方法）
        """
        self._router = llm_router

    @property
    def name(self) -> str:
        return "llm.reason"

    @property
    def description(self) -> str:
        return (
            "基于三元组与已收集证据调用 LLM 综合推理，得出验证结论（supported/refuted/uncertain + 置信度 + 推理过程）"
        )

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "triple_text": "三元组的自然语言描述，如 '刀具 X 适配材料 Y'",
            "evidence_summary": "已收集证据摘要（KG 关系/文本片段/实体匹配等）",
            "focus_dimensions": "重点关注维度（可选，逗号分隔）",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        triple_text = arguments.get("triple_text")
        evidence_summary = arguments.get("evidence_summary", "")
        focus_dimensions = arguments.get("focus_dimensions", "")

        if not triple_text:
            raise ValueError("triple_text 参数不能为空")

        # 构造 user prompt
        user_lines = [
            f"待验证三元组：{triple_text}",
            f"已收集证据：{evidence_summary or '（暂无外部证据）'}",
        ]
        if focus_dimensions:
            user_lines.append(f"重点关注维度：{focus_dimensions}")
        user_prompt = "\n".join(user_lines)

        messages = [
            {"role": "system", "content": REASON_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # 调用 LLM Router
        try:
            response = await self._router.chat_completion(
                messages=messages,
                max_tokens=512,
                temperature=0.2,
            )
        except Exception as e:
            # 路由失败：返回结构化错误，不抛异常
            return {
                "verdict": "uncertain",
                "confidence": 0.0,
                "reasoning": f"LLM 调用失败: {type(e).__name__}: {e}",
                "key_evidence": [],
                "llm_error": True,
            }

        content = response.get("content", "") if isinstance(response, dict) else ""
        parsed = _parse_llm_json(content)

        if parsed is None:
            logger.warning("LLM reason 输出 JSON 解析失败，原始内容: %s", content[:200])
            return {
                "verdict": "uncertain",
                "confidence": 0.0,
                "reasoning": "LLM 输出解析失败",
                "key_evidence": [],
                "raw_output": content[:500],
                "parse_error": True,
            }

        # 字段补全
        return {
            "verdict": parsed.get("verdict", "uncertain"),
            "confidence": float(parsed.get("confidence", 0.0)),
            "reasoning": parsed.get("reasoning", ""),
            "key_evidence": parsed.get("key_evidence", [])[:5],
            "model": response.get("model") if isinstance(response, dict) else None,
            "usage": response.get("usage") if isinstance(response, dict) else None,
        }


# LLM 抽取工具


class LLMExtractTool(BaseTool):
    """LLM 实体/关系抽取工具。

    工具名：`llm.extract`
    输入自由文本，LLM 抽取结构化三元组列表，用于从外部文档中
    补充候选三元组或验证现有三元组的语义边界。
    """

    def __init__(self, llm_router) -> None:
        """Args:
        llm_router: `LLMRouter` 实例
        """
        self._router = llm_router

    @property
    def name(self) -> str:
        return "llm.extract"

    @property
    def description(self) -> str:
        return "从自由文本中抽取制造领域三元组（head-relation-tail），用于发现候选关系或验证实体语义边界"

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "text": "待抽取的文本（如工艺手册片段、论文摘要）",
            "max_triples": "最多抽取三元组数量，默认 5",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        text = arguments.get("text")
        max_triples = int(arguments.get("max_triples", 5))

        if not text:
            raise ValueError("text 参数不能为空")

        # 截断超长文本（避免 token 爆炸）
        text = text[:2000]

        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": f"文本：\n{text}\n\n请抽取至多 {max_triples} 个三元组。"},
        ]

        try:
            response = await self._router.chat_completion(
                messages=messages,
                max_tokens=768,
                temperature=0.0,
            )
        except Exception as e:
            return {
                "triples": [],
                "count": 0,
                "llm_error": True,
                "error": f"{type(e).__name__}: {e}",
            }

        content = response.get("content", "") if isinstance(response, dict) else ""
        parsed = _parse_llm_json(content)

        if parsed is None:
            logger.warning("LLM extract 输出 JSON 解析失败，原始内容: %s", content[:200])
            return {
                "triples": [],
                "count": 0,
                "raw_output": content[:500],
                "parse_error": True,
            }

        triples = parsed.get("triples", [])[:max_triples]
        return {
            "triples": triples,
            "count": len(triples),
            "model": response.get("model") if isinstance(response, dict) else None,
            "usage": response.get("usage") if isinstance(response, dict) else None,
        }


__all__ = ["LLMReasonTool", "LLMExtractTool"]
