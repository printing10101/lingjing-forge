"""端到端关键路径性能测试

测试目标：
    1. 验证完整请求链路（中间件 → 路由 → 业务逻辑 → 响应）的端到端延迟
    2. 验证并发请求下的性能稳定性（无尾部延迟突增）
    3. 验证错误响应路径的性能（异常处理器不引入显著开销）
    4. 验证长时间运行下性能无漂移（无内存泄漏导致的 GC 压力）

设计背景：
    单元性能测试验证了各组件独立性能，但生产环境中用户感知的是
    完整请求链路的端到端延迟。本测试模拟真实请求路径，确保
    中间件链路、路由匹配、业务逻辑、响应序列化的组合性能达标。

运行方式：
    python -m pytest tests/performance/test_end_to_end_performance.py -v
"""

from __future__ import annotations

import asyncio
import time
from typing import List

import pytest
from fastapi import FastAPI, HTTPException

from tests.utils.perf_thresholds import perf_threshold
from fastapi.testclient import TestClient

from app.auth.security_headers_asgi import SecurityHeadersMiddleware
from app.core.request_id import RequestIdMiddleware

pytestmark = pytest.mark.skip_ci


# WinSock 损坏环境检测
# 背景：
# - 当前 Windows 环境 WinSock 损坏，``import asyncio`` 在 conftest.py 中通过
# stub 注入绕过，但 stub 无法支持真实的 TestClient / asyncio.run。
# - 端到端测试需要真实事件循环（创建 socket pair、调度协程），
# 在 stub 环境下必然失败，应跳过而非报 FAIL。
# - 检测方式：尝试 ``asyncio.new_event_loop()`` 并立即关闭；
# - 真实 asyncio：返回事件循环对象，可 close()
# - stub：返回 None，close() 会抛 AttributeError
# - WinSock 损坏：抛 OSError [WinError 10038]


def _check_real_asyncio_available() -> bool:
    """检测真实 asyncio 是否可用（非 stub、非 WinSock 损坏）。"""
    try:
        loop = asyncio.new_event_loop()
        if loop is None:
            return False  # conftest stub 返回 None
        loop.close()
        return True
    except (OSError, AttributeError, TypeError, RuntimeError):
        return False


# 模块级 skipif：WinSock 损坏或 asyncio 为 stub 时跳过整个模块
pytestmark = pytest.mark.skipif(
    not _check_real_asyncio_available(),
    reason="WinSock 损坏或 asyncio 为 stub，端到端测试需要真实事件循环",
)


# Fixtures


@pytest.fixture
def perf_app() -> FastAPI:
    """构造带完整中间件链路的 FastAPI 应用

    包含：RequestIdMiddleware + SecurityHeadersMiddleware + 业务路由
    排除：UnifiedAuth（需 DB 依赖）、CORS（无跨域）、Metrics（需 metrics 实例）
    这些组件已有独立单元性能测试，端到端测试聚焦用户可感知的链路。
    """
    try:
        from enum import StrEnum  # noqa: F401

    except ImportError:
        pytest.skip("StrEnum requires Python 3.11+; skip on 3.10")

    app = FastAPI(title="PerfTestApp")

    # 注册顺序与期望执行顺序相反（Starlette 语义）
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/echo/{item_id}")
    async def echo(item_id: int):
        return {"item_id": item_id, "echoed_at": time.time()}

    @app.get("/compute")
    async def compute():
        # 模拟轻量业务逻辑（10ms 以内的 CPU 工作）
        total = sum(i * i for i in range(1000))
        return {"result": total}

    @app.get("/error")
    async def error_endpoint():
        raise HTTPException(status_code=400, detail="simulated error")

    @app.post("/data")
    async def receive_data(payload: dict):
        # 模拟接收数据并回显
        return {"received": True, "size": len(str(payload))}

    return app


@pytest.fixture
def perf_client(perf_app) -> TestClient:
    """TestClient fixture，预热后供测试使用"""
    client = TestClient(perf_app)
    # 预热：触发中间件初始化与路由编译
    for _ in range(20):
        client.get("/health")
    return client


# 1. 端到端延迟基线


