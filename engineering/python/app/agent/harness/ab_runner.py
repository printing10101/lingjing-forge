"""Harness A/B 对照器（自进化 M1 · 用真实通过率代替行为差异）。

Harness² 原论文在缺少标准答案的开放任务里，只能靠「两次执行的行为差异」
判断改动好坏。本项目的条件更好：G 代码能否通过体素仿真与工艺校验是
**确定性判定**，所以这里直接用配对通过率 + McNemar 精确检验，信号比原
论文更硬。

设计约束：
- executor / judge 均为可注入的纯函数，本模块不依赖 LLM 与 CAM 内核，
  因此可在无模型环境下自测（见 ``ReplayExecutor``）。
- 配对统计：同一 case 分别用 baseline / candidate 跑，构成 2×2 配对表，
  只对「结论不一致」的样本（b、c）做检验，与 McNemar 原意一致。
- 不自动采纳。``ABRunner`` 只出数字，采不采纳由 ``ledger`` 与人决定。

零第三方依赖，仅标准库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "ABResult",
    "ABRunner",
    "CaseOutcome",
    "ReplayExecutor",
    "TaskCase",
    "mcnemar_exact",
]


@dataclass(frozen=True)
class TaskCase:
    """一个可重放的任务样本。

    Attributes:
        case_id: 唯一标识（可直接复用 ``FailureCase.case_id``）。
        source: 失败来源（``llm_planning`` / ``gcode_repair`` 等），用于分层。
        payload: 送入 executor 的输入（如原始需求 + 控制器类型 + 材料）。
        error_codes: 历史错误码，便于按失败模式抽样。
    """

    case_id: str
    source: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseOutcome:
    """单条样本的判定结果。"""

    case_id: str
    passed: bool
    detail: str = ""


class Executor(Protocol):
    """在某个 harness 快照下执行一次任务，返回可判定的输出。"""

    def __call__(self, case: TaskCase, snapshot: Any) -> str: ...


class Judge(Protocol):
    """判定输出是否通过（对应确定性校验链，而非 LLM 打分）。"""

    def __call__(self, case: TaskCase, output: str) -> bool: ...


def mcnemar_exact(b: int, c: int) -> float:
    """McNemar 精确检验（二项版本）的双侧 p 值。

    Args:
        b: baseline 通过、candidate 未通过的样本数。
        c: baseline 未通过、candidate 通过的样本数。

    只统计结论不一致的对子；b + c == 0 时返回 1.0（无差异证据）。
    样本量小时用精确二项分布，避免卡方近似在 n < 25 时失真。
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


@dataclass
class ABResult:
    """一次 A/B 对照的完整结果。"""

    baseline_id: str
    candidate_id: str
    n: int
    baseline_pass: int
    candidate_pass: int
    both_pass: int
    both_fail: int
    only_baseline: int
    only_candidate: int
    p_value: float
    outcomes: list[tuple[CaseOutcome, CaseOutcome]] = field(default_factory=list)

    @property
    def baseline_rate(self) -> float:
        return self.baseline_pass / self.n if self.n else 0.0

    @property
    def candidate_rate(self) -> float:
        return self.candidate_pass / self.n if self.n else 0.0

    @property
    def delta(self) -> float:
        """通过率的绝对差（百分点需 ×100）。"""
        return self.candidate_rate - self.baseline_rate

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "candidate_id": self.candidate_id,
            "n": self.n,
            "baseline_pass": self.baseline_pass,
            "candidate_pass": self.candidate_pass,
            "baseline_rate": round(self.baseline_rate, 4),
            "candidate_rate": round(self.candidate_rate, 4),
            "delta": round(self.delta, 4),
            "both_pass": self.both_pass,
            "both_fail": self.both_fail,
            "only_baseline": self.only_baseline,
            "only_candidate": self.only_candidate,
            "p_value": round(self.p_value, 6),
            "significant": self.significant,
        }

    def to_markdown(self) -> str:
        """便于直接贴进 issue / 对话的对照表。"""
        rows = [
            f"| {self.baseline_id} | {self.baseline_pass}/{self.n} | {self.baseline_rate:.1%} |",
            f"| {self.candidate_id} | {self.candidate_pass}/{self.n} | {self.candidate_rate:.1%} |",
        ]
        sign = "+" if self.delta >= 0 else ""
        verdict = "显著" if self.significant else "不显著"
        return "\n".join(
            [
                "| harness | 通过 | 一次通过率 |",
                "|---|---|---|",
                *rows,
                "",
                f"配对表：both_pass={self.both_pass} · both_fail={self.both_fail} · "
                f"仅基线通过={self.only_baseline} · 仅候选通过={self.only_candidate}",
                f"Δ = {sign}{self.delta * 100:.1f} pp，McNemar 精确检验 p = {self.p_value:.4f}（{verdict}）",
            ]
        )


