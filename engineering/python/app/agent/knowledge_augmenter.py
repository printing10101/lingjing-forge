"""知识增强参数推荐（AI 深度参与核心接线 · "AI 提案、规则裁决"）。

2026-09 全量升格的最大缺口补强：此前工艺规划主链路（7.7k 行纯规则）
完全没有消费自家建好的知识资产——9.7k 行 RAG、工艺四元组索引
（``app/rag/process_quadruple.py``）、跨管线记忆全部躺在旁边，LLM 只在
旁边的问答引擎里。本模块把这三者接进 ``parameter_recommend`` 步骤：

    规则管线产出参数（现有 ProcessPlanningPipeline，保持不动）
        ↓
    知识检索：工艺四元组（feature×material）+ 长期记忆同类经验
        ↓
    LLM 提案：在规则值基础上给出参数调整建议 + 理由（带知识出处）
        ↓
    物理钳制：所有调整值经机床能力边界校验，越界值丢弃并记录
        ↓
    输出 enriched 结果：parameters + decision_source + knowledge_refs
    + ai_explanation（随 orchestrator trace 落盘，可审计）

安全纪律（红线）：
- LLM 只在**提案位**，裁决权在规则层：钳制失败/解析失败/LLM 不可用
  一律回退纯规则参数，管线永不因 AI 失败而中断；
- 主轴/进给越界的调整值直接丢弃（物理边界来自
  ``SafetyValidator.DEFAULT_MACHINE_CONFIG``，与安全校验同源）。

开关：``LNN_AI_AUGMENT_PARAMS``（默认开启；置 0 恢复纯规则行为）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from typing import Any

from app.agent.failure_recorder import record_llm_invalid_output
from app.ai.prompts import AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID, get_prompt_registry
from app.gcode_generation.safety_validator import DEFAULT_MACHINE_CONFIG

logger = logging.getLogger(__name__)

#: 每特征最多检索的四元组方案数
_MAX_QUADS_PER_FEATURE = 3
#: 最多参与检索的特征数（控制 LLM 提示词规模）
_MAX_FEATURES = 4

_ENV_AUGMENT = "LNN_AI_AUGMENT_PARAMS"

# 材料名归一化：中文牌号 → 四元组材料词表
_MATERIAL_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("钛", "ti6al4v", "ti-6al", "tc4", "titanium"), "titanium"),
    (("铝", "aluminum", "alloy", "6061", "7075"), "aluminum"),
    (("铸铁", "灰铁", "iron", "ht2", "ht3"), "cast_iron"),
    (("钢", "steel", "45#", "40cr", "q235", "304"), "steel"),
]


def normalize_material(raw: Any) -> str:
    """把材料牌号（中文/英文/牌号）归一化为四元组材料词表。"""
    text = str(raw or "").strip().lower()
    if not text:
        return "general"
    for keywords, normalized in _MATERIAL_KEYWORDS:
        if any(k in text for k in keywords):
            return normalized
    return text if len(text) <= 24 else "general"


def extract_feature_types(context: dict[str, Any]) -> list[str]:
    """从管线上下文提取特征类型集合（hole/pocket/slot/profile/face...）。

    兼容三种 dxf_parse 形态：
    - features 为 list[dict]（测试桩 / 未来逐特征输出）；
    - features 为 ``StageResult`` 对象（真实 ``DxfProcessService`` 输出，
      summary 含 hole_count 等聚合统计）；
    - features 为其序列化 dict（to_dict 形态）。
    """
    types: list[str] = []
    dxf_output = context.get("dxf_parse") or {}
    feats = dxf_output.get("features")

    if isinstance(feats, dict):
        summary = feats.get("summary") or {}
        if isinstance(summary, dict) and summary.get("hole_count"):
            types.append("hole")
    elif hasattr(feats, "summary"):
        # StageResult 对象形态
        summary = getattr(feats, "summary", None)
        if isinstance(summary, dict) and summary.get("hole_count"):
            types.append("hole")
    elif isinstance(feats, list):
        for feat in feats:
            if isinstance(feat, dict):
                ftype = feat.get("type") or feat.get("feature_type") or ""
            else:
                ftype = getattr(feat, "feature_type", "") or getattr(feat, "type", "")
            if ftype:
                types.append(str(ftype).strip().lower())

    for hole in context.get("input", {}).get("holes", []) or []:
        if isinstance(hole, dict):
            types.append("hole")
    # 去重保序，限制规模
    seen: set[str] = set()
    result: list[str] = []
    for t in types:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result[:_MAX_FEATURES]


def _finite_positive(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


class KnowledgeAugmenter:
    """参数推荐知识增强器（LLM 提案 + 物理钳制，LLM 不可用自动回退）。"""

    def __init__(
        self,
        llm_client: Any = None,
        quadruple_index: Any = None,
        memory: Any = None,
        enabled: bool | None = None,
    ):
        if enabled is None:
            try:
                enabled = os.getenv(_ENV_AUGMENT, "1").strip().lower() not in ("0", "false", "no", "off")
            except (ValueError, AttributeError):
                enabled = True
        self._enabled = bool(enabled)
        self._llm_client = llm_client
        self._quadruple_index = quadruple_index
        self._memory = memory

    # ------------------------------------------------------------------
    # 知识检索
    # ------------------------------------------------------------------

    def _get_quadruple_index(self) -> Any:
        if self._quadruple_index is not None:
            return self._quadruple_index
        try:
            from app.rag.process_quadruple import get_process_quadruple_index

            index = get_process_quadruple_index()
            # 开箱即用：索引为空时注入默认工艺知识（与 app/rag/service.py
            # `_get_process_index` 的首次访问播种逻辑保持一致；用户已持久化
            # 自有四元组时 total>0，不会覆盖）
            if index.get_stats()["total_quadruples"] == 0:
                try:
                    from app.rag.process_quadruple import seed_default_quadruples

                    seeded = seed_default_quadruples(index)
                    logger.info("参数增强：已注入 %d 条默认工艺四元组", seeded)
                except (ValueError, KeyError, RuntimeError) as e:
                    logger.warning("注入默认工艺四元组失败: %s", e)
            self._quadruple_index = index
        except (ImportError, RuntimeError, ValueError) as e:
            logger.debug("工艺四元组索引不可用（降级跳过检索）: %s", e)
            self._quadruple_index = False  # 负缓存，避免重复导入开销
        return self._quadruple_index or None

    def retrieve_knowledge(self, material: str, feature_types: list[str]) -> list[dict[str, Any]]:
        """检索工艺四元组知识（软依赖：索引不可用返回空）。"""
        index = self._get_quadruple_index()
        if index is None:
            return []
        refs: list[dict[str, Any]] = []
        try:
            for feature in feature_types:
                for quad in index.recommend_process(feature, material, top_k=_MAX_QUADS_PER_FEATURE):
                    refs.append(
                        {
                            "feature": quad.get("feature"),
                            "process": quad.get("process"),
                            "tool": quad.get("tool"),
                            "parameters": quad.get("parameters", {}),
                            "confidence": quad.get("confidence"),
                            "source": quad.get("source"),
                        }
                    )
        except (RuntimeError, ValueError, TypeError, AttributeError) as e:
            logger.warning("工艺四元组检索失败（降级为无知识注入）: %s", e)
            return []
        return refs

    # ------------------------------------------------------------------
    # LLM 提案
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        material: str,
        feature_types: list[str],
        rule_parameters: dict[str, Any],
        knowledge_refs: list[dict[str, Any]],
        memory_refs: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        # 提示词走注册表（自进化 M0）：版本由调用方写入输出可追溯
        system, _ = get_prompt_registry().render(AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID)
        user_payload = {
            "material": material,
            "features": feature_types,
            "rule_parameters": rule_parameters,
            "knowledge_refs": knowledge_refs,
            "memory_refs": [{"content": m.get("content"), "type": m.get("memory_type")} for m in memory_refs],
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """从 LLM 输出提取 JSON 对象（容忍 markdown 围栏/前后缀文本）。"""
        if not text:
            return None
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def _llm_propose(
        self,
        material: str,
        feature_types: list[str],
        rule_parameters: dict[str, Any],
        knowledge_refs: list[dict[str, Any]],
        memory_refs: list[dict[str, Any]],
        task_id: str = "",
    ) -> dict[str, Any] | None:
        """调用 LLM 产出调整提案；任何失败返回 None（调用方回退规则值）。

        LLM 不可用/超时属环境信号（不入案例库）；应答但 JSON 解析失败
        属模型质量信号 → 入册 ``llm_param_aug`` 失败案例（自进化 M0）。
        """
        try:
            if self._llm_client is None:
                from app.ai.llm_client import get_llm_client

                client = await get_llm_client()
            else:
                client = self._llm_client
            messages = self._build_prompt(material, feature_types, rule_parameters, knowledge_refs, memory_refs)
            # P2-4：锦上添花调用不陪葬——短超时（默认 20s，可 env 调），
            # 超时直接回退规则参数，不等韧性层 60s×3 次满配
            proposal_timeout = float(os.getenv("LNN_AI_LLM_PROPOSAL_TIMEOUT", "20"))
            response = await asyncio.wait_for(
                client.chat_completion(messages, max_tokens=512, temperature=0.2),
                timeout=proposal_timeout,
            )
        except asyncio.TimeoutError:
            logger.info("参数增强 LLM 提案超时（回退纯规则）")
            return None
        except Exception as e:  # LLMError/AppException 等一律降级
            logger.info("参数增强 LLM 提案不可用（回退纯规则）: %s", type(e).__name__)
            return None
        raw_content = response.get("content", "")
        data = self._extract_json(raw_content)
        if data is None:
            record_llm_invalid_output(
                task_id=task_id or "orchestrator",
                source="llm_param_aug",
                raw_output=raw_content,
            )
        return data

    # ------------------------------------------------------------------
    # 物理钳制
    # ------------------------------------------------------------------

    #: 可调参数精确白名单（P2-7：子串匹配过宽，如 feed_override_pct 会被
    #: 误按 mm/min 上下限校验——单位错配）
    _ADJUSTABLE_KEYS: frozenset = frozenset(
        {"spindle_rpm", "feed_rate_mm_per_min", "depth_of_cut_mm", "width_of_cut_mm", "stepover_pct"}
    )
    #: 切深/切宽保守绝对上界（mm）：最终仍有 validate_safety 兜底，
    #: 但"物理钳制"应在提案阶段就挡掉荒谬值
    MAX_DEPTH_MM = 50.0
    MAX_WIDTH_MM = 100.0

    @classmethod
    def clamp_parameter(cls, key: str, value: Any) -> tuple[float | None, str]:
        """对单个调整值做物理边界校验。

        Returns:
            (合法值 or None, 说明)。None 表示该调整被丢弃。
        """
        if key not in cls._ADJUSTABLE_KEYS:
            return None, f"{key} 不在可调参数白名单，丢弃"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None, f"{key} 调整值 {value!r} 非数值，丢弃"
        if not math.isfinite(v):
            return None, f"{key} 调整值非有限数，丢弃"
        if key == "spindle_rpm":
            lo = float(DEFAULT_MACHINE_CONFIG["spindle"]["min_rpm"])
            hi = float(DEFAULT_MACHINE_CONFIG["spindle"]["max_rpm"])
            if v < lo or v > hi:
                return None, f"{key}={v:g} 超出机床能力 [{lo:g}, {hi:g}]，丢弃"
        elif key == "feed_rate_mm_per_min":
            lo = float(DEFAULT_MACHINE_CONFIG["feed"]["min_rate"])
            hi = float(DEFAULT_MACHINE_CONFIG["feed"]["max_rate"])
            if v <= 0 or v < lo or v > hi:
                return None, f"{key}={v:g} 超出进给范围 ({lo:g}, {hi:g}]，丢弃"
        elif key == "depth_of_cut_mm":
            if v <= 0 or v > cls.MAX_DEPTH_MM:
                return None, f"{key}={v:g} 超出合理切深范围 (0, {cls.MAX_DEPTH_MM:g}]，丢弃"
        elif key == "width_of_cut_mm":
            if v <= 0 or v > cls.MAX_WIDTH_MM:
                return None, f"{key}={v:g} 超出合理切宽范围 (0, {cls.MAX_WIDTH_MM:g}]，丢弃"
        elif key == "stepover_pct":
            if not (0 < v <= 100):
                return None, f"{key}={v:g} 必须在 (0, 100]，丢弃"
        return v, ""

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def augment(self, rule_output: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """对规则管线的参数推荐输出做知识增强。

        Args:
            rule_output: ``_step_parameter_recommend`` 的规则输出
                （含 parameters / operations / confidence）
            context: 管线共享上下文（input / dxf_parse / process_understanding）

        Returns:
            enriched 输出（原字段 + decision_source / knowledge_refs /
            memory_refs / ai_explanation / ai_adjustments）
        """
        enriched = dict(rule_output) if isinstance(rule_output, dict) else {"result": rule_output}
        enriched.setdefault("decision_source", "rule")

        rule_parameters = enriched.get("parameters")
        if not isinstance(rule_parameters, dict) or not rule_parameters:
            return enriched

        # P2-8：开关判断前置——关闭时不做任何检索/播种副作用
        if not self._enabled:
            return enriched

        input_data = context.get("input", {}) if isinstance(context, dict) else {}
        material = normalize_material(input_data.get("material_name") or input_data.get("material"))
        feature_types = extract_feature_types(context or {})

        knowledge_refs = self.retrieve_knowledge(material, feature_types)
        memory_refs: list[dict[str, Any]] = []
        if self._memory is not None:
            try:
                memory_refs = self._memory.recall([t for t in ("dxf_to_gcode", material, *feature_types) if t]) or []
            except (RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug("记忆检索失败（跳过）: %s", e)

        # 无任何参考知识：保持纯规则（不打无谓的 LLM 调用）
        if not knowledge_refs and not memory_refs:
            return enriched

        proposal = await self._llm_propose(
            material,
            feature_types,
            rule_parameters,
            knowledge_refs,
            memory_refs,
            task_id=str((context or {}).get("pipeline_id", "") or ""),
        )
        if proposal is None:
            return enriched

        # 自进化 M0：所用提示词版本随输出可追溯（orchestrator 会镜像进
        # StepResult.ai_metadata）
        prompt_tpl = get_prompt_registry().get(AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID)

        adjustments_raw = proposal.get("adjustments")
        if not isinstance(adjustments_raw, dict):
            adjustments_raw = {}

        applied: dict[str, float] = {}
        rejected: list[str] = []
        merged = dict(rule_parameters)
        for key, value in adjustments_raw.items():
            valid, note = self.clamp_parameter(key, value)
            if valid is None:
                if note:
                    rejected.append(note)
                continue
            merged[key] = valid
            applied[key] = valid

        enriched["parameters"] = merged
        enriched["knowledge_refs"] = knowledge_refs
        enriched["memory_refs"] = [{"content": m.get("content"), "type": m.get("memory_type")} for m in memory_refs]
        enriched["ai_explanation"] = str(proposal.get("rationale", ""))[:500]
        enriched["ai_adjustments"] = applied
        enriched["ai_rejected_adjustments"] = rejected
        enriched["prompt_id"] = prompt_tpl.prompt_id
        enriched["prompt_version"] = prompt_tpl.version
        enriched["decision_source"] = "ai" if applied else "ai_confirmed_rule"
        logger.info(
            "参数知识增强：material=%s adjustments=%s rejected=%d",
            material,
            applied,
            len(rejected),
        )
        return enriched
