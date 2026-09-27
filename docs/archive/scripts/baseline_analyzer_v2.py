#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
M0.1 Baseline data analyzer v2 - extracts more detailed metrics.
"""
from __future__ import annotations
import json
import os
import re
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


# ---------- TRACE LOG ----------
def analyze_trace():
    items, errs = load_jsonl(TRACE_FILE)
    out = {
        "file": str(TRACE_FILE),
        "lines": len(items),
        "parse_errors": errs,
        "size_bytes": TRACE_FILE.stat().st_size,
    }
    if not items:
        return out

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
    out["first_timestamp"] = timestamps[0].isoformat() if timestamps else None
    out["last_timestamp"] = timestamps[-1].isoformat() if timestamps else None
    if timestamps:
        out["span_seconds"] = (timestamps[-1] - timestamps[0]).total_seconds()
        out["span_days"] = (timestamps[-1].date() - timestamps[0].date()).days + 1

    # Unique tasks
    out["unique_tasks"] = len({it.get("task_id") for it in items if it.get("task_id")})

    # Per-task: first/last + count
    task_timestamps = defaultdict(list)
    for it in items:
        tid = it.get("task_id")
        ts = it.get("created_at") or it.get("timestamp")
        if tid and ts:
            try:
                task_timestamps[tid].append(datetime.fromisoformat(ts))
            except Exception:
                pass

    task_durations = []  # seconds
    for tid, tslist in task_timestamps.items():
        if len(tslist) >= 2:
            ts_sorted = sorted(tslist)
            dur = (ts_sorted[-1] - ts_sorted[0]).total_seconds()
            task_durations.append(dur)

    if task_durations:
        out["task_durations"] = {
            "count": len(task_durations),
            "avg_s": round(statistics.mean(task_durations), 4),
            "median_s": round(statistics.median(task_durations), 4),
            "p95_s": round(pct(task_durations, 0.95), 4),
            "min_s": round(min(task_durations), 4),
            "max_s": round(max(task_durations), 4),
        }

    # Step-level status
    step_status = Counter()
    step_names = Counter()
    for it in items:
        result = it.get("result") or {}
        if isinstance(result, dict):
            for step_name, step_data in result.items():
                if isinstance(step_data, dict):
                    step_names[step_name] += 1
                    s = step_data.get("status")
                    if s:
                        step_status[(step_name, s)] += 1

    out["step_distribution"] = dict(step_names)
    out["step_status_distribution"] = {f"{k[0]}|{k[1]}": v for k, v in step_status.items()}

    # Solver inference time
    solver_times = []
    for it in items:
        result = it.get("result") or {}
        if isinstance(result, dict):
            for step_name, step_data in result.items():
                if isinstance(step_data, dict):
                    sr = step_data.get("solver_result")
                    if isinstance(sr, dict):
                        ct = sr.get("computation_time_ms")
                        if ct is not None:
                            solver_times.append(float(ct))
    if solver_times:
        out["solver_inference_time_ms"] = {
            "count": len(solver_times),
            "avg_ms": round(statistics.mean(solver_times), 4),
            "median_ms": round(statistics.median(solver_times), 4),
            "p95_ms": round(pct(solver_times, 0.95), 4),
            "min_ms": min(solver_times),
            "max_ms": max(solver_times),
        }
    else:
        out["solver_inference_time_ms"] = None

    # Validation results
    v_passed = 0
    v_failed = 0
    v_empty = 0
    for it in items:
        vr = it.get("validation_result")
        if not vr:
            v_empty += 1
            continue
        if isinstance(vr, dict):
            if vr.get("passed") is True:
                v_passed += 1
            elif vr.get("passed") is False:
                v_failed += 1
            elif vr.get("validation_passed") is True:
                v_passed += 1
            else:
                v_empty += 1
    out["validation"] = {
        "passed": v_passed,
        "failed": v_failed,
        "empty": v_empty,
        "total_with_validation": v_passed + v_failed,
        "success_rate": round(v_passed / (v_passed + v_failed) * 100, 2) if (v_passed + v_failed) > 0 else None,
    }

    # Error reasons from validation_result.failure_reason
    error_reasons = Counter()
    for it in items:
        vr = it.get("validation_result")
        if isinstance(vr, dict):
            fr = vr.get("failure_reason")
            if fr:
                error_reasons[fr] += 1
            err = vr.get("error")
            if err:
                error_reasons[f"ERROR: {err}"] += 1
    out["error_reasons"] = dict(error_reasons.most_common(20))

    # Feedback errors
    feedback_errors = Counter()
    for it in items:
        fb = it.get("feedback")
        if fb and "失败" in str(fb):
            feedback_errors[fb[:80]] += 1
    out["feedback_error_samples"] = dict(feedback_errors.most_common(10))

    return out


# ---------- WORKFLOW LOGS ----------
def analyze_workflows():
    out = {}
    if not WORKFLOW_DIR.exists():
        return {"exists": False}
    for f in sorted(WORKFLOW_DIR.glob("*.jsonl")):
        items, errs = load_jsonl(f)
        if not items:
            out[f.name] = {"lines": 0, "parse_errors": errs, "size_bytes": f.stat().st_size}
            continue

        timestamps = []
        durations = []
        step_types = Counter()
        step_durations = defaultdict(list)
        success_count = 0
        failure_count = 0
        fallback_count = 0
        for it in items:
            ts = it.get("timestamp")
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        timestamps.append(datetime.fromtimestamp(ts))
                    else:
                        timestamps.append(datetime.fromisoformat(ts))
                except Exception:
                    pass
            st = it.get("step_type")
            if st:
                step_types[st] += 1
            d = it.get("duration_ms")
            if d is not None and d > 0:
                durations.append(float(d))
                if st:
                    step_durations[st].append(float(d))
            if it.get("success") is True:
                success_count += 1
            if it.get("success") is False:
                failure_count += 1
            if it.get("fallback_triggered"):
                fallback_count += 1

        timestamps.sort()
        # workflow_2026-05-05 has step_type field but not success
        # workflow_2026-05-15 has success but no step_type

        if "2026-05-05" in f.name:
            # step-based
            step_total_durations = {}
            for st, durs in step_durations.items():
                if durs:
                    step_total_durations[st] = {
                        "count": len(durs),
                        "avg_ms": round(statistics.mean(durs), 4),
                        "median_ms": round(statistics.median(durs), 4),
                        "p95_ms": round(pct(durs, 0.95), 4),
                        "sum_ms": round(sum(durs), 4),
                    }
            out[f.name] = {
                "lines": len(items),
                "parse_errors": errs,
                "size_bytes": f.stat().st_size,
                "first_timestamp": timestamps[0].isoformat() if timestamps else None,
                "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
                "step_type_counts": dict(step_types),
                "step_durations_ms": step_total_durations,
                "overall_avg_ms": round(statistics.mean(durations), 4) if durations else 0,
                "overall_p95_ms": round(pct(durations, 0.95), 4) if durations else 0,
            }
        else:
            # workflow-execution records
            out[f.name] = {
                "lines": len(items),
                "parse_errors": errs,
                "size_bytes": f.stat().st_size,
                "first_timestamp": timestamps[0].isoformat() if timestamps else None,
                "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
                "success": success_count,
                "failure": failure_count,
                "fallback_triggered": fallback_count,
                "failure_reasons": [it.get("fallback_reason", "") for it in items if it.get("fallback_triggered")],
            }

    return out


# ---------- AUDIT LOG ----------
def analyze_audit():
    if not AUDIT_FILE.exists():
        return {"exists": False}
    items, errs = load_jsonl(AUDIT_FILE)
    return {
        "file": str(AUDIT_FILE),
        "lines": len(items),
        "parse_errors": errs,
        "size_bytes": AUDIT_FILE.stat().st_size,
        "exists": True,
    }


if __name__ == "__main__":
    summary = {
        "trace_log": analyze_trace(),
        "workflow_logs": analyze_workflows(),
        "audit_log": analyze_audit(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
