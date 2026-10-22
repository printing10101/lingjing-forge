"""Reproducible DXF -> NC -> validation -> feedback-driven coordinate policy.

This is a sandboxed rule evolution experiment, not model training or machine
execution. The legacy Z convention is compared with the drawing thickness;
only a fully evaluated, material-removing candidate can become the local policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def acceptance(report: dict[str, Any]) -> tuple[bool, list[str]]:
    """A high aggregate score cannot substitute for any required dimension."""
    reasons = []
    if report.get("passed") is not True:
        reasons.append("EVALUATION_FAILED: 统一校验未通过")
    dimensions = {item["name"]: item for item in report.get("dimensions", [])}
    for name in ("syntax", "geometry", "physics"):
        dim = dimensions.get(name, {})
        if dim.get("status") != "evaluated" or dim.get("passed") is not True:
            reasons.append(f"{name}: 未完整执行或未通过")
    geometry = dimensions.get("geometry", {}).get("detail", {})
    if geometry.get("removed_voxel_count", 0) <= 0:
        reasons.append("NO_MATERIAL_REMOVAL: 刀轨没有实际去除毛坯体素")
    if geometry.get("cutting_segments", 0) <= 0:
        reasons.append("NO_CUTTING_SEGMENTS: 没有切削运动")
    return not reasons, reasons


def cutting_parameters(gcode: str, stock_top_z: float) -> dict[str, Any] | None:
    """Use the generated NC's engaged moves, never the planner's default RPM/feed."""
    from app.simulation.toolpath_parser import ToolpathParser

    segments = ToolpathParser("fanuc_0i").parse_gcode(gcode)
    cuts = [seg for seg in segments if seg.type == "linear" and min(seg.start[2], seg.end[2]) < stock_top_z]
    if not cuts:
        return None
    # Interleaving Z levels from different operations understates their stepdown.
    # Analyse each independently, retaining global modal RPM/feed above.
    stepdowns = []
    for block in re.split(r"(?m)^; ---- OP", gcode):
        op_segments = ToolpathParser("fanuc_0i").parse_gcode(block)
        levels = sorted(
            {stock_top_z, *(seg.end[2] for seg in op_segments if seg.type == "linear" and seg.end[2] < stock_top_z)},
            reverse=True,
        )
        stepdowns.extend(upper - lower for upper, lower in zip(levels, levels[1:]))
    stepdown = max(stepdowns)
    return {
        "spindle_rpm": min(seg.spindle_speed or 0 for seg in cuts),
        "feed_rate": max(seg.feed_rate or 0 for seg in cuts),
        "depth_of_cut": stepdown,
        "material": "steel_45",
        "tool": "endmill_d10",
        "machine": "vmc_850",
    }


def drawing_contract(summary: dict[str, Any]) -> dict[str, float]:
    """Require a simple milling plate; unsupported drilling must not be silently skipped."""
    if summary.get("hole_count", 0):
        raise ValueError("[UNSUPPORTED_DRILLING] 本实验只验收无孔板件。建议操作：先补齐固定循环逐段仿真再选择孔件")
    values = {
        "stock_length": float(summary.get("overall_length", 0)),
        "stock_width": float(summary.get("overall_width", 0)),
        "stock_height": float(summary.get("overall_height", 0)),
    }
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        raise ValueError("[INVALID_STOCK] 图纸包络或板厚无效。建议操作：明确板件尺寸后重试")
    if summary.get("contour_count", 0) != 1:
        raise ValueError("[UNSUPPORTED_CONTOURS] 本实验要求单一闭合外轮廓。建议操作：选择单轮廓板件")
    return values


def load_policy(path: Path, source_sha256: str, height: float) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("source_sha256") != source_sha256 or policy.get("status") != "accepted":
        raise ValueError("[POLICY_MISMATCH] 策略与图纸不匹配或未经验证。建议操作：重新执行本图纸候选验证")
    if policy.get("stock_top_z") != height:
        raise ValueError("[POLICY_Z_MISMATCH] 策略顶面与当前板厚不一致。建议操作：重新校验工件坐标")
    return policy


def run_drawing_feedback(source: Path, output: Path) -> dict[str, Any]:
    """Run baseline, feedback candidate, persisted-policy replay, and rejection test.

    Output must be new: prior evidence is never overwritten. All policy changes
    remain inside this directory; no production prompt, DB, or CNC is updated.
    """
    from app.dxf.process_service import DxfProcessService
    from app.evaluation import EvaluationService, GcodeEvaluationRequest

    source = Path(source).resolve(strict=True)
    if source.suffix.lower() != ".dxf":
        raise ValueError("[INVALID_DRAWING] 输入必须为 DXF。建议操作：提供 DXF 图纸路径")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    service = DxfProcessService()
    evaluation = EvaluationService()
    events: list[dict[str, Any]] = []

    def generate(label: str, top: float | None, max_stepdown: float | None = None) -> tuple[Any, str]:
        directory = output / label
        result = service.process(
            source, output_dir=directory, material="45钢", stock_top_z=top, max_stepdown=max_stepdown
        )
        write_json(directory / "process.json", result.to_dict())
        if not all(stage and stage.success for stage in (result.parse, result.features, result.model3d, result.gcode)):
            raise RuntimeError(
                f"[DRAWING_PIPELINE_FAILED] {label} 阶段失败。建议操作：查看 {directory / 'process.json'}"
            )
        nc = Path(result.gcode.summary["output"])
        return result, nc.read_text(encoding="utf-8")

    baseline, baseline_nc = generate("baseline", None)
    stock = drawing_contract(baseline.features.summary)
    height = stock["stock_height"]
    # The 2.5D generator uses D10, with an exterior offset of 5 mm each side.
    # Reserve explicit blank allowance rather than silently expanding it in validation.
    stock["stock_length"] += 12.0
    stock["stock_width"] += 12.0

    def validate(label: str, nc: str) -> dict[str, Any]:
        params = cutting_parameters(nc, height)
        report = evaluation.evaluate(
            GcodeEvaluationRequest(
                gcode_text=nc,
                safe_z=height + 2.0,
                stock_top_z=height,
                cutting_params=params,
                **stock,
            )
        ).to_dict()
        passed, reasons = acceptance(report)
        report.update(
            {
                "accepted": passed,
                "reasons": reasons,
                "nc_sha256": hashlib.sha256(nc.encode()).hexdigest(),
                "cutting_parameters_from_nc": params,
            }
        )
        write_json(output / label / "validation.json", report)
        return report

    baseline_report = validate("baseline", baseline_nc)
    events.append(
        {
            "kind": "validation_feedback",
            "source": "baseline/validation.json",
            "reasons": baseline_report["reasons"],
            "legacy_stock_top_z": baseline.gcode.summary["stock_top_z"],
            "drawing_thickness": height,
        }
    )
    write_json(output / "feedback.json", events)
    candidate, candidate_nc = generate("candidate", height)
    candidate_report = validate("candidate", candidate_nc)
    stepdown = None
    evidence = "candidate/validation.json"
    # Physics feedback may reduce layer depth, with a bounded three-attempt budget.
    # It never alters validators, total feature depth, or the source drawing.
    for attempt in range(1, 4):
        if candidate_report["accepted"]:
            break
        physics = next((d for d in candidate_report["dimensions"] if d["name"] == "physics"), {})
        params = candidate_report.get("cutting_parameters_from_nc")
        if physics.get("status") != "evaluated" or physics.get("passed") or not params:
            break
        stepdown = params["depth_of_cut"] / 2.0
        label = f"candidate_depth_{attempt}"
        events.append(
            {"kind": "physics_feedback", "source": evidence, "action": "halve_max_stepdown", "max_stepdown": stepdown}
        )
        candidate, candidate_nc = generate(label, height, stepdown)
        candidate_report = validate(label, candidate_nc)
        evidence = f"{label}/validation.json"
    policy = {
        "version": 1,
        "kind": "drawing_coordinate_rule",
        "source_sha256": source_hash,
        "stock_top_z": height,
        "max_stepdown": stepdown,
        "status": "accepted" if candidate_report["accepted"] else "rejected",
        "evidence": evidence,
        "feedback": "feedback.json",
    }
    write_json(output / "candidate_policy.json", policy)
    if not candidate_report["accepted"]:
        summary = {
            "completed": False,
            "stage": "candidate_rejected",
            "policy": policy,
            "reasons": candidate_report["reasons"],
        }
        write_json(output / "summary.json", summary)
        return summary

    policy_path = output / "active_policy.json"
    write_json(policy_path, policy)
    loaded = load_policy(policy_path, source_hash, height)
    _, replay_nc = generate("policy_replay", loaded["stock_top_z"], loaded.get("max_stepdown"))
    replay_report = validate("policy_replay", replay_nc)
    # Re-evaluate the observed legacy candidate to demonstrate refusal to regress.
    before_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    rejected = {
        "stock_top_z": baseline.gcode.summary["stock_top_z"],
        "accepted": baseline_report["accepted"],
        "reasons": baseline_report["reasons"],
    }
    if rejected["accepted"]:
        rejected["rejection_test"] = "not_applicable: legacy candidate also passed"
    else:
        rejected["rejection_test"] = "failed candidate was not promoted"
    write_json(output / "rejected_candidate.json", rejected)
    unchanged = before_hash == hashlib.sha256(policy_path.read_bytes()).hexdigest()
    events.extend(
        [
            {"kind": "candidate_promoted", "policy": "active_policy.json"},
            {"kind": "policy_consumed_on_next_generation", "evidence": "policy_replay/process.json"},
            {"kind": "failed_candidate_rejected", "active_policy_unchanged": unchanged},
        ]
    )
    write_json(output / "feedback.json", events)
    summary = {
        "completed": bool(replay_report["accepted"] and unchanged),
        "source": str(source),
        "source_sha256": source_hash,
        "source_classification": "repository DXF fixture; not a verified factory drawing",
        "controller": "fanuc_0i",
        "material": "45钢",
        "stock": stock,
        "baseline_accepted": baseline_report["accepted"],
        "candidate_accepted": candidate_report["accepted"],
        "replay_accepted": replay_report["accepted"],
        "failed_candidate_rejected": not rejected["accepted"],
        "active_policy_unchanged_after_rejection": unchanged,
        "nc_changed": baseline_report["nc_sha256"] != candidate_report["nc_sha256"],
        "replay_nc_matches_candidate": replay_nc == candidate_nc,
        "final_nc": str(output / "policy_replay" / f"{source.stem}.fanuc_0i.nc"),
        "limitations": [
            "Rule-level coordinate feedback evolution; no LLM, Nexent, or model training in this experiment",
            "2D plate thickness inferred by existing extractor; this is an explicit experiment assumption",
            "Single-contour 3-axis milling; D10 configured tool, no drilling, fixtures or holder simulation",
            "Voxel check detects below-stock-bottom cuts and rapid collisions; it does not verify final shape tolerance",
            "Analytical force/chatter fallbacks are reported; no real machine or machining feedback",
            "Failed candidates are rejected before promotion; this is not a test of post-release automatic rollback",
        ],
    }
    write_json(output / "summary.json", summary)
    return summary
