"""M2 批量生成实验运行器：对案例集逐案跑 LLM 生成闭环（S1-S3）。

用法（系统 Python 3.14）：
    cd engineering/python
    python -m app.benchmarks.machinability_pilot.run_generation \
        --backend deepseek --cases ../../pilot/cases --runs-root ../../pilot/runs
可选: --family F01  --case F01-d3  --limit 5  --max-attempts 3

产物：<runs-root>/<run_id>/{manifest.json, records.jsonl, <case_id>/...}
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

from app.benchmarks.machinability_pilot.generate_loop import generate_for_case
from app.benchmarks.machinability_pilot.llm_backends import backend_by_name
from app.benchmarks.machinability_pilot.metrics_3d import VOXEL_RESOLUTION

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CASES = REPO_ROOT / "pilot" / "cases"
DEFAULT_RUNS = REPO_ROOT / "pilot" / "runs"


def select_cases(cases_dir: Path, family: str | None, case: str | None, limit: int | None) -> list[Path]:
    case_dirs = sorted(p for p in cases_dir.iterdir() if p.is_dir() and (p / "spec.json").exists())
    if case:
        case_dirs = [p for p in case_dirs if p.name == case]
    elif family:
        case_dirs = [p for p in case_dirs if p.name.startswith(f"{family}-")]
    if limit:
        case_dirs = case_dirs[:limit]
    if not case_dirs:
        raise SystemExit("没有选中任何案例目录")
    return case_dirs


async def run(args: argparse.Namespace) -> dict:
    # 沙箱子进程超时（含解释器+cadquery 导入 ~3s）；loft/twist 几何可能超默认 30s
    os.environ.setdefault("LNN_CADQUERY_TIMEOUT", str(args.sandbox_timeout))
    backend = backend_by_name(args.backend)
    cases_dir = Path(args.cases)
    selected = select_cases(cases_dir, args.family, args.case, args.limit)

    run_id = f"{backend.name.replace(':', '_')}_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir = Path(args.runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = {"total": len(selected), "ok": 0, "ast_fail": 0, "exec_fail": 0, "brep_fail": 0, "llm_fail": 0}
    records_path = run_dir / "records.jsonl"
    with records_path.open("a", encoding="utf-8") as records_file:
        for case_dir in selected:
            case_id = case_dir.name
            spec = json.loads((case_dir / "spec.json").read_text(encoding="utf-8"))
            description = (case_dir / "prompt.txt").read_text(encoding="utf-8").strip()
            print(f"[{case_id}] 生成中 (backend={backend.name}) ...", flush=True)
            record = await generate_for_case(
                case_id=case_id,
                description=description,
                truth_stl=case_dir / "truth.stl",
                backend=backend,
                run_dir=run_dir,
                max_attempts=args.max_attempts,
                voxel_resolution=args.voxel_resolution,
                harness=args.harness,
                grounding=frozenset(g for g in args.grounding.split(",") if g),
            )
            record_dict = record.to_dict()
            record_dict["difficulty"] = spec["difficulty"]
            record_dict["family"] = spec["family"]
            record_dict["surface_features"] = spec["surface_features"]
            records_file.write(json.dumps(record_dict, ensure_ascii=False) + "\n")
            records_file.flush()
            if record.ok:
                summary["ok"] += 1
                metric_note = f"cd={record.chamfer_x1e3:.2f} iou={record.voxel_iou:.3f}"
                if record.last_error:
                    metric_note += f" (metrics degraded: {record.last_error})"
                print(f"[{case_id}] ok attempts={record.attempts} {metric_note}", flush=True)
            else:
                key = f"{record.stage_failed}_fail" if record.stage_failed else "unknown_fail"
                summary[key] = summary.get(key, 0) + 1
                print(f"[{case_id}] FAIL stage={record.stage_failed} err={record.last_error}", flush=True)

    manifest = {
        "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": {
            "name": backend.name,
            "base_url": backend.base_url,
            "model": backend.model,
            "temperature": backend.temperature,
            "max_tokens": backend.max_tokens,
        },
        "config": {
            "harness": args.harness,
            "grounding": args.grounding,
            "max_attempts": args.max_attempts,
            "sandbox_timeout_s": args.sandbox_timeout,
            "import_sanitization": "剥离 import cadquery/math 安全行后审计（其余 import 仍拒绝）",
            "voxel_resolution": args.voxel_resolution,
            "chamfer_samples_note": "30000, unit-box normalized, x1e3 (Text2CAD-Bench 口径)",
            "voxel_note": f"{args.voxel_resolution}³（Text2CAD-Bench 为 256³，扩基准阶段统一）",
            "seed_note": "DeepSeek API 未固定随机种子（temperature=0.2），复现口径见 manifest",
        },
        "versions": {"cadquery": cq.__version__, "trimesh": trimesh.__version__},
        "summary": summary,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成: {summary['ok']}/{summary['total']} 通过 S1-S3, run → {run_dir}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="machinability pilot M2 生成实验")
    parser.add_argument("--backend", default="deepseek", help="deepseek | llama[:model]")
    parser.add_argument(
        "--harness",
        default="strict",
        choices=["strict", "relaxed"],
        help="strict=仓库沙箱；relaxed=cq/math 预注入白名单执行（消融条件）",
    )
    parser.add_argument(
        "--grounding", default="", help="方法组件开关，逗号分隔：api,skills,typed_feedback（缺省全关=基线）"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--family", default=None, help="只跑某个族，如 F01")
    parser.add_argument("--case", default=None, help="只跑单个案例，如 F01-d3")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--sandbox-timeout", type=int, default=120, help="沙箱子进程超时秒数")
    parser.add_argument("--voxel-resolution", type=int, default=VOXEL_RESOLUTION)
    args = parser.parse_args()

    import asyncio

    manifest = asyncio.run(run(args))
    # 同 generate_cases：Windows + Python 3.14 上 OCCT 收尾段错误，跳过收尾保退出码
    sys.stdout.flush()
    os._exit(0 if manifest["summary"]["ok"] == manifest["summary"]["total"] else 1)


if __name__ == "__main__":
    main()
