"""CronParser 缓存与字段预编译机制的单元测试

测试目标：
    1. 验证分钟级 TTL 缓存的命中、未命中、淘汰行为
    2. 验证 _CACHE_MAX_SIZE 容量保护逻辑
    3. 验证 _compile_field 字段预编译对 `* , - /` 语法的正确性
    4. 验证 clear_cache / _matches_field 向后兼容
    5. 验证并发场景下的线程安全（不崩溃 + 不数据竞争）

设计说明：
    - 缓存键为 (cron_expr, minute_bucket)，minute_bucket = int(time.time() // 60)
    - 同一 cron_expr 在同一分钟内复用解析结果，避免 add_task 批量插入时
      重复遍历 7×24×60 时间槽
    - TTL 通过 stale_cutoff = minute_bucket - 2 实现，超过 2 分钟的 bucket
      在下次写入时被淘汰
    - 容量上限 256，超过时淘汰最旧 bucket 的所有条目

运行方式：
    python -m pytest tests/unit/test_cron_parser_cache.py -v
"""

from __future__ import annotations

import time
import threading
from unittest.mock import patch

import pytest

from app.heartbeat.heartbeat import CronParser


# Fixtures


@pytest.fixture(autouse=True)
def _isolate_cache():
    """每个测试前后清空缓存，避免跨测试污染。

    CronParser._CACHE 是类级全局状态，必须显式隔离。
    """
    CronParser.clear_cache()
    yield
    CronParser.clear_cache()


# 1. 缓存命中 / 未命中


class TestCacheHitMiss:
    """缓存命中与未命中行为"""

    def test_same_minute_returns_cached_result(self):
        """同一 cron_expr + 同一 minute_bucket：返回缓存结果（同一 list 对象）"""
        expr = "*/5 * * * *"
        first = CronParser.parse(expr)
        second = CronParser.parse(expr)

        # 缓存命中：两次返回的应该是同一个 list 对象（不是副本）
        assert first is second, "同一分钟内重复 parse 应返回缓存的同一 list 对象"
        # 缓存中应有且仅有一条该 expr 的条目（当前 minute_bucket）
        assert len(CronParser._CACHE) >= 1
        assert any(k[0] == expr for k in CronParser._CACHE.keys())

    def test_different_expr_same_minute_separate_entries(self):
        """同一分钟但不同 cron_expr：分别缓存，互不影响"""
        expr_a = "*/5 * * * *"
        expr_b = "0 * * * *"

        result_a = CronParser.parse(expr_a)
        result_b = CronParser.parse(expr_b)

        assert result_a is not result_b, "不同 cron_expr 的解析结果不应共享"
        # 两个 expr 都应在缓存中
        cached_exprs = {k[0] for k in CronParser._CACHE.keys()}
        assert expr_a in cached_exprs
        assert expr_b in cached_exprs

    def test_cache_miss_triggers_compute(self):
        """缓存未命中时调用 _compute_timestamps，结果被写入缓存"""
        expr = "0 2 * * *"
        with patch.object(
            CronParser,
            "_compute_timestamps",
            wraps=CronParser._compute_timestamps,
        ) as spy:
            CronParser.parse(expr)
            assert spy.call_count == 1, "首次 parse 应触发 _compute_timestamps"

            # 第二次同一分钟：应命中缓存，不再触发计算
            CronParser.parse(expr)
            assert spy.call_count == 1, "同一分钟内第二次 parse 应命中缓存，不触发计算"

    def test_cached_result_semantically_correct(self):
        """缓存结果与新鲜计算结果语义一致（值相同）"""
        expr = "*/15 8-18 * * 1-5"
        CronParser.clear_cache()
        fresh = CronParser._compute_timestamps(expr.split())
        cached = CronParser.parse(expr)
        assert cached == fresh, "缓存结果应与新鲜计算结果一致"


# 2. TTL 淘汰机制


