"""从 failure_cases.db 导入真实拦截记录为否决案例（B2 v0）.

只导入 ``outcome='failure'`` 且带结构化错误码的记录；逐条脱敏后写入
JSONL。默认取每个错误码最多 ``--per-code`` 条、按切削参数去重，避免
同一参数的重复样本淹没案例库。

用法（仓库根）::

    python engineering/python/scripts/import_rejection_cases.py \
        --db engineering/python/data/failure_cases.db \
        --out data/rejection_cases/rejection_cases.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from app.cam_validation.rejection_case_store import (  # noqa: E402
    RejectionCase,
    RejectionCaseStore,
    make_case_id,
    redact_text,
)

DEPTH_RE = re.compile(r"axial_depth=([0-9.]+)mm")
LIMIT_RE = re.compile(r"limit_depth=([0-9.]+)mm")

MATERIAL_FAMILY = (("钢", "steel_carbon"), ("铝", "aluminium"), ("钛", "titanium"),
                   ("不锈钢", "stainless"), ("铸铁", "cast_iron"))


def material_family(name: str) -> str:
    for keyword, family in MATERIAL_FAMILY:
        if keyword in (name or ""):
            return family
    return "unknown"


def severity_for(axial_depth: float | None, limit_depth: float | None) -> str:
    """按超限比例分档：>1.5 倍 critical，>1.0 high，其余 medium。"""
    if not axial_depth or not limit_depth:
        return "high"
    ratio = axial_depth / limit_depth
    if ratio >= 1.5:
        return "critical"
    if ratio > 1.0:
        return "high"
    return "medium"


def build_cases(db_path: Path, per_code: int) -> list[RejectionCase]:
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(
            "SELECT case_id, task_id, source, error_codes, error_messages, controller_type,"
            " material_name, created_at FROM failure_cases WHERE outcome='failure'"
        ).fetchall()
    finally:
        connection.close()

    seen_params: dict[str, set[str]] = {}
    cases: list[RejectionCase] = []
    for case_id, task_id, source, error_codes, error_messages, controller, material, created_at in rows:
        codes = json.loads(error_codes or "[]")
        if not codes:
            continue
        messages = json.loads(error_messages or "[]")
        message = messages[0] if messages else ""
        key = codes[0]
        bucket = seen_params.setdefault(key, set())
        if len(bucket) >= per_code:
            continue
        depth_matches = DEPTH_RE.search(message)
        limit_matches = LIMIT_RE.search(message)
        axial = float(depth_matches.group(1)) if depth_matches else None
        limit = float(limit_matches.group(1)) if limit_matches else None
        params = {}
        if axial is not None:
            params["axial_depth_mm"] = axial
        if limit is not None:
            params["limit_depth_mm"] = limit
        signature = json.dumps(params, sort_keys=True)
        if signature in bucket:
            continue
        bucket.add(signature)

        cleaned, notes = redact_text(message[:400])
        cases.append(RejectionCase(
            case_id=make_case_id("failure_cases", str(case_id)),
            source_task_id=str(task_id),
            stage="stability_gate" if "UNSTABLE" in key.upper() else "cam_validation",
            decision="blocked",
            severity=severity_for(axial, limit),
            error_codes=codes,
            evidence_summary=cleaned,
            material_family=material_family(material),
            controller_type=(controller or "").strip(),
            params=params,
            created_at=float(created_at or 0.0),
            desensitized=True,
            redaction_notes=notes,
        ))
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-code", type=int, default=3)
    args = parser.parse_args()
    try:
        if args.per_code <= 0:
            raise ValueError("per-code 必须为正")
        cases = build_cases(args.db, args.per_code)
        if not cases:
            raise ValueError("未从数据库读到可导入的失败案例")
        store = RejectionCaseStore(args.out)
        added = store.append(cases)
        stats = store.stats()
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        print(f"[import failed] {exc}", file=sys.stderr)
        return 1
    print(f"[import] candidates={len(cases)} added={added} total={stats['total']} "
          f"severity={stats['by_severity']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
