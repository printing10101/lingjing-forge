"""采纳台账（自进化 M1 · 谁批的、凭什么批的）。

Harness² 的原始设计里，编辑的成败由一个自动选择器决定。本项目不采用
自动采纳：G 代码直接进机床，任何未经人确认的提示词改动都可能变成物理
事故。因此这里把「采纳」做成一条**带签字的数据记录**，规则如下：

- ``mechanism`` 为空 → 一律拒绝（连数字都不看）。
- ``n < min_samples`` → 强制 hold（样本不足不看结论）。
- ``delta <= 0`` → reject。
- ``p < alpha`` 且 ``delta > 0`` → adopt（仍须 reviewer 签字）。
- 其余 → hold。

记录追加写入 JSONL，只增不改，供审计回溯。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.harness.ab_runner import ABResult

__all__ = ["AdoptionRecord", "AdoptionLedger", "decide", "get_adoption_ledger"]

#: 低于该样本量不做结论（配对检验在小样本下极易假阳性）
MIN_SAMPLES = 10
#: 显著性阈值
ALPHA = 0.05

DECISION_ADOPT = "adopt"
DECISION_HOLD = "hold"
DECISION_REJECT = "reject"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AdoptionRecord:
    """一条采纳决策记录。"""

    harness_id: str
    baseline_id: str
    n: int
    delta: float
    p_value: float
    decision: str
    mechanism: str
    reviewer: str
    reason: str = ""
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision not in {DECISION_ADOPT, DECISION_HOLD, DECISION_REJECT}:
            raise ValueError(f"非法决策: {self.decision!r}")
        if not self.reviewer.strip():
            raise ValueError("reviewer 不能为空：采纳必须有签字人")
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "baseline_id": self.baseline_id,
            "n": self.n,
            "delta": round(self.delta, 4),
            "p_value": round(self.p_value, 6),
            "decision": self.decision,
            "mechanism": self.mechanism,
            "reviewer": self.reviewer,
            "reason": self.reason,
            "created_at": self.created_at,
            **self.extra,
        }


def decide(result: ABResult, *, mechanism: str, min_samples: int = MIN_SAMPLES, alpha: float = ALPHA) -> str:
    """按固定规则给出建议决策（不含签字）。"""
    if not mechanism or not mechanism.strip():
        return DECISION_REJECT
    if result.n < min_samples:
        return DECISION_HOLD
    if result.delta <= 0:
        return DECISION_REJECT
    if result.p_value < alpha:
        return DECISION_ADOPT
    return DECISION_HOLD


class AdoptionLedger:
    """JSONL 追加式台账。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else _default_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        result: ABResult,
        *,
        mechanism: str,
        reviewer: str,
        reason: str = "",
        decision: str | None = None,
    ) -> AdoptionRecord:
        """写入一条决策；``decision`` 缺省时按规则自动给出建议值。"""
        chosen = decision or decide(result, mechanism=mechanism)
        rec = AdoptionRecord(
            harness_id=result.candidate_id,
            baseline_id=result.baseline_id,
            n=result.n,
            delta=result.delta,
            p_value=result.p_value,
            decision=chosen,
            mechanism=mechanism,
            reviewer=reviewer,
            reason=reason,
            extra={
                "only_baseline": result.only_baseline,
                "only_candidate": result.only_candidate,
                "both_fail": result.both_fail,
            },
        )
        self._append(rec)
        return rec

    def _append(self, rec: AdoptionRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    def list_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def _default_path() -> Path:
    """约定 ``python/data/harness_adoptions.jsonl``，``HARNESS_LEDGER`` 可覆盖。"""
    env_path = os.environ.get("HARNESS_LEDGER")
    if env_path:
        return Path(env_path)
    python_dir = Path(__file__).resolve().parents[3]
    data_dir = python_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "harness_adoptions.jsonl"


_ledger: AdoptionLedger | None = None
_ledger_lock = threading.Lock()


def get_adoption_ledger() -> AdoptionLedger:
    global _ledger
    if _ledger is None:
        with _ledger_lock:
            if _ledger is None:
                _ledger = AdoptionLedger()
    return _ledger
