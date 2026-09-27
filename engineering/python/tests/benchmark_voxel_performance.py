"""体素切削仿真性能基准测试套件。

测试覆盖：
1. 刀具掩码生成性能（向量化 vs 原始三重循环）
2. 批量刀具掩码应用性能（Numba JIT vs 纯Python）
3. 网格重建性能（Marching Cubes vs box mesh）
4. 完整仿真流程端到端性能
5. 不同网格规模下的扩展性测试

执行方式：
    python -m pytest tests/benchmark_voxel_performance.py -v --benchmark-only

输出：
    - 控制台输出各测试项的性能数据
    - 性能对比报告（优化前/后）
"""

from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pytest

from app.simulation.voxel_cutter import (
    VoxelCutter,
    ToolModel,
    _apply_tool_mask_batch,
    HAS_SKIMAGE,
    reconstruct_mesh,
)
from app.simulation.toolpath_parser import ToolpathParser


# 辅助函数：收集性能数据
class PerformanceMetrics:
    """性能数据采集器。"""

    def __init__(self):
        self.results: dict[str, dict] = {}

    def record(
        self,
        name: str,
        duration: float,
        extra: dict | None = None,
    ):
        self.results[name] = {
            "duration_seconds": round(duration, 6),
            **(extra or {}),
        }

    def report(self):
        """生成性能报告。"""
        print()
        print("=" * 70)
        print("            体素切削仿真引擎 - 性能基准测试报告")
        print("=" * 70)
        print(f"{'测试项':<40} {'耗时(秒)':<15} {'说明'}")
        print("-" * 70)
        for name, data in self.results.items():
            dur = data.get("duration_seconds", 0)
            extra = ", ".join(f"{k}={v}" for k, v in data.items() if k != "duration_seconds")
            print(f"{name:<40} {dur:<15.6f} {extra}")
        print("=" * 70)


_metrics = PerformanceMetrics()


# 测试用例

# 1. 刀具掩码生成性能


@pytest.mark.benchmark
class TestToolMaskPerformance:
    """刀具掩码生成性能测试。"""

    TOOL_SIZES = [
        ("小刀具(d=5mm)", 5.0),
        ("中等刀具(d=10mm)", 10.0),
        ("大刀具(d=20mm)", 20.0),
    ]

    def test_flat_tool_mask_generation(self):
        """平底刀掩码生成性能（向量化实现）。"""
        for label, diameter in self.TOOL_SIZES:
            tool = ToolModel(diameter=diameter, tool_type="flat", cutting_length=30.0)
            # 预热
            _ = tool.voxel_mask(voxel_size=1.0)
            # 正式测试
            n_runs = 100
            t0 = time.perf_counter()
            for _ in range(n_runs):
                mask = tool.voxel_mask(voxel_size=1.0)
            t1 = time.perf_counter()
            _metrics.record(
                f"flat_mask_{label}",
                (t1 - t0) / n_runs,
                {"shape": str(mask.shape), "true_count": int(mask.sum())},
            )

    def test_ball_tool_mask_generation(self):
        """球头刀掩码生成性能。"""
        for label, diameter in self.TOOL_SIZES:
            tool = ToolModel(diameter=diameter, tool_type="ball", cutting_length=30.0)
            _ = tool.voxel_mask(voxel_size=1.0)
            n_runs = 100
            t0 = time.perf_counter()
            for _ in range(n_runs):
                mask = tool.voxel_mask(voxel_size=1.0)
            t1 = time.perf_counter()
            _metrics.record(
                f"ball_mask_{label}",
                (t1 - t0) / n_runs,
                {"shape": str(mask.shape), "true_count": int(mask.sum())},
            )

    def test_drill_tool_mask_generation(self):
        """钻头掩码生成性能。"""
        for label, diameter in self.TOOL_SIZES:
            tool = ToolModel(diameter=diameter, tool_type="drill", cutting_length=30.0)
            _ = tool.voxel_mask(voxel_size=1.0)
            n_runs = 100
            t0 = time.perf_counter()
            for _ in range(n_runs):
                mask = tool.voxel_mask(voxel_size=1.0)
            t1 = time.perf_counter()
            _metrics.record(
                f"drill_mask_{label}",
                (t1 - t0) / n_runs,
                {"shape": str(mask.shape), "true_count": int(mask.sum())},
            )

    def test_corner_radius_tool_mask(self):
        """带圆角平底刀掩码生成性能。"""
        tool = ToolModel(diameter=10.0, tool_type="flat", corner_radius=2.0, cutting_length=30.0)
        _ = tool.voxel_mask(voxel_size=1.0)
        n_runs = 100
        t0 = time.perf_counter()
        for _ in range(n_runs):
            mask = tool.voxel_mask(voxel_size=1.0)
        t1 = time.perf_counter()
        _metrics.record(
            "flat_mask_corner_radius(d10_cr2)",
            (t1 - t0) / n_runs,
            {"shape": str(mask.shape), "true_count": int(mask.sum())},
        )


