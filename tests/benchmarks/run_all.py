"""全量性能基准测试运行入口。

按顺序执行 LNN 推理、G代码生成、3D渲染帧率基准测试，
生成 JSON/Markdown/HTML 格式报告，存储至数据库。
在 CI 模式下，检测到严重回退时会以非零退出码终止。
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, "..", "..")
_PYTHON_ROOT = os.path.join(_PROJECT_ROOT, "python")
for p in [_PROJECT_ROOT, _PYTHON_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from tests.benchmarks.config.settings import BenchmarkSettings  # noqa: E402
from tests.benchmarks.config.thresholds import (  # noqa: E402
    REGRESSION_THRESHOLDS,
    check_violations,
)
from tests.benchmarks.database.repository import BenchmarkRepository  # noqa: E402
from tests.benchmarks.reporters.html_reporter import generate_html_report  # noqa: E402
from tests.benchmarks.reporters.json_reporter import generate_json_report  # noqa: E402
from tests.benchmarks.reporters.markdown_reporter import generate_markdown_report  # noqa: E402
from tests.benchmarks.runners.gcode_benchmark import GCodeGenerationBenchmark  # noqa: E402
from tests.benchmarks.runners.lnn_benchmark import LNNInferenceBenchmark  # noqa: E402
from tests.benchmarks.runners.render_benchmark import RenderFPSBenchmark  # noqa: E402
from tests.benchmarks.visualizer.trend_chart import TrendVisualizer  # noqa: E402


def _flatten_results(
    results: dict[str, dict[str, Any]],
) -> dict[str, float]:
    flat: dict[str, float] = {}
    for bench_type, metrics in results.items():
        for metric, data in metrics.items():
            if isinstance(data, dict) and "value" in data:
                flat[f"{bench_type}_{metric}"] = float(data["value"])
    return flat


def _check_regression(
    current_flat: dict[str, float],
    repo: BenchmarkRepository,
) -> tuple[list[dict[str, Any]], str]:
    entries: list[dict[str, Any]] = []
    latest_run = repo.get_latest_run()

    if latest_run:
        previous_flat = {
            r.benchmark_type + "_" + r.metric_name: r.metric_value
            for r in latest_run.results
        }
    else:
        previous_flat = {}

    warning_pct = REGRESSION_THRESHOLDS["warning_pct"]
    critical_pct = REGRESSION_THRESHOLDS["critical_pct"]
    has_regression = False
    has_critical = False

    for metric, current in sorted(current_flat.items()):
        previous = previous_flat.get(metric)

        if previous is None or previous == 0:
            entries.append({
                "metric": metric,
                "current": round(current, 3),
                "previous": 0,
                "change_pct": 0,
                "status": "NEW",
            })
            continue

        change_pct = (current - previous) / previous * 100

        if change_pct > critical_pct:
            status = "CRITICAL"
            has_regression = True
            has_critical = True
        elif change_pct > warning_pct:
            status = "WARNING"
            has_regression = True
        elif change_pct < -critical_pct:
            status = "IMPROVED"
        else:
            status = "PASS"

        entries.append({
            "metric": metric,
            "current": round(current, 3),
            "previous": round(previous, 3),
            "change_pct": round(change_pct, 1),
            "status": status,
        })

    if has_critical:
        summary = f"[CRIT] 检测到 {sum(1 for e in entries if e['status'] == 'CRITICAL')} 项严重性能回退"
    elif has_regression:
        summary = f"[WARN] 检测到 {sum(1 for e in entries if e['status'] == 'WARNING')} 项性能回退"
    else:
        summary = "[PASS] 未检测到性能回退"

    return entries, summary


def run_all(settings: BenchmarkSettings | None = None) -> int:
    settings = settings or BenchmarkSettings.from_env()
    settings.ensure_dirs()
    repo = BenchmarkRepository(settings.db_path)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    print("=" * 70)
    print("  灵境制造 V4 性能基准测试套件")
    print("=" * 70)
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  CI 模式: {settings.ci_mode}")
    print(f"  结果数据库: {settings.db_path}")
    print("=" * 70)

    all_results: dict[str, dict[str, Any]] = {}

    runners = [
        ("LNN 推理延迟测试", LNNInferenceBenchmark(settings)),
        ("G代码生成性能测试", GCodeGenerationBenchmark(settings)),
        ("3D 渲染帧率测试", RenderFPSBenchmark(settings)),
    ]

    for name, runner in runners:
        print(f"\n{'=' * 70}")
        print(f"  [{runner.benchmark_type}] {name}")
        print(f"{'=' * 70}")
        try:
            runner.setup()
            results = runner.run()
            if results:
                all_results[runner.benchmark_type] = results
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Benchmark '{name}' 执行失败: {e}", exc_info=True)
            print(f"  [错误] {name} 执行异常: {e}")
            all_results[runner.benchmark_type] = {
                "error": {"value": 0, "status": "ERROR", "message": str(e)},
            }
        finally:
            runner.teardown()

    current_flat = _flatten_results(all_results)

    violations = check_violations(current_flat)
    regression_entries, summary = _check_regression(current_flat, repo)

    print(f"\n{'=' * 70}")
    print(f"  阈值检查结果: {len(violations)} 项违规")
    for v in violations:
        print(f"    [{v.severity}] {v.message}")
    print(f"  回归检测结果: {summary}")

    run_id = repo.create_run(all_results, summary=summary)
    print(f"\n  数据库记录 ID: {run_id}")

    markdown_path = os.path.join(settings.output_dir, f"benchmark_report_{timestamp}.md")
    generate_markdown_report(
        results=all_results,
        regression_results=regression_entries,
        violations=[v.to_dict() for v in violations],
        summary=summary,
        output_path=markdown_path,
    )

    json_path = os.path.join(settings.output_dir, f"benchmark_report_{timestamp}.json")
    generate_json_report(
        results=all_results,
        regression_results=regression_entries,
        violations=[v.to_dict() for v in violations],
        summary=summary,
        output_path=json_path,
    )

    git_info = ""
    try:
        import subprocess
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        git_info = f"{branch}@{commit}"
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("无法获取 git 信息: %s", e)
        git_info = "N/A"

    html_path = os.path.join(settings.output_dir, f"benchmark_report_{timestamp}.html")
    generate_html_report(
        results=all_results,
        regression_results=regression_entries,
        output_path=html_path,
        git_info=git_info,
        env_info=f"Python {sys.version.split()[0]}",
    )

    try:
        visualizer = TrendVisualizer(repo)
        visualizer.set_output_dir(settings.visualizer_dir)
        visualizer.generate_dashboard()
        for metric in list(current_flat.keys())[:10]:
            visualizer.generate_metric_trend_chart(metric, limit=20)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("生成趋势可视化图表失败: %s", e)
        print(f"  [可视化] 生成趋势图时出错: {e}")

    critical_count = sum(1 for r in regression_entries if r["status"] == "CRITICAL")
    warning_count = sum(1 for r in regression_entries if r["status"] == "WARNING")

    print(f"\n{'=' * 70}")
    print("  性能基准测试完成")
    print("  报告路径:")
    print(f"    Markdown: {markdown_path}")
    print(f"    JSON:     {json_path}")
    print(f"    HTML:     {html_path}")
    print("  结果统计:")
    print(f"    CRITICAL: {critical_count}")
    print(f"    WARNING:  {warning_count}")
    print(f"    PASS:     {sum(1 for r in regression_entries if r['status'] == 'PASS')}")
    print(f"    NEW:      {sum(1 for r in regression_entries if r['status'] == 'NEW')}")
    print(f"    IMPROVED: {sum(1 for r in regression_entries if r['status'] == 'IMPROVED')}")
    print(f"  总体: {summary}")
    print(f"{'=' * 70}")

    repo.close()

    if settings.ci_mode and settings.fail_on_regression and critical_count > 0:
        print("\n  [CI] 检测到严重性能回退，构建失败！")
        return 1

    return 0


def main() -> None:
    exit_code = run_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
