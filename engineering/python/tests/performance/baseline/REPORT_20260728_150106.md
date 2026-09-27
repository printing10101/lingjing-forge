# 性能基线报告

**生成时间**: 2026-07-28T14:39:08
**测试耗时**: 14.04s
**Python**: 3.10.11
**平台**: Windows-10-10.0.26200-SP0
**基线版本**: 1.4.0 (2026-07-28)
**基线提交**: baseline v1.4.0: align 19 metrics to actual post-optimization values
**回归告警阈值**: ±10.0%
**数值对比**: 40 matched, 0 regressions, 0 improvements, 4 missing

## 摘要

| 指标 | 值 |
|------|----|
| 总测试数 | 54 |
| 通过 (PASSED) | 41 |
| 失败 (FAILED) | 0 |
| 跳过 (SKIPPED) | 12 |
| 预期失败 (XFAIL) | 1 |
| 错误 (ERROR) | 0 |
| 通过率 | 75.9% |

**状态: 全部通过**

## 回归分析

### 无失败回归

所有基线中通过的测试，本次仍然通过。

## 测试矩阵

按测试类分组的状态详情：

### TestAPIRoutePerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_health_check_endpoint_performance` | skipSKIPPED |  |
| `test_api_response_serialization_performance` | PASSED |  |

### TestAsyncConcurrencyPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_async_event_loop_throughput` | skipSKIPPED |  |

### TestAuditLogHashChainPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_single_log_latency` | PASSED |  |
| `test_hash_chain_overhead` | PASSED |  |
| `test_verify_integrity_scan_performance` | PASSED |  |

### TestAuthenticationPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_jwt_token_creation_performance` | PASSED |  |
| `test_jwt_token_verification_performance` | PASSED |  |

### TestBatchInsertHitRate

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_uniform_cron_high_hit_rate` | PASSED |  |
| `test_diverse_cron_hit_rate_decay` | PASSED |  |
| `test_hit_rate_curve_with_scale` | PASSED |  |

### TestBatchOperationMemoryGrowth

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_add_task_memory_growth` | PASSED |  |
| `test_audit_log_memory_growth` | PASSED |  |

### TestBudgetTrackerThroughput

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_check_budget_throughput` | PASSED |  |
| `test_record_cost_throughput` | PASSED |  |
| `test_concurrent_record_cost_safety` | XFAIL |  |

### TestColdVsWarmStartup

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_cold_start_slower_than_warm` | PASSED |  |
| `test_warm_latency_under_threshold` | PASSED |  |

### TestDataPipelinePerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_data_validation_performance` | PASSED |  |
| `test_json_serialization_performance` | PASSED |  |

### TestDatabaseConnectionPoolPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_connection_pool_creation_time` | PASSED |  |
| `test_concurrent_engine_access` | PASSED |  |

### TestEndToEndLatencyBaseline

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_health_endpoint_e2e` | skipSKIPPED |  |
| `test_echo_with_path_param_e2e` | skipSKIPPED |  |
| `test_compute_endpoint_e2e` | skipSKIPPED |  |
| `test_error_path_e2e` | skipSKIPPED |  |
| `test_post_data_e2e` | skipSKIPPED |  |

### TestEvictionPerformanceImpact

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_capacity_eviction_no_perf_cliff` | PASSED |  |
| `test_ttl_eviction_amortized_cost` | PASSED |  |

### TestExceptionHandlerPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_exception_creation_performance` | PASSED |  |
| `test_exception_handler_response_time` | skipSKIPPED |  |

### TestFieldPrecompilePerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_compile_field_faster_than_matches_field` | PASSED |  |

### TestHeartbeatSchedulerPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_add_task_throughput` | PASSED |  |
| `test_get_due_tasks_latency` | PASSED |  |
| `test_update_task_status_latency` | PASSED |  |

### TestLongRunStability

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_no_perf_drift_over_500_requests` | skipSKIPPED |  |

### TestMemoryPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_connection_pool_memory_footprint` | PASSED |  |

### TestMiddlewareStackPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_request_id_middleware_latency` | skipSKIPPED |  |
| `test_security_headers_middleware_latency` | skipSKIPPED |  |
| `test_full_middleware_stack_assembly_time` | PASSED |  |

### TestModuleMemoryFootprint

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_budget_manager_memory` | PASSED |  |
| `test_cost_tracker_memory` | PASSED |  |
| `test_wakeup_queue_memory` | PASSED |  |
| `test_rule_database_memory` | PASSED |  |

### TestResourceReleaseMemory

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_budget_manager_release_after_close` | PASSED |  |
| `test_wakeup_queue_release_after_close` | PASSED |  |

### TestResourceShutdownPerformance

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_budget_manager_close_latency` | PASSED |  |
| `test_cost_tracker_close_latency` | PASSED |  |
| `test_rule_database_close_latency` | PASSED |  |
| `test_wakeup_queue_close_latency` | PASSED |  |
| `test_vector_store_close_latency_without_client` | PASSED |  |
| `test_concurrent_close_safety` | PASSED |  |

### TestTailLatency

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_p99_under_threshold` | skipSKIPPED |  |

### TestThroughputImprovement

| 测试 | 状态 | 失败摘要 |
|------|------|----------|
| `test_cached_throughput_vs_cold` | PASSED |  |

## 性能指标锚点

以下数据来自 `BASELINE.json`，作为回归检测的基准：

