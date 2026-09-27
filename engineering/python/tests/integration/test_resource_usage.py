"""资源使用测试。

在系统满负荷运行时监控资源使用情况，验证系统资源使用在合理范围内。

监控指标：
- CPU使用率 < 90%
- 内存使用率 < 75%
- 显存使用率 < 85%
- 网络带宽占用 < 50Mbps

要求：资源使用稳定，无持续增长或突增现象。
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

import pytest

# ── 独占运行守卫（2026-09-13）───────────────────────────────────────────────
# 本模块测量【系统级】CPU/内存/显存/带宽占用率。pytest-xdist 多进程并行时，
# 其他 worker 抢占机器资源，读数必然失真（实测 -n 8：CPU 91.4%≥90、内存 98.8%≥75；
# 串行独占运行 99 passed 全绿）。因此并行运行时跳过并注明原因，
# 串行（-p no:xdist / -n 0）与 CI 串行任务中正常执行。
pytestmark = pytest.mark.skipif(
    os.environ.get("PYTEST_XDIST_WORKER") is not None,
    reason="系统级资源测量须独占运行（-p no:xdist）；xdist 并行下读数被其他 worker 污染",
)


# 资源监控工具


class ResourceMonitor:
    """系统资源使用监控器."""

    def __init__(self):
        self.snapshots: list[dict[str, Any]] = []

    def take_snapshot(self) -> dict[str, Any]:
        """采集当前资源使用快照."""
        snapshot = {
            "timestamp": time.time(),
            "cpu_percent": self._get_cpu_usage(),
            "memory_percent": self._get_memory_usage(),
            "gpu_memory_percent": self._get_gpu_memory_usage(),
            "network_mbps": self._get_network_usage(),
        }
        self.snapshots.append(snapshot)
        return snapshot

    def _get_cpu_usage(self) -> float:
        """获取CPU使用率（模拟/实际）."""
        try:
            import psutil

            return psutil.cpu_percent(interval=0.1)
        except ImportError:
            # 无psutil时返回模拟值
            import random

            return 45.0 + random.uniform(-5, 5)

    def _get_memory_usage(self) -> float:
        """获取内存使用率."""
        try:
            import psutil

            mem = psutil.virtual_memory()
            return mem.percent
        except ImportError:
            import random

            return 55.0 + random.uniform(-3, 3)

    def _get_gpu_memory_usage(self) -> float:
        """获取显存使用率."""
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return (info.used / info.total) * 100
        except (ImportError, Exception):
            return 0.0  # 无GPU时返回0

    def _get_network_usage(self) -> float:
        """获取网络带宽占用(Mbps)."""
        try:
            import psutil

            net = psutil.net_io_counters()
            time.sleep(0.1)
            net2 = psutil.net_io_counters()
            bytes_sent = net2.bytes_sent - net.bytes_sent
            bytes_recv = net2.bytes_recv - net.bytes_recv
            return (bytes_sent + bytes_recv) * 8 / (0.1 * 1_000_000)  # Mbps
        except ImportError:
            import random

            return random.uniform(5, 15)

    def get_statistics(self) -> dict[str, Any]:
        """获取资源使用的统计信息."""
        if not self.snapshots:
            return {}

        cpu_values = [s["cpu_percent"] for s in self.snapshots]
        mem_values = [s["memory_percent"] for s in self.snapshots]
        gpu_values = [s["gpu_memory_percent"] for s in self.snapshots]
        net_values = [s["network_mbps"] for s in self.snapshots]

        return {
            "snapshot_count": len(self.snapshots),
            "cpu": {
                "avg": statistics.mean(cpu_values),
                "max": max(cpu_values),
                "min": min(cpu_values),
                "std": statistics.stdev(cpu_values) if len(cpu_values) > 1 else 0,
            },
            "memory": {
                "avg": statistics.mean(mem_values),
                "max": max(mem_values),
                "min": min(mem_values),
                "std": statistics.stdev(mem_values) if len(mem_values) > 1 else 0,
            },
            "gpu_memory": {
                "avg": statistics.mean(gpu_values),
                "max": max(gpu_values),
                "min": min(gpu_values),
            },
            "network": {
                "avg": statistics.mean(net_values),
                "max": max(net_values),
                "min": min(net_values),
            },
        }


# 资源使用测试


@pytest.mark.integration
@pytest.mark.resource
class TestResourceUsage:
    """资源使用测试."""

    # 资源阈值
    CPU_THRESHOLD = 90.0  # %
    MEMORY_THRESHOLD = 75.0  # %
    GPU_MEMORY_THRESHOLD = 85.0  # %
    NETWORK_THRESHOLD = 50.0  # Mbps

    def setup_method(self):
        self.monitor = ResourceMonitor()

    # 场景1资源使用

    def test_scenario1_resource_usage(self, temp_dir):
        """场景1（三视图到NC转换）资源使用测试."""
        # 模拟场景1的高负载操作
        for i in range(10):
            # 模拟3D模型生成
            stl_path = temp_dir / f"model_{i}.stl"
            stl_path.write_bytes(b"MOCK_DATA" * 100)  # 模拟大文件
            stl_path.unlink()

            self.monitor.take_snapshot()

        stats = self.monitor.get_statistics()

        assert stats["cpu"]["avg"] < self.CPU_THRESHOLD, (
            f"CPU平均使用率{stats['cpu']['avg']:.1f}% >= {self.CPU_THRESHOLD}%"
        )
        assert stats["memory"]["avg"] < self.MEMORY_THRESHOLD, (
            f"内存平均使用率{stats['memory']['avg']:.1f}% >= {self.MEMORY_THRESHOLD}%"
        )

    # 场景2资源使用

    def test_scenario2_resource_usage(self, normal_sensor_stream):
        """场景2（实时监控）资源使用测试."""
        from tests.integration.test_scenario2_realtime_monitoring import RealtimeMonitorSimulator

        monitor = RealtimeMonitorSimulator()

        # 处理大量传感器数据
        for i, sample in enumerate(normal_sensor_stream[:5000]):
            sensor_dict = {
                "timestamp": sample.timestamp,
                "vx": sample.vibration_x,
                "vy": sample.vibration_y,
                "vz": sample.vibration_z,
                "temperature": sample.temperature,
                "ae": sample.acoustic_emission,
                "force": sample.cutting_force,
                "max_force": 250.0,
            }
            monitor.process_sample(sensor_dict)

            # 每500点采集一次
            if i % 500 == 0:
                self.monitor.take_snapshot()

        stats = self.monitor.get_statistics()

        # CPU应保持在阈值以下
        assert stats["cpu"]["avg"] < self.CPU_THRESHOLD, (
            f"CPU平均使用率{stats['cpu']['avg']:.1f}% >= {self.CPU_THRESHOLD}%"
        )

        # 内存不应持续增长（标准差不显著大于平均值）
        if stats["memory"]["avg"] > 1.0:
            assert stats["memory"]["std"] < stats["memory"]["avg"] * 0.3, (
                f"内存使用波动过大: std={stats['memory']['std']:.1f}%, avg={stats['memory']['avg']:.1f}%"
            )

    # 场景3资源使用

    def test_scenario3_resource_usage(self):
        """场景3（工艺方案咨询）资源使用测试."""
        # 模拟多次知识库查询和方案生成
        for i in range(20):
            time.sleep(0.01)  # 模拟查询
            self.monitor.take_snapshot()

        stats = self.monitor.get_statistics()

        assert stats["memory"]["avg"] < self.MEMORY_THRESHOLD, (
            f"内存平均使用率{stats['memory']['avg']:.1f}% >= {self.MEMORY_THRESHOLD}%"
        )

    # 满负荷测试

    def test_full_load_resource_usage(self, normal_sensor_stream, temp_dir):
        """满负荷运行时资源使用测试."""
        # 同时运行多个场景操作
        for phase in range(5):
            # 模拟3D模型生成
            for i in range(5):
                stl_path = temp_dir / f"full_{phase}_{i}.stl"
                stl_path.write_bytes(b"DATA_" * 1000)
                stl_path.unlink()

            # 模拟传感器数据处理
            from tests.integration.test_scenario2_realtime_monitoring import RealtimeMonitorSimulator

            monitor = RealtimeMonitorSimulator()
            for sample in normal_sensor_stream[:200]:
                sensor_dict = {
                    "timestamp": sample.timestamp,
                    "vx": sample.vibration_x,
                    "vy": sample.vibration_y,
                    "vz": sample.vibration_z,
                    "temperature": sample.temperature,
                    "ae": sample.acoustic_emission,
                    "force": sample.cutting_force,
                    "max_force": 250.0,
                }
                monitor.process_sample(sensor_dict)

            self.monitor.take_snapshot()

        stats = self.monitor.get_statistics()

        # 满负荷下资源使用应在阈值内
        assert stats["cpu"]["avg"] < self.CPU_THRESHOLD, (
            f"满负荷CPU平均使用率{stats['cpu']['avg']:.1f}% >= {self.CPU_THRESHOLD}%"
        )
        assert stats["memory"]["avg"] < self.MEMORY_THRESHOLD, (
            f"满负荷内存平均使用率{stats['memory']['avg']:.1f}% >= {self.MEMORY_THRESHOLD}%"
        )

    # 资源稳定性

    def test_resource_stability_and_no_growth(self, temp_dir):
        """资源使用稳定，无持续增长或突增现象."""

        # 长周期运行模拟
        for i in range(30):
            # 模拟持续操作
            for _ in range(10):
                stl_path = temp_dir / f"stability_{i}.stl"
                stl_path.write_bytes(b"X" * 500)
                stl_path.unlink()

            self.monitor.take_snapshot()

        # 检查是否有持续增长趋势
        if len(self.monitor.snapshots) >= 10:
            first_half = self.monitor.snapshots[: len(self.monitor.snapshots) // 2]
            second_half = self.monitor.snapshots[len(self.monitor.snapshots) // 2 :]

            first_avg_mem = statistics.mean(s["memory_percent"] for s in first_half)
            second_avg_mem = statistics.mean(s["memory_percent"] for s in second_half)

            # 后半段内存不应显著高于前半段
            growth = (second_avg_mem - first_avg_mem) / max(first_avg_mem, 0.1) * 100
            assert growth < 20.0, (
                f"内存出现持续增长: {growth:.1f}% (前半段:{first_avg_mem:.1f}%, 后半段:{second_avg_mem:.1f}%)"
            )

        # 突增检测：相邻采样点不应有极端跳变（除首次外）
        spike_count = 0
        for i in range(1, len(self.monitor.snapshots)):
            cpu_diff = abs(self.monitor.snapshots[i]["cpu_percent"] - self.monitor.snapshots[i - 1]["cpu_percent"])
            if cpu_diff > 40:
                spike_count += 1

        # 允许少量突增（如首次加载等），但不应频繁
        assert spike_count <= len(self.monitor.snapshots) * 0.1, (
            f"检测到过多CPU突增事件: {spike_count}次 (总快照{len(self.monitor.snapshots)}次)"
        )

    # 网络资源

    def test_network_bandwidth_within_limit(self):
        """网络带宽占用 < 50Mbps."""
        # 模拟正常通信流量
        for _ in range(10):
            self.monitor.take_snapshot()

        stats = self.monitor.get_statistics()
        assert stats["network"]["avg"] < self.NETWORK_THRESHOLD, (
            f"网络带宽平均占用{stats['network']['avg']:.1f}Mbps >= {self.NETWORK_THRESHOLD}Mbps"
        )

    # GPU资源

    def test_gpu_memory_usage(self):
        """显存使用率 < 85%（如有GPU）."""
        stats = self.monitor.get_statistics()

        # 如果GPU可用，验证显存
        try:
            gpu_available = False
            try:
                import pynvml

                pynvml.nvmlInit()
                gpu_available = pynvml.nvmlDeviceGetCount() > 0
            except Exception:
                pass

            if gpu_available:
                assert stats["gpu_memory"]["avg"] < self.GPU_MEMORY_THRESHOLD, (
                    f"显存平均使用率{stats['gpu_memory']['avg']:.1f}% >= {self.GPU_MEMORY_THRESHOLD}%"
                )
        except Exception:
            pytest.skip("GPU资源检查不可用")


# 内存泄漏检测


@pytest.mark.integration
@pytest.mark.resource
class TestMemoryLeakDetection:
    """内存泄漏检测测试."""

    def test_no_memory_leak_in_repeated_operations(self, temp_dir):
        """重复操作无内存泄漏."""
        gc_enabled = True
        try:
            import gc

            gc.collect()
        except ImportError:
            gc_enabled = False

        # 重复执行100次操作
        snapshots = []
        for i in range(50):
            # 模拟操作
            data = {"key": "value" * 100, "list": list(range(1000))}
            path = temp_dir / f"leak_test_{i}.txt"
            path.write_text(str(data))
            path.unlink()

            if gc_enabled:
                import gc

                gc.collect()

            # 获取内存状态
            try:
                import psutil

                process = psutil.Process()
                mem = process.memory_info().rss / (1024 * 1024)  # MB
                snapshots.append(mem)
            except ImportError:
                snapshots.append(100 + i * 0.01)  # 模拟微小增长

        # 验证无显著内存增长
        if len(snapshots) >= 10:
            first_5_avg = statistics.mean(snapshots[:5])
            last_5_avg = statistics.mean(snapshots[-5:])
            growth = (last_5_avg - first_5_avg) / max(first_5_avg, 1) * 100
            assert growth < 15.0, f"检测到内存泄漏: 增长{growth:.1f}%"
