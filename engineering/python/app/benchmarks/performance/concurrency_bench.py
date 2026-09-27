"""并发与压力测试模块。

测试系统在高并发、高负载下的性能表现：
- 多线程并发请求
- 持续压力测试
- 内存泄漏检测
- CPU密集型任务并发
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


class ConcurrencyPerfBenchmark:
    """并发与压力性能基准测试。"""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}
        self._stop_flag = threading.Event()

    def setup(self) -> None:
        """初始化测试环境。"""
        self._stop_flag.clear()

    def teardown(self) -> None:
        """清理测试环境。"""
        self._stop_flag.set()

    def test_thread_pool_concurrency(self, n_workers: int = 10) -> dict[str, float]:
        """测试线程池并发性能。"""
        times: list[float] = []

        def cpu_bound_task(task_id: int) -> float:
            """模拟CPU密集型任务。"""
            t0 = time.perf_counter()
            # 模拟矩阵运算
            for _ in range(50):
                _ = np.random.randn(32) @ np.random.randn(32, 64)
            return (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = [executor.submit(cpu_bound_task, i) for i in range(n_workers * 5)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    elapsed = future.result()
                    times.append(elapsed)
                except Exception as e:
                    logger.debug("线程任务失败: %s", e)

        total_time = (time.perf_counter() - t0) * 1000

        if not times:
            return {"thread_pool_rps": 0}

        times.sort()
        n = len(times)
        result = {
            "thread_pool_workers": n_workers,
            "thread_pool_tasks": len(times),
            "thread_pool_total_ms": round(total_time, 2),
            "thread_pool_avg_ms": round(sum(times) / n, 2),
            "thread_pool_p50_ms": round(times[int(n * 0.50)], 2),
            "thread_pool_p95_ms": round(times[min(int(n * 0.95), n - 1)], 2),
            "thread_pool_rps": round(len(times) / (total_time / 1000), 2),
        }
        self._results.update(result)
        return result

    def test_asyncio_concurrency(self, n_tasks: int = 100) -> dict[str, float]:
        """测试asyncio并发性能。"""
        times: list[float] = []

        async def io_bound_task(task_id: int) -> float:
            """模拟IO密集型任务。"""
            t0 = time.perf_counter()
            # 模拟异步IO操作
            await asyncio.sleep(0.001)
            # 模拟一些计算
            _ = np.random.randn(16) @ np.random.randn(16, 32)
            return (time.perf_counter() - t0) * 1000

        async def run_all_tasks() -> list[float]:
            tasks = [io_bound_task(i) for i in range(n_tasks)]
            # [A-H13] 添加 return_exceptions=True，避免单个任务抛异常终止整个 gather
            return await asyncio.gather(*tasks, return_exceptions=True)

        t0 = time.perf_counter()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(run_all_tasks())
            # [A-H13] 过滤异常结果（return_exceptions=True 时异常会作为返回值）
            times = []
            for r in results:
                if isinstance(r, Exception):
                    logger.debug("asyncio 任务失败: %s", r)
                    continue
                if isinstance(r, (int, float)) and r > 0:
                    times.append(r)
        finally:
            loop.close()

        total_time = (time.perf_counter() - t0) * 1000

        if not times:
            return {"asyncio_rps": 0}

        times.sort()
        n = len(times)
        result = {
            "asyncio_tasks": n_tasks,
            "asyncio_total_ms": round(total_time, 2),
            "asyncio_avg_ms": round(sum(times) / n, 2),
            "asyncio_p50_ms": round(times[int(n * 0.50)], 2),
            "asyncio_p95_ms": round(times[min(int(n * 0.95), n - 1)], 2),
            "asyncio_rps": round(n_tasks / (total_time / 1000), 2),
        }
        self._results.update(result)
        return result

    def test_sustained_load(self, duration_s: float = 5.0) -> dict[str, float]:
        """测试持续负载性能。"""
        request_count = 0
        error_count = 0
        times: list[float] = []
        start_time = time.perf_counter()

        def worker() -> None:
            nonlocal request_count, error_count
            while not self._stop_flag.is_set():
                if time.perf_counter() - start_time > duration_s:
                    break
                t0 = time.perf_counter()
                try:
                    # 模拟请求处理
                    _ = np.random.randn(16) @ np.random.randn(16, 32)
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
                    request_count += 1
                except Exception as e:
                    logging.getLogger(__name__).warning("concurrency_bench worker error: %s", e)
                    error_count += 1

        threads = []
        n_workers = 5
        for _ in range(n_workers):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        actual_duration = time.perf_counter() - start_time

        if not times:
            return {"sustained_rps": 0}

        times.sort()
        n = len(times)
        result = {
            "sustained_duration_s": round(actual_duration, 2),
            "sustained_requests": request_count,
            "sustained_errors": error_count,
            "sustained_rps": round(request_count / actual_duration, 2),
            "sustained_avg_ms": round(sum(times) / n, 2),
            "sustained_p50_ms": round(times[int(n * 0.50)], 2),
            "sustained_p95_ms": round(times[min(int(n * 0.95), n - 1)], 2),
            "sustained_success_rate": round((request_count - error_count) / request_count * 100, 1)
            if request_count > 0
            else 0,
        }
        self._results.update(result)
        return result

    def test_memory_pressure(self, n_iterations: int = 100) -> dict[str, float]:
        """测试内存压力下的性能。"""
        import gc

        gc.collect()
        initial_objects = len(gc.get_objects())

        times: list[float] = []
        allocated_arrays: list[np.ndarray] = []

        for i in range(n_iterations):
            t0 = time.perf_counter()
            # 分配内存
            arr = np.random.randn(1000, 100)
            allocated_arrays.append(arr)
            # 执行计算
            _ = arr @ arr.T
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

            # 每10次迭代清理一半
            if i % 10 == 9:
                allocated_arrays = allocated_arrays[: len(allocated_arrays) // 2]

        gc.collect()
        final_objects = len(gc.get_objects())

        times.sort()
        n = len(times)
        result = {
            "memory_pressure_iterations": n_iterations,
            "memory_pressure_avg_ms": round(sum(times) / n, 2),
            "memory_pressure_p50_ms": round(times[int(n * 0.50)], 2),
            "memory_pressure_p95_ms": round(times[min(int(n * 0.95), n - 1)], 2),
            "memory_object_growth": final_objects - initial_objects,
        }
        self._results.update(result)
        return result

    def test_cpu_stress(self, duration_s: float = 3.0) -> dict[str, float]:
        """测试CPU压力下的性能。"""
        iterations = 0
        start_time = time.perf_counter()

        while time.perf_counter() - start_time < duration_s:
            # CPU密集型计算
            for _ in range(100):
                _ = np.random.randn(64) @ np.random.randn(64, 128)
            iterations += 1

        actual_duration = time.perf_counter() - start_time

        result = {
            "cpu_stress_duration_s": round(actual_duration, 2),
            "cpu_stress_iterations": iterations,
            "cpu_stress_ops_per_s": round(iterations / actual_duration, 2),
        }
        self._results.update(result)
        return result

    def run_all(self) -> dict[str, Any]:
        """运行所有并发与压力测试。"""
        self.setup()

        try:
            logger.info("  测试线程池并发...")
            self.test_thread_pool_concurrency(10)

            logger.info("  测试asyncio并发...")
            self.test_asyncio_concurrency(100)

            logger.info("  测试持续负载...")
            self.test_sustained_load(3.0)

            logger.info("  测试内存压力...")
            self.test_memory_pressure(50)

            logger.info("  测试CPU压力...")
            self.test_cpu_stress(2.0)

        finally:
            self.teardown()

        return self.get_all_results()

    def get_all_results(self) -> dict[str, Any]:
        """获取所有测试结果。"""
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        """保存测试结果到文件。"""
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": self.get_all_results(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_concurrency_performance(benchmark: Any) -> None:
    """pytest-benchmark集成。"""
    bench = ConcurrencyPerfBenchmark()
    benchmark(bench.run_all)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bench = ConcurrencyPerfBenchmark()
    results = bench.run_all()
    logger.info("\n并发与压力测试结果:")
    for k, v in results.items():
        logger.info("  %s: %s", k, v)