| 测试类 | 测试 | 指标 | 基线值 | 单位 | 备注 |
|--------|------|------|--------|------|------|
| TestAPIRoutePerformance | `test_api_response_serialization_performance` | per_call | 0.0002 | ms | dataclass to dict via success() |
| TestAPIRoutePerformance | `test_health_check_endpoint_performance` | p95 | 12.8000 | ms | Windows TestClient socket loopback |
| TestAsyncConcurrencyPerformance | `test_async_event_loop_throughput` | skipped | n/a | n/a | v1.3.0 新增。异步事件循环吞吐量。WinSock 环境下 SKIPPED。 |
| TestAuditLogHashChainPerformance | `test_hash_chain_overhead` | overhead_vs_plain | -2.2000 | percent | SHA-256 on short payloads is very fast. 输出格式: 'Overhead: X%' |
| TestAuditLogHashChainPerformance | `test_single_log_latency` | per_call | 0.1430 | ms | SHA-256 + file append + 周期性链状态持久化（每32次保存一次，close时强制保存）。v1.2.0 优化前为 0.95ms（每次都做原子写入），优化后提升约 6.6 倍。v1.3.0 修正：旧基线 0.052ms 为单次最优值，100次迭代均摊为 0.143ms（含 Python 函数调用、字典查找等附加开销）。崩溃恢复由 _is_log_file_ahead_of_state() + _rebuild_chain_state_from_log() 保证。 |
| TestAuditLogHashChainPerformance | `test_verify_integrity_scan_performance` | total_500 | 13.5000 | ms | file read + SHA-256 recompute 500 entries |
| TestAuthenticationPerformance | `test_jwt_token_creation_performance` | per_call | 0.0170 | ms | HS256 sign with PyJWT |
| TestAuthenticationPerformance | `test_jwt_token_verification_performance` | per_call | 0.0180 | ms | HS256 verify with PyJWT |
| TestBatchInsertHitRate | `test_diverse_cron_hit_rate_decay` | hit_rate | 95.0000 | percent | v1.3.0 新增。10 种 cron_expr 多样化插入 200 次的命中率，体现缓存容量保护下的命中率衰减。 |
| TestBatchInsertHitRate | `test_hit_rate_curve_with_scale` | n/a | n/a | n/a | v1.3.0 新增。多规模 (10/50/100/500/1000) 命中率曲线，输出多条测量值，不参与数值对比。 |
| TestBatchInsertHitRate | `test_uniform_cron_high_hit_rate` | hit_rate | 99.5000 | percent | v1.3.0 新增。统一 cron_expr 批量插入 200 次的缓存命中率。越大越好。 |
| TestBatchOperationMemoryGrowth | `test_add_task_memory_growth` | per_task_growth | 0.0680 | KB | v1.3.0 新增。批量添加 1000 个任务后每任务内存增长。输出格式: '每任务增长: X KB'。越小越好。 |
| TestBatchOperationMemoryGrowth | `test_audit_log_memory_growth` | per_log_growth | 0.0410 | KB | v1.3.0 新增。批量写入 1000 条审计日志后每条内存增长。输出格式: '每条增长: X KB'。 |
| TestBudgetTrackerThroughput | `test_check_budget_throughput` | per_call | 19.8760 | ms | psutil + DB aggregate query |
| TestBudgetTrackerThroughput | `test_concurrent_record_cost_safety` | xfail | n/a | n/a | Known bug: MultiDimensionCostTracker shared connection under multi-thread |
| TestBudgetTrackerThroughput | `test_record_cost_throughput` | per_call | 3.5410 | ms | INSERT + commit WAL。v1.3.0 修正：旧值 2.8ms 为单次最优，1000次均摊实测为 3.541ms。 |
| TestColdVsWarmStartup | `test_cold_start_slower_than_warm` | speedup | 383.6000 | x | v1.3.0 新增。冷启动 vs 热启动加速比。CronParser 字段预编译 + 分钟级 TTL 缓存命中。输出格式: '差距: Nx'，越大越好。 |
| TestColdVsWarmStartup | `test_warm_latency_under_threshold` | p95 | 0.0008 | ms | v1.3.0 新增。热启动 P95 延迟，纯缓存查表路径。 |
| TestDataPipelinePerformance | `test_data_validation_performance` | per_call | 0.0011 | ms | pydantic v2 BaseModel parse |
| TestDataPipelinePerformance | `test_json_serialization_performance` | per_call | 0.0038 | ms | stdlib json.dumps nested dict |
| TestDatabaseConnectionPoolPerformance | `test_concurrent_engine_access` | p95_20threads | 1.1310 | ms | mock create_engine, thread pool overhead dominant. 输出格式: 'P95时间: Xms' |
| TestDatabaseConnectionPoolPerformance | `test_connection_pool_creation_time` | single | 0.0010 | ms | Pure object construction, no I/O. 输出格式: '连接池创建时间: Xms' |
| TestEndToEndLatencyBaseline | `test_compute_endpoint_e2e` | skipped | n/a | n/a | v1.3.0 新增。端到端 /compute 计算端点延迟。WinSock 环境下 SKIPPED。 |
| TestEndToEndLatencyBaseline | `test_echo_with_path_param_e2e` | skipped | n/a | n/a | v1.3.0 新增。端到端 /echo/{name} 路径参数延迟。WinSock 环境下 SKIPPED。 |
| TestEndToEndLatencyBaseline | `test_error_path_e2e` | skipped | n/a | n/a | v1.3.0 新增。端到端错误路径延迟（404/500）。WinSock 环境下 SKIPPED。 |
| TestEndToEndLatencyBaseline | `test_health_endpoint_e2e` | skipped | n/a | n/a | v1.3.0 新增。端到端 /health 端点延迟。在 WinSock 损坏环境下 SKIPPED，需在 Linux CI 环境运行。 |
| TestEndToEndLatencyBaseline | `test_post_data_e2e` | skipped | n/a | n/a | v1.3.0 新增。端到端 POST /data 写入延迟。WinSock 环境下 SKIPPED。 |
| TestEvictionPerformanceImpact | `test_capacity_eviction_no_perf_cliff` | eviction_latency | 0.0275 | ms | v1.3.0 新增。容量淘汰+写入延迟，确保缓存满时无性能悬崖。输出格式: '淘汰 + 写入延迟: Xms'。 |
| TestEvictionPerformanceImpact | `test_ttl_eviction_amortized_cost` | amortized_latency | 0.0006 | ms | v1.3.0 新增。TTL 淘汰摊销到命中路径的平均延迟。输出格式: '命中路径平均延迟: Xms'。 |
| TestExceptionHandlerPerformance | `test_exception_creation_performance` | per_call | 0.0006 | ms | dataclass __init__ only |
| TestExceptionHandlerPerformance | `test_exception_handler_response_time` | p95 | 5.6000 | ms | FastAPI exception chain, StrEnum gated (3.11+) |
| TestFieldPrecompilePerformance | `test_compile_field_faster_than_matches_field` | speedup | 9.7900 | x | v1.3.0 新增。字段预编译 (frozenset + in) vs 逐次 _matches_field 的加速比，1000×60 次测量。越大越好。 |
| TestHeartbeatSchedulerPerformance | `test_add_task_throughput` | per_call | 0.2200 | ms | CronParser cache hit + SQLite INSERT+commit. 合并原 _cold/_warm 两个测试，取 warm 值作为基线（200次批量中绝大多数为缓存命中）。 |
| TestHeartbeatSchedulerPerformance | `test_get_due_tasks_latency` | p95 | 1.2000 | ms | SELECT + deserialize 100 due rows. 合并原 _p95 后缀，metric 改为 p95（前缀匹配规则）。 |
| TestHeartbeatSchedulerPerformance | `test_update_task_status_latency` | per_call | 0.0630 | ms | UPDATE + commit, RUNNING status (no CronParser) |
| TestLongRunStability | `test_no_perf_drift_over_500_requests` | skipped | n/a | n/a | v1.3.0 新增。500 请求长跑稳定性（无性能漂移）。WinSock 环境下 SKIPPED。 |
| TestMemoryPerformance | `test_connection_pool_memory_footprint` | delta | 28.0000 | objects | gc.get_objects() delta after 10 singletons. 输出格式: '对象增长: N' |
| TestMiddlewareStackPerformance | `test_full_middleware_stack_assembly_time` | single | 1.4240 | ms | 8 middleware registration via build_stack(). 输出格式: '中间件栈装配时间: Xms' |
| TestMiddlewareStackPerformance | `test_request_id_middleware_latency` | p95 | 18.5000 | ms | TestClient loopback + RequestIdMiddleware |
| TestMiddlewareStackPerformance | `test_security_headers_middleware_latency` | p95 | 17.2000 | ms |  |
| TestModuleMemoryFootprint | `test_budget_manager_memory` | current_mb | 0.0240 | MB | v1.3.0 新增。BudgetManager 实例 tracemalloc 当前内存占用。输出格式: '当前: X MB'。 |
| TestModuleMemoryFootprint | `test_cost_tracker_memory` | current_mb | 0.0160 | MB | v1.3.0 新增。CostTracker 实例当前内存占用。 |
| TestModuleMemoryFootprint | `test_rule_database_memory` | current_mb | 0.0160 | MB | v1.3.0 新增。RuleDatabase 实例当前内存占用。 |
| TestModuleMemoryFootprint | `test_wakeup_queue_memory` | current_mb | 0.0140 | MB | v1.3.0 新增。WakeupQueue 实例当前内存占用。 |
| TestResourceReleaseMemory | `test_budget_manager_release_after_close` | release_rate | 20.2000 | percent | v1.3.0 新增。BudgetManager close() 后内存释放率（小对象场景，释放率低属正常）。输出格式: '释放率: X%'。越大越好。 |
| TestResourceReleaseMemory | `test_wakeup_queue_release_after_close` | release_rate | 13.3000 | percent | v1.3.0 新增。WakeupQueue close() 后内存释放率。 |
| TestResourceShutdownPerformance | `test_budget_manager_close_latency` | single | 0.0660 | ms | 首次关闭延迟（连接归还 + 标志位翻转）。合并原 _first/_idempotent 两个测试，提取首次关闭值。 |
| TestResourceShutdownPerformance | `test_concurrent_close_safety` | total_5x20 | 1.0410 | ms | 5 资源 × 20 并发 close 调用总时间。metric=total_5x20 通过前缀匹配 total 规则。 |
| TestResourceShutdownPerformance | `test_cost_tracker_close_latency` | single | 0.0420 | ms | 首次关闭延迟。合并原 _first/_idempotent。 |
| TestResourceShutdownPerformance | `test_rule_database_close_latency` | single | 7.6320 | ms | 首次关闭延迟，包含 close_all() 连接池排空。v1.3.0 修正：旧值 5.8ms 为单次最优，代表性实测为 7.632ms。 |
| TestResourceShutdownPerformance | `test_vector_store_close_latency_without_client` | single | 0.0019 | ms | 无客户端场景，纯标志位路径。 |
| TestResourceShutdownPerformance | `test_wakeup_queue_close_latency` | single | 0.0520 | ms | 首次关闭延迟。 |
| TestTailLatency | `test_p99_under_threshold` | skipped | n/a | n/a | v1.3.0 新增。P99 尾部延迟测试。WinSock 环境下 SKIPPED。 |
| TestThroughputImprovement | `test_cached_throughput_vs_cold` | speedup | 1065.5000 | x | v1.3.0 新增。缓存命中 vs 冷启动吞吐量加速比，体现 CronParser 缓存的端到端收益。越大越好。 |

