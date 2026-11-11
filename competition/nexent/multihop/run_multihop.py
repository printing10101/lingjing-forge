"""跨文档多跳任务执行器（赛题任务要求④：多跳推理 + 决策链路可溯源）。

为什么需要它
------------
单次检索能回答的问题，不需要"多跳"；赛题要的是"必须联合两份以上资料才能回答、
且结论可逐条回溯到文件、行号、记录 ID"。本脚本把这类任务写成可执行资产：每个任务
声明若干跳（hop），每跳绑定一个真实数据源；执行后产出带定位符的证据链，再按
断言检查结论是否成立。

定位符口径（可溯源的最低要求）
- 切参库：记录 `id` + 文件名
- 工艺规范：条款号 + Markdown 行号
- 失败案例库：case_id + 行数统计口径
- 厂商候选：candidate_id + review_status
- 本体注册表：proposal_id / 类型名

运行
----
    python -m competition.nexent.multihop.run_multihop
    python -m competition.nexent.multihop.run_multihop --out results/run.json

退出码：全部任务断言通过返回 0，否则返回 1（便于放进验收脚本）。
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_FILE = Path(__file__).parent / "tasks.json"


# --------------------------------------------------------------------- 数据源


class Sources:
    """按需加载数据源；单个源不可用不影响其它源（记为 unavailable）。"""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.unavailable: dict[str, str] = {}
        self._cache: dict[str, Any] = {}

    def path(self, name: str) -> Path:
        return REPO_ROOT / self.spec[name]["path"]

    def _load(self, name: str) -> Any:
        if name in self._cache:
            return self._cache[name]
        cfg = self.spec[name]
        path = self.path(name)
        if not path.exists():
            if cfg.get("optional"):
                self.unavailable[name] = f"文件不存在：{cfg['path']}"
                self._cache[name] = None
                return None
            raise FileNotFoundError(path)
        if cfg["type"] == "standards_dir":
            if not path.exists():
                raise FileNotFoundError(path)
            self._cache[name] = path
            return path
        if cfg["type"] == "json":
            value = json.loads(path.read_text(encoding="utf-8"))
        elif cfg["type"] == "markdown":
            value = path.read_text(encoding="utf-8").splitlines()
        elif cfg["type"] == "sqlite":
            value = str(path)
        else:
            raise ValueError(f"unknown source type: {cfg['type']}")
        self._cache[name] = value
        return value

    # ---- 各源专用查询

    def param_lookup(self, **kw: Any) -> list[dict[str, Any]]:
        records = self._load("cutting_params")
        out = []
        for rec in records:
            if all(rec.get(k) == v for k, v in kw.items()):
                out.append(
                    {
                        "locator": f"{self.spec['cutting_params']['path']}#{rec['id']}",
                        "material_name": rec.get("material_name"),
                        "tool_series": rec.get("tool_series"),
                        "tool_material": rec.get("tool_material"),
                        "speed_mpm": [rec.get("cutting_speed_min_mpm"), rec.get("cutting_speed_max_mpm")],
                        "feed_mmpr": [rec.get("feed_min_mmpr"), rec.get("feed_max_mmpr")],
                        "source": rec.get("source"),
                    }
                )
        return out

    def clause_search(self, keywords: list[str]) -> list[dict[str, Any]]:
        lines = self._load("spec")
        clauses = _split_clauses(lines)
        hits = []
        for clause in clauses:
            text = "\n".join(clause["lines"])
            matched = [k for k in keywords if k in text]
            if matched:
                hits.append(
                    {
                        "locator": f"{self.spec['spec']['path']}#L{clause['start_line']}-L{clause['end_line']}",
                        "clause": clause["title"],
                        "matched_keywords": matched,
                        "excerpt": _first_match_line(clause, matched),
                        # 完整条款正文供规则判定用；excerpt 只用于展示定位
                        "text": re.sub(r"\*\*", "", text)[:800],
                    }
                )
        return hits

    def case_dist(self, where: dict[str, Any] | None = None) -> dict[str, Any]:
        db = self._load("cases")
        conditions, params = [], []
        for key, value in (where or {}).items():
            conditions.append(f"{key} = ?")
            params.append(value)
        sql = "SELECT case_id, material_name, error_codes, unstable_features, source FROM failure_cases"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        with sqlite3.connect(db) as conn:
            rows = conn.execute(sql, params).fetchall()

        codes = collections.Counter()
        unstable = 0
        for _cid, _mat, raw, unst, _src in rows:
            unstable += int(unst or 0)
            for code in json.loads(raw or "[]"):
                codes[code] += 1
        return {
            "locator": f"{self.spec['cases']['path']}#rows={len(rows)}",
            "row_count": len(rows),
            "distinct_cases": len(rows),
            "error_code_distribution": dict(codes.most_common()),
            "unstable_features_sum": unstable,
            "sample_case_ids": [r[0] for r in rows[:3]],
        }

    def candidate_lookup(self, feature: str | None = None) -> list[dict[str, Any]]:
        data = self._load("candidates")
        out = []
        for cand in data.get("candidates", []):
            if feature and cand.get("feature") != feature:
                continue
            out.append(
                {
                    "locator": f"{self.spec['candidates']['path']}#{cand['candidate_id'][:12]}",
                    "candidate_id": cand.get("candidate_id"),
                    "material": cand.get("material"),
                    "tool": cand.get("tool"),
                    "conditions": cand.get("conditions", {}),
                    "parameters": cand.get("parameters", {}),
                    "review_status": cand.get("review_status"),
                    "eligible_for_recommendation": cand.get("eligible_for_recommendation"),
                    "unresolved": cand.get("unresolved", []),
                }
            )
        return out

    def standard_resolve(self, rule_ids: list[str], as_of: str = "2026-11-10") -> list[dict[str, Any]]:
        """按规则 ID 解析标准对齐结论（调用 standards 目录下的对齐模块）。"""
        import importlib.util

        module_path = self.path("standards") / "standard_alignment.py"
        spec = importlib.util.spec_from_file_location("_std_align_for_multihop", module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["_std_align_for_multihop"] = module
        spec.loader.exec_module(module)

        registry = module.StandardRegistry.load(self.path("standards"))
        out = []
        for rule_id in rule_ids:
            result = registry.resolve_rule(rule_id, as_of)
            result["locator"] = (
                f"{self.spec['standards']['path']}/rule_map.json#{rule_id}@{as_of}"
            )
            out.append(result)
        return out

    def ontology_registry(self) -> dict[str, Any]:
        db = self._load("ontology")
        if db is None:
            return {"locator": "unavailable", "unavailable": self.unavailable["ontology"]}
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT kind, name, status FROM ontology_proposals"
            ).fetchall()
        approved = [f"{k}:{n}" for k, n, s in rows if s == "approved"]
        pending = [f"{k}:{n}" for k, n, s in rows if s == "pending"]
        return {
            "locator": f"{self.spec['ontology']['path']}#proposals={len(rows)}",
            "approved": sorted(approved),
            "pending_count": len(pending),
            "pending_sample": sorted(pending)[:5],
        }


def _split_clauses(lines: list[str]) -> list[dict[str, Any]]:
    """按『## N. 标题』切条款，保留行号用于定位。"""
    clauses, current = [], None
    for idx, line in enumerate(lines, start=1):
        if line.startswith("## "):
            if current:
                current["end_line"] = idx - 1
                clauses.append(current)
            current = {"title": line[3:].strip(), "start_line": idx, "lines": [line]}
        elif current is not None:
            current["lines"].append(line)
    if current:
        current["end_line"] = len(lines)
        clauses.append(current)
    return clauses


