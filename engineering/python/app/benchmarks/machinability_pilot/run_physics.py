"""M3 批量运行器：对 M2 通过 S1-S3 的生成模型跑 S4 可达性 + S5 加工物理链。

用法（系统 Python 3.14）：
    cd engineering/python
    python -m app.benchmarks.machinability_pilot.run_physics \
        --source-run ../../pilot/runs/deepseek-chat_20260918_014715
产物：<runs-root>/physics_<ts>/{manifest.json, physics_records.jsonl, <case_id>/...}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cadquery as cq
import trimesh

from app.benchmarks.machinability_pilot.physics_chain import (
    PILOT_PARAMS,
    run_s5,
    s4_accessibility,
)
from app.benchmarks.machinability_pilot.metrics_3d import load_mesh

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_RUNS = REPO_ROOT / "pilot" / "runs"
DEFAULT_CASES = REPO_ROOT / "pilot" / "cases"


def latest_m2_run(runs_root: Path) -> Path:
    candidates = sorted(
        (p for p in runs_root.iterdir() if p.is_dir() and (p / "manifest.json").exists()),
        key=lambda p: p.name,
    )
    if not candidates:
        raise SystemExit(f"runs 目录下没有 M2 运行: {runs_root}")
    return candidates[-1]


async def run(args: argparse.Namespace) -> dict:
    source = Path(args.source_run) if args.source_run else latest_m2_run(DEFAULT_RUNS)
    run_id = f"physics_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir = DEFAULT_RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    case_dirs = sorted(p for p in source.iterdir() if p.is_dir() and (p / "model.stl").exists())
    if args.case:
        case_dirs = [p for p in case_dirs if p.name == args.case]
    if not case_dirs:
        raise SystemExit(f"源运行中没有带 model.stl 的案例: {source}")

    summary = {"total": len(case_dirs), "multiaxis": 0, "physics_fail": 0, "machinable": 0, "error": 0}
    records_path = run_dir / "physics_records.jsonl"
    with records_path.open("a", encoding="utf-8") as records_file:
        for case_dir in case_dirs:
            case_id = case_dir.name
            spec_path = args.cases / case_id / "spec.json"
            spec = (
                json.loads(spec_path.read_text(encoding="utf-8"))
                if spec_path.exists()
                else {"family": case_id.split("-")[0], "difficulty": case_id.split("-")[1]}
            )
            print(f"[{case_id}] S4+S5 评估中 ...", flush=True)
            mesh = load_mesh(case_dir / "model.stl")
            try:
                s4 = s4_accessibility(mesh)
                s5 = run_s5(mesh, run_dir / case_id)
            except Exception as exc:
                summary["error"] += 1
                record = {
                    "case_id": case_id,
                    "family": spec["family"],
                    "difficulty": spec["difficulty"],
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                }
                records_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                records_file.flush()
                print(f"[{case_id}] FATAL {record['fatal_error']}", flush=True)
                continue

            verdict = (
                "machinable"
                if s5["machinable_3axis"] and not s4["needs_multiaxis"]
                else ("multiaxis" if s4["needs_multiaxis"] else "physics_fail")
            )
            summary[verdict] += 1
            record = {
                "case_id": case_id,
                "family": spec["family"],
                "difficulty": spec["difficulty"],
                "s4": s4,
                "s5": s5,
                "verdict": verdict,
            }
            records_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            records_file.flush()
            stages = {k: v.get("ok") for k, v in s5["stages"].items()}
            print(f"[{case_id}] {verdict}  s4_undercut={s4['undercut_fraction']:.2f}  stages={stages}", flush=True)

    manifest = {
        "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_run": str(source),
        "pilot_params": PILOT_PARAMS,
        "versions": {"cadquery": cq.__version__, "trimesh": trimesh.__version__},
        "funnel_note": "输入总体 = M2 通过 S1-S3 的模型；multiaxis = S4 判需多轴（3 轴口径外）",
        "summary": summary,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n完成: {summary['machinable']}/{summary['total']} 3 轴可加工, "
        f"{summary['multiaxis']} 需多轴, {summary['physics_fail']} 物理失败, run → {run_dir}"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="machinability pilot M3 物理评估")
    parser.add_argument("--source-run", default=None, help="M2 运行目录（缺省取 runs 下最新）")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="案例库目录（读 spec.json）")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--case", default=None, help="只跑单个案例")
    parser.add_argument("--material", default=None, help="压测：覆盖 PILOT_PARAMS.material（如 titanium_tc4）")
    parser.add_argument("--stepdown", type=float, default=None, help="压测：覆盖层降 mm")
    parser.add_argument("--spindle-rpm", type=float, default=None, help="压测：覆盖转速")
    parser.add_argument("--force-limit", type=float, default=None, help="压测：覆盖力限 N")
    parser.add_argument("--kc1-1", type=float, default=None, help="压测：覆盖颤振 K_s（N/mm²，与材料对齐）")
    args = parser.parse_args()

    if args.material:
        PILOT_PARAMS["material"] = args.material
    if args.stepdown:
        PILOT_PARAMS["stepdown_mm"] = args.stepdown
    if args.spindle_rpm:
        PILOT_PARAMS["spindle_rpm"] = args.spindle_rpm
    if args.force_limit:
        PILOT_PARAMS["force_limit_n"] = args.force_limit
    if args.kc1_1:
        PILOT_PARAMS["kienzle_kc1_1"] = args.kc1_1

    import asyncio

    manifest = asyncio.run(run(args))
    # 同前：Windows + Python 3.14 OCCT 收尾段错误，跳过收尾保退出码
    sys.stdout.flush()
    os._exit(0 if manifest["summary"]["error"] == 0 else 1)


if __name__ == "__main__":
    main()
