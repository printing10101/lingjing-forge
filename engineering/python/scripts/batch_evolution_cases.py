"""批量自产进化案例（ICT 备赛 C4：failure_cases.db 灌数 0 → 200+）。

按 I1 技术路线 §2 的设计，把「同一案例族 × 切削参数扰动 × 材料扰动」
的批量决策任务跑在**真实 GCodeGenerationPipeline**（规则模式，无 LLM
依赖）上：稳定域成功、超限失败、参数越界、报告态异常都由管线自身
口径写入失败案例库——本脚本不做任何结果改写。

批次溯源走 task_id 前缀 ``batch_evo_v1-``（``FAILURE_CASES_DB`` 语义
不变，source 仍是持久层白名单枚举），与真实使用数据可在 task_id 上
区分。案例库统计用 ``FailureCaseStore.stats()`` 直接出报表。

用法：
    python scripts/batch_evolution_cases.py --write-only   # 只生成输入集
    python scripts/batch_evolution_cases.py                # 生成并跑批
    python scripts/batch_evolution_cases.py --limit 10     # 冒烟
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.gcode_generation import GCodeGenerationConfig  # noqa: E402
from app.gcode_generation.failure_case_store import (  # noqa: E402
    FailureCaseStore,
    reset_failure_case_store,
)
from app.gcode_generation.pipeline import GCodeGenerationPipeline  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("batch_evolution_cases")

BATCH_PREFIX = "batch_evo_v1"
CONTROLLER = "fanuc_0i"

# ---------------------------------------------------------------------------
# 案例族基线（与 replay_harness 种子集同构：特征条目 + 工序 JSON 对）
# ---------------------------------------------------------------------------

#: 材料 → (特征 rpm, 极限切深 limit) 的族基线；铝放宽、钛收紧。
MATERIALS: dict[str, dict[str, Any]] = {
    "steel_45": {"name": "45#钢", "scale": 1.0},
    "aluminum_6061": {"name": "铝合金6061", "scale": 1.6},
    "titanium_ti6al4v": {"name": "钛合金TC4", "scale": 0.6},
}

FAMILIES: dict[str, dict[str, Any]] = {
    "plane": {
        "feature_id": "plane_top", "feature_type": "plane",
        "method": "平面铣削", "op_name": "铣削", "tool": "endmill_d50", "tool_dia": 50.0,
        "rpm": 1800.0, "limit": 5.0, "stock": {"length": 100.0, "width": 80.0},
    },
    "hole": {
        "feature_id": "hole_d10", "feature_type": "hole",
        "method": "钻孔", "op_name": "钻孔", "tool": "drill_d10", "tool_dia": 10.0,
        "rpm": 1200.0, "limit": 20.0, "stock": {"x": 30.0, "y": 30.0},
    },
    "cylinder": {
        "feature_id": "cyl_od", "feature_type": "cylinder",
        "method": "外圆铣削", "op_name": "外圆铣", "tool": "endmill_d20", "tool_dia": 20.0,
        "rpm": 1600.0, "limit": 5.0, "stock": {"length": 80.0, "width": 80.0},
    },
    "boss": {
        "feature_id": "boss_1", "feature_type": "boss",
        "method": "凸台铣削", "op_name": "凸台铣", "tool": "endmill_d12", "tool_dia": 12.0,
        "rpm": 1500.0, "limit": 4.0, "stock": {"length": 30.0, "width": 30.0},
    },
}

#: 切深比扫掠（axial / limit）：0.98 以下稳定带、1.0 以上不稳定带、
#: 0.92-0.98 为低裕度边缘案例（进化曲线最关心的区间）。
RATIOS = (0.30, 0.45, 0.60, 0.75, 0.85, 0.92, 0.96, 0.98, 1.02, 1.06, 1.15, 1.30, 1.50, 1.80)

#: 进给/转速边界扰动（B 轴，仅 steel_45 × 4 族）：正常值 + 越界组合。
FEED_VARIANTS = ("0.15 mm/r", "0.001 mm/r", "500 mm/r", "-0.15 mm/r")
SPEED_VARIANTS = ("120 m/min", "99000 m/min", "0 m/min")

#: C 轴：ChatterReport task_status 非法态（预期 ChatterReportLoadError）。
BAD_STATUSES = ("failed", "pending", "running")

WORK_ROOT = PROJECT_ROOT / "data" / "batch_evo_v1"


@dataclass(frozen=True)
class BatchCase:
    case_id: str
    chatter: dict[str, Any]
    plan: dict[str, Any]
    material_name: str
    expected: str  # success / failure（注释性预期，供人工核对）


def build_matrix() -> list[BatchCase]:
    """确定性展开批量案例矩阵（A 切深比 × 材料族 + B 参数边界 + C 报告态）。"""
    cases: list[BatchCase] = []

    # A 轴：切深比扫掠 × 4 族 × 3 材料
    for material_id, material in MATERIALS.items():
        for family_id, fam in FAMILIES.items():
            limit = round(fam["limit"] * material["scale"], 3)
            for ratio in RATIOS:
                axial = round(limit * ratio, 3)
                stable = ratio < 1.0
                feature = {
                    "feature_id": fam["feature_id"],
                    "feature_type": fam["feature_type"],
                    "material_id": material_id,
                    "spindle_rpm": fam["rpm"] * (material["scale"] ** 0.5),
                    "axial_depth_mm": axial,
                    "limit_depth_mm": limit,
                    "stable": stable,
                    "stability_margin": round(1.0 - ratio, 4),
                    "method": "analytical",
                    "ltc_active": False,
                    "confidence": 0.9 if stable else 0.3,
                }
                case_id = f"a_{family_id}_{material_id.split('_')[0]}_q{int(ratio * 100):03d}"
                cases.append(
                    BatchCase(
                        case_id=case_id,
                        chatter=_chatter(case_id, material_id, [feature]),
                        plan=_plan(fam, axial, "0.15 mm/r", "120 m/min"),
                        material_name=material["name"],
                        expected="success" if stable else "failure",
                    )
                )

    # B 轴：进给/转速边界扰动（steel_45 × 4 族）。产品对越界参数的
    # 设计行为是 L1 clamp 建议（warning）而非拒绝——预期全部成功
    # （带 clamp），用作"参数越界被兜住"的正样本。
    for family_id, fam in FAMILIES.items():
        limit = fam["limit"]
        for f_idx, feed in enumerate(FEED_VARIANTS):
            for s_idx, speed in enumerate(SPEED_VARIANTS):
                axial = round(limit * 0.6, 3)  # 稳定域内，隔离参数越界因素
                feature = {
                    "feature_id": fam["feature_id"],
                    "feature_type": fam["feature_type"],
                    "material_id": "steel_45",
                    "spindle_rpm": fam["rpm"],
                    "axial_depth_mm": axial,
                    "limit_depth_mm": limit,
                    "stable": True,
                    "stability_margin": 0.4,
                    "method": "analytical",
                    "ltc_active": False,
                    "confidence": 0.9,
                }
                case_id = f"b_{family_id}_f{f_idx}_s{s_idx}"
                cases.append(
                    BatchCase(
                        case_id=case_id,
                        chatter=_chatter(case_id, "steel_45", [feature]),
                        plan=_plan(fam, axial, feed, speed),
                        material_name=MATERIALS["steel_45"]["name"],
                        expected="success",
                    )
                )

    # C 轴：ChatterReport 非法 task_status
    fam = FAMILIES["plane"]
    for status in BAD_STATUSES:
        feature = {
            "feature_id": fam["feature_id"], "feature_type": fam["feature_type"],
            "material_id": "steel_45", "spindle_rpm": fam["rpm"],
            "axial_depth_mm": 1.0, "limit_depth_mm": 5.0, "stable": True,
            "stability_margin": 0.8, "method": "analytical", "ltc_active": False,
            "confidence": 0.9,
        }
        case_id = f"c_badstatus_{status}"
        cases.append(
            BatchCase(
                case_id=case_id,
                chatter=_chatter(case_id, "steel_45", [feature], task_status=status),
                plan=_plan(fam, 1.0, "0.15 mm/r", "120 m/min"),
                material_name=MATERIALS["steel_45"]["name"],
                expected="failure",
            )
        )

    return cases


def _chatter(case_id: str, material_id: str, features: list[dict], task_status: str = "succeeded") -> dict:
    return {
        "task_id": f"{BATCH_PREFIX}-{case_id}",
        "task_status": task_status,
        "material_id": material_id,
        "prediction_method": "analytical",
        "feature_results": features,
    }


def _plan(fam: dict[str, Any], axial: float, feed: str, speed: str) -> dict:
    geometry: dict[str, Any] = {"z_depth": axial, **fam["stock"]}
    operation = {
        "seq": 1,
        "name": fam["op_name"],
        "feature_name": fam["feature_id"],
        "machining_method": fam["method"],
        "surface": "top",
        "tolerance_grade": "IT8",
        "tool_type": fam["tool"],
        "cutting_params": {
            "material": "steel",
            "tool_diameter": fam["tool_dia"],
            "recommended_feed": feed,
            "recommended_speed": speed,
            "geometry": geometry,
        },
        "estimated_time_min": 2.0,
        "notes": "batch_evo_v1 case",
    }
    return {
        "operations": [operation],
        "setups": [{"name": "平口钳装夹", "surface": "top", "fixture_type": "vise"}],
    }


def write_input_set(cases: list[BatchCase], root: Path) -> Path:
    """落盘输入集（chatter_report.json + operation_plan.json 对）。"""
    for case in cases:
        case_dir = root / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "chatter_report.json").write_text(
            json.dumps(case.chatter, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (case_dir / "operation_plan.json").write_text(
            json.dumps(case.plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return root


def run_batch(cases: list[BatchCase], root: Path) -> dict[str, Any]:
    """逐案例跑真实 pipeline，汇总实际结局与案例库统计。

    案例库的 task_id 由管线自生成（gc_ 前缀），批次溯源在本函数落盘的
    ``batch_manifest.json``（case_id ↔ 管线 task_id ↔ 实际结局）。
    """
    cfg = GCodeGenerationConfig(output_dir=str(root / "gcode_out"))
    pipeline = GCodeGenerationPipeline(cfg=cfg)
    store = FailureCaseStore()  # 默认生产库（python/data/failure_cases.db）

    per_case: list[dict[str, Any]] = []
    for case in cases:
        case_dir = root / case.case_id
        try:
            task = pipeline.create_task(
                source_chatter_report_path=str(case_dir / "chatter_report.json"),
                source_operation_plan_path=str(case_dir / "operation_plan.json"),
                controller_type=CONTROLLER,
                material_name=case.material_name,
            )
            result = asyncio.run(pipeline.run_pipeline(task.task_id))
            status = result.status
        except Exception as exc:  # noqa: BLE001 批量驱动不允许单例炸整批
            logger.warning("案例 %s 管线异常: %s: %s", case.case_id, type(exc).__name__, exc)
            status = f"exception:{type(exc).__name__}"
            task_id = ""
        else:
            task_id = task.task_id
        recorded = [c for c in store.list_cases(limit=500) if c.task_id == task_id] if task_id else []
        per_case.append(
            {
                "case_id": case.case_id,
                "pipeline_task_id": task_id,
                "expected": case.expected,
                "actual": status,
                "error_codes": recorded[0].error_codes if recorded else [],
            }
        )

    manifest_path = root / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps({"batch": BATCH_PREFIX, "cases": per_case}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"批次 manifest 已写入 {manifest_path}")
    return {"per_case": per_case, "stats": store.stats()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量自产进化案例（C4）")
    parser.add_argument("--write-only", action="store_true", help="只生成输入集不跑批")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个案例（冒烟用，0=全部）")
    parser.add_argument("--work-root", type=Path, default=WORK_ROOT, help="输入集与输出目录")
    args = parser.parse_args(argv)

    cases = build_matrix()
    print(f"批量矩阵: {len(cases)} 案例")
    write_input_set(cases, args.work_root)
    print(f"输入集已写入 {args.work_root}")

    if args.write_only:
        return 0

    todo = cases[: args.limit] if args.limit else cases
    reset_failure_case_store()
    result = run_batch(todo, args.work_root)

    stats = result["stats"]
    # 管线状态 → 成败口径：generated 及之后的人工确认态记成功，failed/异常记失败
    SUCCESS_STATES = {"generated", "reviewed", "succeeded"}

    def _outcome(actual: str) -> str:
        state = actual.split(":", 1)[0]
        return "success" if state in SUCCESS_STATES else "failure"

    matched = sum(1 for p in result["per_case"] if p["expected"] == _outcome(p["actual"]))
    print(f"已运行: {len(result['per_case'])}  预期吻合: {matched}")
    print(
        f"案例库统计: total={stats['total']} failures={stats['failures']} "
        f"successes={stats['successes']} one_pass_rate={stats['one_pass_rate']}"
    )
    print(f"失败来源分布: {stats['by_source']}")
    print(f"错误码分布: {stats['by_code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