class TestEndToEndLatencyBaseline:
    """端到端延迟基线测试

    阈值设定依据（Windows TestClient socket loopback）：
        - /health：纯中间件开销 + 路由匹配 + JSON 序列化，P95 < 30ms
        - /echo/{id}：含路径参数解析，P95 < 35ms
        - /compute：含 10ms 以内 CPU 工作，P95 < 50ms
        - /error：异常处理路径，P95 < 35ms

    Linux/CI 上通常 <5ms，Windows TestClient 走 socket loopback 慢 5-10x。
    """

    def _measure(self, client: TestClient, method: str, path: str, iterations: int = 100) -> List[float]:
        """采样 iterations 次请求的延迟（ms）"""
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            if method == "GET":
                resp = client.get(path)
            elif method == "POST":
                resp = client.post(path, json={"data": "x" * 100})
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            # 健康检查不验证状态码，错误端点验证 400
            if path == "/error":
                assert resp.status_code == 400
            elif path == "/data":
                assert resp.status_code == 200
            else:
                assert resp.status_code == 200
        return times

    def _stats(self, times: List[float]) -> dict:
        times_sorted = sorted(times)
        n = len(times_sorted)
        return {
            "avg": sum(times) / n,
            "p50": times_sorted[int(n * 0.50)],
            "p95": times_sorted[min(int(n * 0.95), n - 1)],
            "p99": times_sorted[min(int(n * 0.99), n - 1)],
            "min": min(times),
            "max": max(times),
        }

    def test_health_endpoint_e2e(self, perf_client):
        """健康检查端到端延迟（最轻量路径）"""
        times = self._measure(perf_client, "GET", "/health", iterations=100)
        stats = self._stats(times)

        assert stats["p95"] < 30.0, f"/health P95 过高: {stats['p95']:.3f}ms"

        print("\n/health 端到端延迟 (100次):")
        for k, v in stats.items():
            print(f"  {k}: {v:.3f}ms")

    def test_echo_with_path_param_e2e(self, perf_client):
        """带路径参数的端到端延迟"""
        times = self._measure(perf_client, "GET", "/echo/42", iterations=100)
        stats = self._stats(times)

        assert stats["p95"] < 35.0, f"/echo P95 过高: {stats['p95']:.3f}ms"

        print("\n/echo/42 端到端延迟 (100次):")
        for k, v in stats.items():
            print(f"  {k}: {v:.3f}ms")

    def test_compute_endpoint_e2e(self, perf_client):
        """含 CPU 工作的端到端延迟"""
        times = self._measure(perf_client, "GET", "/compute", iterations=100)
        stats = self._stats(times)

        assert stats["p95"] < 50.0, f"/compute P95 过高: {stats['p95']:.3f}ms"

        print("\n/compute 端到端延迟 (100次):")
        for k, v in stats.items():
            print(f"  {k}: {v:.3f}ms")

    def test_error_path_e2e(self, perf_client):
        """错误响应路径端到端延迟

        验证异常处理器不引入显著开销：
        /error 路径与 /health 路径的 P95 差距应在合理范围（< 2x）。
        """
        error_times = self._measure(perf_client, "GET", "/error", iterations=100)
        health_times = self._measure(perf_client, "GET", "/health", iterations=100)

        error_stats = self._stats(error_times)
        health_stats = self._stats(health_times)

        # 错误路径 P95 应在 35ms 内
        assert error_stats["p95"] < 35.0, f"/error P95 过高: {error_stats['p95']:.3f}ms"

        # 错误路径不应比健康路径慢 2 倍以上
        ratio = error_stats["p95"] / max(health_stats["p95"], 0.001)
        assert ratio < 2.0, (
            f"错误路径开销过大: error_p95={error_stats['p95']:.3f}ms, "
            f"health_p95={health_stats['p95']:.3f}ms, ratio={ratio:.2f}x"
        )

        print("\n/error vs /health 端到端延迟对比:")
        print(f"  /health P95: {health_stats['p95']:.3f}ms")
        print(f"  /error  P95: {error_stats['p95']:.3f}ms")
        print(f"  ratio:       {ratio:.2f}x")

    def test_post_data_e2e(self, perf_client):
        """POST 请求端到端延迟"""
        times = self._measure(perf_client, "POST", "/data", iterations=100)
        stats = self._stats(times)

        assert stats["p95"] < 35.0, f"/data POST P95 过高: {stats['p95']:.3f}ms"

        print("\n/data POST 端到端延迟 (100次):")
        for k, v in stats.items():
            print(f"  {k}: {v:.3f}ms")


# 2. 尾部延迟分析


