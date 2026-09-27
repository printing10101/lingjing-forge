# 西门子标准极限压力测试报告

- 生成时间：2026-09-27 20:28:12
- Python：3.14.4
- psutil：7.2.2
- 平台：Windows-11-10.0.26200-SP0

## 结果汇总

| 测试 | 状态 | 关键指标 | 阈值 |
|------|------|----------|------|
| 并发JWT创建+验签 | PASS | avg=0.01498; p50=0.0143; p95=0.017; p99=0.0315; max=0.1153; count=5000; qps=4.347e+04; total_ms=115 | p99<1 |
| 并发G代码生成 | PASS | avg=0.3132; p50=0.2598; p95=0.3956; p99=0.6456; max=14.94; count=1000; generated=1000; avg_lines=64.25 | errors<1 |
| 并发工艺规划 | PASS | avg=0.09818; p50=0.0917; p95=0.119; p99=0.1878; max=0.5492; count=600; total=600 | errors<1 |
| 并发心跳调度(独立DB) | PASS | threads=10; tasks_per_thread=200; due_counts=[200,200,200,200,200...] | errors<1 |
| JWT验签吞吐量 | PASS | avg=0.01405; p50=0.0136; p95=0.0156; p99=0.0201; max=0.135; count=5000; qps=7.115e+04 | qps≥1000; p99<1 |
| G代码生成吞吐量 | PASS | avg=0.2849; p50=0.2653; p95=0.3852; p99=0.5922; max=0.6323; count=200; qps=3510 | avg<10; p99<30 |
| 实时传感器处理吞吐量 | PASS | avg=0.002898; p50=0.0027; p95=0.0038; p99=0.0048; max=0.0225; count=1e+04; qps=3.451e+05 | p99<1 |
| 审计日志写入吞吐量 | PASS | avg=0.04986; p50=0.0202; p95=0.0545; p99=0.9495; max=1.315; count=3000; qps=2.005e+04; integrity=1 | avg<5; p99<10 |
| 心跳调度add_task吞吐量 | PASS | avg=0.2483; p50=0.168; p95=0.2842; p99=3.237; max=9.333; count=2000; qps=4028 | avg<10 |
| G代码生成浸泡(2000次) | PASS | baseline_rss_mb=2403; final_growth_pct=0; tail_growth_pct=0; max_growth_pct=0 | growth<15 |
| 审计日志浸泡(3000条) | PASS | baseline_rss_mb=2403; tail_growth_pct=0; max_growth_pct=0; integrity=1 | growth<15 |
| 浸泡延迟漂移(JWT) | PASS | first_p95_ms=0.0169; tail_p95_ms=0.0147; drift_ratio=0.87 | drift_ratio<3 |
| 满负荷资源阈值 | FAIL | cpu_avg=12.8; cpu_max=15; sys_mem_avg=42.3; sys_mem_delta=0; baseline_sys_mem=42.3; app_peak_rss_mb=2410; app_baseline_rss_mb=2404; net_avg_mbps=0.04; net_max_mbps=0.2 | cpu<90; net<50; app_rss_mb<1024; sys_mem_delta_pct<5 |
| G代码生成错误率 | PASS | error_rate_pct=0; errors=0; empty=0 | error_rate<1 |
| 心跳调度错误率 | PASS | error_rate_pct=0; errors=0 | error_rate<1 |
| 并发预算检查错误率 | PASS | error_rate_pct=0; errors=0 | error_rate<1 |
| 过载恢复(JWT) | PASS | baseline_p50_ms=0.0137; recovered_p50_ms=0.0137; recovery_ratio=1; burst_errors=0 | recovery_ratio<3 |
| 过载恢复(G代码生成) | PASS | baseline_p50_ms=0.261; recovered_p50_ms=0.259; recovery_ratio=0.99 | recovery_ratio<3 |

**合计：17/18 通过**

## 未通过项明细

### 满负荷资源阈值
- 状态：FAIL
- 指标：{
  "cpu_avg": 12.8,
  "cpu_max": 15.0,
  "sys_mem_avg": 42.3,
  "sys_mem_delta": 0.0,
  "baseline_sys_mem": 42.3,
  "app_peak_rss_mb": 2409.9,
  "app_baseline_rss_mb": 2403.6,
  "net_avg_mbps": 0.04,
  "net_max_mbps": 0.2
}
- 阈值：{
  "cpu": 90.0,
  "net": 50.0,
  "app_rss_mb": 1024.0,
  "sys_mem_delta_pct": 5.0
}
- 备注：6相位混合负载；硬门禁为应用可归因指标，系统绝对占用仅参考

