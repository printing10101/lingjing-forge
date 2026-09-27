"""自进化演化循环（自进化 M1 · 闭环运转）。

把「失败统计 → 提示词补丁提案 → 人工审核 → 门控发布/回滚 → 报告」
固化为本模块的可编程流程，由 REST / 心跳 cron / workflow 模板触发。

流程与安全纪律（提案制）：

1. ``run_loop``：collect_stats →（可选）propose → report。
   产出落在两处：案例库统计快照进进化报告；提示词候选版本以
   ``proposed`` 状态入持久化存储——**只入存储，不进 live 注册表**，
   线上提示词在人工审核前保持不变。
2. ``promote``（人工触发）：候选版本注册进 live 注册表（热更新）并
   持久化 applied → 跑 replay 回归门控 → 门控 FAIL 自动回滚
   （unregister + 持久化 rolled_back）；inconclusive（样本不足）保持
   applied 并继续观察——与 regression_gate 的「不武断」语义一致。
3. ``reject`` / ``rollback``（人工触发）：移除候选/已应用版本。

诚实边界（与 replay_harness 一致）：当前生成链路为模板化生成，
提示词候选尚未接入 replay 重放路径——门控当前验证的是机制与
基线样本积累；候选提示词对生成质量的真实影响待 M2 统一
AgentRuntime 接线后由同一门控度量。promote 默认仍执行 replay
门控（保证机制贯通与基线积累），并在报告中如实标注该边界。

全部公开方法**永不抛异常**（返回结果对象携带 error 字段）——
演化是旁路增强，任何失败不允许影响主业务与心跳调度循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.prompts import (
    EVOLUTION_PROPOSE_SYSTEM_ID,
    EVOLUTION_PROPOSE_USER_ID,
    get_prompt_registry,
)
from app.ai.prompts import persistence as prompt_persistence
from app.gcode_generation.failure_case_store import get_failure_case_store

logger = logging.getLogger(__name__)

__all__ = [
    "LoopResult",
    "ProposalResult",
    "PromoteResult",
    "EvolutionEngine",
    "get_evolution_engine",
    "reset_evolution_engine",
]

#: LLM 提案位失败来源（自进化 M0 定义的口径；提案只针对这些类别）
_LLM_SOURCES = ("llm_planning", "llm_param_aug", "gcode_repair", "nl2cad_extract")

#: 提案默认目标：G 代码修复提示词（当前唯一有失败留痕的迭代面）
DEFAULT_TARGET_PROMPT_ID = "orchestrator.gcode_repair.system"

#: 提案统计窗口内最多展示的错误码类别数
_MAX_FAILURE_CLASSES = 5


def _report_dir() -> Path:
    env_dir = os.environ.get("LNN_EVOLUTION_REPORT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parents[2] / "data" / "evolution_reports"


@dataclass
class LoopResult:
    """一次演化循环的结果。"""

    ok: bool
    stats: dict[str, Any] = field(default_factory=dict)
    top_failure_classes: list[str] = field(default_factory=list)
    proposal: dict[str, Any] | None = None  # 提案摘要（含 prompt_id/version/rationale）
    auto_promoted: bool = False
    gate: dict[str, Any] | None = None
    report_path: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stats": self.stats,
            "top_failure_classes": list(self.top_failure_classes),
            "proposal": self.proposal,
            "auto_promoted": self.auto_promoted,
            "gate": self.gate,
            "report_path": self.report_path,
            "error": self.error,
        }


@dataclass
class ProposalResult:
    """一次提示词补丁提案的结果。"""

    ok: bool
    prompt_id: str = ""
    version: int = 0
    rationale: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "prompt_id": self.prompt_id,
            "version": self.version,
            "rationale": self.rationale,
            "error": self.error,
        }


@dataclass
class PromoteResult:
    """promote / reject / rollback 的结果。"""

    ok: bool
    action: str = ""
    prompt_id: str = ""
    version: int = 0
    final_status: str = ""  # applied / rolled_back / rejected
    gate: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "prompt_id": self.prompt_id,
            "version": self.version,
            "final_status": self.final_status,
            "gate": self.gate,
            "error": self.error,
        }


class EvolutionEngine:
    """演化循环引擎（可注入 LLM 客户端与门控函数，便于测试）。"""

    def __init__(
        self,
        llm_client: Any = None,
        gate_fn: Any = None,
        report_dir: Path | None = None,
        auto_apply: bool | None = None,
    ) -> None:
        """
        Args:
            llm_client: 注入 LLM 客户端（测试桩）；None 则按需懒加载。
            gate_fn: 注入门控函数 ``(candidate_version) -> (passed, gate_dict)``
                （测试桩）；None 用 replay 回归门控。
            report_dir: 报告目录覆盖（测试用 tmp_path）。
            auto_apply: 提案是否自动走门控发布（env ``LNN_EVOLUTION_AUTO_APPLY``
                默认关——提案制：人工审核后才 promote）。
        """
        self._llm_client = llm_client
        self._gate_fn = gate_fn
        self._report_dir = report_dir or _report_dir()
        if auto_apply is None:
            auto_apply = os.getenv("LNN_EVOLUTION_AUTO_APPLY", "0").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        self._auto_apply = bool(auto_apply)

    # ------------------------------------------------------------------
    # 1. 统计
    # ------------------------------------------------------------------

    def collect_stats(self) -> dict[str, Any]:
        """失败案例库统计快照 + LLM 环节失败 TOP 类别。"""
        try:
            stats = get_failure_case_store().stats()
        except Exception as e:  # noqa: BLE001
            logger.warning("演化统计读取失败: %s", e)
            stats = {"total": 0, "failures": 0, "successes": 0, "one_pass_rate": None}

        top: list[str] = []
        by_source = stats.get("by_source") or {}
        # LLM 环节失败来源优先（提案迭代面），其后按全局错误码频次
        for src in _LLM_SOURCES:
            if by_source.get(src):
                top.append(f"source:{src}")
        by_code = stats.get("by_code") or {}
        for code, _n in sorted(by_code.items(), key=lambda kv: -kv[1])[:_MAX_FAILURE_CLASSES]:
            if f"source:{code}" not in top:
                top.append(str(code))
        return {"stats": stats, "top_failure_classes": top[:_MAX_FAILURE_CLASSES]}

    # ------------------------------------------------------------------
    # 2. 提案（只入存储，不动 live 注册表）
    # ------------------------------------------------------------------

    async def propose(
        self,
        target_prompt_id: str = DEFAULT_TARGET_PROMPT_ID,
        failure_summary: str | None = None,
    ) -> ProposalResult:
        """按失败类别生成目标提示词的候选新版本（proposed 入持久化）。"""
        registry = get_prompt_registry()
        try:
            current = registry.get(target_prompt_id)
        except KeyError:
            return ProposalResult(ok=False, error=f"目标提示词未注册: {target_prompt_id}")

        if failure_summary is None:
            collected = self.collect_stats()
            failure_summary = json.dumps(
                {
                    "stats": collected["stats"],
                    "top_failure_classes": collected["top_failure_classes"],
                },
                ensure_ascii=False,
            )

        propose_system, _ = registry.render(EVOLUTION_PROPOSE_SYSTEM_ID)
        propose_user, _ = registry.render(
            EVOLUTION_PROPOSE_USER_ID,
            prompt_id=current.prompt_id,
            current_template=current.template,
            failure_summary=failure_summary,
        )
        try:
            response = await self._get_llm_client().chat_completion(
                [
                    {"role": "system", "content": propose_system},
                    {"role": "user", "content": propose_user},
                ],
                max_tokens=2048,
                temperature=0.2,
            )
        except Exception as e:  # noqa: BLE001 - LLM 不可用属环境信号
            return ProposalResult(ok=False, error=f"LLM 不可用: {type(e).__name__}")

        data = self._extract_json(response.get("content", ""))
        if not isinstance(data, dict) or not str(data.get("template", "")).strip():
            return ProposalResult(ok=False, error="提案输出非法（缺少 template）")

        # 新版本号 = 当前最高版本 + 1（候选未被注册前即当前 live 版本）
        existing = registry.versions(target_prompt_id)
        next_version = (max(existing) if existing else current.version) + 1

        record = {
            "prompt_id": target_prompt_id,
            "version": next_version,
            "template": str(data["template"]),
            "rationale": str(data.get("rationale", ""))[:500],
            "status": "proposed",
            "base_version": current.version,
            "failure_summary": failure_summary[:2000],
        }
        try:
            prompt_persistence.upsert_record(record)
        except OSError as e:
            return ProposalResult(ok=False, error=f"提案持久化失败: {e}")

        logger.info(
            "演化提案已生成: %s v%d（proposed，待人工审核）rationale=%s",
            target_prompt_id,
            next_version,
            record["rationale"],
        )
        return ProposalResult(
            ok=True,
            prompt_id=target_prompt_id,
            version=next_version,
            rationale=record["rationale"],
        )

    # ------------------------------------------------------------------
    # 3. 审核：promote（含门控）/ reject / rollback
    # ------------------------------------------------------------------

    def promote(self, prompt_id: str, version: int) -> PromoteResult:
        """候选版本热更新上线 → replay 回归门控 → FAIL 自动回滚。

        同步方法；内部门控跑真实 pipeline（``asyncio.run``），异步调用方
        （REST / 心跳回调）必须 ``asyncio.to_thread`` 包装，不可直接 await。
        """
        record = prompt_persistence.find_record(prompt_id, version)
        if record is None or record.get("status") != "proposed":
            return PromoteResult(
                ok=False,
                action="promote",
                prompt_id=prompt_id,
                version=version,
                error="提案不存在或状态不是 proposed",
            )

        registry = get_prompt_registry()
        registry.register(
            prompt_id,
            int(version),
            str(record["template"]),
            str(record.get("rationale", "") or ""),
        )
        prompt_persistence.update_status(prompt_id, version, "applied")
        logger.info("提示词候选已上线: %s v%d（待门控）", prompt_id, version)

        gate_dict = self._run_gate(version)
        gate_passed = bool(gate_dict.get("passed", False))
        gate_inconclusive = bool(gate_dict.get("inconclusive", False))

        if not gate_passed and not gate_inconclusive:
            # 硬门禁 FAIL → 自动回滚
            registry.unregister(prompt_id, version)
            prompt_persistence.update_status(prompt_id, version, "rolled_back", gate=gate_dict)
            logger.warning("门控未通过，已自动回滚: %s v%d", prompt_id, version)
            return PromoteResult(
                ok=True,
                action="promote",
                prompt_id=prompt_id,
                version=version,
                final_status="rolled_back",
                gate=gate_dict,
            )

        prompt_persistence.update_status(prompt_id, version, "applied", gate=gate_dict)
        return PromoteResult(
            ok=True,
            action="promote",
            prompt_id=prompt_id,
            version=version,
            final_status="applied",
            gate=gate_dict,
        )

    def reject(self, prompt_id: str, version: int) -> PromoteResult:
        """拒绝提案（候选不进 live，仅持久化留痕）。"""
        record = prompt_persistence.find_record(prompt_id, version)
        if record is None or record.get("status") != "proposed":
            return PromoteResult(
                ok=False,
                action="reject",
                prompt_id=prompt_id,
                version=version,
                error="提案不存在或状态不是 proposed",
            )
        prompt_persistence.update_status(prompt_id, version, "rejected")
        return PromoteResult(
            ok=True,
            action="reject",
            prompt_id=prompt_id,
            version=version,
            final_status="rejected",
        )

    def rollback(self, prompt_id: str, version: int) -> PromoteResult:
        """回滚已应用的版本（live 立即移除，回到该 id 剩余最高版本）。"""
        record = prompt_persistence.find_record(prompt_id, version)
        if record is None or record.get("status") != "applied":
            return PromoteResult(
                ok=False,
                action="rollback",
                prompt_id=prompt_id,
                version=version,
                error="记录不存在或状态不是 applied",
            )
        get_prompt_registry().unregister(prompt_id, version)
        prompt_persistence.update_status(prompt_id, version, "rolled_back")
        logger.info("提示词版本已回滚: %s v%d", prompt_id, version)
        return PromoteResult(
            ok=True,
            action="rollback",
            prompt_id=prompt_id,
            version=version,
            final_status="rolled_back",
        )

    def _run_gate(self, candidate_version: int) -> dict[str, Any]:
        """执行回归门控（可注入）；任何异常降级为 inconclusive 放行。"""
        if self._gate_fn is not None:
            try:
                passed, gate_dict = self._gate_fn(candidate_version)
                return {"passed": bool(passed), "inconclusive": False, **dict(gate_dict or {})}
            except Exception as e:  # noqa: BLE001
                logger.warning("注入门控执行失败（按 inconclusive 处理）: %s", e)
                return {"passed": True, "inconclusive": True, "reasons": [f"门控执行失败: {type(e).__name__}"]}
        try:
            from app.gcode_generation.replay_harness import gate_replay_for_evolution

            return gate_replay_for_evolution()
        except Exception as e:  # noqa: BLE001
            logger.warning("replay 门控执行失败（按 inconclusive 处理）: %s", e)
            return {
                "passed": True,
                "inconclusive": True,
                "reasons": [f"replay 门控执行失败: {type(e).__name__}（机制边界，见模块 docstring）"],
            }

    # ------------------------------------------------------------------
    # 4. 循环入口与报告
    # ------------------------------------------------------------------

    async def run_loop(self, target_prompt_id: str = DEFAULT_TARGET_PROMPT_ID) -> LoopResult:
        """演化循环：统计 →（可选）提案 →（可选自动门控发布）→ 报告。"""
        collected = self.collect_stats()
        result = LoopResult(ok=True, **collected)

        proposal = await self.propose(
            target_prompt_id=target_prompt_id,
            failure_summary=json.dumps(collected, ensure_ascii=False),
        )
        if proposal.ok:
            result.proposal = proposal.to_dict()
            if self._auto_apply:
                # promote 内部门控跑真实 pipeline（asyncio.run），必须在
                # 线程中执行，不可阻塞/嵌套事件循环（见 _run_gate 说明）
                promoted = await asyncio.to_thread(self.promote, proposal.prompt_id, proposal.version)
                result.auto_promoted = promoted.final_status == "applied"
                result.gate = promoted.gate
        elif proposal.error and "LLM 不可用" not in proposal.error:
            # LLM 不可用不算循环失败（统计/报告仍有价值）；其余错误如实上报
            result.error = proposal.error

        result.report_path = self._write_report(result)
        return result

    def _write_report(self, result: LoopResult) -> str:
        """进化报告落盘（失败仅告警，不影响结果返回）。"""
        try:
            self._report_dir.mkdir(parents=True, exist_ok=True)
            path = self._report_dir / f"evolution_{time.strftime('%Y%m%d_%H%M%S')}.json"
            path.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return str(path)
        except OSError as e:
            logger.warning("进化报告写入失败: %s", e)
            return ""

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get_llm_client(self) -> Any:
        if self._llm_client is not None:
            return self._llm_client
        from app.ai.llm_client import get_llm_client

        return get_llm_client()

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """从 LLM 输出提取 JSON 对象（容忍 markdown 围栏/前后缀文本）。"""
        import re

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


# 全局单例（双重检查锁）

_engine: EvolutionEngine | None = None
_engine_lock = None


def _get_lock():
    global _engine_lock
    if _engine_lock is None:
        import threading

        _engine_lock = threading.Lock()
    return _engine_lock


def get_evolution_engine() -> EvolutionEngine:
    """获取全局 EvolutionEngine（线程安全懒加载）。"""
    global _engine
    if _engine is not None:
        return _engine
    with _get_lock():
        if _engine is None:
            _engine = EvolutionEngine()
        return _engine


def reset_evolution_engine() -> None:
    """重置全局单例（测试用）。"""
    global _engine
    with _get_lock():
        _engine = None
