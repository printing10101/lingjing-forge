# 西门子标准极限压力测试报告

- 生成时间：2026-10-08 01:02:33
- Python：3.14.4
- psutil：7.2.2
- 平台：Windows-11-10.0.26300-SP0

## 结果汇总

| 测试 | 状态 | 关键指标 | 阈值 |
|------|------|----------|------|
| 并发JWT创建+验签 | PASS | avg=0.01664; p50=0.0156; p95=0.0221; p99=0.0374; max=0.1678; count=5000; qps=3.986e+04; total_ms=125.4 | p99<1 |
| 并发G代码生成 | PASS | avg=0.3049; p50=0.2722; p95=0.4639; p99=0.6007; max=0.9595; count=1000; generated=1000; avg_lines=64.25 | errors<1 |
| 并发工艺规划 | PASS | avg=0.1023; p50=0.0964; p95=0.1399; p99=0.2105; max=0.2934; count=600; total=600 | errors<1 |
| 并发心跳调度(独立DB) | PASS | threads=10; tasks_per_thread=200; due_counts=[200,200,200,200,200...] | errors<1 |
| JWT验签吞吐量 | PASS | avg=0.01532; p50=0.0144; p95=0.0187; p99=0.0339; max=0.182; count=5000; qps=6.525e+04 | qps≥1000; p99<1 |
| G代码生成吞吐量 | PASS | avg=0.3003; p50=0.2733; p95=0.4457; p99=0.5322; max=0.5503; count=200; qps=3330 | avg<10; p99<30 |
| 实时传感器处理吞吐量 | PASS | avg=0.003032; p50=0.0027; p95=0.0041; p99=0.0077; max=0.138; count=1e+04; qps=3.298e+05 | p99<1 |
| 审计日志写入吞吐量 | PASS | avg=0.04947; p50=0.019; p95=0.0567; p99=0.9427; max=1.371; count=3000; qps=2.021e+04; integrity=1 | avg<5; p99<10 |
| 心跳调度add_task吞吐量 | PASS | avg=0.2624; p50=0.1769; p95=0.3216; p99=3.469; max=9.452; count=2000; qps=3812 | avg<10 |
| G代码生成浸泡(2000次) | PASS | baseline_rss_mb=2356; final_growth_pct=0; tail_growth_pct=0; max_growth_pct=0 | growth<15 |
| 审计日志浸泡(3000条) | PASS | baseline_rss_mb=2356; tail_growth_pct=0; max_growth_pct=0; integrity=1 | growth<15 |
| 浸泡延迟漂移(JWT) | PASS | first_p95_ms=0.0206; tail_p95_ms=0.0173; drift_ratio=0.84 | drift_ratio<3 |
| 满负荷资源阈值 | FAIL | cpu_avg=10.4; cpu_max=15; sys_mem_avg=55.3; sys_mem_delta=0; baseline_sys_mem=55.3; app_peak_rss_mb=2362; app_baseline_rss_mb=2356; net_avg_mbps=0.31; net_max_mbps=0.49 | cpu<90; net<50; app_rss_mb<1024; sys_mem_delta_pct<5 |
| G代码生成错误率 | PASS | error_rate_pct=0; errors=0; empty=0 | error_rate<1 |
| 心跳调度错误率 | PASS | error_rate_pct=0; errors=0 | error_rate<1 |
| 并发预算检查错误率 | PASS | error_rate_pct=0; errors=0 | error_rate<1 |
| 过载恢复(JWT) | PASS | baseline_p50_ms=0.0152; recovered_p50_ms=0.0153; recovery_ratio=1.01; burst_errors=0 | recovery_ratio<3 |
| 过载恢复(G代码生成) | PASS | baseline_p50_ms=0.281; recovered_p50_ms=0.281; recovery_ratio=1 | recovery_ratio<3 |

**合计：17/18 通过**

## 未通过项明细

### 满负荷资源阈值
- 状态：FAIL
- 指标：{
  "cpu_avg": 10.4,
  "cpu_max": 15.0,
  "sys_mem_avg": 55.3,
  "sys_mem_delta": 0.0,
  "baseline_sys_mem": 55.3,
  "app_peak_rss_mb": 2361.7,
  "app_baseline_rss_mb": 2356.1,
  "net_avg_mbps": 0.31,
  "net_max_mbps": 0.49
}
- 阈值：{
  "cpu": 90.0,
  "net": 50.0,
  "app_rss_mb": 1024.0,
  "sys_mem_delta_pct": 5.0
}
- 备注：6相位混合负载；硬门禁为应用可归因指标，系统绝对占用仅参考

