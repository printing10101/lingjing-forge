"""Markdown 性能报告生成器

功能：
    1. 运行所有性能测试套件（4 个文件）
    2. 解析 pytest 输出，收集每个测试的状态和测量值
    3. 与 baseline/BASELINE.json 对比，识别回归 / 新增 / 移除
    4. 通过 compare_baseline.compare_metrics() 进行数值级回归对比，
       从 print 输出提取实际测量值，按 ±10% 阈值判定回归
    5. 生成结构化 Markdown 报告：
       - 摘要：通过率、运行环境、数值对比统计
       - 测试矩阵：按测试类分组的状态表
       - 回归分析：失败回归、数值回归、数值改进、新增/移除
       - 性能指标锚点：从 BASELINE.json 读取
       - 数值对比明细：基线值 vs 实际值 + 回归百分比 + 状态图标
       - 失败详情：含 pytest 输出片段
       - 阈值配置与改进建议
    6. 输出：baseline/REPORT_{timestamp}.md + baseline/REPORT_LATEST.md

使用方式：
    # 生成完整报告（运行测试 + 对比基线 + 输出 Markdown）
    python tests/performance/generate_report.py

    # 仅基于已有 LATEST.json 生成报告（不重跑测试）
    python tests/performance/generate_report.py --no-run

    # 自定义输出路径
    python tests/performance/generate_report.py --output custom_report.md

退出码（用于 CI 卡点）：
    0 = 全部通过且无数值回归
    1 = 测试失败/错误 或 存在 ≥ 阈值% 的数值回归（仍会生成报告）
    2 = 致命错误（无法生成报告）
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让本目录下的 compare_baseline 模块可被导入（共享数值对比逻辑）
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from compare_baseline import (  # noqa: E402
    REGRESSION_THRESHOLD_PCT as CMP_REGRESSION_THRESHOLD_PCT,
    compare_metrics,
    parse_pytest_output,
)


# 配置

PERF_DIR = Path(__file__).parent
BASELINE_FILE = PERF_DIR / "baseline" / "BASELINE.json"
LATEST_FILE = PERF_DIR / "baseline" / "LATEST.json"
REPORT_LATEST = PERF_DIR / "baseline" / "REPORT_LATEST.md"

# 性能测试文件清单（按重要性排序）
TEST_FILES = [
    "test_critical_modules_performance.py",
    "test_cron_parser_cache_hit_rate.py",
    "test_memory_footprint.py",
    "test_end_to_end_performance.py",
]

# 回归判定阈值（与 compare_baseline 保持一致）
REGRESSION_THRESHOLD_PCT = CMP_REGRESSION_THRESHOLD_PCT


# 运行测试


def run_perf_tests() -> Dict[str, Any]:
    """运行所有性能测试套件，返回结构化结果。

    Returns:
        {
            "ran_at": ISO 时间戳,
            "python_version": str,
            "platform": str,
            "tests": [
                {
                    "node_id": "tests/performance/xxx.py::Class::test_xxx",
                    "file": "xxx.py",
                    "class_name": "Class",
                    "test_name": "test_xxx",
                    "status": "PASSED|FAILED|SKIPPED|XFAIL|ERROR",
                    "output_lines": [str, ...],  # 该测试的 print 输出
                    "failure_excerpt": str,       # 失败时的 traceback 摘要
                },
                ...
            ],
            "summary": {"passed": N, "failed": N, "skipped": N, "xfail": N, "error": N},
            "total_duration_sec": float,
        }
    """
    print(f"[INFO] 运行性能测试套件 ({len(TEST_FILES)} 个文件)...")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *[str(PERF_DIR / f) for f in TEST_FILES],
        "-v",
        "-s",  # 不捕获 print 输出，让测量值（如 "P95: 3.2ms"）出现在 stdout 中，
        # 供 _parse_pytest_output 提取为 output_lines，用于数值回归对比。
        "--tb=short",
        "-o",
        "addopts=",
        # 注意：不能加 -q，否则 pytest 只输出进度点而不打印每个测试的
        # ``node_id PASSED`` 行，_parse_pytest_output 将无法解析测试状态。
        # 禁用 anyio / pytest-asyncio 插件：它们通过 setuptools pytest11 入口点
        # 在 pytest 启动早期（``load_setuptools_entrypoints`` 阶段，早于 conftest.py
        # 加载）触发 ``import asyncio`` ``asyncio.windows_events``
        # OSError [WinError 10038]，导致整个 pytest 进程崩溃、LATEST.json 的 tests
        # 数组为空。性能测试不依赖 anyio/asyncio 插件，禁用它们可绕过 WinSock
        # 损坏问题。根本修复仍需 ``netsh winsock reset`` + 重启系统。
        "-p",
        "no:anyio",
        "-p",
        "no:asyncio",
    ]

    start = datetime.now()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(PERF_DIR.parent.parent),  # engineering/python
        encoding="utf-8",
        errors="replace",
    )
    duration = (datetime.now() - start).total_seconds()

    output = result.stdout + result.stderr
    tests = parse_pytest_output(output)

    summary = {
        "passed": sum(1 for t in tests if t["status"] == "PASSED"),
        "failed": sum(1 for t in tests if t["status"] == "FAILED"),
        "skipped": sum(1 for t in tests if t["status"] in ("SKIPPED",)),
        "xfail": sum(1 for t in tests if t["status"] == "XFAIL"),
        "error": sum(1 for t in tests if t["status"] == "ERROR"),
    }

    return {
        "ran_at": start.isoformat(timespec="seconds"),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "tests": tests,
        "summary": summary,
        "total_duration_sec": round(duration, 2),
        "return_code": result.returncode,
        "raw_output_tail": output[-3000:] if len(output) > 3000 else output,
    }


def _parse_pytest_output(output: str) -> List[Dict[str, Any]]:
    """[已废弃] 旧解析实现，保留作为向后兼容别名。

    新代码应直接使用 ``compare_baseline.parse_pytest_output``。
    本别名仅用于兼容尚未迁移的调用点，解析行为完全委托给共享实现。
    """
    return parse_pytest_output(output)


# 对比基线


def compare_with_baseline(latest: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """对比最新结果与基线，识别回归 / 新增 / 移除。

    Returns:
        {
            "regressions": [测试名列表],   # 基线中 PASS，本次 FAIL/ERROR
            "improvements": [测试名列表],  # 基线中 FAIL/XFAIL，本次 PASS
            "new_tests": [测试名列表],     # 不在基线中的测试
            "removed_tests": [测试名列表], # 基线中有但本次没有的测试
            "baseline_metrics": Dict,      # 基线中的性能锚点
        }
    """
    baseline_metrics = baseline.get("metrics", {})

    # 构建 baseline 中已知测试集合（从 metrics 推断）
    baseline_tests: set = set()
    for class_name, methods in baseline_metrics.items():
        for test_name in methods.keys():
            baseline_tests.add(f"{class_name}::{test_name}")

    # 构建本次测试集合
    latest_tests: set = set()
    latest_status: Dict[str, str] = {}
    for t in latest.get("tests", []):
        key = f"{t['class_name']}::{t['test_name']}"
        latest_tests.add(key)
        latest_status[key] = t["status"]

    # 假设基线中所有测试都是 PASS（BASELINE.json 不存储状态，只存储阈值）
    # 回归定义：本次 FAILED/ERROR
    regressions = [key for key in (latest_tests & baseline_tests) if latest_status[key] in ("FAILED", "ERROR")]

    # 新增测试：本次有但基线没有
    new_tests = sorted(latest_tests - baseline_tests)

    # 移除测试：基线有但本次没有
    removed_tests = sorted(baseline_tests - latest_tests)

    # 改进：基线标记为 xfail 的测试（如 test_concurrent_record_cost_safety）
    # 本次仍然 XFAIL，不算改进；如果变为 PASSED 才算改进
    improvements = []
    for class_name, methods in baseline_metrics.items():
        for test_name, info in methods.items():
            key = f"{class_name}::{test_name}"
            if info.get("metric") == "xfail" and latest_status.get(key) == "PASSED":
                improvements.append(key)

    return {
        "regressions": sorted(regressions),
        "improvements": improvements,
        "new_tests": new_tests,
        "removed_tests": removed_tests,
        "baseline_metrics": baseline_metrics,
    }


# 生成 Markdown 报告


def generate_markdown_report(
    latest: Dict[str, Any],
    baseline: Dict[str, Any],
    comparison: Dict[str, Any],
    metric_comparison: Optional[Dict[str, Any]] = None,
) -> str:
    """生成 Markdown 格式的性能报告。

    Args:
        latest: run_perf_tests() 返回的最新测试结果
        baseline: BASELINE.json 加载的基线数据
        comparison: compare_with_baseline() 返回的状态对比结果
        metric_comparison: compare_metrics() 返回的数值对比结果。
            若提供则在报告中插入"数值对比明细"章节，包含每个测试的
            基线值、实际测量值、回归百分比及告警状态。
    """
    meta = baseline.get("_meta", {})
    summary = latest["summary"]
    total = sum(summary.values())
    pass_rate = (summary["passed"] / total * 100) if total > 0 else 0

    lines: List[str] = []
    lines.append("# 性能基线报告")
    lines.append("")
    lines.append(f"**生成时间**: {latest['ran_at']}")
    lines.append(f"**测试耗时**: {latest.get('total_duration_sec', 0)}s")
    lines.append(f"**Python**: {latest.get('python_version', 'unknown')}")
    lines.append(f"**平台**: {latest.get('platform', 'unknown')}")
    lines.append(f"**基线版本**: {meta.get('version', 'unknown')} ({meta.get('created_at', 'unknown')})")
    lines.append(f"**基线提交**: {baseline.get('last_commit', 'unknown')}")
    if metric_comparison is not None:
        lines.append(f"**回归告警阈值**: ±{REGRESSION_THRESHOLD_PCT:.1f}%")
        lines.append(
            f"**数值对比**: {metric_comparison['matched_count']} matched, "
            f"{metric_comparison['regression_count']} regressions, "
            f"{metric_comparison['improvement_count']} improvements, "
            f"{metric_comparison['missing_actual_count']} missing"
        )
    lines.append("")

    # 摘要
    lines.append("## 摘要")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|----|")
    lines.append(f"| 总测试数 | {total} |")
    lines.append(f"| 通过 (PASSED) | {summary['passed']} |")
    lines.append(f"| 失败 (FAILED) | {summary['failed']} |")
    lines.append(f"| 跳过 (SKIPPED) | {summary['skipped']} |")
    lines.append(f"| 预期失败 (XFAIL) | {summary['xfail']} |")
    lines.append(f"| 错误 (ERROR) | {summary['error']} |")
    lines.append(f"| 通过率 | {pass_rate:.1f}% |")
    lines.append("")

    # 整体状态徽章
    if summary["failed"] == 0 and summary["error"] == 0:
        status_badge = "**状态: 全部通过**"
    elif summary["failed"] == 0 and summary["error"] == 0 and summary["xfail"] > 0:
        status_badge = "**状态: 通过（含已知 xfail）**"
    else:
        status_badge = "**状态: 有失败项，需修复**"
    lines.append(status_badge)
    lines.append("")

    # 回归告警
    lines.append("## 回归分析")
    lines.append("")
    regressions = comparison["regressions"]
    improvements = comparison["improvements"]
    new_tests = comparison["new_tests"]
    removed_tests = comparison["removed_tests"]

    # 数值回归/改进项（来自 compare_metrics）
    metric_regressions: List[Dict[str, Any]] = []
    metric_improvements: List[Dict[str, Any]] = []
    if metric_comparison is not None:
        for c in metric_comparison.get("comparisons", []):
            if c.get("is_regression"):
                metric_regressions.append(c)
            elif c.get("is_improvement"):
                metric_improvements.append(c)

    if regressions:
        lines.append(f"### 失败回归（{len(regressions)} 项）")
        lines.append("")
        lines.append("以下测试在基线中通过，但本次失败：")
        lines.append("")
        for r in regressions:
            lines.append(f"- `{r}`")
        lines.append("")
    else:
        lines.append("### 无失败回归")
        lines.append("")
        lines.append("所有基线中通过的测试，本次仍然通过。")
        lines.append("")

    if metric_regressions:
        lines.append(f"### 数值回归（{len(metric_regressions)} 项，≥ +{REGRESSION_THRESHOLD_PCT:.0f}%）")
        lines.append("")
        lines.append("| 测试 | 指标 | 基线 | 实际 | 回归 |")
        lines.append("|------|------|------|------|------|")
        for c in metric_regressions:
            test_label = f"{c['class_name']}::{c['test_name']}"
            base_str = _format_value(c["baseline_value"], c["unit"])
            actual_str = _format_value(c["actual_value"], c["unit"])
            # regression_pct 可能为 None（实际值未提取到），需兜底
            if c.get("regression_pct") is None:
                reg_str = "n/a"
            else:
                reg_str = f"+{c['regression_pct']:.1f}%"
            lines.append(f"| `{test_label}` | {c['metric']} | {base_str} | {actual_str} | {reg_str} |")
        lines.append("")
        lines.append(
            "> 建议使用 `git diff` 检查最近提交，定位性能回归根因。"
            " 阈值参考 `BASELINE.json` 中的 `threshold_multipliers`。"
        )
        lines.append("")

    if improvements:
        lines.append(f"### xfail 转通过（{len(improvements)} 项）")
        lines.append("")
        lines.append("以下测试在基线中标记为 xfail，但本次通过：")
        lines.append("")
        for imp in improvements:
            lines.append(f"- `{imp}`")
        lines.append("")

    if metric_improvements:
        lines.append(f"### 数值改进（{len(metric_improvements)} 项，≤ -{REGRESSION_THRESHOLD_PCT:.0f}%）")
        lines.append("")
        lines.append("| 测试 | 指标 | 基线 | 实际 | 变化 |")
        lines.append("|------|------|------|------|------|")
        for c in metric_improvements:
            test_label = f"{c['class_name']}::{c['test_name']}"
            base_str = _format_value(c["baseline_value"], c["unit"])
            actual_str = _format_value(c["actual_value"], c["unit"])
            # regression_pct 可能为 None（实际值未提取到），需兜底
            if c.get("regression_pct") is None:
                imp_str = "n/a"
            else:
                imp_str = f"{c['regression_pct']:.1f}%"
            lines.append(f"| `{test_label}` | {c['metric']} | {base_str} | {actual_str} | {imp_str} |")
        lines.append("")
        lines.append("> 建议更新 `BASELINE.json` 以反映新的性能水平。")
        lines.append("")

    if new_tests:
        lines.append(f"### 新增测试（{len(new_tests)} 项）")
        lines.append("")
        lines.append("以下测试未在基线中记录（建议更新 BASELINE.json）：")
        lines.append("")
        for n in new_tests[:20]:  # 最多列 20 个
            lines.append(f"- `{n}`")
        if len(new_tests) > 20:
            lines.append(f"- ...（还有 {len(new_tests) - 20} 项）")
        lines.append("")

    if removed_tests:
        lines.append(f"### 移除测试（{len(removed_tests)} 项）")
        lines.append("")
        lines.append("以下测试在基线中存在，但本次未运行（可能已删除或重命名）：")
        lines.append("")
        for r in removed_tests[:20]:
            lines.append(f"- `{r}`")
        lines.append("")

    # 测试矩阵
    lines.append("## 测试矩阵")
    lines.append("")
    lines.append("按测试类分组的状态详情：")
    lines.append("")

    # 按 class_name 分组
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for t in latest.get("tests", []):
        by_class.setdefault(t["class_name"], []).append(t)

    for class_name in sorted(by_class.keys()):
        tests_in_class = by_class[class_name]
        lines.append(f"### {class_name}")
        lines.append("")
        lines.append("| 测试 | 状态 | 失败摘要 |")
        lines.append("|------|------|----------|")
        for t in tests_in_class:
            status_icon = _status_icon(t["status"])
            failure = t.get("failure_excerpt", "") or ""
            # 转义 Markdown 表格中的管道符
            failure_escaped = failure.replace("|", "\\|").replace("\n", " ")
            if len(failure_escaped) > 100:
                failure_escaped = failure_escaped[:97] + "..."
            lines.append(f"| `{t['test_name']}` | {status_icon} {t['status']} | {failure_escaped} |")
        lines.append("")

    # 性能指标锚点
    lines.append("## 性能指标锚点")
    lines.append("")
    lines.append("以下数据来自 `BASELINE.json`，作为回归检测的基准：")
    lines.append("")
    lines.append("| 测试类 | 测试 | 指标 | 基线值 | 单位 | 备注 |")
    lines.append("|--------|------|------|--------|------|------|")

    baseline_metrics = comparison["baseline_metrics"]
    for class_name in sorted(baseline_metrics.keys()):
        methods = baseline_metrics[class_name]
        for test_name in sorted(methods.keys()):
            info = methods[test_name]
            value = info.get("value")
            if value is None:
                value_str = "n/a"
            elif isinstance(value, float):
                value_str = f"{value:.4f}"
            else:
                value_str = str(value)
            unit = info.get("unit", "")
            metric = info.get("metric", "")
            notes = info.get("notes", "")
            # 转义管道符
            notes_escaped = notes.replace("|", "\\|")
            lines.append(f"| {class_name} | `{test_name}` | {metric} | {value_str} | {unit} | {notes_escaped} |")
    lines.append("")

    # 数值对比明细
    if metric_comparison is not None:
        lines.append("## 数值对比明细")
        lines.append("")
        lines.append(
            f"对比基线与本次实际测量值，回归告警阈值 **±{REGRESSION_THRESHOLD_PCT:.1f}%**。"
            f"延迟类指标越小越好，吞吐类（QPS/命中率/加速比）越大越好。"
        )
        lines.append("")

        comparisons_list = metric_comparison.get("comparisons", [])
        matched = metric_comparison.get("matched_count", 0)
        reg_count = metric_comparison.get("regression_count", 0)
        imp_count = metric_comparison.get("improvement_count", 0)
        miss_count = metric_comparison.get("missing_actual_count", 0)

        lines.append(
            f"**统计**: {matched} matched, {reg_count} regressions, "
            f"{imp_count} improvements, {miss_count} missing"
        )
        lines.append("")

        if not comparisons_list:
            lines.append("_基线中无指标项。_")
            lines.append("")
        else:
            lines.append("| 状态 | 测试 | 指标 | 基线 | 实际 | 回归% | 方向 | 备注 |")
            lines.append("|------|------|------|------|------|-------|------|------|")

            # 按 class_name 分组，组内按 test_name 排序
            sorted_cmps = sorted(
                comparisons_list,
                key=lambda c: (c["class_name"], c["test_name"]),
            )

            for c in sorted_cmps:
                icon = _regression_icon(c)
                test_label = f"{c['class_name']}::{c['test_name']}"
                base_str = _format_value(c["baseline_value"], c["unit"])
                actual_str = _format_value(c["actual_value"], c["unit"])

                if c["regression_pct"] is None:
                    reg_str = "n/a"
                else:
                    sign = "+" if c["regression_pct"] >= 0 else ""
                    reg_str = f"{sign}{c['regression_pct']:.1f}%"

                direction = "↓ 越小越好" if c["lower_is_better"] else "↑ 越大越好"
                notes = (c.get("notes") or "").replace("|", "\\|").replace("\n", " ")
                if len(notes) > 60:
                    notes = notes[:57] + "..."

                lines.append(
                    f"| {icon} | `{test_label}` | {c['metric']} | "
                    f"{base_str} | {actual_str} | {reg_str} | {direction} | {notes} |"
                )
            lines.append("")
            lines.append("> 状态标记: OK 正常 / WARN 回归 / ++ 改进 / ? 未提取到数值 / skip 跳过")
            lines.append("")

    # 失败项详情
    failed_tests = [t for t in latest.get("tests", []) if t["status"] == "FAILED"]
    if failed_tests:
        lines.append("## 失败项详情")
        lines.append("")
        for t in failed_tests:
            lines.append(f"### `{t['test_name']}`")
            lines.append(f"- **文件**: `{t['file']}`")
            lines.append(f"- **类**: `{t['class_name']}`")
            if t.get("failure_excerpt"):
                lines.append(f"- **错误**: `{t['failure_excerpt']}`")
            if t.get("output_lines"):
                lines.append("- **输出**:")
                lines.append("  ```")
                for line in t["output_lines"][-10:]:
                    lines.append(f"  {line}")
                lines.append("  ```")
            lines.append("")

    # 阈值配置
    threshold_multipliers = baseline.get("threshold_multipliers", {})
    if threshold_multipliers:
        lines.append("## 阈值配置")
        lines.append("")
        lines.append("| 配置项 | 值 |")
        lines.append("|--------|----|")
        for k, v in threshold_multipliers.items():
            v_str = str(v).replace("|", "\\|")
            lines.append(f"| {k} | {v_str} |")
        lines.append("")

    # 改进建议
    # 使用动态计数器避免条件分支导致编号不连续
    lines.append("## 改进建议")
    lines.append("")
    suggestions: List[str] = []

    if summary["failed"] > 0:
        suggestions.append(f"**优先修复 {summary['failed']} 个失败项**：参见上方『失败项详情』。")
    if summary["error"] > 0:
        suggestions.append(f"**排查 {summary['error']} 个错误项**：可能是导入错误或 fixture 失败。")
    if regressions:
        suggestions.append(
            f"**状态回归根因分析**：{len(regressions)} 项测试由 PASS 变为 FAILED/ERROR，"
            f"建议 `git diff` 检查最近提交是否影响功能。"
        )
    if metric_regressions:
        reg_names = ", ".join(f"`{r.get('test_name', 'unknown')}`" for r in metric_regressions[:5])
        more = f" 等 {len(metric_regressions)} 项" if len(metric_regressions) > 5 else ""
        suggestions.append(
            f"**数值回归根因分析**：{reg_names}{more} 数值指标超出 ±{REGRESSION_THRESHOLD_PCT:.0f}% 阈值，"
            f"建议 `git diff` 检查最近提交是否影响性能，或更新基线值（若属合理波动）。"
        )
    if new_tests:
        suggestions.append(
            f"**更新 BASELINE.json**：有 {len(new_tests)} 个新测试未纳入基线，建议运行后手动采样 P95 值并添加到基线。"
        )
    if metric_improvements:
        suggestions.append(
            f"**性能提升确认**：{len(metric_improvements)} 项指标有改善，"
            f"若属优化成果建议更新 BASELINE.json 锚点值以固化为新基线。"
        )

    if not suggestions:
        suggestions.append("**状态健康**：所有测试通过，无回归，无数值告警。")
        suggestions.append("**定期更新基线**：每次性能优化提交后，更新 BASELINE.json 中的锚点值。")
        suggestions.append("**CI 集成**：将 `compare_baseline.py` 加入 CI 流水线，在 PR 合并前自动检测回归。")

    for idx, s in enumerate(suggestions, 1):
        lines.append(f"{idx}. {s}")
    lines.append("")

    # 元信息
    lines.append("## 元信息")
    lines.append("")
    lines.append("- **报告生成器**: `tests/performance/generate_report.py`")
    lines.append("- **基线文件**: `tests/performance/baseline/BASELINE.json`")
    lines.append("- **最新结果**: `tests/performance/baseline/LATEST.json`")
    lines.append(f"- **基线更新策略**: {meta.get('update_policy', 'n/a')}")
    lines.append("")

    return "\n".join(lines)


def _status_icon(status: str) -> str:
    """状态对应的图标。"""
    return {
        "PASSED": "OK",
        "FAILED": "X",
        "SKIPPED": "skip",
        "XFAIL": "xfail",
        "ERROR": "!!",
    }.get(status, "?")


def _format_value(value: Any, unit: str) -> str:
    """格式化数值用于 Markdown 表格显示。

    Args:
        value: 数值（float/int）或 None
        unit: 单位字符串（ms, MB, objects, percent, qps 等）

    Returns:
        格式化后的字符串，如 "8.4000 ms" 或 "n/a"
    """
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        # 延迟类（ms）保留 4 位小数；百分比/objects 保留 1 位
        if unit == "ms":
            return f"{value:.4f} ms"
        elif unit in ("MB",):
            return f"{value:.4f} MB"
        elif unit in ("objects", "blocks"):
            return f"{int(value)} {unit}"
        elif unit in ("percent",):
            return f"{value:.1f}%"
        elif unit in ("qps",):
            return f"{value:.2f} qps"
        else:
            return f"{value} {unit}".strip()
    return str(value)


def _regression_icon(c: Dict[str, Any]) -> str:
    """根据对比结果返回状态图标。"""
    if c.get("is_regression"):
        return "WARN"
    if c.get("is_improvement"):
        return "++"
    if c.get("status") == "matched":
        return "OK"
    if c.get("status") == "missing_actual":
        return "?"
    return "skip"


# 主入口


def main():
    parser = argparse.ArgumentParser(description="Markdown 性能报告生成器")
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="不运行测试，仅基于已有 LATEST.json 生成报告",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出 Markdown 文件路径（默认: baseline/REPORT_{timestamp}.md）",
    )
    parser.add_argument(
        "--baseline",
        default=str(BASELINE_FILE),
        help=f"基线 JSON 文件路径 (默认: {BASELINE_FILE})",
    )
    args = parser.parse_args()

    # 加载基线
    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"[ERROR] 基线文件不存在: {baseline_path}")
        return 2
    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    # 获取最新结果
    if args.no_run:
        if not LATEST_FILE.exists():
            print(f"[ERROR] --no-run 模式需要先有 LATEST.json: {LATEST_FILE}")
            return 2
        with open(LATEST_FILE, "r", encoding="utf-8") as f:
            latest = json.load(f)
    else:
        latest = run_perf_tests()
        # 保存 LATEST
        LATEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LATEST_FILE, "w", encoding="utf-8") as f:
            json.dump(latest, f, indent=2, ensure_ascii=False)
        print(f"[INFO] 最新结果已保存到 {LATEST_FILE}")

    # 对比
    comparison = compare_with_baseline(latest, baseline)
    metric_comparison = compare_metrics(latest, baseline)

    # 生成报告
    report = generate_markdown_report(latest, baseline, comparison, metric_comparison=metric_comparison)

    # 输出文件
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = PERF_DIR / "baseline" / f"REPORT_{timestamp}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[INFO] Markdown 报告已生成: {output_path}")

    # 同时更新 REPORT_LATEST.md（便于 CI 直接读取）
    with open(REPORT_LATEST, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[INFO] 最新报告副本: {REPORT_LATEST}")

    # 控制台摘要
    summary = latest["summary"]
    print(f"\n{'=' * 60}")
    print("性能测试摘要:")
    print(
        f"  通过: {summary['passed']}, 失败: {summary['failed']}, "
        f"跳过: {summary['skipped']}, xfail: {summary['xfail']}, "
        f"错误: {summary['error']}"
    )
    if comparison["regressions"]:
        print(f"  失败回归: {len(comparison['regressions'])} 项")
        for r in comparison["regressions"]:
            print(f"    - {r}")
    if metric_comparison is not None:
        mc = metric_comparison
        print(
            f"  数值对比: {mc['matched_count']} matched, "
            f"{mc['regression_count']} regressions, "
            f"{mc['improvement_count']} improvements, "
            f"{mc['missing_actual_count']} missing"
        )
        if mc["regression_count"] > 0:
            print(f"  数值回归明细 (≥ +{REGRESSION_THRESHOLD_PCT:.0f}%):")
            for c in mc.get("comparisons", []):
                if c.get("is_regression"):
                    test_label = f"{c['class_name']}::{c['test_name']}"
                    base_str = _format_value(c["baseline_value"], c["unit"])
                    actual_str = _format_value(c["actual_value"], c["unit"])
                    if c.get("regression_pct") is None:
                        reg_str = "n/a"
                    else:
                        reg_str = f"+{c['regression_pct']:.1f}%"
                    print(f"    - {test_label} [{c['metric']}]: {base_str} -> {actual_str} ({reg_str})")
    if comparison["new_tests"]:
        print(f"  新增测试: {len(comparison['new_tests'])} 项（建议更新基线）")
    print(f"{'=' * 60}")

    # 退出码（用于 CI 卡点）：
    # 0 = 全部通过且无数值回归
    # 1 = 测试失败/错误 或 存在 ≥ 阈值% 的数值回归
    has_test_failure = summary["failed"] > 0 or summary["error"] > 0
    has_metric_regression = metric_comparison is not None and metric_comparison.get("regression_count", 0) > 0
    if has_test_failure or has_metric_regression:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
