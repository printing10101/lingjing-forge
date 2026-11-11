"""Harness 层自检（不接模型、不碰数据库、不写生产数据）。

跑法::

    PYTHONPATH=D:/灵境制造/engineering/python python -m app.agent.harness.selfcheck

覆盖：快照指纹稳定性、mechanism 强校验、McNemar 精确检验、配对统计、
决策规则、台账追加。全部通过打印 ``SELFCHECK PASS``。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.agent.harness.ab_runner import ABRunner, ReplayExecutor, TaskCase, mcnemar_exact
from app.agent.harness.ledger import AdoptionLedger, decide
from app.agent.harness.snapshot import HarnessRegistry, HarnessSnapshot

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, bool(cond), detail))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="harness_selfcheck_"))

    # 1. 指纹：内容相同则指纹相同，时间戳不参与
    a = HarnessSnapshot(harness_id="h-a", prompt_versions={"p.one": 1, "p.two": 2}, mechanism="baseline")
    b = HarnessSnapshot(harness_id="h-b", prompt_versions={"p.two": 2, "p.one": 1}, mechanism="baseline")
    check("指纹与键序无关", a.fingerprint() == b.fingerprint(), a.fingerprint())

    c = HarnessSnapshot(harness_id="h-c", prompt_versions={"p.one": 1, "p.two": 3}, mechanism="baseline")
    check("版本变化则指纹变化", a.fingerprint() != c.fingerprint())

    # 2. mechanism 必填
    try:
        HarnessSnapshot(harness_id="h-bad", prompt_versions={}, mechanism="")
        check("缺 mechanism 应报错", False)
    except ValueError:
        check("缺 mechanism 应报错", True)

    # 3. 注册表读写（临时文件）
    reg = HarnessRegistry(path=tmp / "snapshots.json")
    reg.register(a)
    reg.set_current("h-a")
    check("注册表回读一致", reg.get("h-a").fingerprint() == a.fingerprint())
    check("current 可读", reg.current() is not None and reg.current().harness_id == "h-a")
    try:
        dup = HarnessSnapshot(harness_id="h-a", prompt_versions={"p.one": 9}, mechanism="另一个机制")
        reg.register(dup)
        check("同名不同义应拒绝", False)
    except ValueError:
        check("同名不同义应拒绝", True)

    # 4. McNemar 精确检验（已知值：b=0,c=10 → 2*(1/2^10)=0.001953）
    p = mcnemar_exact(0, 10)
    check("McNemar b=0,c=10 ≈ 0.00195", abs(p - 0.001953) < 1e-5, f"{p:.6f}")
    check("McNemar 无差异时 p=1", mcnemar_exact(0, 0) == 1.0)
    check("McNemar 对称", abs(mcnemar_exact(3, 8) - mcnemar_exact(8, 3)) < 1e-12)

    # 5. 配对统计：20 个样本，候选多通过 6 个、少通过 1 个
    base_snap = HarnessSnapshot(harness_id="h-base", prompt_versions={"p.one": 1}, mechanism="基线")
    cand_snap = HarnessSnapshot(
        harness_id="h-cand",
        prompt_versions={"p.one": 2},
        mechanism="工具描述补『先校验控制器方言再做圆弧插补』，预期减少方言类解析失败",
    )
    cases = [TaskCase(case_id=f"case-{i:02d}", source="llm_planning") for i in range(20)]
    # baseline 在 0-11 通过；candidate 在 0-6 和 12-17 通过
    table: dict[tuple[str, str], str] = {}
    for i in range(20):
        cid = f"case-{i:02d}"
        table[("h-base", cid)] = "PASS" if i < 12 else "FAIL"
        table[("h-cand", cid)] = "PASS" if (i < 7 or 12 <= i < 18) else "FAIL"

    runner = ABRunner(executor=ReplayExecutor(table), judge=lambda _case, out: out == "PASS")
    res = runner.run(base_snap, cand_snap, cases)

    check("配对 n 正确", res.n == 20, str(res.n))
    check("baseline 通过 12", res.baseline_pass == 12, str(res.baseline_pass))
    check("candidate 通过 13", res.candidate_pass == 13, str(res.candidate_pass))
    check("仅候选通过 = 6", res.only_candidate == 6, str(res.only_candidate))
    check("仅基线通过 = 5", res.only_baseline == 5, str(res.only_baseline))
    check("Δ = +5pp", abs(res.delta - 0.05) < 1e-9, f"{res.delta:.4f}")
    check("p 值在 (0,1)", 0 < res.p_value <= 1, f"{res.p_value:.4f}")

    # 6. 决策规则
    check("缺 mechanism → reject", decide(res, mechanism="") == "reject")
    check("样本不足 → hold", decide(res, mechanism="x", min_samples=50) == "hold")
    small = runner.run(base_snap, cand_snap, cases[:5])
    check("n=5 → hold", decide(small, mechanism="x") == "hold", str(small.n))
    neg_table: dict[tuple[str, str], str] = {}
    for i in range(20):
        key = f"case-{i:02d}"
        neg_table[("h-base", key)] = "PASS" if i < 12 else "FAIL"
        neg_table[("h-cand", key)] = "FAIL"
    res_neg = ABRunner(executor=ReplayExecutor(neg_table), judge=lambda _c, o: o == "PASS").run(
        base_snap, cand_snap, cases
    )
    check("Δ<0 → reject", decide(res_neg, mechanism="x") == "reject", f"{res_neg.delta:.4f}")

    # 7. 台账（临时文件，不碰生产数据）
    ledger = AdoptionLedger(path=tmp / "adoptions.jsonl")
    rec = ledger.record(res, mechanism=cand_snap.mechanism, reviewer="selfcheck", decision="hold")
    check("台账已写入", (tmp / "adoptions.jsonl").exists())
    check("台账可回读", len(ledger.list_all()) == 1)
    check("决策被记录", rec.decision == "hold" and rec.reviewer == "selfcheck")
    try:
        ledger.record(res, mechanism="x", reviewer="  ")
        check("reviewer 必填", False)
    except ValueError:
        check("reviewer 必填", True)

    # 8. 执行异常不击穿
    def boom(_case, _snap):
        raise RuntimeError("模拟执行失败")

    res2 = ABRunner(executor=boom, judge=lambda _c, out: out == "PASS").run(base_snap, cand_snap, cases[:3])
    check("执行异常计为未通过而非崩溃", res2.baseline_pass == 0 and res2.n == 3)

    # 汇总
    print()
    for name, ok, detail in _results:
        mark = "OK  " if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"[{mark}] {name}{suffix}")
    failed = [r for r in _results if not r[1]]
    print()
    if failed:
        print(f"SELFCHECK FAIL — {len(failed)}/{len(_results)} 项未通过")
        return 1
    print(f"SELFCHECK PASS — {len(_results)}/{len(_results)}")
    print(f"示例对照表:\n{res.to_markdown()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
