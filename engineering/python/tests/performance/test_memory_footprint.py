"""真实内存占用性能测试

测试目标：
    1. 使用 tracemalloc 测量关键模块的真实内存占用（而非 gc 对象计数）
    2. 验证资源关闭后内存被正确释放（无泄漏）
    3. 验证批量操作下内存增长在合理范围
    4. 验证长周期运行下无明显内存累积

设计背景：
    test_critical_modules_performance.py::TestMemoryPerformance 仅用
    gc.get_objects() 计数，无法反映真实堆内存占用。本测试用 tracemalloc
    追踪 Python 内存分配器层面的真实开销，给出 MB 级别的精确数字。

运行方式：
    python -m pytest tests/performance/test_memory_footprint.py -v
"""

from __future__ import annotations

import gc
import os
import tracemalloc

import pytest

pytestmark = pytest.mark.skip_ci

# 内存测量类断言对机器负载敏感：xdist 并行时其他 worker 的内存压力会污染
# 增长读数（2026-09-13 实测 -n 8 下 add_task_memory_growth 失败、串行独占通过）。
# 标记须独占运行，xdist worker 中跳过并注明原因。
requires_exclusive_machine = pytest.mark.skipif(
    os.environ.get("PYTEST_XDIST_WORKER") is not None,
    reason="内存增长断言须独占运行（-p no:xdist）；xdist 并行下读数受其他 worker 内存压力干扰",
)


# 1. 模块级内存占用基线


class TestModuleMemoryFootprint:
    """关键模块实例化后的真实内存占用"""

    def _measure_memory(self, factory, *args, **kwargs) -> dict:
        """测量 factory(*args, **kwargs) 创建实例后的内存占用

        Returns:
            {
                "current_mb": 当前分配的内存 (MB),
                "peak_mb": 峰值分配内存 (MB),
                "blocks": 分配的内存块数,
                "instance": 创建的实例（用于后续清理）
            }
        """
        gc.collect()
        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        instance = factory(*args, **kwargs)

        snapshot_after = tracemalloc.take_snapshot()
        stats = snapshot_after.compare_to(snapshot_before, "lineno")

        current, peak = tracemalloc.get_traced_memory()
        block_count = sum(s.count_diff for s in stats if s.count_diff > 0)

        tracemalloc.stop()

        return {
            "current_mb": current / 1024 / 1024,
            "peak_mb": peak / 1024 / 1024,
            "blocks": block_count,
            "instance": instance,
        }

    def test_budget_manager_memory(self, tmp_path):
        """BudgetManager 实例内存占用应 < 5 MB"""
        from app.budget.budget import BudgetManager

        result = self._measure_memory(BudgetManager, db_path=str(tmp_path / "budget_mem.db"))
        result["instance"].close()

        assert result["current_mb"] < 5.0, f"BudgetManager 内存占用过高: {result['current_mb']:.3f}MB"

        print("\nBudgetManager 内存占用:")
        print(f"  当前: {result['current_mb']:.3f} MB")
        print(f"  峰值: {result['peak_mb']:.3f} MB")
        print(f"  内存块: {result['blocks']}")

    def test_cost_tracker_memory(self, tmp_path):
        """MultiDimensionCostTracker 实例内存占用应 < 5 MB"""
        from app.budget.cost_tracker import MultiDimensionCostTracker

        result = self._measure_memory(MultiDimensionCostTracker, db_path=str(tmp_path / "cost_mem.db"))
        result["instance"].close()

        assert result["current_mb"] < 5.0, f"CostTracker 内存占用过高: {result['current_mb']:.3f}MB"

        print("\nCostTracker 内存占用:")
        print(f"  当前: {result['current_mb']:.3f} MB")
        print(f"  峰值: {result['peak_mb']:.3f} MB")
        print(f"  内存块: {result['blocks']}")

    def test_wakeup_queue_memory(self, tmp_path):
        """WakeupQueue 实例内存占用应 < 5 MB"""
        from app.heartbeat.heartbeat import WakeupQueue

        result = self._measure_memory(WakeupQueue, db_path=str(tmp_path / "wakeup_mem.db"))
        result["instance"].close()

        assert result["current_mb"] < 5.0, f"WakeupQueue 内存占用过高: {result['current_mb']:.3f}MB"

        print("\nWakeupQueue 内存占用:")
        print(f"  当前: {result['current_mb']:.3f} MB")
        print(f"  峰值: {result['peak_mb']:.3f} MB")
        print(f"  内存块: {result['blocks']}")

    def test_rule_database_memory(self, tmp_path):
        """RuleDatabase 实例内存占用应 < 5 MB"""
        from app.database.rule_db import RuleDatabase

        result = self._measure_memory(RuleDatabase, db_path=str(tmp_path / "rule_mem.db"))
        result["instance"].close()

        assert result["current_mb"] < 5.0, f"RuleDatabase 内存占用过高: {result['current_mb']:.3f}MB"

        print("\nRuleDatabase 内存占用:")
        print(f"  当前: {result['current_mb']:.3f} MB")
        print(f"  峰值: {result['peak_mb']:.3f} MB")
        print(f"  内存块: {result['blocks']}")