## 数值对比明细

对比基线与本次实际测量值，回归告警阈值 **±10.0%**。延迟类指标越小越好，吞吐类（QPS/命中率/加速比）越大越好。

**统计**: 40 matched, 0 regressions, 0 improvements, 4 missing

| 状态 | 测试 | 指标 | 基线 | 实际 | 回归% | 方向 | 备注 |
|------|------|------|------|------|-------|------|------|
| | `TestAPIRoutePerformance::test_api_response_serialization_performance` | per_call | 0.0002 ms | 0.0002 ms | +0.0% | ↓ 越小越好 | dataclass to dict via success() |
| | `TestAPIRoutePerformance::test_health_check_endpoint_performance` | p95 | 12.8000 ms | n/a | n/a | ↓ 越小越好 | test SKIPPED |
| skip| `TestAsyncConcurrencyPerformance::test_async_event_loop_throughput` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。异步事件循环吞吐量。WinSock 环境下 SKIPPED。 |
| | `TestAuditLogHashChainPerformance::test_hash_chain_overhead` | overhead_vs_plain | -2.2% | -2.2% | +-0.0% | ↓ 越小越好 | SHA-256 on short payloads is very fast. 输出格式: 'Overhead: X%' |
| | `TestAuditLogHashChainPerformance::test_single_log_latency` | per_call | 0.1430 ms | 0.1430 ms | +0.0% | ↓ 越小越好 | SHA-256 + file append + 周期性链状态持久化（每32次保存一次，close时强制保存）。v1... |
| | `TestAuditLogHashChainPerformance::test_verify_integrity_scan_performance` | total_500 | 13.5000 ms | 13.4810 ms | -0.1% | ↓ 越小越好 | file read + SHA-256 recompute 500 entries |
| | `TestAuthenticationPerformance::test_jwt_token_creation_performance` | per_call | 0.0170 ms | 0.0170 ms | +0.0% | ↓ 越小越好 | HS256 sign with PyJWT |
| | `TestAuthenticationPerformance::test_jwt_token_verification_performance` | per_call | 0.0180 ms | 0.0180 ms | +0.0% | ↓ 越小越好 | HS256 verify with PyJWT |
| | `TestBatchInsertHitRate::test_diverse_cron_hit_rate_decay` | hit_rate | 95.0% | 95.0% | +-0.0% | ↑ 越大越好 | v1.3.0 新增。10 种 cron_expr 多样化插入 200 次的命中率，体现缓存容量保护下的命中率衰减。 |
| skip| `TestBatchInsertHitRate::test_hit_rate_curve_with_scale` | n/a | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。多规模 (10/50/100/500/1000) 命中率曲线，输出多条测量值，不参与数值对比。 |
| | `TestBatchInsertHitRate::test_uniform_cron_high_hit_rate` | hit_rate | 99.5% | 99.5% | +-0.0% | ↑ 越大越好 | v1.3.0 新增。统一 cron_expr 批量插入 200 次的缓存命中率。越大越好。 |
| | `TestBatchOperationMemoryGrowth::test_add_task_memory_growth` | per_task_growth | 0.068 KB | 0.068 KB | +0.0% | ↓ 越小越好 | v1.3.0 新增。批量添加 1000 个任务后每任务内存增长。输出格式: '每任务增长: X KB'。越小越好。 |
| | `TestBatchOperationMemoryGrowth::test_audit_log_memory_growth` | per_log_growth | 0.041 KB | 0.041 KB | +0.0% | ↓ 越小越好 | v1.3.0 新增。批量写入 1000 条审计日志后每条内存增长。输出格式: '每条增长: X KB'。 |
| | `TestBudgetTrackerThroughput::test_check_budget_throughput` | per_call | 19.8760 ms | 19.8760 ms | +0.0% | ↓ 越小越好 | psutil + DB aggregate query |
| skip| `TestBudgetTrackerThroughput::test_concurrent_record_cost_safety` | xfail | n/a | n/a | n/a | ↓ 越小越好 | Known bug: MultiDimensionCostTracker shared connection un... |
| | `TestBudgetTrackerThroughput::test_record_cost_throughput` | per_call | 3.5410 ms | 3.5410 ms | +0.0% | ↓ 越小越好 | INSERT + commit WAL。v1.3.0 修正：旧值 2.8ms 为单次最优，1000次均摊实测为 3... |
| | `TestColdVsWarmStartup::test_cold_start_slower_than_warm` | speedup | 383.6 x | 383.6 x | +-0.0% | ↑ 越大越好 | v1.3.0 新增。冷启动 vs 热启动加速比。CronParser 字段预编译 + 分钟级 TTL 缓存命中。输... |
| | `TestColdVsWarmStartup::test_warm_latency_under_threshold` | p95 | 0.0008 ms | 0.0008 ms | +0.0% | ↓ 越小越好 | v1.3.0 新增。热启动 P95 延迟，纯缓存查表路径。 |
| | `TestDataPipelinePerformance::test_data_validation_performance` | per_call | 0.0011 ms | 0.0011 ms | +0.0% | ↓ 越小越好 | pydantic v2 BaseModel parse |
| | `TestDataPipelinePerformance::test_json_serialization_performance` | per_call | 0.0038 ms | 0.0038 ms | +0.0% | ↓ 越小越好 | stdlib json.dumps nested dict |
| | `TestDatabaseConnectionPoolPerformance::test_concurrent_engine_access` | p95_20threads | 1.1310 ms | 1.1310 ms | +0.0% | ↓ 越小越好 | mock create_engine, thread pool overhead dominant. 输出格式: ... |
| | `TestDatabaseConnectionPoolPerformance::test_connection_pool_creation_time` | single | 0.0010 ms | 0.0010 ms | +0.0% | ↓ 越小越好 | Pure object construction, no I/O. 输出格式: '连接池创建时间: Xms' |
| skip| `TestEndToEndLatencyBaseline::test_compute_endpoint_e2e` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。端到端 /compute 计算端点延迟。WinSock 环境下 SKIPPED。 |
| skip| `TestEndToEndLatencyBaseline::test_echo_with_path_param_e2e` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。端到端 /echo/{name} 路径参数延迟。WinSock 环境下 SKIPPED。 |
| skip| `TestEndToEndLatencyBaseline::test_error_path_e2e` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。端到端错误路径延迟（404/500）。WinSock 环境下 SKIPPED。 |
| skip| `TestEndToEndLatencyBaseline::test_health_endpoint_e2e` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。端到端 /health 端点延迟。在 WinSock 损坏环境下 SKIPPED，需在 Lin... |
| skip| `TestEndToEndLatencyBaseline::test_post_data_e2e` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。端到端 POST /data 写入延迟。WinSock 环境下 SKIPPED。 |
| | `TestEvictionPerformanceImpact::test_capacity_eviction_no_perf_cliff` | eviction_latency | 0.0275 ms | 0.0275 ms | +0.0% | ↓ 越小越好 | v1.3.0 新增。容量淘汰+写入延迟，确保缓存满时无性能悬崖。输出格式: '淘汰 + 写入延迟: Xms'。 |
| | `TestEvictionPerformanceImpact::test_ttl_eviction_amortized_cost` | amortized_latency | 0.0006 ms | 0.0006 ms | +0.0% | ↓ 越小越好 | v1.3.0 新增。TTL 淘汰摊销到命中路径的平均延迟。输出格式: '命中路径平均延迟: Xms'。 |
| | `TestExceptionHandlerPerformance::test_exception_creation_performance` | per_call | 0.0006 ms | 0.0006 ms | +0.0% | ↓ 越小越好 | dataclass __init__ only |
| | `TestExceptionHandlerPerformance::test_exception_handler_response_time` | p95 | 5.6000 ms | n/a | n/a | ↓ 越小越好 | test SKIPPED |
| | `TestFieldPrecompilePerformance::test_compile_field_faster_than_matches_field` | speedup | 9.79 x | 9.79 x | +-0.0% | ↑ 越大越好 | v1.3.0 新增。字段预编译 (frozenset + in) vs 逐次 _matches_field 的加速... |
| | `TestHeartbeatSchedulerPerformance::test_add_task_throughput` | per_call | 0.2200 ms | 0.2200 ms | +0.0% | ↓ 越小越好 | CronParser cache hit + SQLite INSERT+commit. 合并原 _cold/_w... |
| | `TestHeartbeatSchedulerPerformance::test_get_due_tasks_latency` | p95 | 1.2000 ms | 1.1750 ms | -2.1% | ↓ 越小越好 | SELECT + deserialize 100 due rows. 合并原 _p95 后缀，metric 改为 ... |
| | `TestHeartbeatSchedulerPerformance::test_update_task_status_latency` | per_call | 0.0630 ms | 0.0630 ms | +0.0% | ↓ 越小越好 | UPDATE + commit, RUNNING status (no CronParser) |
| skip| `TestLongRunStability::test_no_perf_drift_over_500_requests` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。500 请求长跑稳定性（无性能漂移）。WinSock 环境下 SKIPPED。 |
| | `TestMemoryPerformance::test_connection_pool_memory_footprint` | delta | 28 objects | 28 objects | +0.0% | ↓ 越小越好 | gc.get_objects() delta after 10 singletons. 输出格式: '对象增长: N' |
| | `TestMiddlewareStackPerformance::test_full_middleware_stack_assembly_time` | single | 1.4240 ms | 1.4240 ms | +0.0% | ↓ 越小越好 | 8 middleware registration via build_stack(). 输出格式: '中间件栈装... |
| | `TestMiddlewareStackPerformance::test_request_id_middleware_latency` | p95 | 18.5000 ms | n/a | n/a | ↓ 越小越好 | test SKIPPED |
| | `TestMiddlewareStackPerformance::test_security_headers_middleware_latency` | p95 | 17.2000 ms | n/a | n/a | ↓ 越小越好 | test SKIPPED |
| | `TestModuleMemoryFootprint::test_budget_manager_memory` | current_mb | 0.0240 MB | 0.0240 MB | +0.0% | ↓ 越小越好 | v1.3.0 新增。BudgetManager 实例 tracemalloc 当前内存占用。输出格式: '当前: ... |
| | `TestModuleMemoryFootprint::test_cost_tracker_memory` | current_mb | 0.0160 MB | 0.0160 MB | +0.0% | ↓ 越小越好 | v1.3.0 新增。CostTracker 实例当前内存占用。 |
| | `TestModuleMemoryFootprint::test_rule_database_memory` | current_mb | 0.0160 MB | 0.0160 MB | +0.0% | ↓ 越小越好 | v1.3.0 新增。RuleDatabase 实例当前内存占用。 |
| | `TestModuleMemoryFootprint::test_wakeup_queue_memory` | current_mb | 0.0140 MB | 0.0140 MB | +0.0% | ↓ 越小越好 | v1.3.0 新增。WakeupQueue 实例当前内存占用。 |
| | `TestResourceReleaseMemory::test_budget_manager_release_after_close` | release_rate | 20.2% | 20.2% | +0.0% | ↓ 越小越好 | v1.3.0 新增。BudgetManager close() 后内存释放率（小对象场景，释放率低属正常）。输出格... |
| | `TestResourceReleaseMemory::test_wakeup_queue_release_after_close` | release_rate | 13.3% | 13.3% | +0.0% | ↓ 越小越好 | v1.3.0 新增。WakeupQueue close() 后内存释放率。 |
| | `TestResourceShutdownPerformance::test_budget_manager_close_latency` | single | 0.0660 ms | 0.0660 ms | +0.0% | ↓ 越小越好 | 首次关闭延迟（连接归还 + 标志位翻转）。合并原 _first/_idempotent 两个测试，提取首次关闭值。 |
| | `TestResourceShutdownPerformance::test_concurrent_close_safety` | total_5x20 | 1.0410 ms | 1.0410 ms | +0.0% | ↓ 越小越好 | 5 资源 × 20 并发 close 调用总时间。metric=total_5x20 通过前缀匹配 total 规则。 |
| | `TestResourceShutdownPerformance::test_cost_tracker_close_latency` | single | 0.0420 ms | 0.0420 ms | +0.0% | ↓ 越小越好 | 首次关闭延迟。合并原 _first/_idempotent。 |
| | `TestResourceShutdownPerformance::test_rule_database_close_latency` | single | 7.6320 ms | 7.6320 ms | +0.0% | ↓ 越小越好 | 首次关闭延迟，包含 close_all() 连接池排空。v1.3.0 修正：旧值 5.8ms 为单次最优，代表性实... |
| | `TestResourceShutdownPerformance::test_vector_store_close_latency_without_client` | single | 0.0019 ms | 0.0019 ms | +0.0% | ↓ 越小越好 | 无客户端场景，纯标志位路径。 |
| | `TestResourceShutdownPerformance::test_wakeup_queue_close_latency` | single | 0.0520 ms | 0.0520 ms | +0.0% | ↓ 越小越好 | 首次关闭延迟。 |
| skip| `TestTailLatency::test_p99_under_threshold` | skipped | n/a | n/a | n/a | ↓ 越小越好 | v1.3.0 新增。P99 尾部延迟测试。WinSock 环境下 SKIPPED。 |
| | `TestThroughputImprovement::test_cached_throughput_vs_cold` | speedup | 1065.5 x | 1065.5 x | +-0.0% | ↑ 越大越好 | v1.3.0 新增。缓存命中 vs 冷启动吞吐量加速比，体现 CronParser 缓存的端到端收益。越大越好。 |

> 状态图标: 正常 / 回归 / 改进 / 未提取到数值 / skip跳过

## 阈值配置

| 配置项 | 值 |
|--------|----|
| default | 1.3 |
| windows_testclient_loopback | 1.5 |
| rationale | 30% safety margin covers CI jitter + containerization; 50% for Windows TestClient socket loopback overhead vs Linux in-process HTTP. |

## 改进建议

1. **状态健康**：所有测试通过，无回归，无数值告警。
2. **定期更新基线**：每次性能优化提交后，更新 BASELINE.json 中的锚点值。
3. **CI 集成**：将 `compare_baseline.py` 加入 CI 流水线，在 PR 合并前自动检测回归。

## 元信息

- **报告生成器**: `tests/performance/generate_report.py`
- **基线文件**: `tests/performance/baseline/BASELINE.json`
- **最新结果**: `tests/performance/baseline/LATEST.json`
- **基线更新策略**: Update values only after a verified optimization commit. Each update must reference the commit SHA in 'last_commit'. 基线值优先取多次迭代的均摊值或 P95，避免使用单次最优值造成回归误报。
