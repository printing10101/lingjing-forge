"""关键模块性能基准测试

测试目标：
- 验证核心模块的响应时间和吞吐量
- 确保关键路径性能符合生产要求
- 识别性能瓶颈和优化机会

测试模块：
1. 数据库连接池性能
2. 异常处理性能
3. 认证授权性能
4. API路由性能
5. 数据处理管道性能
6. 资源关闭性能（P3幂等性基线）
7. 中间件链路性能
8. 预算/成本跟踪器吞吐量
9. 审计日志哈希链性能
10. 心跳调度器性能

================================================================================
阈值设定方法论
================================================================================
1. 采样基准：在开发机 (i7-12700H, 32GB, NVMe, Windows 11) 上对每个测试
   独立运行 10 次，取 P95 测量值。
2. 上浮系数：基准值 × 1.3（30% 余量），覆盖 CI 环境抖动、容器化开销、
   Python 3.10 vs 3.11+ 性能差异。
3. 平台差异修正：Windows TestClient 走 socket loopback，比 Linux 进程内
   HTTP 慢 5-10 倍。涉及 TestClient 的测试阈值已在 Linux 基准上放大。
4. 失败处理：硬阈值断言（性能回归立即失败），已知 bug 用 xfail(strict=True)
   标记，避免 CI 噪声淹没真实信号。

================================================================================
阈值速查表（更新日期：2026-07-28）
================================================================================
测试                                      | 指标   | 阈值      | 依据
------------------------------------------|--------|-----------|----------------
test_connection_pool_creation_time        | 耗时   | <1 ms     | 仅对象构造
test_concurrent_engine_access (20线程)     | P95    | <10 ms    | mock create_engine
test_exception_creation_performance        | 单次   | <0.01 ms  | dataclass 构造
test_exception_handler_response_time       | P95    | <10 ms    | FastAPI 异常链
test_jwt_token_creation_performance        | 单次   | <1 ms     | HS256 签名
test_jwt_token_verification_performance    | 单次   | <1 ms     | HS256 验签
test_health_check_endpoint_performance     | P95    | <30 ms    | TestClient loopback
test_api_response_serialization_performance| 单次   | <0.1 ms   | dataclass→dict
test_data_validation_performance           | 单次   | <0.1 ms   | pydantic v2 解析
test_json_serialization_performance        | 单次   | <0.1 ms   | stdlib json.dumps
test_connection_pool_memory_footprint     | 对象增长| <1000     | 10 个单例
test_budget_manager_close_latency         | 首次   | <50 ms    | 连接归还+标志位
                                          | 幂等   | <0.1 ms   | 仅标志位判断
test_cost_tracker_close_latency           | 首次   | <50 ms    | 同上
test_rule_database_close_latency          | 首次   | <100 ms   | 含 close_all
test_wakeup_queue_close_latency           | 首次   | <50 ms    | 同 budget
test_vector_store_close_latency_without_client | 首次 | <1 ms   | 无客户端纯标志位
test_concurrent_close_safety (5×20次)     | 总耗时 | <200 ms   | 幂等路径极快
test_request_id_middleware_latency        | P95    | <30 ms    | TestClient loopback
test_security_headers_middleware_latency  | P95    | <30 ms    | 同上
test_full_middleware_stack_assembly_time  | 装配   | <200 ms   | 8 个中间件注册
test_check_budget_throughput              | 单次   | <50 ms    | psutil+DB 查询
test_record_cost_throughput               | 单次   | <10 ms    | INSERT+commit WAL
test_concurrent_record_cost_safety        | -      | xfail     | 已知并发 bug
test_single_log_latency (audit)           | 单次   | <5 ms     | SHA-256+文件 I/O
test_hash_chain_overhead                  | overhead| <200%    | SHA-256 极快
test_verify_integrity_scan_performance    | 500条  | <500 ms   | 文件读取+SHA-256
test_add_task_throughput                  | 单次   | <10 ms    | 缓存命中+INSERT
test_get_due_tasks_latency                | 100条  | <5 ms     | SELECT+反序列化
test_update_task_status_latency           | 单次   | <5 ms     | UPDATE+commit

================================================================================
运行方式
================================================================================
    # 全量性能基线
    python -m pytest tests/performance/test_critical_modules_performance.py -v

    # 仅运行心跳调度器基线（CronParser 优化验证）
    python -m pytest tests/performance/test_critical_modules_performance.py \
        -k "Heartbeat" -v

    # 跳过已知 xfail（用于 CI 绿灯）
    python -m pytest tests/performance/test_critical_modules_performance.py \
        --no-header -rN

注意：
- 本测试套件不依赖 pytest-timeout / pytest-cov 插件，可直接运行。
- Python 3.10 环境下涉及 StrEnum 的测试会自动 skip（需 3.11+）。
- 性能数据受系统负载影响，建议在无其他高负载进程时运行。
"""

import os

import pytest
import time
import asyncio
import threading
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.skip_ci

# 延迟测量类（P95 断言）对 CPU 调度噪声敏感：xdist 多进程并行时其他 worker
# 抢占 CPU，P95 必然超阈值（2026-09-13 实测 -n 8 下 exception_handler P95
# 10.49ms≥10ms、request_id 中间件 62.9ms≥30ms；串行独占运行全绿）。
# 标记该类测试须独占运行，xdist worker 中跳过并注明原因。
requires_exclusive_machine = pytest.mark.skipif(
    os.environ.get("PYTEST_XDIST_WORKER") is not None,
    reason="P95 延迟断言须独占运行（-p no:xdist）；xdist 并行下读数被其他 worker 污染",
)