class ABRunner:
    """配对 A/B 对照执行器。

    Example:
        >>> runner = ABRunner(executor=my_exec, judge=my_judge)
        >>> result = runner.run(base_snap, cand_snap, cases)
        >>> print(result.to_markdown())
    """

    def __init__(self, executor: Executor, judge: Judge) -> None:
        self._executor = executor
        self._judge = judge

    def run(self, baseline: Any, candidate: Any, cases: list[TaskCase]) -> ABResult:
        """对同一批 cases 分别跑两套快照，逐条配对统计。"""
        if not cases:
            raise ValueError("cases 为空")
        outcomes: list[tuple[CaseOutcome, CaseOutcome]] = []
        both_pass = both_fail = only_base = only_cand = 0
        base_pass = cand_pass = 0

        for case in cases:
            base_out = self._safe_run(case, baseline)
            cand_out = self._safe_run(case, candidate)
            base_ok = bool(self._judge(case, base_out))
            cand_ok = bool(self._judge(case, cand_out))
            outcomes.append(
                (
                    CaseOutcome(case.case_id, base_ok, base_out[:200]),
                    CaseOutcome(case.case_id, cand_ok, cand_out[:200]),
                )
            )
            base_pass += int(base_ok)
            cand_pass += int(cand_ok)
            if base_ok and cand_ok:
                both_pass += 1
            elif base_ok and not cand_ok:
                only_base += 1
            elif cand_ok and not base_ok:
                only_cand += 1
            else:
                both_fail += 1

        return ABResult(
            baseline_id=_label(baseline),
            candidate_id=_label(candidate),
            n=len(cases),
            baseline_pass=base_pass,
            candidate_pass=cand_pass,
            both_pass=both_pass,
            both_fail=both_fail,
            only_baseline=only_base,
            only_candidate=only_cand,
            p_value=mcnemar_exact(only_base, only_cand),
            outcomes=outcomes,
        )

    def _safe_run(self, case: TaskCase, snapshot: Any) -> str:
        """执行失败视为「未通过」，而不是让整轮对照崩掉。

        与 ``failure_recorder`` 的旁路观测同一取向：实验器不能击穿调用方。
        """
        try:
            return str(self._executor(case, snapshot))
        except Exception as exc:  # noqa: BLE001
            return f"__EXEC_ERROR__:{type(exc).__name__}:{exc}"


class ReplayExecutor:
    """回放执行器：按 ``case_id`` 直接给出预置输出，用于离线自测与回归。

    典型用法：把历史 trace 里的真实输出灌进来，验证判定链与统计逻辑是否正确，
    不消耗任何推理算力。
    """

    def __init__(self, table: dict[tuple[str, str], str]) -> None:
        self._table = dict(table)

    def __call__(self, case: TaskCase, snapshot: Any) -> str:
        key = (getattr(snapshot, "harness_id", str(snapshot)), case.case_id)
        if key not in self._table:
            raise KeyError(f"回放表缺少 {key}")
        return self._table[key]


def _label(snapshot: Any) -> str:
    return str(getattr(snapshot, "harness_id", snapshot))