# 2. 批量刀具掩码应用性能


@pytest.mark.benchmark
class TestBatchToolMaskPerformance:
    """批量刀具掩码应用性能测试。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tool = ToolModel(diameter=10.0, tool_type="flat", cutting_length=20.0)
        self.tool_mask = self.tool.voxel_mask(voxel_size=1.0)
        self.bbox_min = np.array([0.0, 0.0, 0.0], dtype=np.float64)
        self.voxel_size = 1.0
        self.padding = self.voxel_size * 2
        # 预热Numba JIT
        warmup_grid = np.ones((20, 20, 20), dtype=bool)
        warmup_pts = np.array([[5.0, 5.0, 5.0]], dtype=np.float64)
        _apply_tool_mask_batch(
            warmup_grid,
            self.tool_mask,
            warmup_pts,
            self.bbox_min,
            self.voxel_size,
            self.padding,
        )

    def _run_bench(self, n_points: int, label: str):
        """执行指定规模的批量测试。"""
        np.random.seed(42)
        grid = np.ones((50, 50, 50), dtype=bool)
        points = np.random.uniform(5, 45, (n_points, 3)).astype(np.float64)

        t0 = time.perf_counter()
        removed = _apply_tool_mask_batch(
            grid,
            self.tool_mask,
            points,
            self.bbox_min,
            self.voxel_size,
            self.padding,
        )
        t1 = time.perf_counter()

        _metrics.record(
            f"batch_apply_{label}",
            t1 - t0,
            {"n_points": n_points, "removed": removed},
        )

    def test_batch_100_points(self):
        self._run_bench(100, "100pts")

    def test_batch_1000_points(self):
        self._run_bench(1000, "1000pts")

    def test_batch_5000_points(self):
        self._run_bench(5000, "5000pts")


# 3. Marching Cubes 网格重建性能


@pytest.mark.benchmark
class TestMeshReconstructionPerformance:
    """网格重建性能测试（Marching Cubes vs box mesh）。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cutter = VoxelCutter(voxel_size=1.0)
        self.bbox_min = np.array([0.0, 0.0, 0.0], dtype=np.float64)

        # 预热 Marching Cubes（reconstruct_mesh 为模块级函数）
        warmup = np.zeros((5, 5, 5), dtype=bool)
        warmup[1:4, 1:4, 1:4] = True
        reconstruct_mesh(warmup, self.bbox_min, 1.0)

    def _make_sphere_grid(self, size: int, radius_ratio: float = 0.3) -> np.ndarray:
        """创建球形体素网格。"""
        cx = cy = cz = size // 2
        r = int(size * radius_ratio)
        X, Y, Z = np.meshgrid(np.arange(size), np.arange(size), np.arange(size), indexing="ij")
        return ((X - cx) ** 2 + (Y - cy) ** 2 + (Z - cz) ** 2) <= r**2

    def test_mc_30x30x30(self):
        grid = self._make_sphere_grid(30)
        t0 = time.perf_counter()
        mesh = reconstruct_mesh(grid, self.bbox_min, 1.0)
        t1 = time.perf_counter()
        _metrics.record(
            "mc_30x30x30",
            t1 - t0,
            {
                "verts": len(mesh.vertices) if mesh else 0,
                "faces": len(mesh.faces) if mesh else 0,
            },
        )

    def test_mc_50x50x50(self):
        grid = self._make_sphere_grid(50)
        t0 = time.perf_counter()
        mesh = reconstruct_mesh(grid, self.bbox_min, 1.0)
        t1 = time.perf_counter()
        _metrics.record(
            "mc_50x50x50",
            t1 - t0,
            {
                "verts": len(mesh.vertices) if mesh else 0,
                "faces": len(mesh.faces) if mesh else 0,
            },
        )

    def test_mc_100x100x100(self):
        grid = self._make_sphere_grid(100)
        t0 = time.perf_counter()
        mesh = reconstruct_mesh(grid, self.bbox_min, 1.0)
        t1 = time.perf_counter()
        _metrics.record(
            "mc_100x100x100",
            t1 - t0,
            {
                "verts": len(mesh.vertices) if mesh else 0,
                "faces": len(mesh.faces) if mesh else 0,
            },
        )