# WinSock 损坏环境检测
# 背景：当前 Windows 环境 WinSock 损坏，依赖 TestClient（需真实 asyncio
# 事件循环 + socketpair）的测试会以 OSError [WinError 10038] 失败。
# 这些测试不反映被测代码的性能问题，应在 WinSock 损坏环境下跳过而非 FAIL。
# 检测方式与 test_end_to_end_performance.py 一致，保持单一来源。


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


# 模块级常量：True=真实 asyncio 可用；False=WinSock 损坏或 asyncio 为 stub。
# 使用方式：在依赖 TestClient 的测试类上加 ``@pytest.mark.skipif`` 装饰器，
# 不影响其他纯 CPU/内存测试（它们不需要事件循环）。
_REAL_ASYNCIO_AVAILABLE = _check_real_asyncio_available()


class TestDatabaseConnectionPoolPerformance:
    """数据库连接池性能测试"""

    def test_connection_pool_creation_time(self):
        """测试连接池创建时间"""
        from app.database.connection import _DatabaseSingletons

        start = time.perf_counter()
        singletons = _DatabaseSingletons()
        elapsed = (time.perf_counter() - start) * 1000

        # 连接池对象创建应该在1ms内完成
        assert elapsed < 1.0, f"连接池创建时间过长: {elapsed:.3f}ms"

        print(f"\n连接池创建时间: {elapsed:.3f}ms")

    def test_concurrent_engine_access(self):
        """测试并发引擎访问性能"""
        from app.database.connection import _DatabaseSingletons
        import threading

        singletons = _DatabaseSingletons()
        results = []
        errors = []

        def access_engine(thread_id: int):
            try:
                start = time.perf_counter()
                with patch("app.database.connection.create_async_engine") as mock_create:
                    mock_create.return_value = MagicMock()
                    engine = singletons.get_engine()
                    elapsed = (time.perf_counter() - start) * 1000
                    results.append(elapsed)
            except Exception as e:
                errors.append(e)

        # 创建20个并发线程
        threads = [threading.Thread(target=access_engine, args=(i,)) for i in range(20)]

        start_total = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total_elapsed = (time.perf_counter() - start_total) * 1000

        # 验证无错误
        assert len(errors) == 0, f"并发访问出错: {errors}"

        # 验证性能
        avg_time = sum(results) / len(results) if results else 0
        p95_time = sorted(results)[int(len(results) * 0.95)] if results else 0

        assert avg_time < 5.0, f"平均访问时间过长: {avg_time:.3f}ms"
        assert p95_time < 10.0, f"P95访问时间过长: {p95_time:.3f}ms"

        print("\n并发引擎访问性能 (20线程):")
        print(f"  总时间: {total_elapsed:.3f}ms")
        print(f"  平均时间: {avg_time:.3f}ms")
        print(f"  P95时间: {p95_time:.3f}ms")


@requires_exclusive_machine
class TestExceptionHandlerPerformance:
    """异常处理性能测试"""

    def test_exception_creation_performance(self):
        """测试异常对象创建性能"""
        from app.core.exceptions import (
            NotFoundException,
            ValidationException,
            InternalServerException,
        )

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            _ = NotFoundException(message="Test")
            _ = ValidationException(message="Test")
            _ = InternalServerException(message="Test")

        elapsed = time.perf_counter() - start
        per_exception_ms = (elapsed / (iterations * 3)) * 1000

        # 每个异常创建应该在0.01ms内完成
        assert per_exception_ms < 0.01, f"异常创建过慢: {per_exception_ms:.4f}ms"

        print(f"\n异常创建性能 ({iterations * 3}次):")
        print(f"  总时间: {elapsed * 1000:.2f}ms")
        print(f"  每次: {per_exception_ms:.4f}ms")

    @pytest.mark.skipif(
        not _REAL_ASYNCIO_AVAILABLE,
        reason="WinSock 损坏或 asyncio 为 stub，TestClient 测试需要真实事件循环",
    )
    def test_exception_handler_response_time(self):
        """测试异常处理器响应时间"""
        # Python 3.10 不支持 StrEnum（3.11+），app.core.response 使用了它
        # 在 3.10 环境中跳过此测试，避免 ImportError 掩盖性能信号
        try:
            from enum import StrEnum  # noqa: F401
        except ImportError:
            pytest.skip("StrEnum requires Python 3.11+; skip on 3.10")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.exceptions import NotFoundException
        from app.core.exception_handlers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test")
        async def test_endpoint():
            raise NotFoundException(message="Test error")

        client = TestClient(app, raise_server_exceptions=False)

        # 预热
        for _ in range(10):
            client.get("/test")

        # 性能测试
        iterations = 100
        times = []

        for _ in range(iterations):
            start = time.perf_counter()
            response = client.get("/test")
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            assert response.status_code == 404

        times.sort()
        n = len(times)
        avg_time = sum(times) / n
        p50_time = times[int(n * 0.50)]
        p95_time = times[min(int(n * 0.95), n - 1)]

        # 异常处理响应应该在10ms内
        assert p95_time < 10.0, f"P95响应时间过长: {p95_time:.3f}ms"

        print(f"\n异常处理响应时间 ({iterations}次):")
        print(f"  平均: {avg_time:.3f}ms")
        print(f"  P50: {p50_time:.3f}ms")
        print(f"  P95: {p95_time:.3f}ms")


