"""harness 归属抽样（自进化 M1 · 从 trace 反查「哪版配置产出了这条结果」）。

``PipelineResult`` 现在带 ``harness_id`` 并随 trace 落盘，失败案例库里则用
``task_id == pipeline_id`` 关联。本模块负责把两边接起来，回答 A/B 之前必须
先回答的两个问题：

1. **够不够**：某个 harness 快照下已积累多少可复用的失败案例？
2. **取哪些**：按失败来源/错误码分层抽样，构成 ``TaskCase`` 列表。

历史 trace 早于 harness_id 字段，归属为 ``""``；这是预期状态，不是数据
损坏。判定标准写在 ``coverage()`` 的返回值里。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent.harness.ab_runner import TaskCase

__all__ = ["Attribution", "coverage", "load_attribution", "sample_cases"]

TRACE_GLOB = "agent_trace_*.jsonl"
UNATTRIBUTED = ""


@dataclass
class Attribution:
    """trace 目录的归属扫描结果。"""

    by_pipeline: dict[str, str] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    files_scanned: int = 0
    lines_scanned: int = 0
    bad_lines: int = 0

    def harness_of(self, pipeline_id: str) -> str:
        return self.by_pipeline.get(pipeline_id, UNATTRIBUTED)


def _iter_trace_files(trace_dir: Path, days: int | None) -> list[Path]:
    files = sorted(trace_dir.glob(TRACE_GLOB))
    if not files or days is None:
        return files
    cutoff = datetime.now(UTC) - timedelta(days=days)
    keep: list[Path] = []
    for f in files:
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if mtime >= cutoff:
            keep.append(f)
    return keep


def load_attribution(trace_dir: str | Path, days: int | None = None) -> Attribution:
    """扫描 trace 目录，建立 ``pipeline_id -> harness_id`` 映射。

    Args:
        trace_dir: orchestrator 的 ``trace_log_dir``。
        days: 只扫最近 N 天的文件；``None`` 表示全量。

    坏行（JSON 解析失败 / 缺 pipeline_id）只计数不抛异常——trace 是旁路
    产物，不能让审计工具因个别坏行整批失败。
    """
    root = Path(trace_dir)
    att = Attribution()
    if not root.exists():
        return att
    for path in _iter_trace_files(root, days):
        att.files_scanned += 1
        try:
            fh = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                att.lines_scanned += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    att.bad_lines += 1
                    continue
                if not isinstance(row, dict):
                    att.bad_lines += 1
                    continue
                pid = str(row.get("pipeline_id") or "")
                if not pid:
                    att.bad_lines += 1
                    continue
                hid = str(row.get("harness_id") or UNATTRIBUTED)
                att.by_pipeline[pid] = hid
                att.counts[hid] += 1
    return att


def coverage(trace_dir: str | Path, days: int | None = 7) -> dict[str, Any]:
    """归属覆盖率报表：现在能不能做 A/B，看这个。"""
    att = load_attribution(trace_dir, days=days)
    total = att.lines_scanned
    valid = total - att.bad_lines
    attributed = sum(n for h, n in att.counts.items() if h != UNATTRIBUTED)
    return {
        "files": att.files_scanned,
        "lines": total,
        "bad_lines": att.bad_lines,
        "valid_lines": valid,
        "attributed": attributed,
        "unattributed": att.counts.get(UNATTRIBUTED, 0),
        # 分母取「可解析行」：坏行属数据损坏，用 bad_lines 单独暴露，
        # 混进分母会把解析问题误报成归属缺失。
        "coverage": round(attributed / valid, 4) if valid else 0.0,
        "by_harness": dict(att.counts.most_common()),
        "ready_for_ab": attributed >= 20,
    }


def sample_cases(
    trace_dir: str | Path,
    *,
    harness_id: str | None = None,
    limit: int = 50,
    source: str | None = None,
    days: int | None = None,
    store: Any | None = None,
) -> list[TaskCase]:
    """从失败案例库抽样本，并用 trace 归属过滤。

    Args:
        trace_dir: orchestrator 的 trace 目录。
        harness_id: 只保留该快照下的案例；``None`` 表示不过滤归属。
        limit: 最多返回条数（按案例库时间倒序取）。
        source: 只取指定失败来源（``unstable_features`` / ``llm_planning`` 等）。
        days: trace 扫描窗口。
        store: ``FailureCaseStore``；缺省用全局单例。

    Note:
        需先灌足够量的失败样本（``limit`` 内），否则返回可能为空。
    """
    if store is None:
        from app.gcode_generation.failure_case_store import get_failure_case_store

        store = get_failure_case_store()

    att = load_attribution(trace_dir, days=days)
    rows = store.list_cases(limit=limit, source=source, outcome="failure")

    out: list[TaskCase] = []
    for case in rows:
        hid = att.harness_of(case.task_id)
        if harness_id is not None and hid != harness_id:
            continue
        out.append(
            TaskCase(
                case_id=case.case_id,
                source=case.source,
                payload={
                    "task_id": case.task_id,
                    "controller_type": case.controller_type,
                    "material_name": case.material_name,
                    "gcode_text": case.gcode_text,
                    "error_messages": list(case.error_messages),
                    "harness_id": hid,
                },
                error_codes=tuple(case.error_codes),
            )
        )
    return out