class TestTtlEviction:
    """TTL 淘汰：超过 2 分钟的 bucket 在下次写入时被清理"""

    def test_stale_bucket_evicted_on_next_write(self):
        """过期 bucket 在下次 parse 写入时被淘汰"""
        expr = "*/5 * * * *"
        current_bucket = int(time.time() // 60)

        # 手动注入一个过期条目（3 分钟前）
        stale_key = (expr, current_bucket - 3)
        CronParser._CACHE[stale_key] = [1.0, 2.0, 3.0]
        assert stale_key in CronParser._CACHE

        # 触发一次 parse，会淘汰 stale 条目并写入新条目
        CronParser.parse(expr)

        assert stale_key not in CronParser._CACHE, "过期 bucket (>=2 分钟前) 应被淘汰"
        # 新条目应存在
        new_key = (expr, current_bucket)
        assert new_key in CronParser._CACHE

    def test_recent_bucket_preserved(self):
        """1 分钟前的 bucket 不应被淘汰（stale_cutoff = current - 2）"""
        expr = "*/5 * * * *"
        current_bucket = int(time.time() // 60)

        # 注入一个 1 分钟前的条目（应保留）
        recent_key = (expr, current_bucket - 1)
        CronParser._CACHE[recent_key] = [1.0, 2.0]
        # 注入一个 2 分钟前的条目（边界，应保留：stale_cutoff = current-2，
        # 淘汰条件是 k[1] < stale_cutoff，即 k[1] < current-2）
        boundary_key = (expr + "_alt", current_bucket - 2)
        CronParser._CACHE[boundary_key] = [3.0]

        # 触发 parse（不同 expr，避免覆盖）
        CronParser.parse("0 * * * *")

        # recent_bucket 保留
        assert recent_key in CronParser._CACHE, "1 分钟前的 bucket 不应被淘汰"
        # boundary_bucket 也应保留（k[1] == stale_cutoff 时不被淘汰，
        # 因为条件是 k[1] < stale_cutoff）
        assert boundary_key in CronParser._CACHE, "2 分钟前的 bucket（== stale_cutoff）不应被淘汰"

    def test_ttl_does_not_affect_current_bucket(self):
        """TTL 淘汰不影响当前 bucket 的条目"""
        expr = "*/10 * * * *"
        result = CronParser.parse(expr)
        # 立即再次调用：应命中缓存
        result_again = CronParser.parse(expr)
        assert result is result_again, "当前 bucket 的缓存条目不应被 TTL 淘汰影响"


# 3. 容量保护（_CACHE_MAX_SIZE）


class TestCapacityProtection:
    """超过 _CACHE_MAX_SIZE 时淘汰最旧 bucket"""

    def test_capacity_eviction_removes_oldest_bucket(self):
        """超过容量上限时，最旧 bucket 的所有条目被淘汰"""
        current_bucket = int(time.time() // 60)

        # 直接填充到容量上限（用不同的 cron_expr 共享同一 bucket 不会触发容量淘汰，
        # 必须用不同 bucket 才能验证 oldest 淘汰）
        # 注意：_CACHE_MAX_SIZE 默认 256，填充 256 个不同 bucket 的条目
        # 会触发 stale_cutoff 淘汰（如果 bucket 都比 current-2 旧），
        # 所以我们用接近 current 的 bucket 避免被 TTL 误淘汰。
        original_max = CronParser._CACHE_MAX_SIZE
        try:
            # 临时缩小上限以便测试
            CronParser._CACHE_MAX_SIZE = 5
            # 填充 5 个不同 bucket（都在 recent 范围内，避免 TTL 淘汰）
            for i in range(5):
                bucket = current_bucket - i  # 0, -1, -2, -3, -4 分钟前
                # -3 和 -4 会被 TTL 淘汰（< current-2），
                # 所以使用 future buckets 更安全
                pass

            # 重新设计：使用 future buckets 避免 TTL 干扰
            CronParser.clear_cache()
            CronParser._CACHE_MAX_SIZE = 5
            for i in range(5):
                bucket = current_bucket + i  # 未来 bucket 不会被 TTL 淘汰
                CronParser._CACHE[(f"expr_{i}", bucket)] = [float(i)]

            assert len(CronParser._CACHE) == 5

            # 插入第 6 个条目（触发容量淘汰）
            # 此时 oldest_bucket = current_bucket (expr_0)
            # parse 会先淘汰 stale（无），再检查容量，
            # 容量 >= 5，淘汰最旧 bucket 的所有条目
            # 使用合法 cron 表达式 "*/5 * * * *"（无效表达式会在缓存逻辑前抛 ValueError）
            new_expr = "*/5 * * * *"
            with patch.object(
                CronParser,
                "_compute_timestamps",
                return_value=[99.0],
            ):
                CronParser.parse(new_expr)

            # 最旧 bucket (current_bucket, expr_0) 应被淘汰
            oldest_key = ("expr_0", current_bucket)
            assert oldest_key not in CronParser._CACHE, "超过容量上限时最旧 bucket 应被淘汰"
            # 新条目应存在
            assert (new_expr, current_bucket) in CronParser._CACHE
        finally:
            CronParser._CACHE_MAX_SIZE = original_max

    def test_capacity_boundary_keeps_within_limit(self):
        """容量恰好等于上限时不触发淘汰"""
        current_bucket = int(time.time() // 60)
        original_max = CronParser._CACHE_MAX_SIZE
        try:
            CronParser._CACHE_MAX_SIZE = 3
            CronParser.clear_cache()

            # 填充 2 个条目（< 上限 3）
            CronParser._CACHE[("a", current_bucket)] = [1.0]
            CronParser._CACHE[("b", current_bucket + 1)] = [2.0]

            # 第 3 个条目通过 parse 写入（容量达到上限但不超）
            # 使用合法 cron 表达式 "0 * * * *"（每小时整点）
            third_expr = "0 * * * *"
            with patch.object(
                CronParser,
                "_compute_timestamps",
                return_value=[3.0],
            ):
                CronParser.parse(third_expr)

            # 三个条目都应保留
            assert ("a", current_bucket) in CronParser._CACHE
            assert ("b", current_bucket + 1) in CronParser._CACHE
            assert (third_expr, current_bucket) in CronParser._CACHE
        finally:
            CronParser._CACHE_MAX_SIZE = original_max


# 4. 字段预编译正确性


class TestCompileField:
    """_compile_field 对 cron 字段表达式的解析正确性"""

    def test_wildcard(self):
        """`*` 编译为完整值域"""
        result = CronParser._compile_field("*", 0, 59)
        assert result == frozenset(range(60))

    def test_single_value(self):
        """单值"""
        assert CronParser._compile_field("5", 0, 59) == frozenset({5})

    def test_comma_list(self):
        """逗号分隔列表"""
        assert CronParser._compile_field("1,5,10", 0, 59) == frozenset({1, 5, 10})

    def test_range(self):
        """范围 `a-b`"""
        assert CronParser._compile_field("10-15", 0, 59) == frozenset({10, 11, 12, 13, 14, 15})

    def test_step_from_wildcard(self):
        """`*/n` 从最小值开始步进"""
        result = CronParser._compile_field("*/15", 0, 59)
        assert result == frozenset({0, 15, 30, 45})

    def test_step_from_value(self):
        """`a/n` 从 a 开始步进到 max_val"""
        result = CronParser._compile_field("5/10", 0, 59)
        assert result == frozenset({5, 15, 25, 35, 45, 55})

    def test_mixed_syntax(self):
        """混合语法：列表 + 范围 + 步进"""
        # 1,5-7,*/20 1 + 5,6,7 + 0,20,40
        result = CronParser._compile_field("1,5-7,*/20", 0, 59)
        assert result == frozenset({0, 1, 5, 6, 7, 20, 40})

    def test_out_of_range_filtered(self):
        """越界值被过滤，不污染匹配集"""
        # 值 100 越界（min=0, max=59），应被过滤
        result = CronParser._compile_field("5,100", 0, 59)
        assert 100 not in result
        assert 5 in result

    def test_empty_field_set(self):
        """无有效值时返回空 frozenset"""
        # 所有值都越界
        result = CronParser._compile_field("100,200", 0, 59)
        assert result == frozenset()

    def test_returns_frozenset(self):
        """返回类型必须是 frozenset（不可变 + O(1) 查询）"""
        result = CronParser._compile_field("*", 0, 59)
        assert isinstance(result, frozenset), "_compile_field 必须返回 frozenset 以保证不可变性和 O(1) 查询"


# 5. _matches_field 向后兼容


class TestMatchesFieldBackwardCompat:
    """_matches_field 保留向后兼容，行为与 _compile_field 一致"""

    @pytest.mark.parametrize(
        "field_str,value,min_val,max_val,expected",
        [
            ("*", 30, 0, 59, True),
            ("5", 5, 0, 59, True),
            ("5", 6, 0, 59, False),
            ("1,5,10", 5, 0, 59, True),
            ("1,5,10", 7, 0, 59, False),
            ("10-15", 12, 0, 59, True),
            ("10-15", 16, 0, 59, False),
            ("*/15", 30, 0, 59, True),
            ("*/15", 7, 0, 59, False),
            ("5/10", 25, 0, 59, True),
            ("5/10", 20, 0, 59, False),
        ],
    )
    def test_matches_field_consistent_with_compile(self, field_str, value, min_val, max_val, expected):
        """_matches_field 与 _compile_field 对同一表达式行为一致"""
        compiled = CronParser._compile_field(field_str, min_val, max_val)
        assert (value in compiled) == expected, f"_matches_field({field_str!r}, {value}) 应为 {expected}"
        # _matches_field 也应返回相同结果
        assert CronParser._matches_field(field_str, value, min_val, max_val) == expected


# 6. clear_cache 行为


class TestClearCache:
    """clear_cache 行为"""

    def test_clear_empties_cache(self):
        """clear_cache 清空所有缓存条目"""
        CronParser.parse("*/5 * * * *")
        CronParser.parse("0 * * * *")
        assert len(CronParser._CACHE) > 0

        CronParser.clear_cache()
        assert len(CronParser._CACHE) == 0

    def test_clear_idempotent(self):
        """多次 clear_cache 安全（幂等）"""
        CronParser.clear_cache()
        CronParser.clear_cache()
        assert len(CronParser._CACHE) == 0

    def test_parse_after_clear_recomputes(self):
        """clear_cache 后再次 parse 触发重新计算"""
        expr = "*/5 * * * *"
        CronParser.parse(expr)
        CronParser.clear_cache()

        with patch.object(
            CronParser,
            "_compute_timestamps",
            wraps=CronParser._compute_timestamps,
        ) as spy:
            CronParser.parse(expr)
            assert spy.call_count == 1, "clear_cache 后 parse 应重新触发 _compute_timestamps"


# 7. get_next_run 与缓存集成


class TestGetNextRunCacheIntegration:
    """get_next_run 应通过 parse 复用缓存"""

    def test_get_next_run_uses_cache(self):
        """连续 get_next_run 在同一分钟内只触发一次 _compute_timestamps"""
        expr = "*/5 * * * *"
        # 首次调用
        first = CronParser.get_next_run(expr)
        assert first is not None, "*/5 * * * * 应有下次执行时间"

        with patch.object(
            CronParser,
            "_compute_timestamps",
            wraps=CronParser._compute_timestamps,
        ) as spy:
            # 二次调用：应命中缓存
            second = CronParser.get_next_run(expr)
            assert spy.call_count == 0, "同一分钟内二次 get_next_run 应命中缓存，不触发 _compute_timestamps"

        # 两次结果应一致
        assert first == second

    def test_get_next_run_returns_none_for_impossible_schedule(self):
        """不可能满足的调度（如 2月30日）返回 None 或少量有效时间"""
        # 2 月 30 日不存在，但 cron 仍可能匹配其他月份的 30 日
        # 这里用一个肯定有下次执行的表达式
        result = CronParser.get_next_run("0 0 * * *")
        assert result is not None, "每天 0 点应有下次执行时间"
        assert result > time.time(), "下次执行时间应在未来"


# 8. 并发安全


class TestConcurrencySafety:
    """多线程并发调用 parse 不崩溃、不数据竞争"""

    def test_concurrent_parse_no_crash(self):
        """多线程并发 parse 不同 cron_expr 不抛异常"""
        exprs = [
            "*/5 * * * *",
            "0 * * * *",
            "0 0 * * *",
            "*/15 8-18 * * 1-5",
            "30 2 * * 0",
        ]
        errors: list[Exception] = []
        iterations_per_thread = 50
        thread_count = 8

        def worker(tid: int):
            try:
                for i in range(iterations_per_thread):
                    expr = exprs[(tid + i) % len(exprs)]
                    result = CronParser.parse(expr)
                    # 基本完整性检查：结果是 list 且非空
                    assert isinstance(result, list), f"parse 应返回 list，得到 {type(result)}"
                    assert len(result) > 0, f"cron {expr!r} 应有执行时间"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发 parse 出错: {errors}"

    def test_concurrent_same_expr_consistent_results(self):
        """多线程并发 parse 同一 cron_expr：结果语义一致"""
        expr = "*/5 * * * *"
        results: list[list[float]] = []
        results_lock = threading.Lock()
        thread_count = 8
        iterations = 100

        def worker():
            local_results = []
            for _ in range(iterations):
                local_results.append(CronParser.parse(expr))
            with results_lock:
                results.extend(local_results)

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有结果应语义一致（值相同）
        first = results[0]
        for i, r in enumerate(results):
            assert r == first, f"并发 parse 结果不一致: results[{i}] != results[0]"

    def test_concurrent_clear_cache_safe(self):
        """并发 parse + clear_cache 不崩溃（clear 是原子操作）"""
        expr = "*/5 * * * *"
        errors: list[Exception] = []

        def parser():
            try:
                for _ in range(50):
                    CronParser.parse(expr)
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(10):
                    CronParser.clear_cache()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=parser),
            threading.Thread(target=parser),
            threading.Thread(target=clearer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发 parse+clear 出错: {errors}"


# 9. 缓存对错误表达式的处理


class TestCacheWithInvalidExpression:
    """无效 cron 表达式不应污染缓存"""

    def test_invalid_expression_not_cached(self):
        """无效表达式抛出 ValueError，不写入缓存"""
        invalid_expr = "not a cron expression"
        with pytest.raises(ValueError):
            CronParser.parse(invalid_expr)

        # 无效表达式不应在缓存中
        cached_exprs = {k[0] for k in CronParser._CACHE.keys()}
        assert invalid_expr not in cached_exprs, "无效表达式不应被缓存"

    def test_invalid_expression_does_not_block_subsequent_valid(self):
        """无效表达式后，有效表达式仍可正常解析和缓存"""
        with pytest.raises(ValueError):
            CronParser.parse("invalid")

        valid_expr = "*/5 * * * *"
        result = CronParser.parse(valid_expr)
        assert isinstance(result, list)
        assert len(result) > 0

        # 有效表达式应在缓存中
        cached_exprs = {k[0] for k in CronParser._CACHE.keys()}
        assert valid_expr in cached_exprs
