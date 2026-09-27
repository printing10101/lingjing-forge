"""API接口性能基准测试模块。

测试FastAPI端点的响应时间、吞吐量、并发能力。
覆盖健康检查、认证、数据查询、文件上传等关键接口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


class APIPerfBenchmark:
    """API接口性能基准测试。"""

    # 测试专用凭据：从环境变量读取，避免硬编码密码泄露。
    # 仅用于性能基准测试场景，生产环境必须通过正式认证流程获取 token。
    _BENCH_USERNAME = os.environ.get("LJ_BENCH_USERNAME", "BENCH_USER_PLACEHOLDER")
    _BENCH_PASSWORD = os.environ.get("LJ_BENCH_PASSWORD", "BENCH_PASSWORD_PLACEHOLDER")

    def __init__(self, base_url: str | None = None) -> None:
        # 默认对齐后端实际端口 8765（start_server.py 约定），可用
        # ``LNN_API_BENCH_BASE_URL`` 覆盖——此前硬编码 8000 导致 API 套件
        # 长期静默拿不到数据（服务从来不在 8000 监听）。
        self.base_url = base_url or os.environ.get("LNN_API_BENCH_BASE_URL", "http://127.0.0.1:8765")
        self._results: dict[str, Any] = {}
        self._session: aiohttp.ClientSession | None = None
        self._auth_token: str | None = None

    async def setup(self) -> None:
        """初始化测试环境。"""
        self._session = aiohttp.ClientSession()
        await self._authenticate()

    async def teardown(self) -> None:
        """清理测试环境。"""
        if self._session:
            await self._session.close()

    async def _authenticate(self) -> None:
        """获取认证token。"""
        try:
            async with self._session.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"username": self._BENCH_USERNAME, "password": self._BENCH_PASSWORD},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._auth_token = data.get("access_token")
                    logger.info("认证成功")
                else:
                    logger.warning("认证失败: %s", resp.status)
        except Exception as e:
            logger.warning("认证异常: %s", e)

    def _get_headers(self) -> dict[str, str]:
        """获取请求头。"""
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def test_health_endpoint(self) -> dict[str, float]:
        """测试健康检查接口。"""
        times: list[float] = []
        url = f"{self.base_url}/health"

        for _ in range(20):
            t0 = time.perf_counter()
            try:
                async with self._session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    await resp.text()
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
            except Exception as e:
                logger.debug("健康检查请求失败: %s", e)

        if not times:
            return {"health_check_ms": -1}

        times.sort()
        n = len(times)
        result = {
            "health_check_ms_p50": round(times[int(n * 0.50)], 2),
            "health_check_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
            "health_check_ms_mean": round(sum(times) / n, 2),
        }
        self._results.update(result)
        return result

    async def test_auth_endpoint(self) -> dict[str, float]:
        """测试认证接口。"""
        times: list[float] = []
        url = f"{self.base_url}/api/v1/auth/login"

        for _ in range(10):
            t0 = time.perf_counter()
            try:
                async with self._session.post(
                    url,
                    json={"username": self._BENCH_USERNAME, "password": self._BENCH_PASSWORD},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    await resp.json()
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
            except Exception as e:
                logger.debug("认证请求失败: %s", e)

        if not times:
            return {"auth_login_ms": -1}

        times.sort()
        n = len(times)
        result = {
            "auth_login_ms_p50": round(times[int(n * 0.50)], 2),
            "auth_login_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
            "auth_login_ms_mean": round(sum(times) / n, 2),
        }
        self._results.update(result)
        return result

    async def test_data_query_endpoint(self) -> dict[str, float]:
        """测试数据查询接口。"""
        times: list[float] = []
        url = f"{self.base_url}/api/v1/lnn/predict"
        headers = self._get_headers()

        test_data = {
            "features": [0.1] * 64,
            "model_id": "default",
        }

        for _ in range(10):
            t0 = time.perf_counter()
            try:
                async with self._session.post(
                    url,
                    json=test_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    await resp.json()
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
            except Exception as e:
                logger.debug("数据查询请求失败: %s", e)

        if not times:
            return {"data_query_ms": -1}

        times.sort()
        n = len(times)
        result = {
            "data_query_ms_p50": round(times[int(n * 0.50)], 2),
            "data_query_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
            "data_query_ms_mean": round(sum(times) / n, 2),
        }
        self._results.update(result)
        return result

    async def test_concurrent_requests(self, n_requests: int = 50) -> dict[str, float]:
        """测试并发请求能力。"""
        url = f"{self.base_url}/health"

        async def single_request() -> float:
            t0 = time.perf_counter()
            try:
                async with self._session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    await resp.text()
                    return (time.perf_counter() - t0) * 1000
            except Exception as e:
                logging.getLogger(__name__).warning("api_bench request failed for %s: %s", url, e)
                return -1

        t0 = time.perf_counter()
        tasks = [single_request() for _ in range(n_requests)]
        results = await asyncio.gather(*tasks)
        total_time = (time.perf_counter() - t0) * 1000

        valid_times = [t for t in results if t > 0]
        if not valid_times:
            return {"concurrent_rps": 0}

        result = {
            "concurrent_requests": n_requests,
            "concurrent_total_ms": round(total_time, 2),
            "concurrent_avg_ms": round(sum(valid_times) / len(valid_times), 2),
            "concurrent_rps": round(n_requests / (total_time / 1000), 2),
            "concurrent_success_rate": round(len(valid_times) / n_requests * 100, 1),
        }
        self._results.update(result)
        return result

    def run_all(self) -> dict[str, Any]:
        """运行所有API性能测试。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self.setup())

            logger.info("  测试健康检查接口...")
            loop.run_until_complete(self.test_health_endpoint())

            logger.info("  测试认证接口...")
            loop.run_until_complete(self.test_auth_endpoint())

            logger.info("  测试数据查询接口...")
            loop.run_until_complete(self.test_data_query_endpoint())

            logger.info("  测试并发请求...")
            loop.run_until_complete(self.test_concurrent_requests(50))

        finally:
            loop.run_until_complete(self.teardown())
            loop.close()

        return self.get_all_results()

    def get_all_results(self) -> dict[str, Any]:
        """获取所有测试结果。"""
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        """保存测试结果到文件。"""
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "base_url": self.base_url,
            "results": self.get_all_results(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_api_performance(benchmark: Any) -> None:
    """pytest-benchmark集成。"""
    bench = APIPerfBenchmark()
    benchmark(bench.run_all)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bench = APIPerfBenchmark()
    results = bench.run_all()
    logger.info("\nAPI性能测试结果:")
    for k, v in results.items():
        logger.info("  %s: %s", k, v)