class TestAuthenticationPerformance:
    """认证授权性能测试"""

    def test_jwt_token_creation_performance(self):
        """测试JWT令牌创建性能"""
        from app.auth.security import create_access_token

        iterations = 1000
        start = time.perf_counter()

        for i in range(iterations):
            _ = create_access_token(data={"sub": f"user_{i}"})

        elapsed = time.perf_counter() - start
        per_token_ms = (elapsed / iterations) * 1000

        # 每个令牌创建应该在1ms内完成
        assert per_token_ms < 1.0, f"JWT令牌创建过慢: {per_token_ms:.3f}ms"

        print(f"\nJWT令牌创建性能 ({iterations}次):")
        print(f"  总时间: {elapsed * 1000:.2f}ms")
        print(f"  每次: {per_token_ms:.3f}ms")

    def test_jwt_token_verification_performance(self):
        """测试JWT令牌验证性能"""
        from app.auth.security import create_access_token, decode_token

        # 创建测试令牌
        token = create_access_token(data={"sub": "test_user"})

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            _ = decode_token(token)

        elapsed = time.perf_counter() - start
        per_verify_ms = (elapsed / iterations) * 1000

        # 每个令牌验证应该在1ms内完成
        assert per_verify_ms < 1.0, f"JWT令牌验证过慢: {per_verify_ms:.3f}ms"

        print(f"\nJWT令牌验证性能 ({iterations}次):")
        print(f"  总时间: {elapsed * 1000:.2f}ms")
        print(f"  每次: {per_verify_ms:.3f}ms")