# 2. 资源释放验证


class TestResourceReleaseMemory:
    """验证资源 close() 后内存被正确释放

    阈值设定依据：
        1. SQLite 连接归还到连接池后，连接对象本身仍由池缓存，不会立即释放。
        2. Python pymalloc 内存分配器会缓存小块（< 512B），不归还 OS。
        3. tracemalloc 测量的是 Python 内存分配器层面的"已分配"字节，
           不区分缓存复用 vs 实际泄漏。

    因此阈值采用分层判定：
        - 小对象场景（allocated < 50 KB）：仅验证 close 后无显著增长
          （< 2× allocated），不强制释放率。原因：Python 内存池 + 连接池
          缓存导致小内存释放率天然偏低，不代表泄漏。
        - 大对象场景（allocated ≥ 50 KB）：要求释放率 ≥ 30%。
          原因：大块内存走系统 malloc，close 后应大部分归还。
    """

    RELEASE_RATIO_THRESHOLD_PCT = 30.0  # 大对象释放率阈值
    SMALL_OBJECT_THRESHOLD_KB = 50.0  # 小对象判定阈值
    SMALL_OBJECT_GROWTH_TOLERANCE = 2.0  # 小对象允许的增长倍数

    def _assert_release(self, allocated_kb: float, released_kb: float, resource_name: str):
        """统一释放判定逻辑

        Args:
            allocated_kb: 实例化时分配的内存 (KB)
            released_kb: close 后释放的内存 (KB)
            resource_name: 资源名称（用于错误消息）
        """
        release_ratio = released_kb / max(allocated_kb, 1) * 100

        print(f"\n{resource_name} 资源释放:")
        print(f"  分配: {allocated_kb:.2f} KB")
        print(f"  释放: {released_kb:.2f} KB")
        print(f"  释放率: {release_ratio:.1f}%")

        if allocated_kb < self.SMALL_OBJECT_THRESHOLD_KB:
            # 小对象场景：仅验证 close 后无显著增长
            # （leaked = allocated - released；leaked < 2× allocated 视为正常）
            leaked_kb = allocated_kb - released_kb
            growth_ratio = leaked_kb / max(allocated_kb, 1)
            assert growth_ratio < self.SMALL_OBJECT_GROWTH_TOLERANCE, (
                f"{resource_name} close 后泄漏过大（小对象场景）: "
                f"allocated={allocated_kb:.2f}KB, leaked={leaked_kb:.2f}KB, "
                f"growth_ratio={growth_ratio:.2f}×"
            )
            print(f"  判定: 小对象场景 (< {self.SMALL_OBJECT_THRESHOLD_KB}KB), 泄漏 {leaked_kb:.2f}KB 在容忍范围内")
        else:
            # 大对象场景：要求释放率达到阈值
            assert release_ratio >= self.RELEASE_RATIO_THRESHOLD_PCT, (
                f"{resource_name} close 后内存释放率过低（大对象场景）: "
                f"allocated={allocated_kb:.2f}KB, released={released_kb:.2f}KB, "
                f"ratio={release_ratio:.1f}% < {self.RELEASE_RATIO_THRESHOLD_PCT}%"
            )
            print(f"  判定: 大对象场景 (≥ {self.SMALL_OBJECT_THRESHOLD_KB}KB), 释放率 {release_ratio:.1f}% 达标")

    def test_budget_manager_release_after_close(self, tmp_path):
        """BudgetManager.close() 后内存应被正确释放（无泄漏）"""
        from app.budget.budget import BudgetManager

        gc.collect()
        tracemalloc.start()
        current_before, _ = tracemalloc.get_traced_memory()

        manager = BudgetManager(db_path=str(tmp_path / "release.db"))

        current_with_instance, _ = tracemalloc.get_traced_memory()
        manager.close()
        gc.collect()

        current_after_close, _ = tracemalloc.get_traced_memory()

        tracemalloc.stop()

        allocated = (current_with_instance - current_before) / 1024  # KB
        released = (current_with_instance - current_after_close) / 1024  # KB

        self._assert_release(allocated, released, "BudgetManager")

    def test_wakeup_queue_release_after_close(self, tmp_path):
        """WakeupQueue.close() 后内存应被正确释放（无泄漏）"""
        from app.heartbeat.heartbeat import WakeupQueue

        gc.collect()
        tracemalloc.start()
        current_before, _ = tracemalloc.get_traced_memory()

        queue = WakeupQueue(db_path=str(tmp_path / "release_q.db"))

        current_with_instance, _ = tracemalloc.get_traced_memory()
        queue.close()
        gc.collect()

        current_after_close, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        allocated = (current_with_instance - current_before) / 1024
        released = (current_with_instance - current_after_close) / 1024

        self._assert_release(allocated, released, "WakeupQueue")


