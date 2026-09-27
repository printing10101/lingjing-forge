"""性能基线回归对比脚本

功能：
    1. 运行性能测试套件并捕获实际测量值（从 print 输出解析）
    2. 与 tests/performance/baseline/BASELINE.json 对比
    3. 报告每个指标的回归/提升百分比
    4. 在 ≥10% 回归时返回非零退出码（用于 CI 卡点）

使用方式：
    # 运行所有性能测试并对比基线
    python tests/performance/compare_baseline.py

    # 仅对比不运行（需要先有最新测试结果 JSON）
    python tests/performance/compare_baseline.py --compare-only latest.json

    # 自定义基线文件
    python tests/performance/compare_baseline.py --baseline custom_baseline.json

输出：
    - 控制台彩色报告
    - tests/performance/baseline/LATEST.json（最近一次运行结果）
    - 退出码：0=无回归，1=有 ≥10% 回归
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# 配置

PERF_DIR = Path(__file__).parent
BASELINE_FILE = PERF_DIR / "baseline" / "BASELINE.json"
LATEST_FILE = PERF_DIR / "baseline" / "LATEST.json"
TEST_FILES = [
    "test_critical_modules_performance.py",
    "test_cron_parser_cache_hit_rate.py",
    "test_memory_footprint.py",
    "test_end_to_end_performance.py",
]

# 回归阈值（%）：超过此值视为回归
REGRESSION_THRESHOLD_PCT = 10.0


# ANSI 颜色
class Color:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


# 测量值提取规则
# BASELINE.json 中 `metric` 字段到 print 输出正则的映射。
# 顺序很重要：更具体的规则放在前面，避免被通用规则提前匹配。

# (metric 字段关键词, 描述, 正则模式, 取第几个匹配)
# 顺序很重要：更具体的规则放在前面，避免被通用规则提前匹配。
# 每条规则需覆盖实际测试 print 输出格式（见 test_critical_modules_performance.py 等）。
METRIC_PATTERNS: List[Tuple[str, str, str, int]] = [
    # "per_call_first" 优先匹配 "首次:" 或 "首次关闭:"
    ("per_call_first", "首次调用延迟", r"首次(?:关闭)?:\s*([\d.]+)\s*ms", 0),
    # "single" 用于无标识的单次测量，回退到多种实际输出格式：
    # - "首次关闭:" (test_*_close_latency 系列)
    # - "总时间:" (test_concurrent_close_safety 等)
    # - "创建时间:" (test_connection_pool_creation_time)
    # - "装配时间:" (test_full_middleware_stack_assembly_time)
    ("single", "单次延迟", r"(?:首次关闭|总时间|创建时间|装配时间|中间件栈装配时间):\s*([\d.]+)\s*ms", 0),
    # "per_call_avg_*" 与 "per_call" 共享 "每次:" 关键词
    ("per_call_avg", "平均每次延迟", r"每次:\s*([\d.]+)\s*ms", 0),
    ("per_call", "每次延迟", r"每次:\s*([\d.]+)\s*ms", 0),
    # P95 / P50 — 兼容 "P95:" 与 "P95时间:" 两种格式
    ("p95", "P95 延迟", r"P95(?:时间)?:\s*([\d.]+)\s*ms", 0),
    ("p50", "P50 延迟", r"P50(?:时间)?:\s*([\d.]+)\s*ms", 0),
    # 总时间（用于 total_5x20, total_500 等）
    ("total", "总时间", r"总时间:\s*([\d.]+)\s*ms", 0),
    # overhead_vs_plain — 哈希链 overhead 百分比（test_hash_chain_overhead）
    # 支持负数（哈希链可能因波动略快于无哈希链，Overhead 为负）
    ("overhead_vs_plain", "哈希链 overhead", r"Overhead:\s*(-?[\d.]+)%", 0),
    # 内存占用（MB）— 来自 test_memory_footprint.py
    ("current_mb", "当前内存 (MB)", r"当前:\s*([\d.]+)\s*MB", 0),
    ("peak_mb", "峰值内存 (MB)", r"峰值:\s*([\d.]+)\s*MB", 0),
    # 缓存命中率
    ("hit_rate", "缓存命中率", r"命中率:\s*([\d.]+)%", 0),
    # 加速比 / 差距 — 兼容 "加速比: Nx" 与 "差距: Nx" 两种格式
    # (test_cached_throughput_vs_cold 用"加速比"，test_cold_start_slower_than_warm 用"差距")
    ("speedup", "加速比", r"(?:加速比|差距):\s*([\d.]+)x", 0),
    # QPS
    ("qps", "QPS", r"QPS:\s*([\d.]+)", 0),
    # 内存块数（来自 test_memory_footprint）
    ("blocks", "内存块数", r"内存块:\s*(\d+)", 0),
    # delta — 对象增长数（test_connection_pool_memory_footprint）
    ("delta", "对象增长", r"对象增长:\s*(\d+)", 0),
    # release_rate — 资源释放率（TestResourceReleaseMemory）
    ("release_rate", "资源释放率", r"释放率:\s*([\d.]+)%", 0),
    # per_task_growth — 每任务内存增长（TestBatchOperationMemoryGrowth, KB）
    ("per_task_growth", "每任务内存增长", r"每任务增长:\s*([\d.]+)\s*KB", 0),
    # per_log_growth — 每条日志内存增长（TestBatchOperationMemoryGrowth, KB）
    ("per_log_growth", "每条日志内存增长", r"每条增长:\s*([\d.]+)\s*KB", 0),
    # eviction_latency — 淘汰+写入延迟（TestEvictionPerformanceImpact）
    ("eviction_latency", "淘汰写入延迟", r"淘汰 \+ 写入延迟:\s*([\d.]+)\s*ms", 0),
    # amortized_latency — 摊销开销（TestEvictionPerformanceImpact）
    ("amortized_latency", "摊销延迟", r"(?:命中路径平均延迟|平均延迟):\s*([\d.]+)\s*ms", 0),
]


def extract_metric_from_output(output_lines: List[str], metric_key: str) -> Optional[float]:
    """从测试 print 输出中提取指定 metric 的数值。

    Args:
        output_lines: 测试的 print 输出行列表
        metric_key: BASELINE.json 中的 metric 字段值（如 "per_call", "p95"）

    Returns:
        提取到的数值，未找到返回 None
    """
    # "xfail" / "n/a" 是非数值占位符；其他 metric（包括 "delta"）
    # 都尝试从输出中提取数值。
    if metric_key in ("xfail", "n/a"):
        return None

    text = "\n".join(output_lines)

    # 按规则顺序匹配
    for pattern_key, desc, regex, match_idx in METRIC_PATTERNS:
        # 支持前缀匹配：如 "per_call_avg_200" 匹配 "per_call_avg"
        # 但要避免 "per_call" 误匹配 "per_call_first"
        if pattern_key == "per_call" and metric_key.startswith("per_call_"):
            continue
        if not metric_key.startswith(pattern_key) and metric_key != pattern_key:
            continue

        match = re.search(regex, text)
        if match:
            # 处理带 alternation 的正则（如 single 规则有两个捕获组）
            for g in match.groups():
                if g is not None:
                    try:
                        return float(g)
                    except ValueError:
                        continue
    return None


# 运行性能测试


def run_perf_tests() -> Dict[str, Any]:
    """运行性能测试套件并解析输出

    通过 pytest 的 -v 输出捕获 PASSED/FAILED/SKIPPED 状态，
    并通过 print 输出捕获实际测量值。

    Returns:
        {
            "ran_at": "2026-07-28T12:34:56",
            "python_version": str,
            "platform": str,
            "passed": N,
            "failed": N,
            "skipped": N,
            "xfail": N,
            "error": N,
            "tests": [
                {
                    "node_id": ...,
                    "file": ...,
                    "class_name": ...,
                    "test_name": ...,
                    "status": "PASSED"|...,
                    "output_lines": [...],
                    "failure_excerpt": str,
                }
            ],
            "total_duration_sec": float,
        }
    """
    print(f"{Color.CYAN}[INFO]{Color.RESET} 运行性能测试套件 ({len(TEST_FILES)} 个文件)...")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *[str(PERF_DIR / f) for f in TEST_FILES],
        "-v",
        "-s",  # 不捕获 print 输出，让测量值出现在 stdout 中供 extract_metric_from_output 提取
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
    tests = _parse_pytest_output(output)

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
        "platform": sys.platform,
        "passed": summary["passed"],
        "failed": summary["failed"],
        "skipped": summary["skipped"],
        "xfail": summary["xfail"],
        "error": summary["error"],
        "return_code": result.returncode,
        "tests": tests,
        "total_duration_sec": round(duration, 2),
        "stdout_tail": output[-2000:] if len(output) > 2000 else output,
    }


def parse_pytest_output(output: str) -> List[Dict[str, Any]]:
    """解析 pytest -v 输出，提取每个测试的状态和 print 输出。

    兼容两种 pytest 输出格式：

    - **旧格式**（pytest < 9 或 tty 模式）：``node_id PASSED`` 在同一行，
      print 输出位于 *上一个* 测试结果行之后、当前 node_id 行之前。
    - **新格式**（pytest 9.x 非 tty 模式）：``node_id`` 单独一行（尾部
      带一个空格），随后是 print 输出，最后是 ``PASSED`` 单独一行。

    新格式示例::

        tests\\performance\\test_xxx.py::TestClass::test_method
        连接池创建时间: 0.002ms
        PASSED

    通过检测 node_id 行是否包含同行状态关键字自动区分两种格式，
    混合格式（部分行旧、部分行新）也能正确处理。
    """
    tests: List[Dict[str, Any]] = []

    # 匹配 node_id 行：特征是包含 ``.py::`` 且以 ``::`` 分隔的三段式。
    # 可选地捕获同行状态（旧格式）。行尾允许空白（pytest 9.x 在
    # node_id 后会有一个尾随空格）。
    node_id_re = re.compile(
        r"^(?P<node_id>\S*?\.py::\S+?::\S+?)"
        r"(?:\s+(?P<inline_status>PASSED|FAILED|SKIPPED|XFAIL|ERROR))?"
        r"\s*$",
        re.MULTILINE,
    )
    # 状态行正则：独立一行的状态关键字（新格式）
    status_re = re.compile(
        r"^(PASSED|FAILED|SKIPPED|XFAIL|ERROR)\b",
        re.MULTILINE,
    )

    node_matches = list(node_id_re.finditer(output))

    # 第一遍：收集每个测试的元信息（node_id、状态、各位置锚点）
    entries: List[Dict[str, Any]] = []
    for i, nm in enumerate(node_matches):
        node_id = nm.group("node_id")
        inline_status = nm.group("inline_status")
        next_nm_start = node_matches[i + 1].start() if i + 1 < len(node_matches) else len(output)
        block_after_node = output[nm.end() : next_nm_start]

        if inline_status:
            # 旧格式：状态与 node_id 在同一行
            entries.append(
                {
                    "node_id": node_id,
                    "status": inline_status,
                    "nm_start": nm.start(),
                    "nm_end": nm.end(),
                    "status_start": nm.start(),
                    "status_end": nm.end(),
                    "is_new_format": False,
                }
            )
        else:
            # 新格式：在 node_id 之后查找独立的状态行
            sm = status_re.search(block_after_node)
            if sm:
                entries.append(
                    {
                        "node_id": node_id,
                        "status": sm.group(1),
                        "nm_start": nm.start(),
                        "nm_end": nm.end(),
                        "status_start": nm.end() + sm.start(),
                        "status_end": nm.end() + sm.end(),
                        "is_new_format": True,
                    }
                )
            # 未找到状态关键字的 node_id 行（如收集阶段噪声）跳过

    # 第二遍：提取 print 输出和失败详情
    for i, entry in enumerate(entries):
        node_id = entry["node_id"]
        status = entry["status"]

        if entry["is_new_format"]:
            # 新格式：print 输出在 node_id 行之后、状态行之前
            output_block = output[entry["nm_end"] : entry["status_start"]]
        else:
            # 旧格式：print 输出在上一个测试状态行结束之后、当前 node_id 之前
            prev_status_end = entries[i - 1]["status_end"] if i > 0 else 0
            output_block = output[prev_status_end : entry["nm_start"]]

        parts = node_id.split("::")
        file_path = parts[0] if parts else ""
        file_name = Path(file_path).name if file_path else ""
        class_name = parts[1] if len(parts) >= 2 else ""
        test_name = parts[2] if len(parts) >= 3 else class_name

        output_lines = [
            line.strip()
            for line in output_block.splitlines()
            if line.strip()
            and not line.strip().startswith("=")
            and not line.strip().startswith("-")
            and "PASSED" not in line
            and "FAILED" not in line
            and "SKIPPED" not in line
            and "XFAIL" not in line
            and "ERROR" not in line
            and "::" not in line
        ]

        # 失败时提取 traceback 摘要
        failure_excerpt = ""
        if status == "FAILED":
            next_start = entries[i + 1]["nm_start"] if i + 1 < len(entries) else len(output)
            fail_block = output[entry["status_end"] : next_start]
            err_match = re.search(
                r"(AssertionError|TypeError|ValueError|AttributeError|KeyError|"
                r"ImportError|RuntimeError)[^\n]*",
                fail_block,
            )
            if err_match:
                failure_excerpt = err_match.group(0).strip()

        tests.append(
            {
                "node_id": node_id,
                "file": file_name,
                "class_name": class_name,
                "test_name": test_name,
                "status": status,
                "output_lines": output_lines[-30:],
                "failure_excerpt": failure_excerpt,
            }
        )

    return tests


# 向后兼容别名（模块内其他位置可能通过私有名调用）
_parse_pytest_output = parse_pytest_output


# 数值对比


def compute_regression_pct(actual: float, baseline: float, lower_is_better: bool = True) -> float:
    """计算回归百分比。

    Args:
        actual: 实际测量值
        baseline: 基线值
        lower_is_better: True=延迟类（越小越好），False=吞吐类（越大越好）

    Returns:
        正数=回归（变差），负数=改进（变好）
        例如：基线 10ms，实际 12ms → +20% 回归
              基线 10ms，实际 8ms → -20% 改进
    """
    if baseline == 0:
        return 0.0
    delta_pct = (actual - baseline) / baseline * 100
    if not lower_is_better:
        delta_pct = -delta_pct
    return delta_pct


def is_lower_is_better(metric_key: str, unit: str) -> bool:
    """判断该指标是否"越小越好"。

    - 延迟类（ms）：越小越好
    - 内存类（MB, objects, blocks）：越小越好
    - 吞吐类（QPS, hit_rate, speedup）：越大越好
    - overhead_vs_plain：越小越好（哈希链开销百分比）
    """
    if unit in ("qps",) or metric_key in ("hit_rate", "speedup"):
        return False
    return True


def compare_metrics(latest: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """对比 latest 与 baseline 中的数值指标。

    Returns:
        {
            "comparisons": [
                {
                    "class_name": str,
                    "test_name": str,
                    "metric": str,
                    "unit": str,
                    "baseline_value": float,
                    "actual_value": float | None,
                    "regression_pct": float | None,
                    "is_regression": bool,
                    "is_improvement": bool,
                    "lower_is_better": bool,
                    "notes": str,
                    "status": "matched"|"missing_actual"|"missing_baseline"|"skipped",
                }
            ],
            "regression_count": int,
            "improvement_count": int,
            "matched_count": int,
            "missing_actual_count": int,
        }
    """
    baseline_metrics = baseline.get("metrics", {})
    tests_by_key: Dict[str, Dict[str, Any]] = {}
    for t in latest.get("tests", []):
        key = f"{t['class_name']}::{t['test_name']}"
        tests_by_key[key] = t

    comparisons: List[Dict[str, Any]] = []
    regression_count = 0
    improvement_count = 0
    matched_count = 0
    missing_actual_count = 0

    for class_name, methods in baseline_metrics.items():
        for test_name, info in methods.items():
            metric = info.get("metric", "")
            unit = info.get("unit", "")
            baseline_value = info.get("value")
            notes = info.get("notes", "")

            # 跳过无数值项
            if baseline_value is None or metric in ("xfail", "n/a"):
                comparisons.append(
                    {
                        "class_name": class_name,
                        "test_name": test_name,
                        "metric": metric,
                        "unit": unit,
                        "baseline_value": baseline_value,
                        "actual_value": None,
                        "regression_pct": None,
                        "is_regression": False,
                        "is_improvement": False,
                        "lower_is_better": True,
                        "notes": notes,
                        "status": "skipped",
                    }
                )
                continue

            # 找到对应测试
            key = f"{class_name}::{test_name}"
            test = tests_by_key.get(key)
            if test is None:
                comparisons.append(
                    {
                        "class_name": class_name,
                        "test_name": test_name,
                        "metric": metric,
                        "unit": unit,
                        "baseline_value": baseline_value,
                        "actual_value": None,
                        "regression_pct": None,
                        "is_regression": False,
                        "is_improvement": False,
                        "lower_is_better": True,
                        "notes": notes,
                        "status": "missing_actual",
                    }
                )
                missing_actual_count += 1
                continue

            # 测试失败时不提取数值
            if test["status"] != "PASSED":
                comparisons.append(
                    {
                        "class_name": class_name,
                        "test_name": test_name,
                        "metric": metric,
                        "unit": unit,
                        "baseline_value": baseline_value,
                        "actual_value": None,
                        "regression_pct": None,
                        "is_regression": test["status"] in ("FAILED", "ERROR"),
                        "is_improvement": False,
                        "lower_is_better": True,
                        "notes": f"test {test['status']}",
                        "status": "missing_actual",
                    }
                )
                if test["status"] in ("FAILED", "ERROR"):
                    regression_count += 1
                else:
                    missing_actual_count += 1
                continue

            # 提取实际值
            actual_value = extract_metric_from_output(test["output_lines"], metric)
            lower_is_better = is_lower_is_better(metric, unit)

            if actual_value is None:
                comparisons.append(
                    {
                        "class_name": class_name,
                        "test_name": test_name,
                        "metric": metric,
                        "unit": unit,
                        "baseline_value": baseline_value,
                        "actual_value": None,
                        "regression_pct": None,
                        "is_regression": False,
                        "is_improvement": False,
                        "lower_is_better": lower_is_better,
                        "notes": notes + " [未提取到数值]",
                        "status": "missing_actual",
                    }
                )
                missing_actual_count += 1
                continue

            # 计算回归百分比
            regression_pct = compute_regression_pct(actual_value, float(baseline_value), lower_is_better)
            is_regression = regression_pct >= REGRESSION_THRESHOLD_PCT
            is_improvement = regression_pct <= -REGRESSION_THRESHOLD_PCT

            if is_regression:
                regression_count += 1
            elif is_improvement:
                improvement_count += 1
            matched_count += 1

            comparisons.append(
                {
                    "class_name": class_name,
                    "test_name": test_name,
                    "metric": metric,
                    "unit": unit,
                    "baseline_value": baseline_value,
                    "actual_value": actual_value,
                    "regression_pct": regression_pct,
                    "is_regression": is_regression,
                    "is_improvement": is_improvement,
                    "lower_is_better": lower_is_better,
                    "notes": notes,
                    "status": "matched",
                }
            )

    return {
        "comparisons": comparisons,
        "regression_count": regression_count,
        "improvement_count": improvement_count,
        "matched_count": matched_count,
        "missing_actual_count": missing_actual_count,
    }


# 报告输出


def compare_with_baseline(latest: Dict[str, Any], baseline: Dict[str, Any]) -> int:
    """对比 latest 与 baseline，输出报告

    Returns:
        0 = 无回归
        1 = 有 ≥10% 回归
    """
    print(f"\n{Color.BOLD}{'=' * 78}{Color.RESET}")
    print(f"{Color.BOLD}性能基线回归报告{Color.RESET}")
    print(f"{'=' * 78}")
    print(f"基线版本: {baseline.get('_meta', {}).get('version', 'unknown')}")
    print(f"基线日期: {baseline.get('_meta', {}).get('created_at', 'unknown')}")
    print(f"基线提交: {baseline.get('last_commit', 'unknown')}")
    print(f"运行时间: {latest.get('ran_at', 'unknown')}")
    print(
        f"测试结果: {latest['passed']} passed, {latest['failed']} failed, "
        f"{latest['skipped']} skipped, {latest['xfail']} xfail"
    )
    print(f"{'-' * 78}")

    if latest["failed"] > 0:
        print(f"\n{Color.RED}[FAIL]{Color.RESET} 有 {latest['failed']} 个测试失败，需先修复后再对比基线。")
        print(f"\n最近输出:\n{latest.get('stdout_tail', '')}")
        return 1

    # 数值对比
    comparison = compare_metrics(latest, baseline)
    comparisons = comparison["comparisons"]

    # 阈值配置
    threshold_multipliers = baseline.get("threshold_multipliers", {})
    default_multiplier = threshold_multipliers.get("default", 1.3)

    print(f"\n{Color.BOLD}阈值配置:{Color.RESET}")
    print(f"  默认上浮系数: {default_multiplier}x (基线 × {default_multiplier} = 测试阈值)")
    print(f"  回归告警阈值: ±{REGRESSION_THRESHOLD_PCT}%")
    print(
        f"  对比结果: {comparison['matched_count']} matched, "
        f"{comparison['missing_actual_count']} missing, "
        f"{comparison['regression_count']} regressions, "
        f"{comparison['improvement_count']} improvements"
    )

    # 详细对比表
    print(f"\n{Color.BOLD}数值对比明细:{Color.RESET}")
    print(f"{'-' * 78}")
    print(f"{'测试':<50} {'基线':>10} {'实际':>10} {'回归%':>10}")
    print(f"{'-' * 78}")

    for c in comparisons:
        if c["status"] == "skipped":
            continue

        test_label = f"{c['class_name']}::{c['test_name']}"
        if len(test_label) > 48:
            test_label = test_label[:45] + "..."

        baseline_str = f"{c['baseline_value']:.4f}" if c["baseline_value"] is not None else "n/a"
        actual_str = f"{c['actual_value']:.4f}" if c["actual_value"] is not None else "n/a"

        if c["regression_pct"] is None:
            regression_str = "n/a"
            color = Color.YELLOW
        elif c["is_regression"]:
            regression_str = f"+{c['regression_pct']:.1f}%"
            color = Color.RED
        elif c["is_improvement"]:
            regression_str = f"{c['regression_pct']:.1f}%"
            color = Color.GREEN
        else:
            regression_str = f"{c['regression_pct']:+.1f}%"
            color = Color.RESET

        unit = c["unit"]
        print(
            f"{test_label:<50} {baseline_str:>8}{unit:<2} "
            f"{actual_str:>8}{unit:<2} {color}{regression_str:>10}{Color.RESET}"
        )

    print(f"{'-' * 78}")

    # 回归告警
    if comparison["regression_count"] > 0:
        print(
            f"\n{Color.RED}{Color.BOLD}[REGRESSION]{Color.RESET} "
            f"检测到 {comparison['regression_count']} 项性能回归 "
            f"(≥ +{REGRESSION_THRESHOLD_PCT}%):"
        )
        for c in comparisons:
            if c["is_regression"]:
                print(
                    f"  {Color.RED}•{Color.RESET} "
                    f"{c['class_name']}::{c['test_name']}: "
                    f"基线 {c['baseline_value']:.4f}{c['unit']} → "
                    f"实际 {c['actual_value']:.4f}{c['unit']} "
                    f"(+{c['regression_pct']:.1f}%)"
                )
        print(f"\n{Color.YELLOW}[建议]{Color.RESET} 使用 `git diff` 检查最近提交，定位性能回归根因。")
        return 1

    # 改进提示
    if comparison["improvement_count"] > 0:
        print(
            f"\n{Color.GREEN}[IMPROVEMENT]{Color.RESET} "
            f"检测到 {comparison['improvement_count']} 项性能改进 "
            f"(≤ -{REGRESSION_THRESHOLD_PCT}%):"
        )
        for c in comparisons:
            if c["is_improvement"]:
                print(
                    f"  {Color.GREEN}•{Color.RESET} "
                    f"{c['class_name']}::{c['test_name']}: "
                    f"基线 {c['baseline_value']:.4f}{c['unit']} → "
                    f"实际 {c['actual_value']:.4f}{c['unit']} "
                    f"({c['regression_pct']:.1f}%)"
                )
        print(f"\n{Color.YELLOW}[建议]{Color.RESET} 建议更新 BASELINE.json 以反映新的性能水平。")

    # 缺失项
    if comparison["missing_actual_count"] > 0:
        print(
            f"\n{Color.YELLOW}[WARN]{Color.RESET} "
            f"{comparison['missing_actual_count']} 项指标未能从测试输出中提取数值，"
            f"建议检查 print 格式或扩展 extract_metric_from_output 规则。"
        )

    print(f"\n{Color.GREEN}[OK]{Color.RESET} 性能基线对比完成，无回归。")
    print(f"  - LATEST 文件: {LATEST_FILE}")
    print(f"  - BASELINE 文件: {BASELINE_FILE}")
    print(f"{'=' * 78}\n")

    return 0


# 兼容别名（历史代码可能使用其他名称）
compute_metrics_comparison = compare_metrics


# 主入口


def main():
    parser = argparse.ArgumentParser(description="性能基线回归对比工具")
    parser.add_argument(
        "--baseline",
        default=str(BASELINE_FILE),
        help=f"基线 JSON 文件路径 (默认: {BASELINE_FILE})",
    )
    parser.add_argument(
        "--compare-only",
        default=None,
        help="仅对比已有 JSON 结果，不运行测试",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="不运行测试，仅生成空 LATEST.json",
    )
    args = parser.parse_args()

    # 加载基线
    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"{Color.RED}[ERROR]{Color.RESET} 基线文件不存在: {baseline_path}")
        return 1

    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    # 获取 latest 结果
    if args.compare_only:
        with open(args.compare_only, "r", encoding="utf-8") as f:
            latest = json.load(f)
    elif args.no_run:
        latest = {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "xfail": 0,
            "error": 0,
            "return_code": 0,
            "tests": [],
            "total_duration_sec": 0,
            "stdout_tail": "(no run)",
        }
    else:
        latest = run_perf_tests()
        # 保存 LATEST
        LATEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LATEST_FILE, "w", encoding="utf-8") as f:
            json.dump(latest, f, indent=2, ensure_ascii=False)
        print(f"{Color.CYAN}[INFO]{Color.RESET} 最新结果已保存到 {LATEST_FILE}")

    # 对比
    return compare_with_baseline(latest, baseline)


if __name__ == "__main__":
    sys.exit(main())