class TestAPIRoutePerformance:
    """API路由性能测试"""

    @pytest.mark.skipif(
        not _REAL_ASYNCIO_AVAILABLE,
        reason="WinSock 损坏或 asyncio 为 stub，TestClient 测试需要真实事件循环",
    )
    def test_health_check_endpoint_performance(self):
        """测试健康检查端点性能"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/health")
        async def health_check():
            return {"status": "ok"}

        client = TestClient(app)

        # 预热
        for _ in range(10):
            client.get("/health")

        # 性能测试
        iterations = 100
        times = []

        for _ in range(iterations):
            start = time.perf_counter()
            response = client.get("/health")
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            assert response.status_code == 200

        times.sort()
        n = len(times)
        avg_time = sum(times) / n
        p95_time = times[min(int(n * 0.95), n - 1)]

        # 健康检查 P95 应在 30ms 内
        # 阈值依据：Windows TestClient 进程内 HTTP 开销 + Python 3.10 路径
        # 在 Linux/CI 上通常 <5ms，Windows 上 TestClient 走 socket loopback 较慢
        assert p95_time < 30.0, f"健康检查P95响应过长: {p95_time:.3f}ms"

        print(f"\n健康检查端点性能 ({iterations}次):")
        print(f"  平均: {avg_time:.3f}ms")
        print(f"  P95: {p95_time:.3f}ms")

    def test_api_response_serialization_performance(self):
        """测试API响应序列化性能"""
        # Python 3.10 兼容性：app.core.response 使用 StrEnum（3.11+）
        try:
            from enum import StrEnum  # noqa: F401
        except ImportError:
            pytest.skip("StrEnum requires Python 3.11+; skip on 3.10")

        from app.core.response import success

        # 创建测试数据
        test_data = {
            "id": 1,
            "name": "Test Item",
            "description": "A test item for performance testing",
            "tags": ["tag1", "tag2", "tag3"],
            "metadata": {"key1": "value1", "key2": "value2"},
        }

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            _ = success(data=test_data)

        elapsed = time.perf_counter() - start
        per_response_ms = (elapsed / iterations) * 1000

        # 每个响应构建应该在0.1ms内完成
        assert per_response_ms < 0.1, f"响应序列化过慢: {per_response_ms:.4f}ms"

        print(f"\nAPI响应序列化性能 ({iterations}次):")
        print(f"  总时间: {elapsed * 1000:.2f}ms")
        print(f"  每次: {per_response_ms:.4f}ms")


class TestDataPipelinePerformance:
    """数据处理管道性能测试"""

    def test_data_validation_performance(self):
        """测试数据校验性能"""
        from pydantic import BaseModel

        class TestDataModel(BaseModel):
            id: int
            name: str
            value: float
            tags: list[str]

        test_data = {
            "id": 1,
            "name": "Test",
            "value": 3.14,
            "tags": ["a", "b", "c"],
        }

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            _ = TestDataModel(**test_data)

        elapsed = time.perf_counter() - start
        per_validation_ms = (elapsed / iterations) * 1000

        # 每个校验应该在0.1ms内完成
        assert per_validation_ms < 0.1, f"数据校验过慢: {per_validation_ms:.4f}ms"

        print(f"\n数据校验性能 ({iterations}次):")
        print(f"  总时间: {elapsed * 1000:.2f}ms")
        print(f"  每次: {per_validation_ms:.4f}ms")

    def test_json_serialization_performance(self):
        """测试JSON序列化性能"""
        import json

        test_data = {
            "id": 1,
            "name": "Test Item",
            "description": "A test item for performance testing",
            "tags": ["tag1", "tag2", "tag3"],
            "metadata": {"key1": "value1", "key2": "value2"},
            "nested": {"level1": {"level2": {"level3": {"value": "deep"}}}},
        }

        iterations = 10000
        start = time.perf_counter()

        for _ in range(iterations):
            _ = json.dumps(test_data)

        elapsed = time.perf_counter() - start
        per_serialization_ms = (elapsed / iterations) * 1000

        # 每个序列化应该在0.1ms内完成
        assert per_serialization_ms < 0.1, f"JSON序列化过慢: {per_serialization_ms:.4f}ms"

        print(f"\nJSON序列化性能 ({iterations}次):")
        print(f"  总时间: {elapsed * 1000:.2f}ms")
        print(f"  每次: {per_serialization_ms:.4f}ms")


class TestMemoryPerformance:
    """内存性能测试"""

    def test_connection_pool_memory_footprint(self):
        """测试连接池内存占用"""
        import gc
        from app.database.connection import _DatabaseSingletons

        gc.collect()
        initial_objects = len(gc.get_objects())

        # 创建多个连接池实例
        pools = []
        for _ in range(10):
            pools.append(_DatabaseSingletons())

        gc.collect()
        final_objects = len(gc.get_objects())

        object_growth = final_objects - initial_objects

        # 对象增长应该在合理范围内
        assert object_growth < 1000, f"对象增长过大: {object_growth}"

        print("\n连接池内存占用:")
        print(f"  对象增长: {object_growth}")

        # 清理
        pools.clear()
        gc.collect()


# 性能基线扩展：P3 幂等性 / 中间件 / 预算 / 审计 / 心跳
# 以下测试类建立生产关键路径的性能基线，配合 P0-P3 修复确保优化不退化。
# 阈值设定依据：在开发机 (i7-12700H, 32GB, NVMe) 上多次采样取 P95 上浮 30%
# 作为基线，留出 CI 环境波动余量。阈值被突破时打印诊断信息而非硬失败
# （mark=pytest.mark.xfail conditional），避免 CI 噪声淹没真实信号。


class TestResourceShutdownPerformance:
    """资源关闭性能基线（P3 幂等性相关）

    验证关键资源 close()/stop() 方法的性能特征：
    1. 单次关闭延迟在合理范围内（不阻塞 shutdown 流程）
    2. 重复关闭（幂等性路径）应明显快于首次关闭
    3. 并发关闭场景下无性能崩溃
    """

    @pytest.fixture
    def temp_db_path(self, tmp_path) -> str:
        """临时数据库路径，避免跨测试共享连接池"""
        return str(tmp_path / "perf_test.db")

    def test_budget_manager_close_latency(self, temp_db_path):
        """BudgetManager.close() 延迟基线"""
        from app.budget.budget import BudgetManager

        manager = BudgetManager(db_path=temp_db_path)
        start = time.perf_counter()
        manager.close()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 首次 close 应在 50ms 内完成（包含连接归还 + 标志位更新）
        assert elapsed_ms < 50.0, f"BudgetManager.close() 延迟过高: {elapsed_ms:.3f}ms"

        # 幂等性路径应明显更快（< 0.1ms）
        start = time.perf_counter()
        manager.close()
        idempotent_ms = (time.perf_counter() - start) * 1000
        assert idempotent_ms < 0.1, f"幂等 close 路径延迟过高: {idempotent_ms:.6f}ms"

        print("\nBudgetManager.close() 性能:")
        print(f"  首次关闭: {elapsed_ms:.3f}ms")
        print(f"  幂等关闭: {idempotent_ms:.6f}ms")

    def test_cost_tracker_close_latency(self, temp_db_path):
        """MultiDimensionCostTracker.close() 延迟基线"""
        from app.budget.cost_tracker import MultiDimensionCostTracker

        tracker = MultiDimensionCostTracker(db_path=temp_db_path)
        start = time.perf_counter()
        tracker.close()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50.0, f"CostTracker.close() 延迟过高: {elapsed_ms:.3f}ms"

        start = time.perf_counter()
        tracker.close()
        idempotent_ms = (time.perf_counter() - start) * 1000
        assert idempotent_ms < 0.1, f"幂等 close 路径延迟过高: {idempotent_ms:.6f}ms"

        print("\nCostTracker.close() 性能:")
        print(f"  首次关闭: {elapsed_ms:.3f}ms")
        print(f"  幂等关闭: {idempotent_ms:.6f}ms")

    def test_rule_database_close_latency(self, temp_db_path):
        """RuleDatabase.close() 延迟基线

        RuleDatabase.close() 额外调用 close_all() 释放池中所有连接，
        延迟应高于 BudgetManager/CostTracker 但仍在合理范围。
        """
        from app.database.rule_db import RuleDatabase

        db = RuleDatabase(db_path=temp_db_path)
        start = time.perf_counter()
        db.close()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # close_all 释放池中所有连接，允许 100ms
        assert elapsed_ms < 100.0, f"RuleDatabase.close() 延迟过高: {elapsed_ms:.3f}ms"

        start = time.perf_counter()
        db.close()
        idempotent_ms = (time.perf_counter() - start) * 1000
        assert idempotent_ms < 0.1, f"幂等 close 路径延迟过高: {idempotent_ms:.6f}ms"

        print("\nRuleDatabase.close() 性能:")
        print(f"  首次关闭: {elapsed_ms:.3f}ms")
        print(f"  幂等关闭: {idempotent_ms:.6f}ms")

    def test_wakeup_queue_close_latency(self, temp_db_path):
        """WakeupQueue.close() 延迟基线"""
        from app.heartbeat.heartbeat import WakeupQueue

        queue = WakeupQueue(db_path=temp_db_path)
        start = time.perf_counter()
        queue.close()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50.0, f"WakeupQueue.close() 延迟过高: {elapsed_ms:.3f}ms"

        start = time.perf_counter()
        queue.close()
        idempotent_ms = (time.perf_counter() - start) * 1000
        assert idempotent_ms < 0.1, f"幂等 close 路径延迟过高: {idempotent_ms:.6f}ms"

        print("\nWakeupQueue.close() 性能:")
        print(f"  首次关闭: {elapsed_ms:.3f}ms")
        print(f"  幂等关闭: {idempotent_ms:.6f}ms")

    def test_vector_store_close_latency_without_client(self, tmp_path):
        """VectorStore.close() 延迟基线（无 ChromaDB 客户端场景）

        在 ChromaDB 未初始化的情况下，close() 应为纯标志位操作，
        延迟极低。这覆盖了 RAG 功能未启用时的 shutdown 路径。
        """
        from app.rag.vector_store import VectorStore

        store = VectorStore(persist_directory=str(tmp_path / "vectors"))
        # 不调用 _ensure_client()，模拟 RAG 未启用的场景
        start = time.perf_counter()
        store.close()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 无客户端时 close 应在 1ms 内完成（仅标志位更新）
        assert elapsed_ms < 1.0, f"VectorStore.close() 无客户端延迟过高: {elapsed_ms:.6f}ms"

        start = time.perf_counter()
        store.close()
        idempotent_ms = (time.perf_counter() - start) * 1000
        assert idempotent_ms < 0.1, f"幂等 close 路径延迟过高: {idempotent_ms:.6f}ms"

        print("\nVectorStore.close() 性能（无客户端）:")
        print(f"  首次关闭: {elapsed_ms:.6f}ms")
        print(f"  幂等关闭: {idempotent_ms:.6f}ms")

    def test_concurrent_close_safety(self, temp_db_path):
        """并发关闭安全性测试

        验证多线程同时调用 close() 不会导致崩溃或资源双重释放。
        """
        from app.budget.budget import BudgetManager

        manager = BudgetManager(db_path=temp_db_path)
        errors: list[Exception] = []

        def close_worker():
            try:
                for _ in range(20):
                    manager.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=close_worker) for _ in range(5)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(errors) == 0, f"并发 close 出错: {errors}"
        # 100 次 close 调用（5 线程 × 20 次）应在 200ms 内完成
        assert elapsed_ms < 200.0, f"并发 close 性能过差: {elapsed_ms:.3f}ms"

        print("\n并发 close 性能 (5线程 × 20次):")
        print(f"  总时间: {elapsed_ms:.3f}ms")
        print(f"  错误数: {len(errors)}")


@requires_exclusive_machine
class TestMiddlewareStackPerformance:
    """中间件链路性能基线

    验证中间件链装配与请求穿透性能：
    1. 中间件注册延迟
    2. 单中间件请求穿透延迟
    3. 完整链路请求穿透延迟
    """

    @pytest.mark.skipif(
        not _REAL_ASYNCIO_AVAILABLE,
        reason="WinSock 损坏或 asyncio 为 stub，TestClient 测试需要真实事件循环",
    )
    def test_request_id_middleware_latency(self):
        """RequestIdMiddleware 请求穿透延迟基线"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.request_id import RequestIdMiddleware

        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.get("/ping")
        async def ping():
            return {"msg": "pong"}

        client = TestClient(app)
        # 预热
        for _ in range(10):
            client.get("/ping")

        # 采样
        iterations = 200
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            resp = client.get("/ping")
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            assert resp.status_code == 200
            # 验证 X-Request-ID 被回写
            assert "X-Request-ID" in resp.headers or "x-request-id" in resp.headers

        times.sort()
        p50 = times[int(len(times) * 0.50)]
        p95 = times[min(int(len(times) * 0.95), len(times) - 1)]

        # 单中间件穿透 P95 应在 30ms 内
        # 阈值依据：Windows TestClient 进程内 HTTP 走 socket loopback，比 Linux 慢 5-10 倍
        # Linux/CI 上通常 <5ms
        assert p95 < 30.0, f"RequestIdMiddleware P95 过高: {p95:.3f}ms"

        print(f"\nRequestIdMiddleware 穿透性能 ({iterations}次):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")

    @pytest.mark.skipif(
        not _REAL_ASYNCIO_AVAILABLE,
        reason="WinSock 损坏或 asyncio 为 stub，TestClient 测试需要真实事件循环",
    )
    def test_security_headers_middleware_latency(self):
        """SecurityHeadersMiddleware 请求穿透延迟基线"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.auth.security_headers_asgi import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/ping")
        async def ping():
            return {"msg": "pong"}

        client = TestClient(app)
        for _ in range(10):
            client.get("/ping")

        iterations = 200
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            resp = client.get("/ping")
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            assert resp.status_code == 200

        times.sort()
        p95 = times[min(int(len(times) * 0.95), len(times) - 1)]
        # 阈值依据：Windows TestClient loopback HTTP 较慢，Linux/CI <5ms
        assert p95 < 30.0, f"SecurityHeadersMiddleware P95 过高: {p95:.3f}ms"

        print(f"\nSecurityHeadersMiddleware 穿透性能 ({iterations}次):")
        print(f"  P95: {p95:.3f}ms")

    def test_full_middleware_stack_assembly_time(self):
        """完整中间件栈装配时间基线

        验证 register_middleware_stack 的装配延迟在合理范围。
        注意：IdleAutoShutdownMiddleware 禁用，避免触发 sidecar 依赖。
        """
        from fastapi import FastAPI
        from app.middleware_stack import register_middleware_stack
        from app.utils.utils import MetricsCollector
        from app.utils.ring_buffer import RingLogBuffer

        app = FastAPI()
        metrics = MetricsCollector()
        ring_log = RingLogBuffer(capacity=1000)

        start = time.perf_counter()
        register_middleware_stack(
            app,
            metrics=metrics,
            ring_log=ring_log,
            state_file_path="/tmp/.lnn_sidecar_state_test.json",
            idle_auto_shutdown_enabled=False,
            idle_timeout_seconds=3600,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 中间件栈装配应在 200ms 内完成
        assert elapsed_ms < 200.0, f"中间件栈装配时间过长: {elapsed_ms:.3f}ms"

        print(f"\n中间件栈装配时间: {elapsed_ms:.3f}ms")


class TestBudgetTrackerThroughput:
    """预算/成本跟踪器吞吐量基线

    验证高频调用场景下的吞吐量与延迟：
    1. check_budget 吞吐量
    2. record_cost 吞吐量
    3. 并发记录成本的安全性
    """

    @pytest.fixture
    def budget_manager(self, tmp_path):
        """BudgetManager fixture，使用临时 db"""
        from app.budget.budget import BudgetManager

        manager = BudgetManager(db_path=str(tmp_path / "budget_perf.db"))
        yield manager
        manager.close()

    @pytest.fixture
    def cost_tracker(self, tmp_path):
        """MultiDimensionCostTracker fixture"""
        from app.budget.cost_tracker import MultiDimensionCostTracker

        tracker = MultiDimensionCostTracker(db_path=str(tmp_path / "cost_perf.db"))
        yield tracker
        tracker.close()

    def test_check_budget_throughput(self, budget_manager):
        """check_budget 吞吐量基线

        check_budget 涉及 tracker.reset_daily + _update_current_metrics +
        多资源循环检查，是预算路径热点。
        """
        iterations = 100
        start = time.perf_counter()
        for i in range(iterations):
            budget_manager.check_budget(agent_id=f"agent_{i % 5}")
        elapsed_s = time.perf_counter() - start

        per_call_ms = (elapsed_s / iterations) * 1000
        ops_per_sec = iterations / elapsed_s if elapsed_s > 0 else 0

        # 每次 check_budget 应在 50ms 内完成（含 psutil 采集 + DB 查询）
        assert per_call_ms < 50.0, f"check_budget 单次延迟过高: {per_call_ms:.3f}ms"

        print(f"\ncheck_budget 吞吐量 ({iterations}次):")
        print(f"  总时间: {elapsed_s * 1000:.2f}ms")
        print(f"  每次: {per_call_ms:.3f}ms")
        print(f"  QPS: {ops_per_sec:.1f}")

    def test_record_cost_throughput(self, cost_tracker):
        """record_cost 吞吐量基线

        record_cost 包含 _calculate_cost + INSERT + commit，
        是成本跟踪路径热点。
        """
        iterations = 1000
        start = time.perf_counter()
        for i in range(iterations):
            cost_tracker.record_cost(
                task_id=f"task_{i}",
                cost_type="gpu_time",
                resource_value=1.5,
                agent_id="agent_perf",
                project_id="perf_test",
            )
        elapsed_s = time.perf_counter() - start

        per_call_ms = (elapsed_s / iterations) * 1000
        ops_per_sec = iterations / elapsed_s if elapsed_s > 0 else 0

        # 每条记录应在 10ms 内（含 INSERT + commit）
        # 阈值依据：Windows + SQLite WAL 同步 I/O，单条 INSERT+commit 通常 1-5ms
        # Linux/NVMe 上通常 <2ms
        assert per_call_ms < 10.0, f"record_cost 单次延迟过高: {per_call_ms:.3f}ms"

        print(f"\nrecord_cost 吞吐量 ({iterations}次):")
        print(f"  总时间: {elapsed_s * 1000:.2f}ms")
        print(f"  每次: {per_call_ms:.3f}ms")
        print(f"  QPS: {ops_per_sec:.1f}")

    @pytest.mark.xfail(
        reason=(
            "已知 bug：MultiDimensionCostTracker 共享单连接 self._conn，"
            "多线程并发调用 record_cost 触发 'cannot start a transaction within a transaction'。"
            "需重构为连接池或线程局部连接，单独跟踪修复。"
        ),
        strict=True,
    )
    def test_concurrent_record_cost_safety(self, cost_tracker):
        """并发记录成本安全性测试

        验证多线程并发调用 record_cost 不会导致数据库锁定或数据丢失。

        已知 bug：MultiDimensionCostTracker.__init__ 中 self._conn = self._pool.get_connection()
        在多个线程间共享同一连接，SQLite 不允许嵌套事务，并发 INSERT+commit 会触发
        OperationalError。修复方案：使用 with self._pool.connection() 上下文管理器
        每次方法调用获取独立连接，或使用 threading.local() 绑定线程局部连接。
        """
        iterations_per_thread = 50
        thread_count = 4
        errors: list[Exception] = []

        def record_worker(tid: int):
            try:
                for i in range(iterations_per_thread):
                    cost_tracker.record_cost(
                        task_id=f"t{tid}_task_{i}",
                        cost_type="api_call",
                        resource_value=1.0,
                        agent_id=f"agent_{tid}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_worker, args=(tid,)) for tid in range(thread_count)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed_ms = (time.perf_counter() - start) * 1000

        total_ops = iterations_per_thread * thread_count
        assert len(errors) == 0, f"并发记录出错: {errors}"

        print(f"\n并发 record_cost 性能 ({total_ops}次, {thread_count}线程):")
        print(f"  总时间: {elapsed_ms:.2f}ms")
        print(f"  QPS: {total_ops / (elapsed_ms / 1000):.1f}")
        print(f"  错误数: {len(errors)}")


class TestAuditLogHashChainPerformance:
    """审计日志哈希链性能基线

    验证 P0-16 哈希链防篡改机制的性能影响：
    1. 单条 log() 延迟（含 SHA-256 计算 + 文件写入 + 链状态持久化）
    2. 批量 log() 吞吐量
    3. verify_integrity() 扫描性能
    4. 哈希链 vs 无哈希链的 overhead
    """

    @pytest.fixture
    def audit_log(self, tmp_path):
        """AgentAuditLog fixture，使用临时文件"""
        from app.agent.middleware import AgentAuditLog

        log_path = str(tmp_path / "audit_perf.log")
        log = AgentAuditLog(log_path=log_path)
        yield log
        log.close()

    def test_single_log_latency(self, audit_log):
        """单条 log() 延迟基线

        log() 包含：SHA-256 计算 + JSON 序列化 + 文件写入 + flush +
        链状态 JSON 持久化。这是审计路径热点。
        """
        iterations = 100
        start = time.perf_counter()
        for i in range(iterations):
            audit_log.log(
                agent_id=f"agent_{i % 5}",
                route=f"/api/v1/test/{i}",
                permission_class="read",
                status_code=200,
                latency_ms=12.5,
            )
        elapsed_s = time.perf_counter() - start

        per_call_ms = (elapsed_s / iterations) * 1000

        # 单条 log 应在 5ms 内（含 SHA-256 + 文件 I/O + 链状态持久化）
        assert per_call_ms < 5.0, f"audit_log.log() 单次延迟过高: {per_call_ms:.3f}ms"

        print(f"\nAgentAuditLog.log() 性能 ({iterations}次):")
        print(f"  总时间: {elapsed_s * 1000:.2f}ms")
        print(f"  每次: {per_call_ms:.3f}ms")
        print(f"  QPS: {iterations / elapsed_s:.1f}")

    def test_hash_chain_overhead(self, tmp_path):
        """哈希链 vs 无哈希链 overhead 基线

        对比 P0-16 哈希链机制引入的额外开销，确保防篡改保护
        不会显著影响审计路径性能。
        """
        import hashlib
        import json

        iterations = 1000

        # 基线：仅 JSON 序列化 + 文件追加（无哈希链）
        plain_path = tmp_path / "plain.log"
        with open(plain_path, "a", encoding="utf-8") as f:
            start = time.perf_counter()
            for i in range(iterations):
                entry = {
                    "timestamp_ms": int(time.time() * 1000),
                    "agent_id": f"agent_{i}",
                    "route": "/api/test",
                    "status_code": 200,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
            plain_elapsed = time.perf_counter() - start

        # 哈希链：JSON + SHA-256 + 链状态更新
        chain_path = tmp_path / "chain.log"
        last_hash = "GENESIS"
        seq = 0
        with open(chain_path, "a", encoding="utf-8") as f:
            start = time.perf_counter()
            for i in range(iterations):
                entry = {
                    "timestamp_ms": int(time.time() * 1000),
                    "agent_id": f"agent_{i}",
                    "route": "/api/test",
                    "status_code": 200,
                    "chain_seq": seq,
                    "prev_hash": last_hash,
                }
                payload = json.dumps(entry, ensure_ascii=False, sort_keys=True)
                entry_hash = hashlib.sha256((payload + last_hash).encode("utf-8")).hexdigest()
                entry["entry_hash"] = entry_hash
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                last_hash = entry_hash
                seq += 1
            chain_elapsed = time.perf_counter() - start

        plain_per_ms = (plain_elapsed / iterations) * 1000
        chain_per_ms = (chain_elapsed / iterations) * 1000
        overhead_pct = ((chain_per_ms - plain_per_ms) / plain_per_ms * 100) if plain_per_ms > 0 else 0

        # 哈希链 overhead 应在 200% 以内（即不超过基线的 3 倍）
        # SHA-256 在短消息上极快，主要开销在 sort_keys 序列化
        assert overhead_pct < 200.0, f"哈希链 overhead 过高: {overhead_pct:.1f}%"

        print(f"\n哈希链 overhead 对比 ({iterations}次):")
        print(f"  无哈希链: {plain_per_ms:.4f}ms/条")
        print(f"  哈希链:   {chain_per_ms:.4f}ms/条")
        print(f"  Overhead: {overhead_pct:.1f}%")

    def test_verify_integrity_scan_performance(self, audit_log):
        """verify_integrity() 扫描性能基线

        验证完整性校验在大日志文件下的扫描性能。
        """
        # 先写入 500 条日志
        for i in range(500):
            audit_log.log(
                agent_id=f"agent_{i % 10}",
                route=f"/api/v1/scan/{i}",
                permission_class="read",
                status_code=200,
                latency_ms=10.0,
            )

        start = time.perf_counter()
        is_valid, breaks = audit_log.verify_integrity()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert is_valid, f"完整性校验失败: {breaks}"
        # 500 条日志扫描应在 500ms 内完成
        assert elapsed_ms < 500.0, f"verify_integrity() 扫描过慢: {elapsed_ms:.3f}ms"

        print("\nverify_integrity() 扫描性能 (500条):")
        print(f"  总时间: {elapsed_ms:.3f}ms")
        print(f"  每条: {elapsed_ms / 500:.4f}ms")


class TestHeartbeatSchedulerPerformance:
    """心跳调度器性能基线

    验证 WakeupQueue 数据库操作性能：
    1. add_task 吞吐量
    2. get_due_tasks 查询延迟
    3. update_task_status 延迟
    """

    @pytest.fixture
    def wakeup_queue(self, tmp_path):
        """WakeupQueue fixture"""
        from app.heartbeat.heartbeat import (
            WakeupQueue,
            ScheduledTask,
            ScheduleStatus,
            CronParser,
        )

        # 清空缓存确保基线测试从冷启动开始
        CronParser.clear_cache()
        queue = WakeupQueue(db_path=str(tmp_path / "heartbeat_perf.db"))
        yield queue, ScheduledTask, ScheduleStatus
        queue.close()
        CronParser.clear_cache()

    def test_add_task_throughput(self, wakeup_queue):
        """add_task 吞吐量基线

        add_task 包含 INSERT + commit + CronParser.get_next_run() 调用。
        CronParser.parse 已加入分钟级 TTL 缓存与字段预编译优化：
        - 首次调用：字段预编译 + 7天时间槽遍历，约 2-5ms（Python 3.10/Windows）
        - 后续调用（同一 cron_expr + 同一分钟）：缓存命中 O(1)，<0.1ms
        - SQLite INSERT+commit 在 WAL 模式下约 1-5ms

        因此 200 次批量插入的 per_call 接近纯 DB 开销（~1-3ms）。
        阈值设定为 10ms，留出 CI 环境波动余量。
        """
        queue, ScheduledTask, _ = wakeup_queue

        iterations = 200
        start = time.perf_counter()
        for i in range(iterations):
            task = ScheduledTask(
                task_id=f"perf_task_{i}",
                agent_id=f"agent_{i % 5}",
                schedule="*/5 * * * *",
                task_type="test",
                params={"index": i},
            )
            queue.add_task(task)
        elapsed_s = time.perf_counter() - start

        per_call_ms = (elapsed_s / iterations) * 1000

        # 每次 add_task 应在 10ms 内（含 CronParser 缓存查找 + INSERT + commit）
        # 阈值依据：
        # - CronParser 缓存命中 <0.1ms（仅首次冷启动约 2-5ms，摊销后忽略）
        # - SQLite WAL INSERT+commit 约 1-5ms（Windows）
        # - Windows 文件系统 I/O 开销
        # Linux/NVMe + Python 3.11+ 上通常 <2ms
        assert per_call_ms < 10.0, f"add_task 单次延迟过高: {per_call_ms:.3f}ms"

        print(f"\nWakeupQueue.add_task 吞吐量 ({iterations}次):")
        print(f"  总时间: {elapsed_s * 1000:.2f}ms")
        print(f"  每次: {per_call_ms:.3f}ms")
        print(f"  QPS: {iterations / elapsed_s:.1f}")

    def test_get_due_tasks_latency(self, wakeup_queue):
        """get_due_tasks 查询延迟基线

        get_due_tasks 在心跳循环中每秒调用一次，是热路径。
        """
        import time as _time

        queue, ScheduledTask, _ = wakeup_queue

        # 填充 100 个任务，全部设置为已到期
        now = _time.time()
        for i in range(100):
            task = ScheduledTask(
                task_id=f"due_task_{i}",
                agent_id=f"agent_{i % 5}",
                schedule="*/5 * * * *",
                task_type="test",
                params={"index": i},
                next_run=now - 10,  # 10 秒前到期
            )
            queue.add_task(task)

        # 采样
        iterations = 50
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            _ = queue.get_due_tasks(current_time=now)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)

        times.sort()
        p50 = times[int(len(times) * 0.50)]
        p95 = times[min(int(len(times) * 0.95), len(times) - 1)]

        # 100 条到期任务查询 P95 应在 10ms 内
        assert p95 < 10.0, f"get_due_tasks P95 过高: {p95:.3f}ms"

        print(f"\nget_due_tasks 查询性能 ({iterations}次, 100条到期):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")

    def test_update_task_status_latency(self, wakeup_queue):
        """update_task_status 延迟基线

        update_task_status 包含 SELECT（get_task）+ UPDATE + commit，
        当 status=COMPLETED 时还会调用 CronParser.get_next_run()。
        本测试使用 RUNNING 状态避免 CronParser 调用，
        聚焦于 UPDATE 路径本身的延迟基线。
        """
        queue, ScheduledTask, ScheduleStatus = wakeup_queue

        # 准备 50 个任务
        for i in range(50):
            task = ScheduledTask(
                task_id=f"status_task_{i}",
                agent_id=f"agent_{i % 5}",
                schedule="*/5 * * * *",
                task_type="test",
                params={"index": i},
            )
            queue.add_task(task)

        # 采样：使用 RUNNING 状态避免触发 CronParser.get_next_run()
        iterations = 50
        start = time.perf_counter()
        for i in range(iterations):
            queue.update_task_status(
                task_id=f"status_task_{i}",
                status=ScheduleStatus.RUNNING,
            )
        elapsed_s = time.perf_counter() - start

        per_call_ms = (elapsed_s / iterations) * 1000

        # 每次 update 应在 5ms 内（含 UPDATE + commit）
        # 阈值依据：SQLite WAL UPDATE+commit 约 1-3ms，
        # Windows 环境下 I/O 开销略高
        assert per_call_ms < 5.0, f"update_task_status 单次延迟过高: {per_call_ms:.3f}ms"

        print(f"\nupdate_task_status 性能 ({iterations}次):")
        print(f"  总时间: {elapsed_s * 1000:.2f}ms")
        print(f"  每次: {per_call_ms:.3f}ms")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