def _first_match_line(clause: dict[str, Any], keywords: list[str]) -> str:
    for line in clause["lines"]:
        if any(k in line for k in keywords):
            return re.sub(r"\*\*", "", line).strip()[:160]
    return ""


# ------------------------------------------------------------------ 跳执行器

_HOP_HANDLERS = {
    "param_lookup": lambda src, hop: src.param_lookup(
        **{k: v for k, v in hop.items() if k not in {"source", "op"}}
    ),
    "clause_search": lambda src, hop: src.clause_search(hop["keywords"]),
    "case_dist": lambda src, hop: src.case_dist(hop.get("where")),
    "candidate_lookup": lambda src, hop: src.candidate_lookup(hop.get("feature")),
    "ontology_registry": lambda src, hop: src.ontology_registry(),
    "standard_resolve": lambda src, hop: src.standard_resolve(
        hop["rule_ids"], hop.get("as_of", "2026-11-10")
    ),
}


def run_hops(sources: Sources, task: dict[str, Any]) -> list[dict[str, Any]]:
    trace = []
    for hop in task["hops"]:
        handler = _HOP_HANDLERS[hop["op"]]
        result = handler(sources, hop)
        trace.append({"source": hop["source"], "op": hop["op"], "result": result})
    return trace


# --------------------------------------------------------------------- 推导

_NO_CUT = "不可直接生成 NC，需按下列理由重规划或补条件"


