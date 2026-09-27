"""Markdown 格式性能报告生成器。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any


def generate_markdown_report(
    results: dict[str, dict[str, Any]],
    regression_results: list[dict[str, Any]] | None = None,
    violations: list[dict[str, Any]] | None = None,
    summary: str = "",
    output_path: str | None = None,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# 性能基准测试报告",
        "",
        f"**生成时间**: {timestamp}",
        f"**状态**: {summary}",
        "",
    ]

    has_regression = False
    has_critical = False
    if regression_results:
        for r in regression_results:
            if r.get("status") == "CRITICAL":
                has_critical = True
                has_regression = True
            elif r.get("status") == "WARNING":
                has_regression = True

    if has_critical:
        lines.append("> [!CAUTION]")
        lines.append("> **检测到严重性能回退！** 请立即检查以下指标。")
        lines.append("")
    elif has_regression:
        lines.append("> [!WARNING]")
        lines.append("> **检测到性能回退！** 建议审查相关变更。")
        lines.append("")

    lines.extend([
        "## 总体概览",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总体状态 | {'CRITICAL' if has_critical else 'WARNING' if has_regression else 'PASS'} |",
        f"| 基准测试模块 | {len(results)} |",
        f"| 回归检测项 | {len(regression_results) if regression_results else 0} |",
        "",
    ])

    for bench_type, metrics in results.items():
        lines.extend([
            f"## {bench_type.upper()} 基准测试",
            "",
            "| 指标 | 值 | 状态 |",
            "|------|------|------|",
        ])
        for metric, data in metrics.items():
            if isinstance(data, dict) and "value" in data:
                value = data["value"]
                unit = data.get("unit", "")
                status = data.get("status", "PASS")
                status_icon = {
                    "PASS": "✓", "WARNING": "⚠", "CRITICAL": "✗",
                    "SKIP": "-", "IMPROVED": "↑",
                }.get(status, "?")
                lines.append(f"| {metric} | {value} {unit} | {status_icon} {status} |")
        lines.append("")

    if regression_results:
        lines.extend([
            "## 回归检测结果",
            "",
            "| 指标 | 当前值 | 上次值 | 变化率 | 状态 |",
            "|------|--------|--------|--------|------|",
        ])
        for r in regression_results:
            change_pct = r.get("change_pct", 0)
            change_str = f"{change_pct:+.1f}%" if isinstance(change_pct, (int, float)) else "-"
            status = r.get("status", "")
            status_icon = {"PASS": "✓", "WARNING": "⚠", "CRITICAL": "✗", "NEW": "+", "IMPROVED": "↑"}.get(status, "?")
            lines.append(
                f"| {r.get('metric', '')} | {r.get('current', '')} | "
                f"{r.get('previous', '')} | {change_str} | {status_icon} {status} |"
            )
        lines.append("")

    if violations:
        lines.extend([
            "## 阈值违规",
            "",
            "| 指标 | 当前值 | 阈值 | 严重程度 | 描述 |",
            "|------|--------|------|----------|------|",
        ])
        for v in violations:
            lines.append(
                f"| {v.get('metric', '')} | {v.get('current_value', '')} | "
                f"{v.get('threshold_value', '')} | {v.get('severity', '')} | {v.get('message', '')} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"*报告由灵境制造性能基准测试系统自动生成于 {timestamp}*")

    content = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Markdown 报告已保存: {output_path}")

    return content
