"""西门子标准极限压力测试包。

本包包含依据西门子工业软件验证规范（Siemens Industry Software
Validation Standard）设计的极限压力测试：

    - 持续负载稳定性（Soak / Endurance）：长时间高负载下的内存泄漏与性能漂移
    - 并发压力（Concurrency Stress）：高并发访问关键路径，无死锁/数据损坏
    - 吞吐量（Throughput）：关键路径 QPS / 延迟分布
    - 尾部延迟（Tail Latency）：并发负载下的 p95 / p99 有界性
    - 资源阈值（Resource）：CPU < 80% / 内存 < 75% / 显存 < 85% / 网络 < 50Mbps
    - 错误率（Error Rate）：极限负载下错误率 < 1%
    - 过载恢复（Recovery）：过载尖峰后延迟恢复至基线

运行方式：
    python -m pytest engineering/python/tests/stress/test_stress_siemens.py -v -s
    python -m pytest engineering/python/tests/stress/ -v -s
"""
