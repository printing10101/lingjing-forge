"""I19: machine pre-check for manual_audit_20.csv; does not replace human sign-off."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

directory = Path(__file__).resolve().parent
root = directory.parents[2]
base = root / "engineering/python/data/training_sets/cutting_force_v2"
audit_path = base / "manual_audit_20.csv"
lookup_path = base / "conditions_lookup.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    with audit_path.open(encoding="utf-8-sig", newline="") as handle:
        audit = list(csv.DictReader(handle))
    with lookup_path.open(encoding="utf-8-sig", newline="") as handle:
        lookup = {row["experiment_tag"]: row for row in csv.DictReader(handle)}

    issues: list[dict[str, object]] = []
    checked_conditions = 0
    checked_numbers = 0
    for line_no, row in enumerate(audit, start=2):
        tag = (row.get("experiment_tag") or "").strip()
        source = lookup.get(tag)
        if source is None:
            issues.append({"line": line_no, "kind": "missing_lookup", "tag": tag})
            continue
        for field in ("spindle_rpm", "feed_per_tooth_mm", "axial_doc_mm"):
            raw = (row.get(field) or "").strip()
            expected = (source.get(field) or "").strip()
            if not raw:
                issues.append({"line": line_no, "kind": "missing_value", "tag": tag, "field": field})
                continue
            checked_conditions += 1
            if expected and abs(float(raw) - float(expected)) > 1e-9:
                issues.append({"line": line_no, "kind": "condition_mismatch", "tag": tag,
                               "field": field, "audit": raw, "lookup": expected})
        for field in ("force_z_window_mean", "force_z_window_rms", "t_start_s", "t_end_s"):
            raw = (row.get(field) or "").strip()
            if not raw:
                issues.append({"line": line_no, "kind": "missing_value", "tag": tag, "field": field})
                continue
            value = float(raw)
            checked_numbers += 1
            if not math.isfinite(value):
                issues.append({"line": line_no, "kind": "nonfinite_value", "tag": tag,
                               "field": field, "value": raw})

    report = {
        "checked_at": "2026-10-20",
        "scope": "manual_audit_20.csv vs conditions_lookup.csv",
        "rows": len(audit),
        "tags": sorted({row["experiment_tag"] for row in audit}),
        "datasets": sorted({row["dataset_tag"] for row in audit}),
        "checked_conditions": checked_conditions,
        "checked_numbers": checked_numbers,
        "condition_mismatches": 0 if not any(i["kind"] == "condition_mismatch" for i in issues) else
        sum(1 for i in issues if i["kind"] == "condition_mismatch"),
        "nonfinite_values": sum(1 for i in issues if i["kind"] == "nonfinite_value"),
        "missing_lookup": sum(1 for i in issues if i["kind"] == "missing_lookup"),
        "missing_values": sum(1 for i in issues if i["kind"] == "missing_value"),
        "issues": issues,
        "audit_sha256": _sha256(audit_path),
        "lookup_sha256": _sha256(lookup_path),
        "human_signoff": False,
        "conclusion": "机器预检通过不等于人工签注；20 行仍需工艺人员逐行确认量纲、工况与数值。",
    }
    target = directory / "manual-audit-precheck.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "rows", "checked_conditions", "checked_numbers", "condition_mismatches",
        "nonfinite_values", "missing_lookup", "missing_values", "human_signoff")},
        ensure_ascii=False), flush=True)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
