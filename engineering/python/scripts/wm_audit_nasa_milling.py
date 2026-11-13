"""NASA Milling（UC Berkeley BEST Lab / NASA PCoE）结构审计.

只做事实核验，不改动原始数据：字段、案例数、走刀数、参数组合、
VB 覆盖与信号长度/有限性。审计结果用于决定适配器是否可行，
不产出任何模型结论。

用法（仓库根）::

    python engineering/python/scripts/wm_audit_nasa_milling.py \
        --mat data/external/nasa_milling/mill.mat \
        --out reports/nasa_milling_audit_<date>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

REQUIRED_FIELDS = ("case", "run", "VB", "time", "DOC", "feed", "material",
                   "smcAC", "smcDC", "vib_table", "vib_spindle", "AE_table", "AE_spindle")
SIGNAL_FIELDS = ("smcAC", "smcDC", "vib_table", "vib_spindle", "AE_table", "AE_spindle")


def _scalar(entry, field: str):
    value = entry[field].ravel()
    return None if value.size == 0 else float(value[0])


def _signal(entry, field: str) -> np.ndarray:
    return np.asarray(entry[field]).ravel().astype(np.float64)


def audit(mat_path: Path) -> dict:
    from scipy.io import loadmat

    loaded = loadmat(mat_path, struct_as_record=True, squeeze_me=False)
    if "mill" not in loaded:
        raise ValueError("mat 文件缺少 mill 结构")
    mill = loaded["mill"].ravel()
    fields = set(mill[0].dtype.names or ())
    missing = set(REQUIRED_FIELDS) - fields
    if missing:
        raise ValueError(f"mill 结构缺少字段: {sorted(missing)}")
    cases: dict[int, dict] = {}
    runs = []
    for index, entry in enumerate(mill):
        case = int(_scalar(entry, "case"))
        run = int(_scalar(entry, "run"))
        vb = _scalar(entry, "VB")
        doc = _scalar(entry, "DOC")
        feed = _scalar(entry, "feed")
        material = _scalar(entry, "material")
        lengths = {name: int(_signal(entry, name).size) for name in SIGNAL_FIELDS}
        finite = all(bool(np.isfinite(_signal(entry, name)).all()) for name in SIGNAL_FIELDS)
        runs.append({"index": index, "case": case, "run": run, "VB": vb, "DOC": doc,
                     "feed": feed, "material": material, "signal_lengths": lengths, "finite": finite})
        case_entry = cases.setdefault(case, {"DOC": set(), "feed": set(), "material": set(),
                                            "runs": 0, "vb_measured": 0, "vb_values": []})
        case_entry["DOC"].add(doc)
        case_entry["feed"].add(feed)
        case_entry["material"].add(material)
        case_entry["runs"] += 1
        if vb is not None and np.isfinite(vb):
            case_entry["vb_measured"] += 1
            case_entry["vb_values"].append(vb)
    combos: dict[str, list[int]] = {}
    case_summary = {}
    for case, entry in sorted(cases.items()):
        combo = f"material={sorted(entry['material'])},DOC={sorted(entry['DOC'])},feed={sorted(entry['feed'])}"
        combos.setdefault(combo, []).append(case)
        case_summary[str(case)] = {"DOC": sorted(entry["DOC"]), "feed": sorted(entry["feed"]),
                                   "material": sorted(entry["material"]), "runs": entry["runs"],
                                   "vb_measured": entry["vb_measured"],
                                   "vb_min": min(entry["vb_values"]) if entry["vb_values"] else None,
                                   "vb_max": max(entry["vb_values"]) if entry["vb_values"] else None}
    signal_lengths = sorted({length for run in runs for length in run["signal_lengths"].values()})
    vb_all = [run["VB"] for run in runs if run["VB"] is not None and np.isfinite(run["VB"])]
    return {
        "source": {"path": str(mat_path.resolve()), "sha256": hashlib.sha256(mat_path.read_bytes()).hexdigest(),
                   "size_bytes": mat_path.stat().st_size},
        "structure": {"runs": len(runs), "cases": len(cases), "fields": sorted(fields),
                      "signal_lengths_unique": signal_lengths,
                      "all_signals_finite": bool(all(run["finite"] for run in runs))},
        "actions": {"combos": {combo: cases for combo, cases in sorted(combos.items())},
                    "cases_per_combo": {combo: len(cases) for combo, cases in sorted(combos.items())},
                    "within_case_variation": "DOC/feed/material 每案例恒定（set 大小见 case_summary）",
                    "spindle_speed_field_present": "rpm" in fields or "speed" in fields},
        "labels": {"vb_measured_runs": len(vb_all), "vb_missing_runs": len(runs) - len(vb_all),
                   "vb_min": float(min(vb_all)) if vb_all else None,
                   "vb_max": float(max(vb_all)) if vb_all else None},
        "case_summary": case_summary,
        "verdict": {"adapter_feasible": bool(len(signal_lengths) == 1 and all(run["finite"] for run in runs)),
                    "note": "动作仅案例级常量；转速字段缺失需补录或标注"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mat", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise FileExistsError("输出已存在，请使用新路径")
        report = audit(args.mat)
    except (ValueError, OSError, KeyError) as exc:
        print(f"[audit failed] {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    structure = report["structure"]
    print(f"[audit] cases={structure['cases']} runs={structure['runs']} "
          f"signal_len={structure['signal_lengths_unique']} vb={report['labels']['vb_measured_runs']}/"
          f"{structure['runs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