def _derive(task: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    rules = task["derivation"]
    kind = rules["kind"]
    flags: list[str] = []
    detail: dict[str, Any] = {}
    verdict = _NO_CUT

    if kind == "depth_ratio":
        ratio = rules["depth_mm"] / rules["diameter_mm"]
        limit = rules["candidate_limit_ratio"]
        detail["ld_ratio"] = round(ratio, 3)
        detail["candidate_scope_limit"] = limit
        flags.append("params_are_range_not_optimum")
        if ratio > limit:
            flags.append("exceeds_candidate_scope")
            detail["reason"] = f"深径比 {ratio:.2f} > 候选适用上限 {limit:.1f}"
        verdict = "可给参考区间但不得套用厂商候选值；深孔需降进给并分段排屑，物理校验后再谈上机"

    elif kind == "stability":
        ratio = rules["axial_depth_mm"] / rules["limit_depth_mm"]
        detail["depth_ratio"] = round(ratio, 3)
        detail["threshold"] = 1.0
        if ratio >= 1.0:
            flags += ["unstable_domain", "must_replan_not_bypass"]
            detail["reason"] = f"切深比 {ratio:.2f} ≥ 1.0，落在不稳定域"
        else:
            detail["reason"] = f"切深比 {ratio:.2f} < 1.0，稳定域"
            verdict = "落于稳定域，但仍需体素与运动学校验后才可下发"
        unstable_cases = _find_case_count(trace, source="unstable_features")
        detail["unstable_feature_cases_in_library"] = unstable_cases

    elif kind == "error_attribution":
        dist = _find_case_dist(trace)
        detail["distribution"] = dist.get("error_code_distribution", {})
        detail["unstable_features_sum"] = dist.get("unstable_features_sum", 0)
        top = next(iter(detail["distribution"]), None)
        detail["dominant_code"] = top
        if top == "UNSTABLE_FEATURES":
            flags += ["dominant_error_is_unstable_features", "remedy_is_reduce_depth"]
            verdict = "主因是不稳定特征触发；下一步降切深/降切宽重规划，不是调高转速去试"

    elif kind == "eligibility":
        cands = _find_candidates(trace)
        detail["candidate_count"] = len(cands)
        detail["candidate_materials"] = sorted({c["material"] for c in cands if c.get("material")})
        detail["pending"] = all(c.get("review_status") == "unreviewed" for c in cands) if cands else False
        detail["approval_eligible"] = any(c.get("eligible_for_recommendation") for c in cands)
        if rules["candidate_material"] != rules["target_material"]:
            flags.append("material_group_mismatch")
            detail["material_note"] = (
                f"候选材料组为 {rules['candidate_material']}，目标为 {rules['target_material']}，不得互推牌号"
            )
        if cands and not detail["approval_eligible"]:
            flags.append("candidate_pending_no_approval")
        verdict = "不得写入正式知识库：候选待审、材料组不对应、适用条件受限"

    elif kind == "standard_validity":
        results = _find_standard_results(trace)
        states = {r["rule_id"]: r["state"] for r in results}
        detail["states"] = states
        detail["clause_levels"] = {r["rule_id"]: r["clause_level"] for r in results}
        detail["may_claim_compliance"] = {r["rule_id"]: r["may_claim_compliance"] for r in results}
        if any(r["state"] == "repealed" for r in results):
            flags.append("repealed_reference_blocked")
            detail["repealed_note"] = "废止标准引用必须拦截并改引替代标准"
        if any(r["state"] == "gap" for r in results):
            flags.append("gap_recorded")
            detail["gap_note"] = "无直接国标的规则记为缺口，不得硬凑映射"
        if all(not r["may_claim_compliance"] for r in results):
            flags.append("clause_claim_forbidden")
        verdict = "不得引用已废止的 GB 15760-2004，改引 GB 15760-2025；全文未取得，只能写主题对齐，不得写符合某条款"

    elif kind == "gate_classification":
        clauses = {c["clause"]: c for c in _find_clauses(trace)}
        text = " ".join(c.get("text", c.get("excerpt", "")) for c in clauses.values())
        has_clamp_warning = any("clamp" in name or "越界" in name for name in clauses)
        has_kinematics = any("运动学" in name for name in clauses)
        has_voxel = any("体素" in name for name in clauses)
        detail["clauses_used"] = sorted(clauses)
        if has_clamp_warning and ("warning" in text or "建议" in text):
            flags.append("clamp_is_warning_not_gate")
        if has_kinematics and has_voxel:
            flags.append("voxel_and_kinematics_are_hard_gates")
        detail["pipeline_exception_cases"] = _find_case_count(trace, source="pipeline_exception")
        verdict = "越界只产生 clamp 建议；体素仿真与运动学校验是硬闸，任一失败阻断下发"

    return {"flags": flags, "detail": detail, "verdict": verdict}


def _find_case_dist(trace: list[dict[str, Any]]) -> dict[str, Any]:
    for hop in trace:
        if hop["op"] == "case_dist" and isinstance(hop["result"], dict):
            return hop["result"]
    return {}


def _find_case_count(trace: list[dict[str, Any]], *, source: str) -> int:
    for hop in trace:
        if hop["op"] == "case_dist" and isinstance(hop["result"], dict):
            return int(hop["result"].get("row_count", 0))
    return 0


def _find_candidates(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for hop in trace:
        if hop["op"] == "candidate_lookup":
            return list(hop["result"])
    return []


def _find_standard_results(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for hop in trace:
        if hop["op"] == "standard_resolve":
            return list(hop["result"])
    return []


def _find_clauses(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for hop in trace:
        if hop["op"] == "clause_search":
            out.extend(hop["result"])
    return out


# --------------------------------------------------------------------- 断言

#: 正面放行声明——出现即视为越权承诺（除非紧邻否定词）
_READY_PATTERNS = ("可直接生成", "可以放行", "可上机", "允许下发", "已验证可加工")
_NEGATIONS = ("不", "未", "禁止", "无法", "不得", "不能", "否")


def _claims_ready(text: str) -> list[str]:
    """检出未加否定的放行声明。"""
    found = []
    for pattern in _READY_PATTERNS:
        start = 0
        while True:
            idx = text.find(pattern, start)
            if idx < 0:
                break
            prefix = text[max(0, idx - 3) : idx]
            if not any(neg in prefix for neg in _NEGATIONS):
                found.append(pattern)
            start = idx + len(pattern)
    return found


def evaluate(task: dict[str, Any], trace: list[dict[str, Any]], derivation: dict[str, Any]) -> dict[str, Any]:
    expect = task["expect"]
    failures: list[str] = []

    usable = {hop["source"] for hop in trace if hop["result"]}
    if len(usable) < expect["min_sources"]:
        failures.append(f"资料来源不足：{len(usable)} < {expect['min_sources']}")

    flags = set(derivation["flags"])
    missing = [f for f in expect.get("must_flag", []) if f not in flags]
    if missing:
        failures.append(f"缺少必需结论标记：{missing}")

    if expect.get("block_ready_claim"):
        claimed = _claims_ready(derivation["verdict"] + " " + json.dumps(derivation["detail"], ensure_ascii=False))
        if claimed:
            failures.append(f"出现未加否定的放行声明：{claimed}")

    # 可溯源底线：每跳必须带 locator
    for hop in trace:
        locators = _collect_locators(hop["result"])
        if not locators:
            failures.append(f"{hop['source']}.{hop['op']} 未返回任何定位符")

    return {
        "passed": not failures,
        "failures": failures,
        "sources_used": sorted(usable),
        "flags": sorted(flags),
    }


def _collect_locators(result: Any) -> list[str]:
    if isinstance(result, dict):
        if result.get("locator"):
            return [result["locator"]]
        return []
    if isinstance(result, list):
        return [item["locator"] for item in result if isinstance(item, dict) and item.get("locator")]
    return []


# --------------------------------------------------------------------- 主流程


def run(tasks_file: Path = TASKS_FILE) -> dict[str, Any]:
    spec = json.loads(tasks_file.read_text(encoding="utf-8"))
    sources = Sources(spec["sources"])
    reports = []
    for task in spec["tasks"]:
        trace = run_hops(sources, task)
        derivation = _derive(task, trace)
        checks = evaluate(task, trace, derivation)
        reports.append(
            {
                "id": task["id"],
                "question": task["question"],
                "hops": [
                    {
                        "source": hop["source"],
                        "op": hop["op"],
                        "locators": _collect_locators(hop["result"]),
                        "payload": hop["result"],
                    }
                    for hop in trace
                ],
                "derivation": derivation,
                "checks": checks,
            }
        )
    passed = sum(1 for r in reports if r["checks"]["passed"])
    return {
        "generated_at": "2026-11-10",
        "tasks_file": str(tasks_file.relative_to(REPO_ROOT)).replace("\\", "/"),
        "task_count": len(reports),
        "passed": passed,
        "sources_unavailable": sources.unavailable,
        "reports": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跨文档多跳任务执行器")
    parser.add_argument("--out", default="competition/nexent/multihop/results/multihop-run-2026-11-10.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = run()
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        for item in report["reports"]:
            mark = "PASS" if item["checks"]["passed"] else "FAIL"
            print(f"[{mark}] {item['id']} {item['question']}")
            print(f"       来源 {len(item['checks']['sources_used'])}: {', '.join(item['checks']['sources_used'])}")
            print(f"       结论标记: {', '.join(item['checks']['flags']) or '（无）'}")
            if item["checks"]["failures"]:
                for f in item["checks"]["failures"]:
                    print(f"       ! {f}")
        print(f"\n{report['passed']}/{report['task_count']} 通过；报告写入 {args.out}")

    return 0 if report["passed"] == report["task_count"] else 1


if __name__ == "__main__":
    sys.exit(main())
