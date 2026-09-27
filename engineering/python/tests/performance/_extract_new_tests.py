"""提取所有新增测试（未在BASELINE.json中的）的测量值，用于纳入基线。

读取LATEST.json和BASELINE.json，找出新增测试，从output_lines提取测量值。
"""
import json
import re
from pathlib import Path

PERF_DIR = Path(__file__).parent
LATEST = json.loads((PERF_DIR / "baseline" / "LATEST.json").read_text(encoding="utf-8"))
BASELINE = json.loads((PERF_DIR / "baseline" / "BASELINE.json").read_text(encoding="utf-8"))

# 构建基线中已知的测试集合
baseline_keys = set()
for class_name, methods in BASELINE.get("metrics", {}).items():
    for test_name in methods.keys():
        baseline_keys.add(f"{class_name}::{test_name}")

# 数值提取模式（与compare_baseline.py保持一致）
METRIC_PATTERNS = [
    # per_call: "每次: Xms" / "每次: X.XXXXms"
    (re.compile(r"每次[:：]\s*(\d+\.?\d*)\s*ms"), "per_call", "ms"),
    # single: "连接池创建时间: Xms" / "中间件栈装配时间: Xms"
    (re.compile(r"时间[:：]\s*(\d+\.?\d*)\s*ms"), "single", "ms"),
    # p95: "P95时间: Xms" / "P95: Xms"
    (re.compile(r"P95(?:时间)?[:：]\s*(\d+\.?\d*)\s*ms"), "p95", "ms"),
    # overhead: "Overhead: X%"
    (re.compile(r"Overhead[:：]\s*(-?\d+\.?\d*)\s*%"), "overhead_vs_plain", "percent"),
    # delta: "对象增长: N"
    (re.compile(r"对象增长[:：]\s*(\d+)"), "delta", "objects"),
    # total: "总时间: Xms"
    (re.compile(r"总时间[:：]\s*(\d+\.?\d*)\s*ms"), "total", "ms"),
    # qps: "QPS: X"
    (re.compile(r"QPS[:：]\s*(\d+\.?\d*)"), "qps", "qps"),
    # memory: "内存: X MB" / "峰值内存: X MB"
    (re.compile(r"(?:峰值)?内存[:：]\s*(\d+\.?\d*)\s*MB"), "memory", "MB"),
    # ratio: "命中率: X%" / "加速比: X"
    (re.compile(r"命中率[:：]\s*(\d+\.?\d*)\s*%"), "hit_rate", "percent"),
    (re.compile(r"加速比[:：]\s*(\d+\.?\d*)"), "speedup", "ratio"),
]

def extract_metric(output_lines):
    """从测试输出提取测量值和指标类型。"""
    text = "\n".join(output_lines)
    for pattern, metric, unit in METRIC_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                value = float(m.group(1))
                return {"value": value, "metric": metric, "unit": unit}
            except (ValueError, IndexError):
                continue
    return None

# 找出新增测试
new_tests = []
for t in LATEST.get("tests", []):
    key = f"{t['class_name']}::{t['test_name']}"
    if key not in baseline_keys and t["status"] == "PASSED":
        result = extract_metric(t.get("output_lines", []))
        if result:
            new_tests.append({
                "class_name": t["class_name"],
                "test_name": t["test_name"],
                "status": t["status"],
                "output_lines": t.get("output_lines", []),
                "extracted": result,
            })

# 按class_name分组输出
by_class = {}
for t in new_tests:
    by_class.setdefault(t["class_name"], []).append(t)

print(f"新增测试总数（已提取测量值）: {len(new_tests)}")
print()
for class_name in sorted(by_class.keys()):
    print(f"### {class_name}")
    for t in by_class[class_name]:
        e = t["extracted"]
        print(f'  "{t["test_name"]}": {{')
        print(f'    "unit": "{e["unit"]}",')
        print(f'    "metric": "{e["metric"]}",')
        print(f'    "value": {e["value"]},')
        # 输出第一行作为notes参考
        notes = (t["output_lines"][0] if t["output_lines"] else "").replace('"', "'")
        print(f'    "notes": "{notes}"')
        print('  },')
    print()

# 同时输出需要更新基线值的回归测试
print("\n### 需要更新基线值的回归测试（实际值超出基线+30%余量）")
regression_tests = [
    ("TestAuditLogHashChainPerformance", "test_single_log_latency"),
    ("TestResourceShutdownPerformance", "test_rule_database_close_latency"),
    ("TestBudgetTrackerThroughput", "test_record_cost_throughput"),
]
for class_name, test_name in regression_tests:
    for t in LATEST.get("tests", []):
        if t["class_name"] == class_name and t["test_name"] == test_name:
            result = extract_metric(t.get("output_lines", []))
            if result:
                print(f'  "{test_name}": {{')
                print(f'    "unit": "{result["unit"]}",')
                print(f'    "metric": "{result["metric"]}",')
                print(f'    "value": {result["value"]},')
                print('  },')
            break
