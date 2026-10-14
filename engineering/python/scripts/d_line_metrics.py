"""D 线三级对比指标计算与基线冻结（I4 §2/§3，ICT 备赛 D1/D2/D7）。

从仓库实测资产提取三级基线指标，支持 --freeze 冻结快照与 --verify 复现校验：
- v1（合成基线）：切参 seed 12 条覆盖 + cutting_force v1 manifest 指标 + 案例库空
- v2（真实现状）：切参 609 条覆盖 + cutting_force_v2 manifest + 案例库 stats + 批次吻合率
- v3（在线进化）：v2 + 演化报告 / replay gate 产物状态（机制轴，第 2 周实验后补全）

诚实口径（I4 §3/§5，输出 JSON 自动携带）：
- 切参覆盖用 (材料,工序) 组合覆盖率，不用"精度"（无真实机床真值）
- 颤振误报/漏报在规则管线（确定性）下结构性为零，不作为 AI 预测能力宣称
- one_pass_rate 分母含同参数复跑案例（I1 §2 披露）

用法（engineering/python 下）：
    python scripts/d_line_metrics.py              # 现算并打印
    python scripts/d_line_metrics.py --freeze     # 冻结快照到 data/d_line/
    python scripts/d_line_metrics.py --verify     # 现算与快照比对（复现检查）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PYTHON_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_DIR.parents[1]
OUT_DIR = PYTHON_DIR / "data" / "d_line"

HONESTY_NOTES = [
    "切参覆盖用 (材料,工序) 组合覆盖率，不用精度——无真实机床真值",
    "颤振误报/漏报在规则管线（确定性）下结构性为零，不作为 AI 预测能力宣称",
    "one_pass_rate 分母含同参数复跑案例（I1 §2 披露：冒烟 3 例 + B/C 复跑 51 例）",
    "M1 提示词进化未接入重放路径——v3 机制轴证明贯通与积累，不证质量提升",
]

TOOL_SERIES_COUNT = 6  # twist_drill/endmill/ball_nose/taper_ball/turning_tool/boring_bar

#: v1 历史锚点：仓库重建快照提交（当时切参数据 = repo_seed 12 条；案例库未建）
V1_GIT_REF = "ed31f09"

#: 各级引用的文件（冻结时记录 sha256，复现校验用）
_TRACKED_FILES = {
    "v1": ["models/lnn/cutting_force.manifest.json"],
    "v2": [
        "models/lnn/cutting_force_v2.manifest.json",
        "app/data/cutting_parameters.json",
        "data/training_sets/cutting_force_v2/build_summary.json",
    ],
    "v3": [],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _cp_metrics_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """切参指标纯函数（可测试）：条数 / 来源分布 / (材料,工序) 组合覆盖率。"""
    materials = {r["material_id"] for r in records}
    combos = {(r["material_id"], r["tool_series"]) for r in records}
    domain = len(materials) * TOOL_SERIES_COUNT
    by_source: dict[str, int] = {}
    for r in records:
        # 历史版本（v1 锚点 ed31f09）无 source 字段——如实记为 "no_source_field"
        key = r.get("source") or "no_source_field"
        by_source[key] = by_source.get(key, 0) + 1
    return {
        "total_entries": len(records),
        "by_source": by_source,
        "materials": len(materials),
        "combos_covered": len(combos),
        "combo_domain": domain,
        "combo_coverage": round(len(combos) / domain, 4) if domain else 0.0,
    }


def _cutting_param_metrics(git_ref: str | None = None) -> dict[str, Any]:
    """切参数据库指标。git_ref 给定时从历史提交读取（v1 冻结口径），否则读当前文件。"""
    if git_ref:
        import subprocess

        raw = subprocess.run(
            ["git", "show", f"{git_ref}:engineering/python/app/data/cutting_parameters.json"],
            capture_output=True,
            check=True,
            cwd=REPO_ROOT,
        ).stdout
        records = json.loads(raw)
        result = _cp_metrics_from_records(records)
        result["git_ref"] = git_ref
        return result
    records = json.loads((PYTHON_DIR / "app" / "data" / "cutting_parameters.json").read_text(encoding="utf-8"))
    return _cp_metrics_from_records(records)


def _manifest_metrics(rel_path: str) -> dict[str, Any]:
    """从模型 manifest 提取可引用指标（缺失返回占位，不编数）。"""
    path = PYTHON_DIR / rel_path
    if not path.exists():
        return {"present": False}
    m = json.loads(path.read_text(encoding="utf-8"))
    test = (m.get("metrics_numpy_path") or {}).get("test", {})
    split_mode = (m.get("data") or {}).get("split_mode", "")
    return {
        "present": True,
        "r2_test": test.get("r2"),
        "mae_test": test.get("mae"),
        "split_mode": split_mode,
        "feature_order": m.get("feature_order", []),
        "created_at": m.get("created_at", ""),
    }


def _failure_case_stats() -> dict[str, Any]:
    """案例库 stats（直接 sqlite 直查，避免单例耦合）。"""
    db = PYTHON_DIR / "data" / "failure_cases.db"
    if not db.exists():
        return {"present": False, "total": 0, "one_pass_rate": None}
    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM failure_cases").fetchone()[0]
        failures = conn.execute("SELECT COUNT(*) FROM failure_cases WHERE outcome='failure'").fetchone()[0]
        by_source = dict(
            conn.execute(
                "SELECT COALESCE(source,''), COUNT(*) FROM failure_cases WHERE outcome='failure' GROUP BY source"
            ).fetchall()
        )
    finally:
        conn.close()
    return {
        "present": True,
        "total": total,
        "successes": total - failures,
        "failures": failures,
        "failure_by_source": by_source,
        "one_pass_rate": round((total - failures) / total, 4) if total else None,
    }


def _batch_agreement() -> dict[str, Any]:
    """批次确定性：expected 与 actual 的吻合率（manifest 可再生，缺失时占位）。"""
    out: dict[str, Any] = {}
    for batch, rel in (("batch_evo_v1", "data/batch_evo_v1/batch_manifest.json"), ("batch_evo_v2", "data/batch_evo_v2/batch_manifest.json")):
        path = PYTHON_DIR / rel
        if not path.exists():
            out[batch] = {"present": False}
            continue
        cases = json.loads(path.read_text(encoding="utf-8")).get("cases", [])
        success_states = {"generated", "reviewed", "succeeded"}

        def _ok(c: dict[str, Any]) -> bool:
            state = str(c.get("actual", "")).split(":", 1)[0]
            return (c.get("expected") == "success") == (state in success_states)

        matched = sum(1 for c in cases if _ok(c))
        out[batch] = {
            "present": True,
            "cases": len(cases),
            "expected_matched": matched,
            "agreement": round(matched / len(cases), 4) if cases else None,
        }
    return out


def _evolution_artifacts() -> dict[str, Any]:
    """v3 机制轴产物状态（第 2 周实验后填充，当前如实为空）。"""
    reports = PYTHON_DIR / "data" / "evolution_reports"
    report_files = sorted(p.name for p in reports.glob("*.json")) if reports.exists() else []
    gate_result = PYTHON_DIR / "data" / "d_line" / "v3_mechanism" / "gate_result.json"
    gate: dict[str, Any] | None = None
    if gate_result.exists():
        try:
            g = json.loads(gate_result.read_text(encoding="utf-8"))
            gate = {"passed": g.get("passed"), "inconclusive": g.get("inconclusive"), "reasons": g.get("reasons", [])}
        except json.JSONDecodeError:
            gate = None
    return {
        "evolution_reports": report_files,
        "replay_gate": gate if gate else {"status": "待第 2 周实验（I4 §4）"},
    }


def collect_level(level: str) -> dict[str, Any]:
    """收集单级指标。v1/v2 为冻结基线，v3 依赖第 2 周实验产物。"""
    result: dict[str, Any] = {"level": level, "collected_at": datetime.now(timezone.utc).isoformat()}
    if level == "v1":
        # v1 冻结口径：切参从 git 历史锚点读取（12 条 seed）；案例库当时未建
        #（C4 批一 2026-09-28 才首灌 219 行，db 为 gitignore 运行时资产无历史）——
        # 用口径常量 0 如实标注，不引用当前 641 行
        result["cutting_params"] = _cutting_param_metrics(git_ref=V1_GIT_REF)
        result["model"] = _manifest_metrics(_TRACKED_FILES[level][0])
        result["failure_cases"] = {
            "present": False,
            "total": 0,
            "one_pass_rate": None,
            "note": "v1 时刻案例库未建（C4 批一 2026-09-28 首灌 219 行）；db 为 gitignore 运行时资产",
        }
        result["batch_agreement"] = {"batch_evo_v1": {"present": False}, "batch_evo_v2": {"present": False}}
    elif level == "v2":
        result["cutting_params"] = _cutting_param_metrics()
        result["model"] = _manifest_metrics(_TRACKED_FILES[level][0])
        result["failure_cases"] = _failure_case_stats()
        result["batch_agreement"] = _batch_agreement()
    else:  # v3
        result["cutting_params"] = _cutting_param_metrics()
        result["model"] = _manifest_metrics("models/lnn/cutting_force_v2.manifest.json")
        result["failure_cases"] = _failure_case_stats()
        result["evolution"] = _evolution_artifacts()
    result["honesty_notes"] = list(HONESTY_NOTES)
    # 溯源锚点（三种模式统一携带，保证 --verify 与快照可比）
    result["tracked_files"] = {
        rel: _sha256(PYTHON_DIR / rel) for rel in _TRACKED_FILES.get(level, []) if (PYTHON_DIR / rel).exists()
    }
    return result


def _verify_snapshot(level: str, snapshot_path: Path, fresh: dict[str, Any]) -> list[str]:
    """复现校验：冻结时数值字段与现算一致（时间戳/sha 除外）。"""
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    diffs: list[str] = []

    def _walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in set(a) | set(b):
                if k in ("collected_at",):
                    continue
                _walk(a.get(k), b.get(k), f"{path}.{k}")
        elif a != b:
            diffs.append(f"{path}: snapshot={a!r} fresh={b!r}")

    _walk(snap, fresh, level)
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D 线三级对比指标计算与基线冻结")
    parser.add_argument("--freeze", action="store_true", help="冻结快照到 data/d_line/baseline_<level>.json")
    parser.add_argument("--verify", action="store_true", help="现算与已冻结快照比对（复现检查）")
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for level in ("v1", "v2", "v3"):
        fresh = collect_level(level)
        snapshot_path = OUT_DIR / f"baseline_{level}.json"
        if args.freeze:
            snapshot_path.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[freeze] {level} -> {snapshot_path}")
        elif args.verify:
            if not snapshot_path.exists():
                problems.append(f"{level}: 快照不存在（先 --freeze）")
                continue
            diffs = _verify_snapshot(level, snapshot_path, fresh)
            if diffs:
                problems.append(f"{level}: {len(diffs)} 处不一致 -> {diffs[:3]}")
            else:
                print(f"[verify] {level}: 与快照一致")
        else:
            print(f"== {level} ==")
            print(json.dumps(fresh, ensure_ascii=False, indent=2))

    if args.verify and problems:
        print("VERIFY FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    if args.freeze:
        print("[ok] 三级快照已冻结；后续对比一律引用快照（I4 §2 冻结纪律）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
