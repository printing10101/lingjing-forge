#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
M0.1 Baseline data analyzer.

Reads:
  - data/traces/trace_log.jsonl
  - logs/workflows/*.jsonl
  - logs/audit/audit_log.jsonl (often empty)

Outputs a JSON summary to stdout for downstream markdown report.
"""
from __future__ import annotations
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\<user>\Desktop\灵境制造（上线版）")
TRACE_FILE = ROOT / "data" / "traces" / "trace_log.jsonl"
AUDIT_FILE = ROOT / "logs" / "audit" / "audit_log.jsonl"
WORKFLOW_DIR = ROOT / "logs" / "workflows"


def load_jsonl(path: Path):
    items = []
    parse_errs = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except json.JSONDecodeError:
                parse_errs += 1
    return items, parse_errs


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def summarize_trace():
    if not TRACE_FILE.exists():
        return {"exists": False}
    items, errs = load_jsonl(TRACE_FILE)
    if not items:
        return {"exists": True, "line_count": 0, "parse_errors": errs}

    # Time range
    timestamps = []
    for it in items:
        ts = it.get("created_at") or it.get("timestamp")
        if ts:
            try:
                timestamps.append(datetime.fromisoformat(ts))
            except Exception:
                pass

    timestamps.sort()

    # Detect LNN-related records
    # LNN records would be those with: model_name=LNN, or workflow step containing "lnn"
    # In this dataset: each record has result blocks; check constraint_parsing, parameter_optimization,
    # solver_execution, result_validation (which are step status).
    lnn_records = []
    lnn_inference_times = []  # in milliseconds
    step_status = Counter()
    step_durations = defaultdict(list)  # step -> durations (synthetic, computed from contiguous traces)
    by_task = defaultdict(int)
    sota_count = 0
    validation_passed = 0
    validation_failed = 0

    for it in items:
        by_task[it.get("task_id", "unknown")] += 1
        if it.get("is_sota"):
            sota_count += 1
        vr = it.get("validation_result") or {}
        if isinstance(vr, dict):
            if vr.get("passed") is True:
                validation_passed += 1
            elif vr.get("passed") is False:
                validation_failed += 1

        # step-level result blocks
        result = it.get("result") or {}
        if isinstance(result, dict):
            for step_name, step_data in result.items():
                if isinstance(step_data, dict):
                    status = step_data.get("status")
                    if status:
                        step_status[status] += 1
                    # LNN-related: parameter_optimization step
                    # solver_result.computation_time_ms
                    if "solver_result" in step_data and isinstance(step_data["solver_result"], dict):
                        ct = step_data["solver_result"].get("computation_time_ms")
                        if ct is not None:
                            lnn_inference_times.append(float(ct))

    return {
        "exists": True,
        "line_count": len(items),
        "parse_errors": errs,
        "file_size_bytes": TRACE_FILE.stat().st_size,
        "first_timestamp": timestamps[0].isoformat() if timestamps else None,
        "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
        "unique_tasks": len(by_task),
        "sota_records": sota_count,
        "validation_passed": validation_passed,
        "validation_failed": validation_failed,
        "step_status": dict(step_status),
        "solver_inference_times_count": len(lnn_inference_times),
        "solver_inference_times_stats": {
            "avg_ms": round(statistics.mean(lnn_inference_times), 4) if lnn_inference_times else 0.0,
            "median_ms": round(statistics.median(lnn_inference_times), 4) if lnn_inference_times else 0.0,
            "p95_ms": round(pct(lnn_inference_times, 0.95), 4) if lnn_inference_times else 0.0,
            "min_ms": min(lnn_inference_times) if lnn_inference_times else 0.0,
            "max_ms": max(lnn_inference_times) if lnn_inference_times else 0.0,
        },
    }


def summarize_workflow():
    out = {}
    if not WORKFLOW_DIR.exists():
        return {"exists": False}
    for f in sorted(WORKFLOW_DIR.glob("*.jsonl")):
        items, errs = load_jsonl(f)
        if not items:
            out[f.name] = {"lines": 0, "parse_errors": errs}
            continue
        # Get first/last timestamp
        timestamps = []
        durations = []
        step_types = Counter()
        for it in items:
            ts = it.get("timestamp")
            if ts:
                try:
                    timestamps.append(datetime.fromisoformat(ts))
                except Exception:
                    pass
            st = it.get("step_type")
            if st:
                step_types[st] += 1
            d = it.get("duration_ms")
            if d is not None and d > 0:
                durations.append(float(d))
        timestamps.sort()
        out[f.name] = {
            "lines": len(items),
            "parse_errors": errs,
            "file_size_bytes": f.stat().st_size,
            "first_timestamp": timestamps[0].isoformat() if timestamps else None,
            "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
            "step_type_counts": dict(step_types),
            "duration_ms_count": len(durations),
            "duration_ms_avg": round(statistics.mean(durations), 4) if durations else 0.0,
            "duration_ms_p95": round(pct(durations, 0.95), 4) if durations else 0.0,
        }
    return {"exists": True, "files": out}


def summarize_audit():
    if not AUDIT_FILE.exists():
        return {"exists": False}
    items, errs = load_jsonl(AUDIT_FILE)
    return {
        "exists": True,
        "line_count": len(items),
        "parse_errors": errs,
        "file_size_bytes": AUDIT_FILE.stat().st_size,
    }


if __name__ == "__main__":
    summary = {
        "trace_log": summarize_trace(),
        "workflow_logs": summarize_workflow(),
        "audit_log": summarize_audit(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
