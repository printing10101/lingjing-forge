"""CronParser 缓存命中率与性能曲线专项测试

测试目标：
    1. 验证批量插入场景下的缓存命中率（应接近 100%）
    2. 验证缓存命中率随批量规模变化的曲线
    3. 验证冷启动 vs 热启动延迟差距
    4. 验证不同 cron_expr 多样性下的命中率衰减
    5. 验证缓存淘汰不会导致命中率突降

设计背景：
    CronParser 已加入分钟级 TTL 缓存（key: cron_expr + minute_bucket）
    与字段预编译优化。批量 add_task 场景下，同一 cron_expr 在同一分钟内
    应能命中缓存，避免重复遍历 7×24×60 时间槽。

运行方式：
    python -m pytest tests/performance/test_cron_parser_cache_hit_rate.py -v
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.heartbeat.heartbeat import CronParser

pytestmark = pytest.mark.skip_ci


# Fixtures


@pytest.fixture(autouse=True)
def _isolate_cache():
    """每个测试前后清空缓存，避免跨测试污染。"""
    CronParser.clear_cache()
    yield
    CronParser.clear_cache()


def _measure_parse_call(cron_expr: str) -> float:
    """单次 parse 调用延迟（ms）"""
    start = time.perf_counter()
    CronParser.parse(cron_expr)
    return (time.perf_counter() - start) * 1000


# 1. 冷启动 vs 热启动


class TestColdVsWarmStartup:
    """冷启动 vs 热启动延迟对比"""

    def test_cold_start_slower_than_warm(self):
        """首次解析（冷启动）应明显慢于缓存命中（热启动）

        冷启动：字段预编译 + 7天时间槽遍历
        热启动：缓存命中 O(1) dict lookup
        """
        expr = "*/5 8-18 * * 1-5"
        cold_ms = _measure_parse_call(expr)
        warm_ms = _measure_parse_call(expr)

        # 冷启动至少应比热启动慢 3 倍（典型情况慢 10-50 倍）
        assert cold_ms > warm_ms * 3, f"冷启动未明显慢于热启动: cold={cold_ms:.4f}ms, warm={warm_ms:.6f}ms"

        print("\n冷启动 vs 热启动:")
        print(f"  冷启动: {cold_ms:.4f}ms")
        print(f"  热启动: {warm_ms:.6f}ms")
        print(f"  差距:   {cold_ms / max(warm_ms, 1e-9):.1f}x")

    def test_warm_latency_under_threshold(self):
        """热启动延迟应 < 0.1ms（纯字典查找）"""
        expr = "*/5 * * * *"
        CronParser.parse(expr)  # 预热

        # 采样 1000 次取 P95
        samples = [_measure_parse_call(expr) for _ in range(1000)]
        samples.sort()
        p95 = samples[int(len(samples) * 0.95)]

        assert p95 < 0.1, f"热启动 P95 延迟过高: {p95:.6f}ms"

        print("\n热启动延迟 (1000 次采样):")
        print(f"  P50: {samples[500]:.6f}ms")
        print(f"  P95: {p95:.6f}ms")


# 2. 批量插入场景缓存命中率


class TestBatchInsertHitRate:
    """批量 add_task 场景下的缓存命中率

    模拟真实生产场景：心跳调度器批量插入任务，多任务共享同一 cron_expr。
    """

    def test_uniform_cron_high_hit_rate(self):
        """200 个任务共享同一 cron_expr：命中率应 ≥ 99.5%

        场景：心跳调度器批量注册相同调度策略的任务
        """
        expr = "*/5 * * * *"

        # 通过 spy _compute_timestamps 统计冷启动次数
        with patch.object(
            CronParser,
            "_compute_timestamps",
            wraps=CronParser._compute_timestamps,
        ) as spy:
            iterations = 200
            for _ in range(iterations):
                CronParser.parse(expr)

            compute_count = spy.call_count
            hit_count = iterations - compute_count
            hit_rate = hit_count / iterations

        assert hit_rate >= 0.995, f"批量插入缓存命中率过低: {hit_rate:.4f} (compute={compute_count}, hit={hit_count})"

        print(f"\n统一 cron_expr 批量插入 ({iterations}次):")
        print(f"  命中率: {hit_rate:.4%}")
        print(f"  compute 调用: {compute_count}")
        print(f"  cache 命中: {hit_count}")

    def test_diverse_cron_hit_rate_decay(self):
        """10 种不同 cron_expr × 20 次 = 200 次：命中率应 ≥ 90%

        场景：多策略调度，每种策略有独立缓存条目
        """
        exprs = [
            "*/5 * * * *",
            "0 * * * *",
            "0 2 * * *",
            "*/15 8-18 * * 1-5",
            "0 0 * * 0",
            "30 9 * * 1-5",
            "*/10 * * * *",
            "0 12 * * *",
            "0 18 * * 5",
            "*/30 * * * *",
        ]

        with patch.object(
            CronParser,
            "_compute_timestamps",
            wraps=CronParser._compute_timestamps,
        ) as spy:
            iterations_per_expr = 20
            total = len(exprs) * iterations_per_expr
            for expr in exprs:
                for _ in range(iterations_per_expr):
                    CronParser.parse(expr)

            compute_count = spy.call_count
            hit_count = total - compute_count
            hit_rate = hit_count / total

        # 10 种 expr 各 1 次冷启动 = 10 次 compute
        # 其余 190 次应命中缓存
        assert hit_rate >= 0.90, f"多样化 cron_expr 命中率过低: {hit_rate:.4%}"

        print(f"\n多样化 cron_expr 批量插入 ({total}次, {len(exprs)}种 expr):")
        print(f"  命中率: {hit_rate:.4%}")
        print(f"  compute 调用: {compute_count}")
        print(f"  cache 命中: {hit_count}")

    def test_hit_rate_curve_with_scale(self):
        """命中率随批量规模变化的曲线

        验证：批量越大，命中率越高（首次冷启动开销被摊销）
        """
        expr = "*/5 * * * *"
        scales = [10, 50, 100, 500, 1000]
        hit_rates = []

        for scale in scales:
            CronParser.clear_cache()
            with patch.object(
                CronParser,
                "_compute_timestamps",
                wraps=CronParser._compute_timestamps,
            ) as spy:
                for _ in range(scale):
                    CronParser.parse(expr)
                compute_count = spy.call_count
                hit_rate = (scale - compute_count) / scale
                hit_rates.append(hit_rate)

        # 所有规模下命中率应 ≥ 90%
        for scale, rate in zip(scales, hit_rates):
            assert rate >= 0.90, f"规模 {scale} 时命中率过低: {rate:.4%}"

        # 规模越大，命中率应越高（或保持 100%）
        # 允许小规模因首次冷启动占比较大而略低
        print("\n命中率随规模变化曲线:")
        for scale, rate in zip(scales, hit_rates):
            print(f"  scale={scale:4d}: {rate:.4%}")


# 3. 吞吐量提升对比


class TestThroughputImprovement:
    """验证缓存优化对吞吐量的提升"""

    def test_cached_throughput_vs_cold(self):
        """缓存命中路径吞吐量应远高于冷启动路径

        冷启动：每次调用都走 _compute_timestamps（200ms+）
        热启动：缓存命中 O(1)（<0.1ms）
        """
        expr = "*/5 8-18 * * 1-5"

        # 冷启动路径（每次清缓存）
        cold_iterations = 100
        start = time.perf_counter()
        for _ in range(cold_iterations):
            CronParser.clear_cache()
            CronParser.parse(expr)
        cold_elapsed = time.perf_counter() - start
        cold_per_call_ms = (cold_elapsed / cold_iterations) * 1000

        # 热启动路径（缓存命中）
        CronParser.parse(expr)  # 预热
        warm_iterations = 10000
        start = time.perf_counter()
        for _ in range(warm_iterations):
            CronParser.parse(expr)
        warm_elapsed = time.perf_counter() - start
        warm_per_call_ms = (warm_elapsed / warm_iterations) * 1000

        speedup = cold_per_call_ms / max(warm_per_call_ms, 1e-9)

        # 缓存命中应至少比冷启动快 50 倍
        assert speedup >= 50, (
            f"缓存优化加速比过低: {speedup:.1f}x (cold={cold_per_call_ms:.4f}ms, warm={warm_per_call_ms:.6f}ms)"
        )

        print("\n吞吐量提升对比:")
        print(f"  冷启动: {cold_per_call_ms:.4f}ms/次 (QPS={1000 / cold_per_call_ms:.0f})")
        print(f"  热启动: {warm_per_call_ms:.6f}ms/次 (QPS={1000 / warm_per_call_ms:.0f})")
        print(f"  加速比: {speedup:.1f}x")


# 4. 缓存淘汰对性能的影响


class TestEvictionPerformanceImpact:
    """验证缓存淘汰不会导致性能突降"""

    def test_capacity_eviction_no_perf_cliff(self):
        """容量淘汰触发时，单次延迟不应突增

        场景：缓存填满 256 个不同 expr 后，第 257 个 expr 触发淘汰
        淘汰逻辑本身应在 1ms 内完成
        """
        original_max = CronParser._CACHE_MAX_SIZE
        try:
            CronParser._CACHE_MAX_SIZE = 10  # 缩小上限便于测试
            CronParser.clear_cache()

            # 填充 10 个不同 expr（不同 minute_bucket 避免被 TTL 误淘汰）
            current_bucket = int(time.time() // 60)
            for i in range(10):
                # 使用未来 bucket 避免 TTL 淘汰
                CronParser._CACHE[(f"*/{i + 1} * * * *", current_bucket + i)] = [float(i)]

            # 第 11 个 expr 触发容量淘汰
            # 使用合法 cron 表达式
            new_expr = "*/30 * * * *"
            with patch.object(
                CronParser,
                "_compute_timestamps",
                return_value=[99.0],
            ):
                start = time.perf_counter()
                CronParser.parse(new_expr)
                elapsed_ms = (time.perf_counter() - start) * 1000

            # 淘汰 + 写入应在 1ms 内完成
            assert elapsed_ms < 1.0, f"容量淘汰触发时延迟突增: {elapsed_ms:.4f}ms"

            print("\n容量淘汰性能影响:")
            print(f"  淘汰 + 写入延迟: {elapsed_ms:.4f}ms")
            print(f"  缓存大小: {len(CronParser._CACHE)}")
        finally:
            CronParser._CACHE_MAX_SIZE = original_max

    def test_ttl_eviction_amortized_cost(self):
        """TTL 淘汰的摊销开销应可忽略

        场景：连续多次 parse，TTL 淘汰只在写路径触发，
        读路径（缓存命中）无淘汰开销
        """
        expr = "*/5 * * * *"

        # 注入若干过期条目
        current_bucket = int(time.time() // 60)
        for i in range(20):
            stale_key = (f"expr_{i}", current_bucket - 3)
            CronParser._CACHE[stale_key] = [float(i)]

        # 读路径（命中）应不受过期条目影响
        CronParser.parse(expr)  # 预热
        start = time.perf_counter()
        for _ in range(1000):
            CronParser.parse(expr)
        elapsed_ms = (time.perf_counter() - start) * 1000
        per_call_ms = elapsed_ms / 1000

        # 命中路径应 < 0.1ms，即使缓存中有过期条目
        assert per_call_ms < 0.1, f"缓存命中路径受过期条目影响: {per_call_ms:.6f}ms"

        print("\nTTL 淘汰摊销开销:")
        print(f"  命中路径平均延迟: {per_call_ms:.6f}ms")
        print("  过期条目数: 20")


# 5. 字段预编译性能验证


class TestFieldPrecompilePerformance:
    """验证 _compile_field 字段预编译的性能优势"""

    def test_compile_field_faster_than_matches_field(self):
        """预编译 + 集合查询应快于逐次 _matches_field 调用

        场景：7×24×60 = 10080 次匹配检查
        - 旧路径：每次 _matches_field 解析字段字符串
        - 新路径：1 次预编译 + 10080 次集合查找
        """
        field_str = "*/5,30-45"
        value_range = list(range(60))

        # 旧路径：逐次 _matches_field
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            for v in value_range:
                CronParser._matches_field(field_str, v, 0, 59)
        old_elapsed = time.perf_counter() - start

        # 新路径：预编译 + 集合查询
        start = time.perf_counter()
        for _ in range(iterations):
            compiled = CronParser._compile_field(field_str, 0, 59)
            for v in value_range:
                _ = v in compiled
        new_elapsed = time.perf_counter() - start

        speedup = old_elapsed / max(new_elapsed, 1e-9)

        # 预编译路径应至少快 2 倍
        assert speedup >= 2.0, (
            f"预编译未带来性能提升: {speedup:.2f}x (old={old_elapsed * 1000:.4f}ms, new={new_elapsed * 1000:.4f}ms)"
        )

        print("\n字段预编译 vs 逐次匹配:")
        print(f"  旧路径 (1000×60 _matches_field): {old_elapsed * 1000:.4f}ms")
        print(f"  新路径 (1000× compile+in):       {new_elapsed * 1000:.4f}ms")
        print(f"  加速比: {speedup:.2f}x")
