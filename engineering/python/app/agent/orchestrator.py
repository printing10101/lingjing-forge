"""Agent Gateway Orchestrator.

High-level orchestration layer that chains business pipeline steps:
DXF parsing -> process understanding -> parameter recommendation -> G-code generation.

Supports orchestration patterns:
- Sequential chain: linear step-by-step execution
- Conditional branching (2026-09 全量升格兑现): CONDITIONAL 模式由 LLM 规划器
  在安全不变量约束下（校验/生成步骤不可剔除）选择步骤子集；
  LLM 不可用时自动回退静态步骤表
- Error fallback: degradation when a step fails
- Validation repair loop (W1.1): when ``validate_safety`` rejects the generated
  G-code, structured diagnostics are fed back into ``gcode_generate`` and the
  step pair is re-executed with a bounded repair budget (default 3, env
  ``LNN_ORCHESTRATOR_MAX_REPAIRS``). Exhausted budget or non-repairable errors
  escalate to a human handoff with the full diagnostics attached.
- LLM-assisted repair (2026-09): 白名单外的安全错误可交 LLM 诊断修复
  （提案位，重验仍在 validate_safety；env ``LNN_ORCHESTRATOR_LLM_REPAIR``）
- Knowledge-augmented parameter recommendation (2026-09): 规则参数经
  工艺四元组/长期记忆检索 + LLM 提案 + 物理钳制增强
  （"AI 提案、规则裁决"，见 ``app/agent/knowledge_augmenter.py``）
- Cross-pipeline memory (2026-09): 修复/升级经验写入长期记忆并在同类
  任务中检索复用（见 ``app/agent/memory.py``）

每步结果带 ``decision_source``（rule / ai / ai_confirmed_rule）与
``ai_metadata``，随 trace 落盘——AI 参与可度量、可审计。

Provides unified interface for MCP Server tools and frontend API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# [H18] 补充 Optional 导入——dataclass 字段多处使用 Optional[str]/Optional[float]
from typing import Any
from collections.abc import Callable

from app.agent.failure_recorder import record_agent_failure, record_agent_success
from app.agent.knowledge_augmenter import KnowledgeAugmenter
from app.agent.memory import OrchestratorMemory, summarize_pipeline_for_memory
from app.ai.prompts import (
    ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID,
    ORCHESTRATOR_PLANNING_SYSTEM_ID,
    ORCHESTRATOR_PLANNING_USER_ID,
    get_prompt_registry,
)
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    """Pipeline step execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    FALLBACK = "fallback"


class OrchestratorMode(str, Enum):
    """Orchestration execution mode."""

    SEQUENTIAL = "sequential"
    CONDITIONAL = "conditional"


@dataclass
class StepResult:
    """Result from a single pipeline step."""

    step_name: str
    status: StepStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    # AI 参与标注（2026-09 全量升格）：该步骤的决策来源。
    # rule = 纯规则；ai = LLM 提案已应用；ai_confirmed_rule = LLM 认可规则值
    decision_source: str = "rule"
    # AI 参与细节（知识出处/调整明细/拒绝原因），随 trace 落盘可审计
    ai_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
            "decision_source": self.decision_source,
            "ai_metadata": self.ai_metadata,
        }


@dataclass
class PipelineResult:
    """Result from a complete pipeline execution."""

    pipeline_id: str
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_duration_ms: float = 0.0
    fallback_triggered: bool = False
    fallback_reason: str = ""
    trace_id: str = ""
    timestamp: float = 0.0
    # W1.1 校验修复闭环：repair_count 为已消耗的修复预算，repair_history
    # 记录每轮「诊断 → 修复动作 → 应用结果」，随 trace 一并落盘可审计。
    repair_count: int = 0
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    # 2026-09 全量升格：规划来源（static / llm / llm_fallback）与记忆命中数
    planning_source: str = "static"
    planning_rationale: str = ""
    # 自进化 M0：条件规划所用提示词版本（trace 可追溯到提示词迭代）
    planning_prompt_id: str = ""
    planning_prompt_version: int = 0
    memory_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "success": self.success,
            "steps": [s.to_dict() for s in self.steps],
            "final_output": self.final_output,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "fallback_triggered": self.fallback_triggered,
            "fallback_reason": self.fallback_reason,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "repair_count": self.repair_count,
            "repair_history": self.repair_history,
            "planning_source": self.planning_source,
            "planning_rationale": self.planning_rationale,
            "planning_prompt_id": self.planning_prompt_id,
            "planning_prompt_version": self.planning_prompt_version,
            "memory_used": self.memory_used,
        }