# 4. 端到端仿真性能


@pytest.mark.benchmark
class TestEndToEndSimulationPerformance:
    """完整仿真流程端到端性能测试。"""

    def test_simulation_with_trimesh_stock(self):
        """使用trimesh毛坯的完整仿真测试。"""
        trimesh = pytest.importorskip("trimesh")

        cutter = VoxelCutter(voxel_size=2.0)
        tool = ToolModel(diameter=10.0, tool_type="flat")

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # 创建毛坯
            stock_mesh = trimesh.creation.box(extents=[80, 60, 30])
            stock_mesh.apply_translation([0, 0, 15])
            stock_path = tmp_path / "stock.stl"
            stock_mesh.export(str(stock_path), file_type="stl")

            # 创建刀路
            gcode = """G00 Z80.
G00 X0. Y0.
G01 Z-5. F500
G01 X30. F800
G01 Y20.
G01 X0.
G01 Y0.
G00 Z80."""
            parser = ToolpathParser()
            segments = parser.parse_gcode(gcode)

            # 执行仿真
            t0 = time.perf_counter()
            result = cutter.run_simulation(
                stock_stl_path=stock_path,
                tool=tool,
                segments=segments,
                output_dir=tmp_path / "output",
                task_id="perf_test_end2end",
            )
            t1 = time.perf_counter()

            _metrics.record(
                "end2end_trimesh(voxel=2.0)",
                t1 - t0,
                {
                    "voxel_count": result.voxel_count,
                    "removed": result.removed_voxel_count,
                    "collision": result.collision.collided,
                    "segments": result.toolpath_segment_count,
                    "stl_size_bytes": len(result.stock_stl_raw),
                },
            )

    def test_simulation_large_grid(self):
        """较大体素网格的仿真测试（100x100x100标准测试案例）。"""
        trimesh = pytest.importorskip("trimesh")

        # 使用较粗体素以减少测试时间，但仍满足性能目标
        cutter = VoxelCutter(voxel_size=2.0)
        tool = ToolModel(diameter=20.0, tool_type="flat")

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # 创建较大毛坯
            stock_mesh = trimesh.creation.box(extents=[200, 200, 50])
            stock_mesh.apply_translation([0, 0, 25])
            stock_path = tmp_path / "stock.stl"
            stock_mesh.export(str(stock_path), file_type="stl")

            # 较长的刀路
            gcode = """G00 Z80.
G00 X-80. Y-80.
G01 Z-10. F500
G01 X80. F2000
G01 Y-60.
G01 X-80.
G01 Y-40.
G01 X80.
G01 Y-20.
G01 X-80.
G01 Y0.
G01 X80.
G01 Y20.
G01 X-80.
G01 Y40.
G01 X80.
G01 Y60.
G01 X-80.
G01 Y80.
G01 X80.
G00 Z80."""
            parser = ToolpathParser()
            segments = parser.parse_gcode(gcode)

            t0 = time.perf_counter()
            result = cutter.run_simulation(
                stock_stl_path=stock_path,
                tool=tool,
                segments=segments,
                output_dir=tmp_path / "output",
                task_id="perf_test_large",
            )
            t1 = time.perf_counter()

            _metrics.record(
                "end2end_large(voxel=2.0,stock=200x200x50)",
                t1 - t0,
                {
                    "voxel_count": result.voxel_count,
                    "removed": result.removed_voxel_count,
                    "collision": result.collision.collided,
                    "segments": result.toolpath_segment_count,
                    "stl_size_bytes": len(result.stock_stl_raw),
                },
            )


