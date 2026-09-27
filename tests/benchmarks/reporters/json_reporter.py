"""JSON 格式性能报告生成器。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


def generate_json_report(
    results: dict[str, dict[str, Any]],
    regression_results: list[dict[str, Any]] | None = None,
    violations: list[dict[str, Any]] | None = None,
    summary: str = "",
    output_path: str | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    report = {
        "timestamp": timestamp,
        "summary": summary,
        "results": results,
        "regression": {
            "entries": regression_results or [],
            "total_regressions": sum(
                1 for r in (regression_results or [])
                if r.get("status") in ("WARNING", "CRITICAL")
            ),
            "has_regression_issue": any(
                r.get("status") == "CRITICAL" for r in (regression_results or [])
            ),
        },
        "violations": violations or [],
        "metadata": {
            "generated_by": "灵境制造性能基准测试系统",
            "version": "1.0",
        },
    }

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"  JSON 报告已保存: {output_path}")

    return report
