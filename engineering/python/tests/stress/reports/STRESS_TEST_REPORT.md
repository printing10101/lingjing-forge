# 西门子标准极限压力测试报告

- 生成时间：2026-10-21 22:13:37
- Python：3.14.4
- psutil：7.2.2
- 平台：Windows-11-10.0.26300-SP0

## 结果汇总

| 测试 | 状态 | 关键指标 | 阈值 |
|------|------|----------|------|
| 并发G代码生成 | PASS | avg=1.129; p50=0.5744; p95=1.229; p99=15.93; max=46.04; count=1000; generated=1000; avg_lines=64.25 | errors<1 |
| 并发工艺规划 | PASS | avg=0.2722; p50=0.1985; p95=0.3613; p99=1.108; max=24.04; count=600; total=600 | errors<1 |
| G代码生成吞吐量 | PASS | avg=0.6809; p50=0.6055; p95=1.229; p99=2.073; max=2.207; count=200; qps=1469 | avg<10; p99<30 |
| G代码生成浸泡(2000次) | PASS | baseline_rss_mb=1391; final_growth_pct=-0.07; tail_growth_pct=-0.07; max_growth_pct=-0.02 | growth<15 |
| G代码生成错误率 | PASS | error_rate_pct=0; errors=0; empty=0 | error_rate<1 |
| 过载恢复(G代码生成) | PASS | baseline_p50_ms=0.618; recovered_p50_ms=0.575; recovery_ratio=0.93 | recovery_ratio<3 |

**合计：6/6 通过**
