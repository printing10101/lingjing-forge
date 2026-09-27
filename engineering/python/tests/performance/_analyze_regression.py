"""分析 test_get_due_tasks_latency 的回归是否真实。

读取 LATEST.json 中该测试的实际输出，并对比 BASELINE.json 的基线值。
"""

import json
from pathlib import Path

BASELINE = Path(__file__).parent / "baseline" / "BASELINE.json"
LATEST = Path(__file__).parent / "baseline" / "LATEST.json"

baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
latest = json.loads(LATEST.read_text(encoding="utf-8"))

target_cls = "TestHeartbeatSchedulerPerformance"
target_test = "test_get_due_tasks_latency"

print("=== 基线值 ===")
b = baseline["metrics"][target_cls][target_test]
print(f"  metric: {b['metric']}")
print(f"  value:  {b['value']} {b['unit']}")
print(f"  notes:  {b.get('notes', '')}")

print("\n=== 本次实际输出 ===")
for t in latest["tests"]:
    if t["class_name"] == target_cls and t["test_name"] == target_test:
        print(f"  node_id: {t['node_id']}")
        print(f"  status:  {t['status']}")
        print(f"  输出 ({len(t['output_lines'])} 行):")
        for line in t["output_lines"]:
            print(f"    {line}")
        break

print("\n=== 全部 HeartbeatScheduler 测试 ===")
for t in latest["tests"]:
    if t["class_name"] == target_cls:
        print(f"\n[{t['test_name']}] status={t['status']}")
        for line in t["output_lines"][:8]:
            print(f"  {line}")
