"""S6 可检性跑批：对 10 个真值案例族算不确定度预算，并与 S4 可造性对账。

用法（仓库根 engineering/python 下）::

    python -m app.benchmarks.machinability_pilot.inspectability.run_inspectability

产出 JSON 落 ``inspectability/data/run_<id>.json``（含冻结判据版本号与命令行参数，
便于复现），stdout 打汇总表。M2 判定所需的对照量是**真值模型**——不经 LLM 生成，
因此与生成失败模式无关，只考察"设计本身能不能被验证"。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cadquery as cq

from app.benchmarks.machinability_pilot.case_families import DIFFICULTY_TIERS, FAMILY_REGISTRY
from app.benchmarks.machinability_pilot.inspectability.access import GeometryCriteria, assess_case
from app.benchmarks.machinability_pilot.metrics_3d import load_mesh
from app.benchmarks.machinability_pilot.physics_chain import s4_accessibility

DATA_DIR = Path(__file__).parent / "data"


def assess_truth_case(
    family_id: str, tier: str, crit: GeometryCriteria, band_mm: float, work_dir: Path
) -> dict[str, Any]:
    """单个真值模型：建模 → 导出 STL → S6 可检性 + S4 可造性。"""
    fam = FAMILY_REGISTRY[family_id]
    stl = work_dir / f"{family_id}-{tier}.stl"
    cq.exporters.export(fam.build(fam.params_for(tier)), str(stl), tolerance=0.05, angularTolerance=0.2)
    mesh = load_mesh(stl)
    result = assess_case(mesh, crit, tolerance_band_mm=band_mm, label=f"{family_id}-{tier}")
    result["s4"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s4_accessibility(mesh).items()}
    result["faces"] = int(len(mesh.faces))
    return result


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """M2 判定：三轴可造但不可检的比例，以及分族分布。"""
    manufacturable = [r for r in records if not r["s4"]["needs_multiaxis"]]
    # "不可检"判据：所需不确定度落到光学量级以下（结构光锚点 0.020 mm，见 criteria 的
    # instrument_ladder_for_translation_only，仅用于翻译结论、不进入主指标）
    ladder = 0.020
    hard_to_certify = [r for r in manufacturable if r["U95_required_mm"] < ladder]
    per_family: dict[str, dict[str, int]] = {}
    for r in records:
        row = per_family.setdefault(r["label"].split("-")[0], {"cases": 0, "mfg_ok": 0, "uncertifiable": 0})
        row["cases"] += 1
        if not r["s4"]["needs_multiaxis"]:
            row["mfg_ok"] += 1
            if r["U95_required_mm"] < ladder:
                row["uncertifiable"] += 1
    ratio = len(hard_to_certify) / len(records) if records else 0.0
    return {
        "n_cases": len(records),
        "n_manufacturable_3axis": len(manufacturable),
        "n_mfg_but_uncertifiable": len(hard_to_certify),
        "share_of_all_cases": round(ratio, 4),
        "m2_threshold_pass": ratio >= 0.15,
        "m2_kill_line": ratio < 0.10,
        "per_family": per_family,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S6 可检性评定（真值案例族）")
    ap.add_argument("--band", type=float, default=0.20, help="公差带宽度 mm（双边带总宽）")
    ap.add_argument("--criteria", type=Path, default=None, help="冻结判据 JSON 路径")
    ap.add_argument("--out", type=Path, default=DATA_DIR, help="结果输出目录")
    ap.add_argument("--families", nargs="*", default=None, help="限定族，如 F06 F02")
    args = ap.parse_args(argv)

    crit = GeometryCriteria.load(args.criteria)
    ids = args.families or sorted(FAMILY_REGISTRY)
    unknown = [f for f in ids if f not in FAMILY_REGISTRY]
    if unknown:
        raise SystemExit(f"[参数非法] 未知案例族 {unknown}。建议操作：可用族 {sorted(FAMILY_REGISTRY)}")

    args.out.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    records: list[dict[str, Any]] = []
    work = Path(tempfile.mkdtemp(prefix="s6_"))
    try:
        t0 = time.perf_counter()
        for fid in ids:
            for tier in DIFFICULTY_TIERS:
                rec = assess_truth_case(fid, tier, crit, args.band, work)
                records.append(rec)
                print(
                    f"{rec['label']:>9} P99={rec['penalty_p99']:.3f} "
                    f"U95_req={rec['U95_required_mm'] * 1000:7.1f}um "
                    f"无视线={rec['frac_no_optic_view'] * 100:5.1f}% "
                    f"测头不可达={rec['frac_no_tactile_reach'] * 100:5.1f}% "
                    f"S4多轴={rec['s4']['needs_multiaxis']}",
                    flush=True,
                )
        summary = summarize(records)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    payload = {
        "run_id": run_id,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "tolerance_band_mm": args.band,
        "criteria": {"version": crit.raw.get("version"), "frozen_at": crit.raw.get("frozen_at")},
        "reproduce": (
            f"python -m app.benchmarks.machinability_pilot.inspectability.run_inspectability --band {args.band}"
        ),
        "summary": summary,
        "cases": records,
    }
    out_file = args.out / f"{run_id}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== M2 判定 ===\n{json.dumps(summary, ensure_ascii=False, indent=2)}")
    print(f"结果落盘：{out_file}")
    # 同 generate_cases/run_generation/run_physics：Windows + Python 3.14 上 OCCT 在解释器
    # 收尾阶段段错误（此处产物已写盘、stdout 已 flush），跳过收尾以保住真实退出码。
    sys.stdout.flush()
    ok = len(records) == len(ids) * len(DIFFICULTY_TIERS)
    os._exit(0 if ok and summary["n_cases"] == len(records) else 1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
