"""持久化与查询方法组：状态存取/历史/统计。"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.dreaming._closed_loop_models import (
    ClosedLoopDecision,
    RuleOutcomeRecord,
)

logger = logging.getLogger(__name__)


class _ClosedLoopPersistenceMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _decision_history: Any
    _demote_confidence: Any
    _fusion: Any
    _lock: Any
    _max_conflict_for_promote: Any
    _min_samples_for_decision: Any
    _promote_confidence: Any
    _router: Any
    _state_dir: Any
    _window_size: Any
    _windows: Any

    def save_state(self) -> None:
        """将当前窗口与决策历史持久化到磁盘。"""
        state: dict[str, Any] = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "windows": {},
            "decision_history": {},
        }
        with self._lock:
            for rule_id, window in self._windows.items():
                state["windows"][rule_id] = [
                    {
                        "rule_id": r.rule_id,
                        "success": r.success,
                        "confidence": r.confidence,
                        "source": r.source,
                        "recorded_at": r.recorded_at,
                    }
                    for r in window
                ]
            for rule_id, history in self._decision_history.items():
                state["decision_history"][rule_id] = [d.to_dict() for d in history]

        try:
            state_file = self._state_dir / "closed_loop_state.json"
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info("ClosedLoop: 状态已保存到 %s", state_file)
        except OSError as e:
            logger.warning("ClosedLoop: 状态保存失败：%s", e)

    def load_state(self) -> None:
        """从磁盘恢复窗口与决策历史。"""
        state_file = self._state_dir / "closed_loop_state.json"
        if not state_file.exists():
            return
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)

            with self._lock:
                self._windows.clear()
                for rule_id, samples in state.get("windows", {}).items():
                    window: deque[RuleOutcomeRecord] = deque(maxlen=self._window_size)
                    for s in samples:
                        window.append(
                            RuleOutcomeRecord(
                                rule_id=s["rule_id"],
                                success=s["success"],
                                confidence=s["confidence"],
                                source=s.get("source", "restored"),
                                recorded_at=s.get(
                                    "recorded_at",
                                    datetime.now(timezone.utc).isoformat(),
                                ),
                            )
                        )
                    self._windows[rule_id] = window

                self._decision_history.clear()
                for rule_id, history in state.get("decision_history", {}).items():
                    self._decision_history[rule_id] = [
                        ClosedLoopDecision(
                            rule_id=d["rule_id"],
                            action=d["action"],
                            target_stage=d.get("target_stage"),
                            reason=d.get("reason", ""),
                            fused_confidence=d.get("fused_confidence", 0.0),
                            conflict=d.get("conflict", 0.0),
                            ds_mass=d.get("ds_mass", 0.0),
                            sample_count=d.get("sample_count", 0),
                            evaluated_at=d.get(
                                "evaluated_at",
                                datetime.now(timezone.utc).isoformat(),
                            ),
                            applied=d.get("applied", False),
                            apply_error=d.get("apply_error"),
                        )
                        for d in history
                    ]
            logger.info(
                "ClosedLoop: 状态已恢复（%d 条规则，%d 个样本）",
                len(self._windows),
                sum(len(w) for w in self._windows.values()),
            )
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("ClosedLoop: 状态恢复失败：%s", e)

    def _persist_iteration(self, decisions: list[ClosedLoopDecision]) -> None:
        """持久化单次迭代结果到 JSON 文件。"""
        if not decisions:
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self._state_dir / f"iteration_{timestamp}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "iteration_at": datetime.now(timezone.utc).isoformat(),
                        "decision_count": len(decisions),
                        "decisions": [d.to_dict() for d in decisions],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.debug("ClosedLoop: 迭代结果已持久化 %s", output_file)
        except OSError as e:
            logger.warning("ClosedLoop: 迭代结果持久化失败（不影响决策）：%s", e)

    def get_decision_history(self, rule_id: str, limit: int = 10) -> list[ClosedLoopDecision]:
        """获取指定规则的决策历史。"""
        with self._lock:
            history = self._decision_history.get(rule_id, [])
            return list(history[-limit:])

    def get_window_samples(self, rule_id: str) -> list[RuleOutcomeRecord]:
        """获取指定规则的当前窗口样本。"""
        with self._lock:
            window = self._windows.get(rule_id, deque(maxlen=self._window_size))
            return list(window)

    def get_stats(self) -> dict[str, Any]:
        """获取闭环整体统计信息。"""
        with self._lock:
            total_samples = sum(len(w) for w in self._windows.values())
            rule_count = len(self._windows)
            total_decisions = sum(len(h) for h in self._decision_history.values())
        return {
            "tracked_rule_count": rule_count,
            "total_samples": total_samples,
            "total_decisions": total_decisions,
            "window_size": self._window_size,
            "promote_confidence": self._promote_confidence,
            "demote_confidence": self._demote_confidence,
            "max_conflict_for_promote": self._max_conflict_for_promote,
            "min_samples_for_decision": self._min_samples_for_decision,
            "fusion_available": self._fusion is not None,
            "router_available": self._router is not None,
        }