class AgentOrchestrator:
    """Agent Gateway orchestrator for multi-step manufacturing pipelines.

    Chains business logic steps (DXF parsing, process understanding,
    parameter recommendation, G-code generation) with error handling
    and execution tracing.
    """

    def __init__(
        self,
        trace_log_dir: str | None = None,
        max_repair_attempts: int | None = None,
        memory: OrchestratorMemory | bool | None = None,
        augmenter: KnowledgeAugmenter | None = None,
    ):
        self._trace_log_dir = trace_log_dir or os.path.join(os.getcwd(), "data", "traces")
        self._pipeline_history: list[PipelineResult] = []
        self._step_registry: dict[str, Callable] = {}
        # W1.1：校验失败自动修复预算（默认 3，可用环境变量覆盖；0 = 关闭修复闭环）
        if max_repair_attempts is None:
            try:
                max_repair_attempts = int(os.getenv("LNN_ORCHESTRATOR_MAX_REPAIRS", "3"))
            except ValueError:
                logger.warning("LNN_ORCHESTRATOR_MAX_REPAIRS 非法，使用默认值 3")
                max_repair_attempts = 3
        self.max_repair_attempts = max(0, max_repair_attempts)
        # 2026-09 全量升格：跨管线记忆（None=按环境变量懒初始化；False=关闭）
        self._memory: OrchestratorMemory | None | bool = memory
        # 知识增强参数推荐（None=按环境变量懒初始化；测试可注入桩）
        self._augmenter: KnowledgeAugmenter | None = augmenter
        # LLM 诊断修复开关（白名单外安全错误交 LLM 提案，重验仍在 validate_safety）
        self._llm_repair_enabled = os.getenv("LNN_ORCHESTRATOR_LLM_REPAIR", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        self._validate_dependencies()
        self._register_default_steps()

    # ------------------------------------------------------------------
    # 记忆与增强器（懒初始化，避免 import 期副作用）
    # ------------------------------------------------------------------

    def _env_enabled(self, key: str, default: str = "1") -> bool:
        return os.getenv(key, default).strip().lower() not in ("0", "false", "no", "off")

    def _get_memory(self) -> OrchestratorMemory | None:
        if self._memory is False:
            return None
        if isinstance(self._memory, OrchestratorMemory):
            return self._memory
        if self._memory is None and not self._env_enabled("LNN_ORCHESTRATOR_MEMORY", "1"):
            self._memory = False
            return None
        try:
            self._memory = OrchestratorMemory()
        except (OSError, ValueError, RuntimeError) as e:
            logger.warning("编排器记忆初始化失败（本次以无记忆运行）: %s", e)
            self._memory = False
            return None
        return self._memory

    def _get_augmenter(self) -> KnowledgeAugmenter:
        if self._augmenter is None:
            self._augmenter = KnowledgeAugmenter(
                memory=self._get_memory(),
                enabled=self._env_enabled("LNN_AI_AUGMENT_PARAMS", "1"),
            )
        return self._augmenter

    def _validate_dependencies(self) -> None:
        """Validate that required pipeline step modules are available.

        Logs warnings for missing modules but does not prevent initialization,
        as modules may be loaded lazily at runtime.
        """
        required_modules = [
            ("app.dxf.process_service", "DXF processing"),
            ("app.ai.process_understanding.engine", "Process understanding"),
            ("app.process_planning.pipeline", "Process planning"),
            ("app.process_planning.gcode_generator", "G-code generation"),
        ]

        missing = []
        for module_name, description in required_modules:
            try:
                __import__(module_name)
            except ImportError:
                missing.append((module_name, description))

        if missing:
            logger.warning(
                "Some pipeline dependencies are not available: %s. "
                "Pipeline steps using these modules will use fallback implementations or simplified logic.",
                ", ".join(f"{desc} ({mod})" for mod, desc in missing),
            )

    def _register_default_steps(self) -> None:
        """Register default pipeline step handlers."""
        self._step_registry["dxf_parse"] = self._step_dxf_parse
        self._step_registry["process_understanding"] = self._step_process_understanding
        self._step_registry["parameter_recommend"] = self._step_parameter_recommend
        self._step_registry["gcode_generate"] = self._step_gcode_generate
        self._step_registry["validate_safety"] = self._step_validate_safety

    def register_step(self, name: str, handler: Callable) -> None:
        """Register a custom pipeline step handler."""
        self._step_registry[name] = handler

    async def execute_pipeline(
        self,
        pipeline_type: str,
        input_data: dict[str, Any],
        mode: OrchestratorMode = OrchestratorMode.SEQUENTIAL,
    ) -> PipelineResult:
        """Execute a manufacturing pipeline.

        Args:
            pipeline_type: Type of pipeline ("dxf_to_gcode", "process_plan", etc.)
            input_data: Input parameters for the pipeline
            mode: Orchestration mode (sequential or conditional)

        Returns:
            PipelineResult with execution details and trace
        """
        pipeline_id = f"pipe_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        start_time = time.perf_counter()

        result = PipelineResult(
            pipeline_id=pipeline_id,
            success=False,
            trace_id=trace_id,
            timestamp=time.time(),
        )

        try:
            # 2026-09 全量升格：CONDITIONAL 模式由 LLM 规划器在安全不变量
            # 约束下规划步骤子集；SEQUENTIAL 保持静态步骤表
            if mode == OrchestratorMode.CONDITIONAL:
                steps, planning_meta = await self._plan_steps_conditionally(pipeline_type, input_data, pipeline_id)
            else:
                steps, planning_meta = self._get_pipeline_steps(pipeline_type, input_data), {}
            result.planning_source = planning_meta.get("source", "static")
            result.planning_rationale = planning_meta.get("rationale", "")
            result.planning_prompt_id = str(planning_meta.get("prompt_id", "") or "")
            result.planning_prompt_version = int(planning_meta.get("prompt_version", 0) or 0)

            context: dict[str, Any] = {"input": input_data}
            # 自进化 M0：pipeline_id 注入共享上下文，供下游提案位
            # （知识增强 / LLM 修复）的失败入册关联任务
            context["pipeline_id"] = pipeline_id

            # 跨管线记忆：把同类任务的历史经验注入上下文（只作参考，不作决策）。
            # P2-5：checkpoint 反序列化是同步 I/O，移出事件循环
            memory_hits = await asyncio.to_thread(self._recall_memory, pipeline_type, input_data)
            if memory_hits:
                context["memory"] = memory_hits
                result.memory_used = len(memory_hits)

            index = 0
            repair_attempt = 0
            while index < len(steps):
                step_name, step_config = steps[index]
                step_result = await self._execute_step(step_name, step_config, context, pipeline_id)
                if repair_attempt > 0 and step_name in ("validate_safety", "gcode_generate"):
                    # 修复轮次的步骤在 trace 中带轮次标记，便于回溯每一轮诊断
                    step_result.step_name = f"{step_name}#repair{repair_attempt}"
                result.steps.append(step_result)

                if step_result.status == StepStatus.FAILED:
                    if step_config.get("optional", False):
                        step_result.status = StepStatus.SKIPPED
                        logger.warning(
                            "Optional step '%s' failed, skipping: %s",
                            step_name,
                            step_result.error,
                        )
                        index += 1
                        continue
                    result.fallback_triggered = True
                    result.fallback_reason = f"Step '{step_name}' failed: {step_result.error}"
                    break

                context[step_name] = step_result.output

                # W1.1 校验修复闭环：安全校验不通过时不立即失败，而是把结构化
                # 诊断回注生成步骤重试；预算耗尽或无可修复动作则转人工。
                if step_name == "validate_safety" and step_result.output.get("safety_valid") is False:
                    if await self._maybe_repair(
                        result=result,
                        steps=steps,
                        context=context,
                        pipeline_id=pipeline_id,
                        validate_result=step_result,
                        repair_attempt=repair_attempt,
                    ):
                        # _maybe_repair 已把校验步骤标记为 FAILED 并写明升级原因
                        result.fallback_triggered = True
                        result.fallback_reason = step_result.error or "安全校验未通过"
                        break
                    repair_attempt += 1
                    # 不前进 index：带着修复后的产物重新执行 validate_safety
                    continue

                index += 1

            result.success = all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in result.steps)
            result.final_output = self._extract_final_output(context, result.steps)
            # 自进化 M0：成败入册（旁路钩子，任何异常不影响主流程）
            self._record_pipeline_outcome(result, context)

        except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as exc:
            logger.exception("Pipeline execution failed: %s", exc)
            result.fallback_triggered = True
            result.fallback_reason = f"Pipeline error: {exc}"
            # 记录异常但继续执行，不 re-raise，因为需要返回 PipelineResult
            # 这是设计决策：允许上层代码通过 result.success 和 result.fallback_triggered 判断状态

        result.total_duration_ms = (time.perf_counter() - start_time) * 1000
        self._pipeline_history.append(result)
        self._write_trace(result)
        await asyncio.to_thread(self._record_memory, pipeline_type, input_data, result)

        return result

    def _get_pipeline_steps(
        self,
        pipeline_type: str,
        input_data: dict[str, Any],
        mode: OrchestratorMode = OrchestratorMode.SEQUENTIAL,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Determine pipeline steps based on type and mode."""
        if pipeline_type == "dxf_to_gcode":
            return [
                ("dxf_parse", {"input_key": "dxf_path", "optional": False}),
                ("process_understanding", {"input_key": "dxf_parse", "optional": False}),
                ("parameter_recommend", {"input_key": "process_understanding", "optional": False}),
                ("gcode_generate", {"input_key": "parameter_recommend", "optional": False}),
                ("validate_safety", {"input_key": "gcode_generate", "optional": False}),
            ]
        elif pipeline_type == "process_plan":
            return [
                ("process_understanding", {"input_key": "description", "optional": False}),
                ("parameter_recommend", {"input_key": "process_understanding", "optional": False}),
            ]
        else:
            logger.warning("Unknown pipeline type '%s', using empty steps", pipeline_type)
            return []

    # ------------------------------------------------------------------
    # 2026-09 全量升格：LLM 条件规划 + 记忆接线
    # ------------------------------------------------------------------

    # LLM 规划器可剔除的"可选"步骤（安全不变量：validate_safety / 主链
    # 的生成步骤永远不可剔除，规划只能在约束集内做选择）
    _DROPPABLE_STEPS = frozenset({"dxf_parse", "process_understanding"})

    async def _plan_steps_conditionally(
        self,
        pipeline_type: str,
        input_data: dict[str, Any],
        pipeline_id: str = "",
    ) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
        """CONDITIONAL 模式：LLM 规划器在安全约束内选择步骤子集。

        规则：
        - 候选集 = 该管线类型的静态步骤表；
        - 不可剔除步骤（gcode_generate / validate_safety 等）强制保留——
          这是规划器的安全不变量，LLM 无权绕过校验；
        - LLM 不可用/超时 → 回退静态步骤表（环境信号，planning_source
          可观测，不入案例库）；LLM 应答但输出非法 → 除回退外另入册
          ``llm_planning`` 失败案例（模型质量信号，供提示词迭代）；
        - 提供了 dxf 特征的输入可跳过 dxf_parse（规则短路，不耗 LLM）。
        """
        base_steps = self._get_pipeline_steps(pipeline_type, input_data)

        # 规则短路：输入已带特征 → 跳过 dxf_parse，输入键重映射到 input
        if pipeline_type == "dxf_to_gcode" and input_data.get("features"):
            steps = []
            for name, cfg in base_steps:
                if name == "dxf_parse":
                    continue
                if name == "process_understanding":
                    cfg = dict(cfg)
                    cfg["input_key"] = "input"
                steps.append((name, cfg))
            return steps, {
                "source": "static_features_provided",
                "rationale": "输入已包含解析后的特征，跳过 dxf_parse",
            }

        if pipeline_type != "dxf_to_gcode":
            return base_steps, {"source": "static", "rationale": ""}

        summary = {
            "material": input_data.get("material_name") or input_data.get("material"),
            "has_features": bool(input_data.get("features")),
            "description": str(input_data.get("description", ""))[:200],
        }
        catalog = [
            {"step": name, "droppable": name in self._DROPPABLE_STEPS, "description": desc}
            for name, desc in [
                ("dxf_parse", "解析 DXF 图纸并抽取特征（输入无特征时必需）"),
                ("process_understanding", "NLP 过程理解（输入描述含明确工艺要求时可跳过）"),
                ("parameter_recommend", "工艺参数推荐（必需）"),
                ("gcode_generate", "G 代码生成（必需）"),
                ("validate_safety", "安全校验（必需，不可跳过）"),
            ]
        ]
        try:
            from app.ai.llm_client import get_llm_client

            # 提示词走注册表（自进化 M0）：版本随 planning_meta 入 trace
            planning_system, _ = get_prompt_registry().render(ORCHESTRATOR_PLANNING_SYSTEM_ID)
            prompt, planning_tpl = get_prompt_registry().render(
                ORCHESTRATOR_PLANNING_USER_ID,
                catalog_json=json.dumps(catalog, ensure_ascii=False),
                summary_json=json.dumps(summary, ensure_ascii=False),
            )
            client = await get_llm_client()
            # P2-4：锦上添花调用不陪葬——短超时（默认 15s），超时回退静态表，
            # 不等韧性层 60s×3 次满配
            planning_timeout = float(os.getenv("LNN_AI_LLM_PLANNING_TIMEOUT", "15"))
            response = await asyncio.wait_for(
                client.chat_completion(
                    [
                        {"role": "system", "content": planning_system},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=256,
                    temperature=0.1,
                ),
                timeout=planning_timeout,
            )
        except asyncio.TimeoutError:
            logger.info("CONDITIONAL 规划 LLM 超时（回退静态步骤表）")
            return base_steps, {"source": "llm_fallback", "rationale": "规划超时"}
        except Exception as e:
            logger.info("CONDITIONAL 规划 LLM 不可用（回退静态步骤表）: %s", type(e).__name__)
            return base_steps, {"source": "llm_fallback", "rationale": "LLM 不可用"}

        raw_content = response.get("content", "")
        data = KnowledgeAugmenter._extract_json(raw_content)
        include = data.get("include") if isinstance(data, dict) else None
        if not isinstance(include, list) or not include:
            # LLM 应答了但输出非法：模型质量信号，入册（口径见 failure_recorder）
            record_agent_failure(
                task_id=pipeline_id or "orchestrator",
                source="llm_planning",
                error_codes=["LLM_INVALID_OUTPUT"],
                error_messages=[f"规划器原始输出(截断): {str(raw_content)[:400]}"],
            )
            return base_steps, {"source": "llm_fallback", "rationale": "规划输出非法"}

        name_set = {n for n, _ in base_steps}
        chosen: set[str] = set()
        for item in include:
            if isinstance(item, str) and item in name_set:
                chosen.add(item)
        # 安全不变量：必需步骤强制保留
        for name, cfg in base_steps:
            if name not in self._DROPPABLE_STEPS:
                chosen.add(name)
        # 连通性守卫：保留某步则其全部上游必需（传递闭包）。
        # 注：process_understanding 不设上游（P2-3）——parameter_recommend
        # 直接从 DXF 桥接特征构建 part_description（_build_part_description），
        # 不再依赖过程理解输出；其自身输入缺失时 _execute_step 也会优雅
        # 回退到 context["input"]。因此 LLM 规划器可以真正剔除它。
        upstream_of = {
            "gcode_generate": {"parameter_recommend"},
            "validate_safety": {"gcode_generate"},
        }
        changed = True
        while changed:
            changed = False
            for step, ups in upstream_of.items():
                if step in chosen:
                    for u in ups:
                        if u in name_set and u not in chosen:
                            chosen.add(u)
                            changed = True
        ordered: list[tuple[str, dict[str, Any]]] = []
        for name, cfg in base_steps:
            if name in chosen:
                ordered.append((name, cfg))
        rationale = str(data.get("rationale", ""))[:200]
        logger.info("CONDITIONAL 规划：include=%s rationale=%s", sorted(chosen), rationale)
        return ordered, {
            "source": "llm",
            "rationale": rationale,
            "prompt_id": planning_tpl.prompt_id,
            "prompt_version": planning_tpl.version,
        }

    def _recall_memory(self, pipeline_type: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        """按管线类型/材料检索长期记忆，注入执行上下文。"""
        memory = self._get_memory()
        if memory is None:
            return []
        tags = [pipeline_type]
        if isinstance(input_data, dict):
            for key in ("material_name", "material"):
                if input_data.get(key):
                    tags.append(str(input_data[key]))
        try:
            return memory.recall(tags)
        except (RuntimeError, ValueError, TypeError) as e:
            logger.debug("记忆检索失败（跳过）: %s", e)
            return []

    def _record_memory(self, pipeline_type: str, input_data: dict[str, Any], result: PipelineResult) -> None:
        """管线结束后把修复/升级经验写入长期记忆（失败静默，不影响主流程）。"""
        memory = self._get_memory()
        if memory is None:
            return
        try:
            for entry in summarize_pipeline_for_memory(pipeline_type, input_data, result):
                memory.record(
                    content=entry["content"],
                    memory_type=entry["memory_type"],
                    importance=entry["importance"],
                    tags=entry["tags"],
                    metadata=entry.get("metadata"),
                )
        except (RuntimeError, ValueError, TypeError, KeyError) as e:
            logger.debug("记忆写入失败（跳过）: %s", e)

    # ------------------------------------------------------------------
    # 自进化 M0：成败入册（执行留痕）
    # ------------------------------------------------------------------

    def _record_pipeline_outcome(self, result: PipelineResult, context: dict[str, Any]) -> None:
        """把含安全校验的管线成败写入失败案例库（一次通过口径，旁路钩子）。

        口径（与 gcode_generation/pipeline 的入册语义对齐）：
        - 任一 validate_safety 步骤 FAILED（修复预算耗尽 / 不可修复 /
          修复后重新生成失败等全部升级路径汇聚点）→ failure(source=safety_validator)；
        - 一次通过（首次校验即过且 repair_count==0）→ success；
        - 经修复后通过 → 不入册（介于成败之间，避免拉高一次通过率；
          修复过程已在 repair_history 完整可追溯）；
        - 无 validate_safety 步骤的管线（如 process_plan）不入册。
        本方法任何异常只 debug 记录，绝不影响主流程。
        """
        try:
            validate_steps = [s for s in result.steps if s.step_name.startswith("validate_safety")]
            if not validate_steps:
                return

            gen_output = context.get("gcode_generate")
            gen_output = gen_output if isinstance(gen_output, dict) else {}
            gcode = str(gen_output.get("gcode", "") or "")
            controller = str(gen_output.get("controller_type", "") or "")
            pipeline_input = context.get("input") if isinstance(context.get("input"), dict) else {}
            material = str(pipeline_input.get("material_name") or pipeline_input.get("material") or "")

            failed = [s for s in validate_steps if s.status == StepStatus.FAILED]
            if failed:
                codes: list[str] = []
                for s in failed:
                    codes.extend(str(c) for c in (s.output.get("error_codes") or []))
                record_agent_failure(
                    task_id=result.pipeline_id,
                    source="safety_validator",
                    error_codes=codes or ["SAFETY_ESCALATED"],
                    error_messages=[s.error or "安全校验未通过" for s in failed],
                    gcode_text=gcode,
                    controller_type=controller,
                    material_name=material,
                )
            elif result.success and result.repair_count == 0:
                record_agent_success(
                    task_id=result.pipeline_id,
                    controller_type=controller,
                    material_name=material,
                )
        except Exception as e:  # noqa: BLE001 - 防御性兜底，入册永不击穿主流程
            logger.debug("管线成败入册跳过: %s", e)

    # W1.1 校验修复闭环

    # 可自动修复的安全错误码 → 修复动作（其余错误码先走 LLM 诊断修复，
    # 仍失败才转人工，不做半吊子修复）
    _REPAIRABLE_CODES: dict[str, str] = {
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

    async def _maybe_repair(
        self,
        result: PipelineResult,
        steps: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any],
        pipeline_id: str,
        validate_result: StepResult,
        repair_attempt: int,
    ) -> bool:
        """校验失败后的修复决策。

        Returns:
            True  = 升级人工（校验步骤已标记 FAILED，管线应回退）；
            False = 已完成一轮修复并重跑生成步骤，管线应重新校验。
        """
        report = validate_result.output.get("safety_report") or {}
        error_codes = [i.get("code") for i in report.get("issues", []) if i.get("severity") == "error"]

        def _escalate(reason: str) -> bool:
            validate_result.status = StepStatus.FAILED
            validate_result.error = reason
            logger.warning("Pipeline %s 修复闭环升级人工: %s", pipeline_id, reason)
            return True

        if repair_attempt >= self.max_repair_attempts:
            return _escalate(
                f"安全校验未通过，自动修复预算（{self.max_repair_attempts} 次）已耗尽，需人工介入；"
                f"错误码：{error_codes}"
            )

        if "gcode_generate" not in context or "gcode_generate" not in self._step_registry:
            return _escalate(f"安全校验未通过且无生成产物可修复，需人工介入；错误码：{error_codes}")

        actions = self._plan_repairs(report)
        if not actions:
            # 2026-09 升级：白名单外错误先尝试 LLM 诊断修复（提案位）；
            # 修复后的 G 代码会回到 validate_safety 重验，LLM 无权绕过校验。
            llm_repaired = await self._llm_repair_gcode(report, context)
            if llm_repaired is not None:
                attempt = repair_attempt + 1
                # 自进化 M0：修复所用提示词版本入 repair_history（可追溯）
                repair_tpl = get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID)
                # P2-9：新 dict 替换而非原地改写——原 dict 已被 append 进
                # result.steps，原地改写会让 trace 中上一步显示修复后文本，
                # 原始失败产物不可追溯
                context["gcode_generate"] = {
                    **context["gcode_generate"],
                    "gcode": llm_repaired,
                    "repair_warnings": ["LLM 诊断修复（已经安全重验）"],
                }
                result.repair_count = attempt
                result.repair_history.append(
                    {
                        "attempt": attempt,
                        "error_codes": error_codes,
                        "actions": ["llm_diagnose_repair"],
                        "source": "llm",
                        "applied": ["LLM 诊断修复已应用（待重验）"],
                        "prompt_id": repair_tpl.prompt_id,
                        "prompt_version": repair_tpl.version,
                    }
                )
                logger.info("Pipeline %s 修复第 %d 轮完成（LLM 诊断修复）", pipeline_id, attempt)
                return False
            return _escalate(f"安全校验未通过且错误类型不支持自动修复，需人工介入；错误码：{error_codes}")

        attempt = repair_attempt + 1
        repair_record: dict[str, Any] = {
            "attempt": attempt,
            "error_codes": error_codes,
            "actions": actions,
            "source": "rule",
            "applied": [],
        }

        # 修复顺序（顺序错误会让修复被重新生成覆盖，等于没修）：
        # 1) 参数级修复先注入生成上下文 → 2) 重新生成 → 3) 文本级修复落在
        #    新生成的 G 代码上 → 4) 回到 validate_safety 重验。
        param_actions = [a for a in actions if a["action"] == "clamp_parameter"]
        text_actions = [a for a in actions if a["action"] != "clamp_parameter"]
        applied: list[str] = []
        if param_actions:
            applied.extend(self._apply_parameter_repairs(context, param_actions))

        gen_config = next(
            (cfg for name, cfg in steps if name == "gcode_generate"),
            {"input_key": "gcode_generate"},
        )
        gen_result = await self._execute_step("gcode_generate", gen_config, context, pipeline_id)
        gen_result.step_name = f"gcode_generate#repair{attempt}"
        result.steps.append(gen_result)

        if gen_result.status == StepStatus.FAILED:
            if param_actions:
                # 参数级修复后重新生成失败：产物与注入参数不一致，无法安全修复 → 升级人工
                validate_result.status = StepStatus.FAILED
                validate_result.error = (
                    f"安全校验未通过，自动修复后重新生成失败：{gen_result.error}；错误码：{error_codes}"
                )
                logger.warning("Pipeline %s 修复后重新生成失败", pipeline_id)
                return True
            # 纯文本修复：参数未变，重新生成失败时沿用既有产物（输出等价），
            # 文本修复照常应用，最终由 validate_safety 重验裁决
            logger.info("Pipeline %s 重新生成失败，文本修复沿用既有产物", pipeline_id)
        else:
            context["gcode_generate"] = gen_result.output
        if text_actions:
            applied.extend(self._apply_text_repairs(context["gcode_generate"], text_actions))

        repair_record["applied"] = applied
        result.repair_count = attempt
        result.repair_history.append(repair_record)

        logger.info(
            "Pipeline %s 修复第 %d 轮完成：%s",
            pipeline_id,
            attempt,
            applied,
        )
        return False

    def _plan_repairs(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """根据安全报告规划修复动作；存在任何不可修复错误时返回空（转 LLM/人工）。"""
        actions: list[dict[str, Any]] = []
        for issue in report.get("issues", []):
            if issue.get("severity") != "error":
                continue
            code = issue.get("code", "")
            mapped = self._REPAIRABLE_CODES.get(code)
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

    def _apply_parameter_repairs(
        self,
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

    def _apply_text_repairs(
        self,
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
                gcode, desc = self._clamp_negative_feed_line(gcode, action.get("line"))
                if desc:
                    applied.append(desc)
            elif kind == "clamp_feed_range":
                gcode, desc = self._clamp_feed_range_line(gcode, action.get("line"), action.get("value"))
                if desc:
                    applied.append(desc)

        if isinstance(gen_output, dict) and applied:
            gen_output["gcode"] = gcode
            warnings = gen_output.setdefault("repair_warnings", [])
            warnings.append("自动修复：" + "；".join(applied))
        return applied

    @staticmethod
    def _clamp_negative_feed_line(gcode: str, target_line: int | None) -> tuple[str, str]:
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

    @staticmethod
    def _clamp_feed_range_line(gcode: str, target_line: int | None, recommended: float | None) -> tuple[str, str]:
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

    async def _llm_repair_gcode(
        self,
        report: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        """白名单外安全错误的 LLM 诊断修复（提案位）。

        把结构化诊断 + 当前 G 代码交 LLM 产出修复版本；返回 None 表示
        LLM 不可用/输出不合法（调用方转人工）。**LLM 无权绕过校验**——
        修复产物会回到 validate_safety 重验，重验失败仍走升级人工。

        开关：``LNN_ORCHESTRATOR_LLM_REPAIR``（默认开）。
        """
        if not self._llm_repair_enabled:
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

    async def _execute_step(
        self,
        step_name: str,
        config: dict[str, Any],
        context: dict[str, Any],
        pipeline_id: str,
    ) -> StepResult:
        """Execute a single pipeline step."""
        step_result = StepResult(
            step_name=step_name,
            status=StepStatus.PENDING,
            started_at=time.perf_counter(),
        )

        handler = self._step_registry.get(step_name)
        if not handler:
            step_result.status = StepStatus.FAILED
            step_result.error = f"No handler registered for step '{step_name}'"
            return step_result

        step_result.status = StepStatus.RUNNING
        try:
            input_key = config.get("input_key", step_name)
            step_input = context.get(input_key, context.get("input", {}))
            output = await handler(step_input, context)
            step_result.output = output if isinstance(output, dict) else {"result": output}
            step_result.status = StepStatus.COMPLETED
            # AI 参与标注回填（2026-09）：步骤输出声明了决策来源时镜像到
            # StepResult，随 trace 落盘，供统计与审计
            if isinstance(output, dict) and "decision_source" in output:
                step_result.decision_source = str(output.get("decision_source") or "rule")
                step_result.ai_metadata = {
                    k: output[k]
                    for k in (
                        "ai_explanation",
                        "ai_adjustments",
                        "ai_rejected_adjustments",
                        "knowledge_refs",
                        "memory_refs",
                        # 自进化 M0：提示词版本随步骤可追溯
                        "prompt_id",
                        "prompt_version",
                    )
                    if output.get(k)
                }
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as exc:
            step_result.status = StepStatus.FAILED
            # safe_error_message returns a dict with 'message' field
            error_info = safe_error_message(exc)
            step_result.error = error_info.get("message", str(exc))
            logger.warning("Step '%s' failed: %s", step_name, exc)

        step_result.completed_at = time.perf_counter()
        step_result.duration_ms = (
            step_result.completed_at - (step_result.started_at or step_result.completed_at)
        ) * 1000
        return step_result

    # Default step handlers

    async def _step_dxf_parse(self, input_data: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Parse DXF file and extract normalized machining features.

        2026-09 P1 特征桥修复：此前 ``features`` 直接放置
        ``DxfProcessResult.features``（StageResult 聚合统计对象），下游规划器
        拿不到孔明细——真实 DXF 端到端必然"工序规划结果为空"。现桥接为
        规划器兼容的规范化特征列表（holes/planes dict）。
        """
        dxf_path = input_data if isinstance(input_data, str) else input_data.get("dxf_path", "")
        if not dxf_path:
            raise ValueError("dxf_path is required")

        try:
            from app.dxf.process_service import DxfProcessService

            svc = DxfProcessService()
            # [A-H9] DXF 解析涉及文件 I/O + CPU 计算，用 asyncio.to_thread 包装
            parse_result = await asyncio.to_thread(svc.process, dxf_path)
        except ImportError as e:
            logger.error("DXF module not available: %s", e)
            raise RuntimeError(f"DXF解析模块不可用，请确保已安装依赖: {e}") from e

        features, metadata = self._normalize_dxf_output(parse_result)
        return {
            "status": "success",
            "features": features,
            "metadata": metadata,
            "dxf_path": dxf_path,
        }

    @staticmethod
    def _normalize_dxf_output(parse_result: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """把 ``DxfProcessResult`` 桥接为规划器兼容的规范化特征列表。

        HoleFeatureInfo → 规划器孔 dict 契约：
        ``{type, id, hole_type, position{x,y}, diameter, depth,
        tolerance_grade, surface}``。通孔深度缺失（=0）时以板厚
        （overall_height）推断——规划器校验要求通孔深度 > 0。
        """
        features: list[dict[str, Any]] = []
        stage = getattr(parse_result, "features", None)
        summary = getattr(stage, "summary", None) if stage is not None else None
        if not isinstance(summary, dict):
            summary = {}
        plate_thickness = float(summary.get("overall_height") or 0.0)

        for h in summary.get("holes_detail") or []:
            if not isinstance(h, dict):
                continue
            hole_type = str(h.get("hole_type") or "through_hole")
            depth = float(h.get("depth") or 0.0)
            if depth <= 0 and hole_type == "through_hole":
                depth = plate_thickness
            features.append(
                {
                    "type": "hole",
                    "id": h.get("hole_id") or f"H{len(features) + 1:03d}",
                    "hole_type": hole_type,
                    "position": {
                        "x": float(h.get("center_x") or 0.0),
                        "y": float(h.get("center_y") or 0.0),
                    },
                    "diameter": float(h.get("diameter") or 0.0),
                    "depth": depth,
                    "tolerance_grade": h.get("tolerance_grade") or "IT8",
                    "surface": h.get("surface") or "A",
                }
            )
        for p in summary.get("planes_detail") or []:
            if not isinstance(p, dict):
                continue
            features.append(
                {
                    "type": "plane",
                    "id": p.get("plane_id") or f"P{len(features) + 1:03d}",
                    "position": {
                        "x": float(p.get("center_x") or 0.0),
                        "y": float(p.get("center_y") or 0.0),
                    },
                    "length": float(p.get("length") or 0.0),
                    "width": float(p.get("width") or 0.0),
                    "surface": p.get("surface") or "A",
                }
            )
        metadata = {
            "feature_stage_success": bool(getattr(stage, "success", False)),
            "feature_stage_error": getattr(stage, "error", "") or "",
            "hole_count": len([f for f in features if f["type"] == "hole"]),
            "plane_count": len([f for f in features if f["type"] == "plane"]),
            "overall": {
                "length": summary.get("overall_length"),
                "width": summary.get("overall_width"),
                "height": summary.get("overall_height"),
            },
        }
        return features, metadata

    async def _step_process_understanding(self, input_data: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Analyze part features and determine process requirements."""
        try:
            from app.ai.process_understanding.engine import ProcessUnderstandingEngine

            engine = ProcessUnderstandingEngine()
            description = input_data.get("description", "") if isinstance(input_data, dict) else str(input_data)
            # [A-H9] NLP 处理可能是 CPU 密集操作，用 asyncio.to_thread 包装
            result = await asyncio.to_thread(engine.process, description)
            return {
                "status": "success",
                "task_type": result.task_type if hasattr(result, "task_type") else "unknown",
                "intent": result.intent if hasattr(result, "intent") else "",
                "entities": result.entities if hasattr(result, "entities") else {},
                "confidence": result.confidence if hasattr(result, "confidence") else 0.0,
            }
        except ImportError as e:
            logger.error("Process understanding module not available: %s", e)
            raise RuntimeError(f"过程理解模块不可用，请确保已安装依赖: {e}") from e

    async def _step_parameter_recommend(self, input_data: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Recommend machining parameters based on features and material.

        2026-09 P1 特征桥修复：此前把过程理解输出直接当规划器输入（无 holes
        明细），且读取规划器 PipelineResult 上不存在的 ``.parameters/.operations``
        属性（真实字段是 ``operation_plan``）——真实链路必然产出空参数。
        现构建正确的 part_description 并从 ``operation_plan`` 提取工序/参数。
        """
        try:
            from app.process_planning.pipeline import ProcessPlanningPipeline

            pipeline = ProcessPlanningPipeline()
            input_dict = input_data if isinstance(input_data, dict) else {}
            part_desc = self._build_part_description(input_dict, context)
            if not part_desc.get("holes") and not part_desc.get("planes"):
                raise ValueError("无可规划特征：输入缺少 holes/planes 明细，且 DXF 桥接未产出特征")
            # [A-H9] 工艺规划流水线涉及多步计算，用 asyncio.to_thread 包装
            plan_result = await asyncio.to_thread(pipeline.run, part_desc)

            if not getattr(plan_result, "success", False):
                stage_errors = "; ".join(s.error for s in getattr(plan_result, "stages", []) if getattr(s, "error", ""))
                raise RuntimeError(f"工艺规划失败: {stage_errors or getattr(plan_result, 'summary', '')}")

            op_plan = getattr(plan_result, "operation_plan", None)
            plan_dict = (
                op_plan.to_dict()
                if op_plan is not None
                else {"operations": [], "setups": [], "estimated_time_min": 0.0, "face_change_count": 0}
            )
            parameters = self._aggregate_cutting_parameters(plan_dict.get("operations", []))
            pipeline_input = context.get("input", {}) if isinstance(context.get("input"), dict) else {}

            output = {
                "status": "success",
                "parameters": parameters,
                "operations": plan_dict.get("operations", []),
                "setups": plan_dict.get("setups", []),
                "estimated_time_min": plan_dict.get("estimated_time_min", 0.0),
                "face_change_count": plan_dict.get("face_change_count", 0),
                # gcode_generate 步骤从本输出读取这些键（此前从未透传，一直
                # 落默认值）；显式输入优先，规划器未提供时回退
                "material_name": (input_dict.get("material_name") or part_desc.get("material") or "45#钢"),
                "controller_type": pipeline_input.get("controller_type", "fanuc_0i"),
                "safe_z": pipeline_input.get("safe_z", 50.0),
                "program_number": pipeline_input.get("program_number", 1000),
                "confidence": 0.8 if parameters else 0.4,
                "decision_source": "rule",
            }

            # 2026-09 全量升格：知识增强（工艺四元组/长期记忆检索 → LLM 提案
            # → 物理钳制）。LLM/知识库不可用时 enricher 原样回退规则参数。
            try:
                augmenter = self._get_augmenter()
                output = await augmenter.augment(output, context)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.warning("参数知识增强失败（保持纯规则参数）: %s", e)
            return output
        except ImportError as e:
            logger.error("Parameter recommendation module not available: %s", e)
            raise RuntimeError(f"参数推荐模块不可用，请确保已安装依赖: {e}") from e

    # 参数键名归一化：规划器知识库体系（cutting_speed_mpm/feed_mmpr 等）
    # → 四元组/增强器标准键（spindle_rpm/feed_rate_mm_per_min 等）
    _PARAM_KEY_ALIASES: dict[str, str] = {
        "rpm": "spindle_rpm",
        "spindle_speed": "spindle_rpm",
        "spindle": "spindle_rpm",
        "feed_rate": "feed_rate_mm_per_min",
        "feed": "feed_rate_mm_per_min",
        "feed_mm_min": "feed_rate_mm_per_min",
        "depth": "depth_of_cut_mm",
        "depth_of_cut": "depth_of_cut_mm",
        "width_of_cut": "width_of_cut_mm",
        "stepover": "stepover_pct",
    }

    @classmethod
    def _aggregate_cutting_parameters(cls, operations: list[Any]) -> dict[str, Any]:
        """从工序列表聚合代表性切削参数（键名归一化，供知识增强消费）。

        只聚合数值型参数，首次出现的键优先——不同工序的同名参数以先
        出现者为准（粗加工优先，偏保守）。
        """
        merged: dict[str, Any] = {}
        for op in operations or []:
            if not isinstance(op, dict):
                continue
            params = op.get("cutting_params")
            if not isinstance(params, dict):
                continue
            for key, value in params.items():
                std_key = cls._PARAM_KEY_ALIASES.get(str(key).lower(), str(key).lower())
                if std_key not in merged and isinstance(value, (int, float)):
                    merged[std_key] = value
        return merged

    def _build_part_description(
        self,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """组装规划器 part_description：显式输入优先，其次 DXF 桥接特征。

        真实链路中 ``input_data`` 是过程理解输出（无 material/holes），
        用户原始输入在 ``context["input"]``——两者都要取。
        """
        input_data = input_data if isinstance(input_data, dict) else {}
        pipeline_input = context.get("input") if isinstance(context.get("input"), dict) else {}
        dxf_output = context.get("dxf_parse") or {}
        dxf_features = dxf_output.get("features")
        dxf_features = dxf_features if isinstance(dxf_features, list) else []

        def _first_list(*candidates: Any) -> list[Any]:
            for c in candidates:
                if isinstance(c, list) and c:
                    return c
            return []

        holes = _first_list(input_data.get("holes"), pipeline_input.get("holes")) or [
            f for f in dxf_features if isinstance(f, dict) and f.get("type") == "hole"
        ]
        planes = _first_list(input_data.get("planes"), pipeline_input.get("planes")) or [
            f for f in dxf_features if isinstance(f, dict) and f.get("type") == "plane"
        ]

        material_raw = (
            input_data.get("material_name")
            or input_data.get("material")
            or pipeline_input.get("material_name")
            or pipeline_input.get("material")
            or ""
        )
        part_desc: dict[str, Any] = {
            # 材料名归一化：知识库材料名为"45钢"形态，用户输入常见
            # "45#钢"——去除井号与空白提升命中率；未命中时规划器有
            # 通用刀具建议兜底
            "material": re.sub(r"[#\s]", "", str(material_raw)),
            "part_type": input_data.get("part_type") or pipeline_input.get("part_type", "general"),
        }
        if holes:
            part_desc["holes"] = holes
        if planes:
            part_desc["planes"] = planes
        return part_desc

    async def _step_gcode_generate(self, input_data: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Generate G-code from process plan and parameters."""
        try:
            from app.process_planning.gcode_generator import GCodeGenerator
            from app.process_planning.operation_sequencer import OperationPlan, Operation
            from app.process_planning.feature_dependency import Setup

            generator = GCodeGenerator()

            # 将 dict 转换为 OperationPlan 对象
            if isinstance(input_data, dict):
                # 从 dict 构建 OperationPlan
                operations_data = input_data.get("operations", [])
                operations = []
                for op_data in operations_data:
                    if isinstance(op_data, Operation):
                        operations.append(op_data)
                    elif isinstance(op_data, dict):
                        operations.append(
                            Operation(
                                seq=op_data.get("seq", 0),
                                name=op_data.get("name", ""),
                                feature_name=op_data.get("feature_name", ""),
                                machining_method=op_data.get("machining_method", ""),
                                surface=op_data.get("surface", ""),
                                tolerance_grade=op_data.get("tolerance_grade", ""),
                                tool_type=op_data.get("tool_type", ""),
                                cutting_params=op_data.get("cutting_params", {}),
                                estimated_time_min=op_data.get("estimated_time_min", 0.0),
                                notes=op_data.get("notes", ""),
                            )
                        )

                setups_data = input_data.get("setups", [])
                setups = []
                for setup_data in setups_data:
                    if isinstance(setup_data, Setup):
                        setups.append(setup_data)
                    elif isinstance(setup_data, dict):
                        setups.append(
                            Setup(
                                name=setup_data.get("name", ""),
                                surface=setup_data.get("surface", "A"),
                                datum_features=setup_data.get("datum_features", []),
                                fixture_type=setup_data.get("fixture_type", ""),
                                clamped_features=setup_data.get("clamped_features", []),
                            )
                        )

                operation_plan = OperationPlan(
                    operations=operations,
                    setups=setups,
                    estimated_time_min=input_data.get("estimated_time_min", 0.0),
                    face_change_count=input_data.get("face_change_count", 0),
                )
            elif isinstance(input_data, OperationPlan):
                operation_plan = input_data
            else:
                raise TypeError(f"input_data 必须是 dict 或 OperationPlan，实际类型: {type(input_data).__name__}")

            # 从 context 中提取额外参数
            controller_type = (
                input_data.get("controller_type", "fanuc_0i") if isinstance(input_data, dict) else "fanuc_0i"
            )
            material_name = input_data.get("material_name", "45#钢") if isinstance(input_data, dict) else "45#钢"
            program_number = input_data.get("program_number", 1000) if isinstance(input_data, dict) else 1000
            safe_z = input_data.get("safe_z", 50.0) if isinstance(input_data, dict) else 50.0

            gcode_result = await asyncio.to_thread(
                generator.generate,
                operation_plan=operation_plan,
                controller_type=controller_type,
                material_name=material_name,
                program_number=program_number,
                safe_z=safe_z,
            )
            return {
                "status": "success",
                "gcode": gcode_result.program_text,
                "metadata": gcode_result.metadata,
                "warnings": gcode_result.warnings,
                "errors": gcode_result.errors,
            }
        except ImportError as e:
            logger.error("G-code generator not available: %s", e)
            raise RuntimeError(f"G-code生成器不可用，请确保已安装依赖: {e}") from e

    async def _step_validate_safety(self, input_data: Any, context: dict[str, Any]) -> dict[str, Any]:
        """安全校验步骤：对生成的 G 代码做多层安全门禁（L5 语法合规 + L6 结构完整性）。

        借鉴 NumCraft SafetyValidator：error 级问题使步骤失败，触发编排回退；
        warning 级问题随结果返回，交由工程师审核。
        """
        try:
            from app.gcode_generation.safety_validator import SafetyValidator
        except ImportError as e:
            logger.error("SafetyValidator module not available: %s", e)
            raise RuntimeError(f"安全校验模块不可用，请确保已安装依赖: {e}") from e

        gcode = ""
        controller_type = "fanuc_0i"
        if isinstance(input_data, dict):
            gcode = input_data.get("gcode", "") or ""
            controller_type = input_data.get("controller_type", "fanuc_0i") or "fanuc_0i"
        elif isinstance(input_data, str):
            gcode = input_data
        if not gcode:
            raise ValueError("validate_safety: gcode 为空，无法执行安全校验")

        validator = SafetyValidator(controller_type=controller_type)
        report = validator.validate_gcode_text(gcode, controller_type=controller_type)
        if not report.is_valid:
            # W1.1：不抛异常中断管线，返回结构化失败供修复闭环规划修复动作；
            # 修复预算耗尽后由编排器把本步骤标记为 FAILED 并转人工。
            logger.info(
                "validate_safety 未通过 controller=%s errors=%s",
                controller_type,
                report.error_codes,
            )
            return {
                "status": "validation_failed",
                "safety_valid": False,
                "error_codes": report.error_codes,
                "warning_count": len(report.warnings),
                "safety_report": report.to_dict(),
            }
        logger.info(
            "validate_safety 通过 controller=%s warnings=%d",
            controller_type,
            len(report.warnings),
        )
        return {
            "status": "success",
            "safety_valid": True,
            "warning_count": len(report.warnings),
            "warnings": [w.message for w in report.warnings],
        }

    def _extract_final_output(self, context: dict[str, Any], steps: list[StepResult]) -> dict[str, Any]:
        """Extract the final output from pipeline context.

        Args:
            context: Pipeline execution context
            steps: List of step results

        Returns:
            Final output dictionary
        """
        # Return the last successful step's output as final output
        for step in reversed(steps):
            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                return step.output

        # Fallback to context if no successful steps
        return context.get("input", {})

    # Trace and history

    def _write_trace(self, result: PipelineResult) -> None:
        """Write pipeline execution trace to log file."""
        try:
            os.makedirs(self._trace_log_dir, exist_ok=True)
            trace_file = os.path.join(
                self._trace_log_dir,
                f"agent_trace_{datetime.now().strftime('%Y-%m-%d')}.jsonl",
            )
            with open(trace_file, "a", encoding="utf-8") as f:
                # default=str：真实链路的步骤输出可能含业务对象（如 StageResult），
                # 不可序列化时降级为 repr 字符串，保证审计 trace 不丢失——
                # 修复存量 bug：此前此类 trace 整行写入失败被静默丢弃
                f.write(json.dumps(result.to_dict(), ensure_ascii=False, default=str) + "\n")
        except (OSError, IOError, TypeError, ValueError) as exc:
            logger.error("Failed to write agent trace: %s", exc)

    def get_history(self, limit: int = 50) -> list[PipelineResult]:
        """Get recent pipeline execution history."""
        return self._pipeline_history[-limit:]

    def get_pipeline_history(self, limit: int = 50, offset: int = 0) -> list[PipelineResult]:
        """Get pipeline execution history with pagination.

        Args:
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            List of PipelineResult records
        """
        total_records = self._pipeline_history
        start_idx = offset
        end_idx = offset + limit
        return total_records[start_idx:end_idx]

    def get_pipeline_trace(self, pipeline_id: str) -> dict[str, Any] | None:
        """Get detailed trace for a specific pipeline execution.

        Args:
            pipeline_id: The pipeline ID to look up

        Returns:
            Dictionary with trace details or None if not found
        """
        for result in self._pipeline_history:
            if result.pipeline_id == pipeline_id:
                return result.to_dict()
        return None

    def get_statistics(self) -> dict[str, Any]:
        """Get orchestrator statistics."""
        total = len(self._pipeline_history)
        successful = sum(1 for p in self._pipeline_history if p.success)
        fallback_count = sum(1 for p in self._pipeline_history if p.fallback_triggered)
        repaired = sum(1 for p in self._pipeline_history if p.repair_count > 0)
        # AI 深度参与度量（2026-09）：任一步骤由 AI 提案/确认的管线占比
        ai_participated = sum(
            1 for p in self._pipeline_history if any(s.decision_source.startswith("ai") for s in p.steps)
        )
        llm_repaired = sum(1 for p in self._pipeline_history if any(r.get("source") == "llm" for r in p.repair_history))

        return {
            "total_pipelines": total,
            "successful_pipelines": successful,
            "failed_pipelines": total - successful,
            "fallback_count": fallback_count,
            "repaired_pipelines": repaired,
            "repair_actions_total": sum(p.repair_count for p in self._pipeline_history),
            "ai_participated_pipelines": ai_participated,
            "ai_participation_rate": ai_participated / total if total > 0 else 0.0,
            "llm_repaired_pipelines": llm_repaired,
            "success_rate": successful / total if total > 0 else 0.0,
            "registered_steps": list(self._step_registry.keys()),
        }


# Singleton instance
_orchestrator: AgentOrchestrator | None = None
_orchestrator_lock = threading.Lock()


def get_orchestrator() -> AgentOrchestrator:
    """Get the global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = AgentOrchestrator()
    return _orchestrator