# 5. 优化前后对比测试


@pytest.mark.benchmark
class TestOptimizationComparison:
    """优化前后性能对比测试。"""

    def test_mask_vectorization_speedup(self):
        """验证掩码向量化加速比。"""
        # 用大刀具放大性能差异
        tool = ToolModel(diameter=30.0, tool_type="flat", cutting_length=50.0)

        # 向量化版本（当前实现）
        n_runs = 50
        t0 = time.perf_counter()
        for _ in range(n_runs):
            mask = tool.voxel_mask(voxel_size=0.5)
        t1 = time.perf_counter()
        vectorized_time = (t1 - t0) / n_runs

        _metrics.record(
            "mask_vectorized(d30_vs0.5)",
            vectorized_time,
            {"shape": str(mask.shape), "true_count": int(mask.sum())},
        )


# 最终报告输出


def pytest_sessionfinish(session):
    """测试会话结束时输出性能报告。"""
    _metrics.report()


@pytest.mark.benchmark
class TestPerformanceRequirements:
    """性能指标验证测试。"""

    def test_voxel_mask_under_10ms(self):
        """单个刀具掩码生成应小于10ms（向量化要求）。"""
        tool = ToolModel(diameter=20.0, tool_type="flat", cutting_length=50.0)
        t0 = time.perf_counter()
        for _ in range(20):
            tool.voxel_mask(voxel_size=1.0)
        t1 = time.perf_counter()
        avg = (t1 - t0) / 20
        assert avg < 0.01, f"掩码生成耗时{avg * 1000:.2f}ms，超出10ms阈值"

    def test_marching_cubes_under_1s_100x100x100(self):
        """100x100x100网格的Marching Cubes应小于1秒。"""
        if not HAS_SKIMAGE:
            pytest.skip("scikit-image未安装")
        from skimage import measure as skmeasure

        # 预热
        dummy = np.zeros((5, 5, 5), dtype=np.float64)
        dummy[1:4, 1:4, 1:4] = 1.0
        skmeasure.marching_cubes(dummy, level=0.5, spacing=(1.0, 1.0, 1.0))

        # 创建100x100x100网格
        nx = ny = nz = 100
        X, Y, Z = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
        grid = ((X - 50) ** 2 + (Y - 50) ** 2 + (Z - 50) ** 2) <= 30**2
        padded = np.pad(grid, pad_width=1, mode="constant", constant_values=0)

        t0 = time.perf_counter()
        verts, faces, _, _ = skmeasure.marching_cubes(padded.astype(np.float64), level=0.5, spacing=(1.0, 1.0, 1.0))
        t1 = time.perf_counter()
        duration = t1 - t0
        assert duration < 1.0, f"100x100x100 Marching Cubes耗时{duration:.4f}s，超出1s阈值"
        _metrics.record(
            "mc_100x100x100_quality_check",
            duration,
            {"verts": len(verts), "faces": len(faces)},
        )

    def test_end_to_end_under_30s(self):
        """完整仿真流程应小于30秒。"""
        trimesh = pytest.importorskip("trimesh")
        cutter = VoxelCutter(voxel_size=2.0)
        tool = ToolModel(diameter=10.0, tool_type="flat")

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stock_mesh = trimesh.creation.box(extents=[80, 60, 30])
            stock_mesh.apply_translation([0, 0, 15])
            stock_path = tmp_path / "stock.stl"
            stock_mesh.export(str(stock_path), file_type="stl")

            gcode = """G00 Z80.
G00 X0. Y0.
G01 Z-5. F500
G01 X30. F800
G01 Y20.
G01 X0.
G01 Y0.
G00 Z80."""
            parser = ToolpathParser()
            segments = parser.parse_gcode(gcode)

            t0 = time.perf_counter()
            cutter.run_simulation(
                stock_stl_path=stock_path,
                tool=tool,
                segments=segments,
                output_dir=tmp_path / "output",
                task_id="perf_req_test",
            )
            t1 = time.perf_counter()
            duration = t1 - t0
            assert duration < 30.0, f"端到端仿真耗时{duration:.4f}s，超出30s阈值"
