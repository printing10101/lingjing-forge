"""检查 missing 项的实际输出，分析为何 metric 提取失败。"""

import json
from pathlib import Path

d = json.loads(Path(__file__).parent.joinpath("baseline", "LATEST.json").read_text(encoding="utf-8"))

# 需要检查的 missing 测试（来自报告中的 项）
missing_tests = [
    ("TestDatabaseConnectionPoolPerformance", "test_connection_pool_creation_time"),
    ("TestDatabaseConnectionPoolPerformance", "test_concurrent_engine_access"),
    ("TestAuditLogHashChainPerformance", "test_hash_chain_overhead"),
    ("TestMiddlewareStackPerformance", "test_full_middleware_stack_assembly_time"),
    ("TestResourceShutdownPerformance", "test_budget_manager_close_latency"),
    ("TestResourceShutdownPerformance", "test_cost_tracker_close_latency"),
    ("TestResourceShutdownPerformance", "test_rule_database_close_latency"),
    ("TestResourceShutdownPerformance", "test_wakeup_queue_close_latency"),
    ("TestResourceShutdownPerformance", "test_vector_store_close_latency_without_client"),
    ("TestResourceShutdownPerformance", "test_concurrent_close_safety"),
    ("TestMemoryPerformance", "test_connection_pool_memory_footprint"),
]

print("=== Missing 测试的实际输出 ===\n")
for cls, name in missing_tests:
    found = False
    for t in d["tests"]:
        if t["class_name"] == cls and t["test_name"] == name:
            print(f"[{cls}::{name}]")
            for line in t["output_lines"]:
                print(f"  {line}")
            print()
            found = True
            break
    if not found:
        print(f"[{cls}::{name}] —— 未在 LATEST.json 中找到！\n")