class TestTailLatency:
    """尾部延迟分析

    生产环境关注 P99/P99.9 而非平均值，因为用户感知的是最差体验。
    """

    def test_p99_under_threshold(self, perf_client):
        """P99 延迟应 < 50ms（含偶发 GC 抖动）

        1000 次采样中，P99 = 第 990 次排序后的值
        """
        times = []
        for _ in range(1000):
            start = time.perf_counter()
            resp = perf_client.get("/health")
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            assert resp.status_code == 200

        times.sort()
        p50 = times[500]
        p95 = times[950]
        p99 = times[990]
        p999 = times[min(999, len(times) - 1)]

        # P99 应在 50ms 内（含偶发抖动）
        assert p99 < 50.0, f"P99 过高: {p99:.3f}ms"

        # P99/P50 比值应 < 5（尾部延迟可控）
        tail_ratio = p99 / max(p50, 0.001)
        assert tail_ratio < 5.0, f"尾部延迟突增: P99={p99:.3f}ms, P50={p50:.3f}ms, ratio={tail_ratio:.2f}x"

        print("\n/health 尾部延迟分析 (1000次):")
        print(f"  P50:  {p50:.3f}ms")
        print(f"  P95:  {p95:.3f}ms")
        print(f"  P99:  {p99:.3f}ms")
        print(f"  P99.9: {p999:.3f}ms")
        print(f"  P99/P50: {tail_ratio:.2f}x")


# 3. 长时间运行性能稳定性


class TestLongRunStability:
    """长时间运行性能稳定性

    验证连续请求下性能无漂移（无内存泄漏导致的 GC 压力上升）。
    """

    def test_no_perf_drift_over_500_requests(self, perf_client):
        """500 次连续请求，前后两段延迟差距应 < 30%

        将 500 次请求分为 5 段，每段 100 次，
        计算各段 P95，验证最后一段与第一段的差距。
        """
        segment_size = 100
        segment_count = 5
        segment_p95s = []

        for seg in range(segment_count):
            times = []
            for _ in range(segment_size):
                start = time.perf_counter()
                resp = perf_client.get("/health")
                elapsed_ms = (time.perf_counter() - start) * 1000
                times.append(elapsed_ms)
                assert resp.status_code == 200
            times.sort()
            segment_p95s.append(times[int(segment_size * 0.95)])

        first_p95 = segment_p95s[0]
        last_p95 = segment_p95s[-1]
        # 仅判定"性能退化"漂移：last > first 时计算退化百分比；
        # last < first（性能变好，通常是 JIT/缓存预热效果）不算漂移。
        # 冷启动场景下首段 P95 偏高是正常现象，原实现用 abs() 会误报。
        if last_p95 > first_p95:
            drift_pct = (last_p95 - first_p95) / max(first_p95, 0.001) * 100
        else:
            drift_pct = 0.0  # 性能提升不计漂移

        # 性能退化应 < 30%（允许 GC 抖动）
        assert drift_pct < 30.0, (
            f"性能漂移过大: first_p95={first_p95:.3f}ms, last_p95={last_p95:.3f}ms, drift={drift_pct:.1f}%"
        )

        print(f"\n长时间运行性能稳定性 ({segment_count}×{segment_size}次):")
        for i, p95 in enumerate(segment_p95s):
            print(f"  段{i + 1} P95: {p95:.3f}ms")
        print(f"  漂移: {drift_pct:.1f}%")


# 4. 异步并发性能（asyncio）


class TestAsyncConcurrencyPerformance:
    """异步并发请求性能

    使用 asyncio + httpx 验证并发请求下的吞吐量。
    注意：TestClient 是同步阻塞的，无法测真实并发，
    本测试改用 ASGI 直接调用验证事件循环开销。
    """

    def test_async_event_loop_throughput(self, perf_app):
        """直接 ASGI 调用的事件循环吞吐量

        绕过 TestClient 的 socket 层，测试纯 ASGI 中间件 + 路由的并发性能。
        """

        async def call_app(scope):
            """单次 ASGI 调用"""
            received = []
            send_queue: list = []

            async def receive():
                return {"type": "http.request", "body": b"", "more": False}

            async def send(message):
                received.append(message)

            await perf_app(scope, receive, send)
            return received

        async def run_batch(n: int):
            """批量并发调用"""
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/health",
                "raw_path": b"/health",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
                "root_path": "",
                "http_version": "1.1",
                "app": perf_app,
            }

            start = time.perf_counter()
            tasks = [call_app(scope) for _ in range(n)]
            results = await asyncio.gather(*tasks)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return elapsed_ms, len(results)

        # 运行 200 个并发请求
        elapsed_ms, count = asyncio.run(run_batch(200))

        # 200 个并发 ASGI 调用应在 500ms 内完成
        # 阈值依据：纯事件循环调度 + 中间件链路 + 路由匹配
        assert elapsed_ms < perf_threshold(500.0), f"并发 ASGI 调用过慢: {count}次 in {elapsed_ms:.3f}ms"

        qps = count / (elapsed_ms / 1000)
        print(f"\n异步并发 ASGI 吞吐量 ({count}次):")
        print(f"  总耗时: {elapsed_ms:.3f}ms")
        print(f"  QPS:    {qps:.0f}")
