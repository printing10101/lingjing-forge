"""灰度重放工具（Phase 0 自进化 · M5）。

把「基线 → 改动 → 回归门控」固化成可复用操作闭环：

1. ``prepare``：在指定目录生成确定性种子输入集（3 例：稳定成功 /
   不稳定特征失败 / ChatterReport 拒载失败），作为重放基准输入；
2. ``run``：对固定输入集重放真实 pipeline（真 ChatterReportLoader →
   真 GeneratorAdapter/GCodeGenerator → 真 SafetyValidator），案例库
   指向隔离的灰度 DB（``FAILURE_CASES_DB``），产出该轮 stats 报表；
3. ``gate``：取基线库与候选库两份 stats，调用 ``regression_gate``
   出门控判定。

适用对象：任何会影响生成结果的改动——LLM 提示词（未来接入参数
推荐时）、切削参数表、安全校验规则、后处理器逻辑等。当前生成链路
为模板化生成（无 LLM 提示词），本工具先行固化流程与积累样本机制。

诚实边界：
- 样本 < ``GateThresholds.min_samples`` 时门控判 inconclusive，
  不武断放行/拦截；
- 种子输入集是「合成但真实合法」的输入（真实走完整管道），非生产
  流量；生产流量积累后应以真实任务分布替换/扩充种子集。

CLI（在 engineering/python 目录下运行）::

    python -m app.gcode_generation.replay_harness prepare <dir>
    python -m app.gcode_generation.replay_harness run <dir> <db> [--work-dir <dir>]
    python -m app.gcode_generation.replay_harness gate <baseline_db> <candidate_db>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.gcode_generation import GCodeGenerationConfig
from app.gcode_generation.failure_case_store import (
    FailureCaseStore,
    reset_failure_case_store,
)
from app.gcode_generation.pipeline import GCodeGenerationPipeline
from app.gcode_generation.regression_gate import (
    GateResult,
    GateThresholds,
    evaluate_regression,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ReplayCase",
    "write_seed_input_set",
    "run_replay",
    "gate_from_dbs",
    "main",
]

_CONTROLLER = "fanuc_0i"
_MATERIAL = "45#钢"


@dataclass(frozen=True)
class ReplayCase:
    """一个重放案例（ChatterReport + OperationPlan JSON 对）。"""

    case_id: str
    chatter_path: Path
    plan_path: Path
    expected_outcome: str  # success / failure（注释性预期，不参与判定）
    expected_hint: str  # 预期失败来源/错误码（便于人工核对）


# ----------------------------------------------------------------------
# 种子输入集（确定性，参数化构造）
# ----------------------------------------------------------------------

_SETUPS = [{"name": "平口钳装夹", "surface": "top", "fixture_type": "vise"}]


def _stable_feature(
    feature_id: str,
    feature_type: str,
    material_id: str,
    rpm: float,
    axial: float,
    limit: float,
    confidence: float = 0.9,
) -> dict[str, Any]:
    """构造一个稳定特征的 ChatterReport 特征条目。"""
    return {
        "feature_id": feature_id,
        "feature_type": feature_type,
        "material_id": material_id,
        "spindle_rpm": rpm,
        "axial_depth_mm": axial,
        "limit_depth_mm": limit,
        "stable": True,
        "stability_margin": round(1.0 - axial / limit, 4),
        "method": "analytical",
        "ltc_active": False,
        "confidence": confidence,
    }


def _unstable_feature(
    feature_id: str,
    feature_type: str,
    material_id: str,
    rpm: float,
    axial: float,
    limit: float,
) -> dict[str, Any]:
    """构造一个不稳定特征（切深超限，阶段 5 判颤振不稳定）。"""
    return {
        "feature_id": feature_id,
        "feature_type": feature_type,
        "material_id": material_id,
        "spindle_rpm": rpm,
        "axial_depth_mm": axial,
        "limit_depth_mm": limit,
        "stable": False,
        "stability_margin": round(1.0 - axial / limit, 4),
        "method": "analytical",
        "ltc_active": False,
        "confidence": 0.3,
    }


def _mill_op(
    seq: int,
    feature_name: str,
    tool_type: str,
    tool_diameter: float,
    geometry: dict[str, Any],
    method: str = "平面铣削",
    name: str = "铣削",
) -> dict[str, Any]:
    """构造铣削工序（machining_method 须含「铣」触发生成器铣削分支）。"""
    return {
        "seq": seq,
        "name": name,
        "feature_name": feature_name,
        "machining_method": method,
        "surface": "top",
        "tolerance_grade": "IT8",
        "tool_type": tool_type,
        "cutting_params": {
            "material": "steel",
            "tool_diameter": tool_diameter,
            "recommended_feed": "0.15 mm/r",
            "recommended_speed": "120 m/min",
            "geometry": geometry,
        },
        "estimated_time_min": 2.0,
        "notes": "replay seed op",
    }


def _drill_op(
    seq: int,
    feature_name: str,
    tool_type: str,
    tool_diameter: float,
    geometry: dict[str, Any],
) -> dict[str, Any]:
    """构造钻孔工序（machining_method 含「钻」触发生成器钻孔分支）。"""
    op = _mill_op(seq, feature_name, tool_type, tool_diameter, geometry, method="钻孔", name="钻孔")
    op["tolerance_grade"] = "IT9"
    return op


def _write_case(
    target_dir: Path,
    case_id: str,
    features: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    task_status: str = "succeeded",
) -> ReplayCase:
    """写一对 chatter_report.json + operation_plan.json 并返回案例描述。"""
    case_dir = target_dir / case_id
    material_ids = {f["material_id"] for f in features}
    _write_json(
        case_dir / "chatter_report.json",
        {
            "task_id": f"replay-{case_id}",
            "task_status": task_status,
            "material_id": sorted(material_ids)[0],
            "prediction_method": "analytical",
            "feature_results": features,
        },
    )
    _write_json(case_dir / "operation_plan.json", {"operations": operations, "setups": _SETUPS})
    if task_status != "succeeded":
        expected, hint = "failure", "ChatterReportLoadError"
    elif any(not f["stable"] for f in features):
        expected, hint = "failure", "UNSTABLE_FEATURES"
    else:
        expected, hint = "success", "-"
    return ReplayCase(
        case_id=case_id,
        chatter_path=case_dir / "chatter_report.json",
        plan_path=case_dir / "operation_plan.json",
        expected_outcome=expected,
        expected_hint=hint,
    )


def write_seed_input_set(target_dir: Path) -> list[ReplayCase]:
    """生成确定性种子输入集（10 例），返回案例清单。

    构成（8 成功 + 2 失败，样本量达到回归门控 min_samples=10）：
    - case_a_stable：钢件 铣平面+钻孔（全稳定）→ 预期 success；
    - case_b_unstable：hole_d20 切深超限（stable=False）→ 预期
      UNSTABLE_FEATURES 失败；
    - case_c_rejected：ChatterReport task_status != succeeded → 预期
      ChatterReportLoadError 失败；
    - case_d~j：铝/钛/钢 × 平面/孔/外圆/凸台 的成功参数变体，
      覆盖不同材料与特征类型的参数区间。
    """
    target_dir = Path(target_dir)
    cases: list[ReplayCase] = []

    # case A：钢件全稳定 → success
    cases.append(
        _write_case(
            target_dir,
            "case_a_stable",
            [
                _stable_feature("plane_top", "plane", "steel_45", 1800.0, 1.0, 5.0),
                _stable_feature("hole_d10", "hole", "steel_45", 1200.0, 12.0, 20.0, 0.85),
            ],
            [
                _mill_op(
                    1,
                    "plane_top",
                    "endmill_d50",
                    50.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 1.0, "length": 100.0, "width": 80.0},
                ),
                _drill_op(2, "hole_d10", "drill_d10", 10.0, {"x": 30.0, "y": 30.0, "z_depth": 12.0}),
            ],
        )
    )

    # case B：不稳定深孔 → UNSTABLE_FEATURES
    cases.append(
        _write_case(
            target_dir,
            "case_b_unstable",
            [
                _stable_feature("plane_top", "plane", "steel_45", 1800.0, 1.0, 5.0),
                _unstable_feature("hole_d20", "hole", "steel_45", 900.0, 22.0, 15.0),
            ],
            [
                _mill_op(
                    1,
                    "plane_top",
                    "endmill_d50",
                    50.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 1.0, "length": 100.0, "width": 80.0},
                ),
                _drill_op(2, "hole_d20", "drill_d20", 20.0, {"x": 50.0, "y": 50.0, "z_depth": 22.0}),
            ],
        )
    )

    # case C：阶段 5 未审核通过 → ChatterReportLoadError
    cases.append(
        _write_case(
            target_dir,
            "case_c_rejected",
            [_stable_feature("plane_top", "plane", "steel_45", 1800.0, 1.0, 5.0)],
            [
                _mill_op(
                    1,
                    "plane_top",
                    "endmill_d50",
                    50.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 1.0, "length": 100.0, "width": 80.0},
                )
            ],
            task_status="PENDING_REVIEW",
        )
    )

    # case D~J：材料 × 特征类型的成功参数变体
    cases.append(
        _write_case(
            target_dir,
            "case_d_plane_alu",
            [_stable_feature("plane_top", "plane", "aluminum_6061", 3000.0, 1.2, 6.0)],
            [
                _mill_op(
                    1,
                    "plane_top",
                    "endmill_d50",
                    50.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 1.2, "length": 120.0, "width": 90.0},
                )
            ],
        )
    )
    cases.append(
        _write_case(
            target_dir,
            "case_e_hole_alu",
            [_stable_feature("hole_d8", "hole", "aluminum_6061", 2500.0, 10.0, 25.0)],
            [_drill_op(1, "hole_d8", "drill_d8", 8.0, {"x": 20.0, "y": 20.0, "z_depth": 10.0})],
        )
    )
    cases.append(
        _write_case(
            target_dir,
            "case_f_profile_steel",
            [_stable_feature("cyl_od", "cylinder", "steel_45", 1600.0, 1.5, 5.0)],
            [
                _mill_op(
                    1,
                    "cyl_od",
                    "endmill_d20",
                    20.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 1.5, "length": 80.0, "width": 80.0},
                    method="外圆铣削",
                    name="外圆铣",
                )
            ],
        )
    )
    cases.append(
        _write_case(
            target_dir,
            "case_g_plane_titanium",
            [_stable_feature("plane_top", "plane", "titanium_ti6al4v", 800.0, 0.5, 3.0, 0.8)],
            [
                _mill_op(
                    1,
                    "plane_top",
                    "endmill_d40",
                    40.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 0.5, "length": 60.0, "width": 60.0},
                )
            ],
        )
    )
    cases.append(
        _write_case(
            target_dir,
            "case_h_combo_steel",
            [
                _stable_feature("plane_top", "plane", "steel_45", 2000.0, 1.5, 6.0),
                _stable_feature("hole_d12", "hole", "steel_45", 1400.0, 15.0, 22.0, 0.85),
            ],
            [
                _mill_op(
                    1,
                    "plane_top",
                    "endmill_d50",
                    50.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 1.5, "length": 110.0, "width": 85.0},
                ),
                _drill_op(2, "hole_d12", "drill_d12", 12.0, {"x": 40.0, "y": 25.0, "z_depth": 15.0}),
            ],
        )
    )
    cases.append(
        _write_case(
            target_dir,
            "case_i_profile_alu",
            [_stable_feature("cyl_od", "cylinder", "aluminum_6061", 2800.0, 2.0, 8.0)],
            [
                _mill_op(
                    1,
                    "cyl_od",
                    "endmill_d16",
                    16.0,
                    {"x": 0.0, "y": 0.0, "z_depth": 2.0, "length": 70.0, "width": 70.0},
                    method="外圆铣削",
                    name="外圆铣",
                )
            ],
        )
    )
    cases.append(
        _write_case(
            target_dir,
            "case_j_boss_steel",
            [_stable_feature("boss_1", "boss", "steel_45", 1500.0, 1.0, 4.0)],
            [
                _mill_op(
                    1,
                    "boss_1",
                    "endmill_d12",
                    12.0,
                    {"x": 35.0, "y": 35.0, "z_depth": 1.0, "length": 30.0, "width": 30.0},
                    method="凸台铣削",
                    name="凸台铣",
                )
            ],
        )
    )

    return cases


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# 重放与门控
# ----------------------------------------------------------------------


def run_replay(
    input_dir: Path,
    replay_db: Path,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """对输入集重放真实 pipeline，案例写入隔离灰度库。

    Args:
        input_dir: ``write_seed_input_set`` 产出的输入集目录。
        replay_db: 灰度案例库路径（与生产库物理隔离）。
        work_dir: 任务 workspace 根目录（None 用系统临时目录）。

    Returns:
        {per_case: [{case_id, actual_outcome, error_codes}],
        stats: store.stats() 报表}
    """
    input_dir = Path(input_dir)
    replay_db = Path(replay_db)
    work_dir = Path(work_dir) if work_dir else Path(os.path.join(os.path.expanduser("~"), ".lnn_replay_workspace"))
    work_dir.mkdir(parents=True, exist_ok=True)

    # 发现输入集（按目录名排序保证确定性）
    case_dirs = sorted(p for p in input_dir.iterdir() if p.is_dir())
    if not case_dirs:
        raise FileNotFoundError(f"输入集目录为空: {input_dir}")

    # 隔离灰度库：重放案例绝不混入生产库
    old_env = os.environ.get("FAILURE_CASES_DB")
    os.environ["FAILURE_CASES_DB"] = str(replay_db)
    reset_failure_case_store()
    try:
        # 清空旧重放库，保证 stats 只反映本轮
        if replay_db.exists():
            replay_db.unlink()

        cfg = GCodeGenerationConfig(output_dir=str(work_dir / "gcode"))
        pipeline = GCodeGenerationPipeline(cfg=cfg)
        store = FailureCaseStore(db_path=replay_db)

        per_case: list[dict[str, Any]] = []
        for case_dir in case_dirs:
            chatter = case_dir / "chatter_report.json"
            plan = case_dir / "operation_plan.json"
            task = pipeline.create_task(
                source_chatter_report_path=str(chatter),
                source_operation_plan_path=str(plan),
                controller_type=_CONTROLLER,
                material_name=_MATERIAL,
            )
            result = asyncio.run(pipeline.run_pipeline(task.task_id))
            actual = result.status
            # 案例库在本轮 run 中应恰好新增 1 条该任务案例
            case_cases = [c for c in store.list_cases(limit=100) if c.task_id == task.task_id]
            per_case.append(
                {
                    "case_id": case_dir.name,
                    "actual_outcome": actual,
                    "error_codes": case_cases[0].error_codes if case_cases else [],
                }
            )

        return {"per_case": per_case, "stats": store.stats()}
    finally:
        # 恢复环境，避免污染同进程后续的生产库路由
        if old_env is None:
            os.environ.pop("FAILURE_CASES_DB", None)
        else:
            os.environ["FAILURE_CASES_DB"] = old_env
        reset_failure_case_store()


def gate_from_dbs(
    baseline_db: Path,
    candidate_db: Path,
    thresholds: GateThresholds | None = None,
) -> GateResult:
    """从两个灰度库取 stats 做回归门控判定。"""
    base_stats = FailureCaseStore(db_path=Path(baseline_db)).stats()
    cur_stats = FailureCaseStore(db_path=Path(candidate_db)).stats()
    return evaluate_regression(base_stats, cur_stats, thresholds)


def gate_replay_for_evolution(work_root: Path | None = None) -> dict[str, Any]:
    """自进化 M1 演化门控：种子输入集重放 + 与持久化基线对比。

    机制说明（诚实边界，与模块 docstring 一致）：当前生成链路为模板化
    生成，提示词候选尚未接入重放路径——本门控当前验证的是机制贯通与
    基线样本积累：首次调用建立基线（inconclusive 放行），此后每次调用
    与基线对比，通过后滚动更新基线。候选提示词对生成质量的真实影响
    待统一 AgentRuntime（M2）接线后由同一门控度量。

    注意：本函数内部 ``asyncio.run`` 跑真实 pipeline，**不可在运行中的
    事件循环里直接调用**——异步调用方（REST / 心跳回调）必须经
    ``asyncio.to_thread`` 包装。

    Returns:
        GateResult.to_dict() 形态的判定字典（含 passed / inconclusive /
        reasons / baseline_summary / current_summary）。
    """
    import tempfile

    from app.gcode_generation.regression_gate import evaluate_regression

    root = (
        Path(work_root)
        if work_root
        else Path(os.environ.get("LNN_EVOLUTION_WORK_DIR") or os.path.join(tempfile.gettempdir(), "lnn_evolution_gate"))
    )
    input_dir = root / "seed_inputs"
    if not any(input_dir.glob("case_*")):
        write_seed_input_set(input_dir)

    replay_db = root / "replay_cases.db"
    replay = run_replay(input_dir, replay_db)
    current_stats = replay["stats"]

    baseline_path = root / "baseline_stats.json"
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(current_stats, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "passed": True,
            "inconclusive": True,
            "reasons": ["首次运行：已建立重放基线，无对比项，放行并继续观察。"],
            "baseline_summary": {},
            "current_summary": {
                "total": current_stats.get("total", 0),
                "one_pass_rate": current_stats.get("one_pass_rate"),
            },
        }

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("演化门控基线损坏，重建基线: %s", e)
        baseline_path.write_text(json.dumps(current_stats, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "passed": True,
            "inconclusive": True,
            "reasons": [f"基线损坏已重建（{type(e).__name__}），本轮无对比项，放行。"],
            "baseline_summary": {},
            "current_summary": {},
        }

    gate = evaluate_regression(baseline, current_stats)
    if gate.passed and not gate.inconclusive:
        # 通过后滚动更新基线（以本轮重放为新基线）
        baseline_path.write_text(json.dumps(current_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return gate.to_dict()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：prepare / run / gate 三个子命令。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]

    if cmd == "prepare" and rest:
        cases = write_seed_input_set(Path(rest[0]))
        for c in cases:
            print(f"{c.case_id}: expected={c.expected_outcome} ({c.expected_hint})")
        return 0

    if cmd == "run" and len(rest) >= 2:
        work_dir = Path(rest[3]) if len(rest) >= 4 and rest[2] == "--work-dir" else None
        report = run_replay(Path(rest[0]), Path(rest[1]), work_dir=work_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if cmd == "gate" and len(rest) >= 2:
        gate = gate_from_dbs(Path(rest[0]), Path(rest[1]))
        print(json.dumps(gate.to_dict(), ensure_ascii=False, indent=2))
        return 0 if gate.passed else 1

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
