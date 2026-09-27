"""性能回归检测 CLI 工具。

支持：
- 对比最新两次基准测试运行结果
- 对比指定运行 ID 间的结果
- 检查所有指标是否在阈值范围内
- 输出回归检测报告（Markdown/JSON）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, "..", "..")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests.benchmarks.config.settings import BenchmarkSettings  # noqa: E402
from tests.benchmarks.config.thresholds import REGRESSION_THRESHOLDS, check_violations  # noqa: E402
from tests.benchmarks.database.repository import BenchmarkRepository  # noqa: E402


def _format_regression_table(entries: list[dict[str, Any]]) -> str:
    lines = [
        "| 指标 | 当前值 | 上次值 | 变化率 | 状态 |",
        "|------|--------|--------|--------|------|",
    ]
    for e in entries:
        change = e.get("change_pct", 0)
        change_str = f"{change:+.1f}%" if isinstance(change, (int, float)) else "-"
        lines.append(
            f"| {e.get('metric', '')} | {e.get('current', '')} | "
            f"{e.get('previous', '')} | {change_str} | {e.get('status', '')} |"
        )
    return "\n".join(lines)


def check_latest(args: argparse.Namespace) -> int:
    settings = BenchmarkSettings.from_env()
    repo = BenchmarkRepository(settings.db_path)
    latest = repo.get_latest_run()
    if not latest:
        print("错误: 数据库中没有基准测试记录。请先运行基准测试。")
        repo.close()
        return 1

    prev_results = repo.get_runs(limit=2, branch=latest.git_branch)
    if len(prev_results) < 2:
        # 提示走 stderr：--json 模式下 stdout 必须是纯 JSON，
        # 否则 CI 端 json.load 会因前缀文本解析失败
        print("信息: 数据库中仅有一条记录，无法进行回归对比。", file=sys.stderr)
        if args.json:
            print(json.dumps({"status": "insufficient_data", "message": "仅有一条记录"}, indent=2))
        else:
            print(f"最新运行: {latest.run_id} ({latest.created_at})")
        repo.close()
        return 0

    second = prev_results[1]
    comparison = repo.compare_versions(second.run_id, latest.run_id)
    comparisons = comparison.get("comparisons", [])

    entries = []
    for c in comparisons:
        change_pct = c.get("change_pct") or 0
        if c["value_a"] is not None and c["value_b"] is not None and c["value_a"] != 0:
            if change_pct > REGRESSION_THRESHOLDS["critical_pct"]:
                status = "CRITICAL"
            elif change_pct > REGRESSION_THRESHOLDS["warning_pct"]:
                status = "WARNING"
            elif change_pct < -REGRESSION_THRESHOLDS["critical_pct"]:
                status = "IMPROVED"
            else:
                status = "PASS"
        else:
            status = "NEW"
        entries.append({
            "metric": c.get("metric_name", ""),
            "current": c.get("value_b"),
            "previous": c.get("value_a"),
            "change_pct": change_pct,
            "status": status,
        })

    critical_count = sum(1 for e in entries if e["status"] == "CRITICAL")
    warning_count = sum(1 for e in entries if e["status"] == "WARNING")
    has_issues = critical_count > 0 or (warning_count > 0 and not args.ignore_warnings)

    violations = check_violations({
        e["metric"]: e["current"] for e in entries if e["current"] is not None
    })

    if args.json:
        output = {
            "timestamp": datetime.now().isoformat(),
            "run_a": {
                "id": comparison["run_a"]["run_id"],
                "commit": comparison["run_a"]["git_commit"],
                "date": comparison["run_a"]["created_at"],
            },
            "run_b": {
                "id": comparison["run_b"]["run_id"],
                "commit": comparison["run_b"]["git_commit"],
                "date": comparison["run_b"]["created_at"],
            },
            "entries": entries,
            "violations": [v.to_dict() for v in violations],
            "summary": {
                "critical": critical_count,
                "warning": warning_count,
                "critical_regression_detected": critical_count > 0,
            },
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print("=" * 70)
        print("性能回归检测报告")
        print("=" * 70)
        print(f"运行 A: {comparison['run_a']['run_id'][:20]} ({comparison['run_a']['date']})")
        print(f"        commit: {comparison['run_a']['git_commit']}")
        print(f"运行 B: {comparison['run_b']['run_id'][:20]} ({comparison['run_b']['date']})")
        print(f"        commit: {comparison['run_b']['git_commit']}")
        print()
        print(_format_regression_table(entries))
        print()
        if violations:
            print("阈值违规:")
            for v in violations:
                print(f"  [{v.severity}] {v.message}")
        print()
        print(f"CRITICAL: {critical_count}, WARNING: {warning_count}")
        if has_issues:
            print("\n结果: 检测到性能回退问题！")
        else:
            print("\n结果: 所有指标正常。")

    repo.close()
    return 1 if has_issues else 0


def check_run(args: argparse.Namespace) -> int:
    settings = BenchmarkSettings.from_env()
    repo = BenchmarkRepository(settings.db_path)
    comparison = repo.compare_versions(args.run_a, args.run_b)

    if "error" in comparison:
        print(f"错误: {comparison['error']}")
        repo.close()
        return 1

    print(json.dumps(comparison, indent=2, ensure_ascii=False))
    repo.close()
    return 0


def list_runs(args: argparse.Namespace) -> int:
    settings = BenchmarkSettings.from_env()
    repo = BenchmarkRepository(settings.db_path)
    runs = repo.get_runs(limit=args.limit)

    if args.json:
        print(json.dumps([r.to_dict() for r in runs], indent=2, ensure_ascii=False))
    else:
        print(f"{'Run ID':<30} {'Branch':<20} {'Commit':<12} {'Date':<20} {'Status':<10}")
        print("-" * 92)
        for r in runs:
            status = "CRIT" if r.has_critical else "WARN" if r.has_regression else "PASS"
            print(f"{r.run_id:<30} {(r.git_branch or '-'):<20} {(r.git_commit_hash or '-'):<12} "
                  f"{(r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '-'):<20} {status:<10}")

    repo.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="性能回归检测工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    latest_parser = subparsers.add_parser("latest", help="检测最新运行的回归")
    latest_parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    latest_parser.add_argument("--ignore-warnings", action="store_true", help="忽略警告级别")
    latest_parser.set_defaults(func=check_latest)

    compare_parser = subparsers.add_parser("compare", help="对比两个运行")
    compare_parser.add_argument("run_a", help="运行 A 的 ID")
    compare_parser.add_argument("run_b", help="运行 B 的 ID")
    compare_parser.set_defaults(func=check_run)

    list_parser = subparsers.add_parser("list", help="列出运行记录")
    list_parser.add_argument("--limit", type=int, default=20, help="条数限制")
    list_parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    list_parser.set_defaults(func=list_runs)

    args = parser.parse_args()
    if args.command:
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