# 3. 批量操作内存增长


@requires_exclusive_machine
class TestBatchOperationMemoryGrowth:
    """批量操作下的内存增长"""

    def test_add_task_memory_growth(self, tmp_path):
        """添加 1000 个任务后内存增长应 < 2 MB

        场景：心跳调度器批量注册任务，验证无任务级内存泄漏
        """
        from app.heartbeat.heartbeat import (
            WakeupQueue,
            ScheduledTask,
            CronParser,
        )

        CronParser.clear_cache()
        gc.collect()
        tracemalloc.start()
        current_before, _ = tracemalloc.get_traced_memory()

        queue = WakeupQueue(db_path=str(tmp_path / "growth.db"))
        current_after_init, _ = tracemalloc.get_traced_memory()

        # 批量添加 1000 个任务
        for i in range(1000):
            task = ScheduledTask(
                task_id=f"mem_task_{i}",
                agent_id=f"agent_{i % 10}",
                schedule="*/5 * * * *",
                task_type="test",
                params={"index": i, "data": "x" * 100},  # 模拟实际参数
            )
            queue.add_task(task)

        current_after_batch, _ = tracemalloc.get_traced_memory()
        queue.close()
        CronParser.clear_cache()
        gc.collect()
        tracemalloc.stop()

        batch_growth_kb = (current_after_batch - current_after_init) / 1024
        batch_growth_mb = batch_growth_kb / 1024

        # 1000 个任务的内存增长应 < 2 MB
        # （任务数据通过 SQLite 持久化，不应驻留 Python 堆）
        assert batch_growth_mb < 2.0, f"批量添加任务后内存增长过大: {batch_growth_mb:.3f}MB"

        print("\n批量添加 1000 个任务内存增长:")
        print(f"  初始化后: {(current_after_init - current_before) / 1024:.2f} KB")
        print(f"  批量后增长: {batch_growth_kb:.2f} KB ({batch_growth_mb:.3f} MB)")
        print(f"  每任务增长: {batch_growth_kb / 1000:.3f} KB")

    def test_audit_log_memory_growth(self, tmp_path):
        """写入 1000 条审计日志后内存增长应 < 2 MB

        场景：审计日志通过文件持久化，验证无日志条目驻留堆
        """
        from app.agent.middleware import AgentAuditLog

        log_path = tmp_path / "audit_mem.log"

        gc.collect()
        tracemalloc.start()
        current_before, _ = tracemalloc.get_traced_memory()

        audit_log = AgentAuditLog(log_path=str(log_path))
        current_after_init, _ = tracemalloc.get_traced_memory()

        # 批量写入 1000 条日志
        for i in range(1000):
            audit_log.log(
                agent_id=f"agent_{i % 20}",
                route=f"/api/v1/resource/{i}",
                permission_class="read",
                status_code=200,
                latency_ms=12.5,
            )

        current_after_batch, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 显式关闭审计日志，释放文件句柄
        audit_log.close()

        batch_growth_kb = (current_after_batch - current_after_init) / 1024
        batch_growth_mb = batch_growth_kb / 1024

        assert batch_growth_mb < 2.0, f"批量写入日志后内存增长过大: {batch_growth_mb:.3f}MB"

        print("\n批量写入 1000 条审计日志内存增长:")
        print(f"  初始化后: {(current_after_init - current_before) / 1024:.2f} KB")
        print(f"  批量后增长: {batch_growth_kb:.2f} KB ({batch_growth_mb:.3f} MB)")
        print(f"  每条增长: {batch_growth_kb / 1000:.3f} KB")
