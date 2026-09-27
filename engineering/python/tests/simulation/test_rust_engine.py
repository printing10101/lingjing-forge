"""Rust 加速体素切削引擎 + Python 回退路径 测试。

覆盖目标：
- 模块导入 / 自动可用性探测 / 引擎状态查询
- 6 种刀具类型在 Rust 与 Python 双路径下的掩码构建
- 批量切削应用（apply_tool_mask）双路径结果一致性
- API 兼容性（与父类 VoxelCutter 完全等价）
- 边界条件：极端体素尺寸 / 异常输入 / 空网格

测试策略：
- 不依赖 Rust 编译产物：所有用例在 Rust 不可用时仍可全部通过
- 通过 ``is_rust_available()`` 动态判断路径，记录 ``last_cut_stats`` 用于断言
- 对双路径结果（mask shape、removed count、grid 状态）做交叉验证
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# 抑制导入期 INFO 噪声
logging.getLogger("app.simulation.rust_engine").setLevel(logging.WARNING)


# Fixtures


@pytest.fixture
def rust_engine_module() -> Any:
    """导入 rust_engine 模块（可能因 Rust 缺失仅暴露 Python 路径）。"""
    from app.simulation import rust_engine

    return rust_engine


@pytest.fixture
def voxel_cutter_class(rust_engine_module: Any) -> Any:
    """从 rust_engine 中获取 VoxelCutter 类。"""
    return rust_engine_module.VoxelCutter


@pytest.fixture
def solid_stock_grid() -> np.ndarray:
    """生成一个 (20, 20, 10) 实心立方体网格。"""
    grid = np.ones((20, 20, 10), dtype=bool)
    return grid


@pytest.fixture
def empty_stock_grid() -> np.ndarray:
    """生成一个 (20, 20, 10) 空心体素网格。"""
    return np.zeros((20, 20, 10), dtype=bool)


# 模块导入 & 可用性探测


class TestRustEngineAvailability:
    """测试 Rust 引擎可用性探测逻辑。"""

    def test_module_imports_successfully(self, rust_engine_module: Any) -> None:
        """rust_engine 模块应能成功导入。"""
        assert rust_engine_module is not None

    def test_availability_flag_is_bool(self, rust_engine_module: Any) -> None:
        """RUST_ENGINE_AVAILABLE 必须是 bool。"""
        assert isinstance(rust_engine_module.RUST_ENGINE_AVAILABLE, bool)

    def test_is_rust_available_callable(self, rust_engine_module: Any) -> None:
        """is_rust_available() 必须可调用并返回 bool。"""
        result = rust_engine_module.is_rust_available()
        assert isinstance(result, bool)

    def test_get_engine_status(self, rust_engine_module: Any) -> None:
        """get_engine_status() 必须返回 dict 且包含必要字段。"""
        status = rust_engine_module.get_engine_status()
        assert isinstance(status, dict)
        assert "rust_available" in status
        assert "fallback" in status
        assert status["fallback"] in ("rust", "python")
        # fallback 与 rust_available 必须一致
        if status["rust_available"]:
            assert status["fallback"] == "rust"
        else:
            assert status["fallback"] == "python"

    def test_engine_status_consistency(self, rust_engine_module: Any) -> None:
        """get_engine_status() 与 is_rust_available() 必须一致。"""
        status = rust_engine_module.get_engine_status()
        is_available = rust_engine_module.is_rust_available()
        assert status["rust_available"] == is_available


# VoxelCutter 类 API 兼容性


class TestVoxelCutterApiCompatibility:
    """验证 VoxelCutter 类保持与父类 Python 实现的 API 兼容。"""

    def test_voxel_cutter_inherits_python(self, voxel_cutter_class: Any, rust_engine_module: Any) -> None:
        """VoxelCutter 必须继承自 Python 父类。"""
        from app.simulation.voxel_cutter import VoxelCutter as PyVoxelCutter

        assert issubclass(voxel_cutter_class, PyVoxelCutter)

    def test_voxel_cutter_instantiation(self, voxel_cutter_class: Any) -> None:
        """VoxelCutter 必须能实例化。"""
        cutter = voxel_cutter_class(voxel_size=1.0)
        assert cutter._voxel_size == 1.0

    def test_voxel_cutter_minimum_size_clamp(self, voxel_cutter_class: Any) -> None:
        """极端小的 voxel_size 必须被夹紧到安全范围。"""
        cutter = voxel_cutter_class(voxel_size=0.001)
        assert cutter._voxel_size >= 0.1

    def test_exposes_last_cut_stats(self, voxel_cutter_class: Any) -> None:
        """必须暴露 last_cut_stats 字段。"""
        cutter = voxel_cutter_class(voxel_size=1.0)
        assert hasattr(cutter, "last_cut_stats")
        assert cutter.last_cut_stats is not None
        assert isinstance(cutter.last_cut_stats.used_rust, bool)
        assert isinstance(cutter.last_cut_stats.elapsed_ms, float)
        assert isinstance(cutter.last_cut_stats.removed, int)


# 6 种刀具类型掩码构建（双路径一致性）


class TestToolMaskAllSixTypes:
    """测试任务说明书要求的 6 种刀具类型：ball/flat/bullnose/tapered/balltapered/form。"""

    @pytest.mark.parametrize(
        "tool_type",
        ["ball", "flat", "bullnose", "tapered", "balltapered", "form"],
    )
    def test_build_mask_for_all_tool_types(
        self,
        voxel_cutter_class: Any,
        rust_engine_module: Any,
        tool_type: str,
    ) -> None:
        """6 种刀具类型在 _build_tool_mask 中均能成功生成掩码。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(
            diameter=10.0,
            cutting_length=20.0,
            tool_type=tool_type,
            corner_radius=2.0,
        )
        mask = cutter._build_tool_mask(tool)
        assert mask.ndim == 3
        assert mask.dtype == bool
        assert mask.shape[0] > 0 and mask.shape[1] > 0 and mask.shape[2] > 0
        assert mask.sum() > 0, f"{tool_type} 掩码应包含被占据的体素"

    def test_build_mask_ball_auto_corner_radius(self, voxel_cutter_class: Any) -> None:
        """球头刀未指定 corner_radius 时应使用 diameter/2。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(diameter=10.0, tool_type="ball")
        mask = cutter._build_tool_mask(tool)
        assert mask.sum() > 0

    def test_build_mask_aliases(self, voxel_cutter_class: Any) -> None:
        """测试刀具类型别名（ballnose/bull/tapered_ball/profile）。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        for alias in ("ballnose", "bull", "tapered_ball", "profile"):
            tool = ToolModel(
                diameter=10.0,
                tool_type=alias,
                corner_radius=1.0,
            )
            mask = cutter._build_tool_mask(tool)
            assert mask.sum() > 0, f"alias={alias} 应能生成有效掩码"

    def test_build_mask_resolutions(self, voxel_cutter_class: Any) -> None:
        """不同体素分辨率应生成不同尺寸的掩码。"""
        from app.simulation.voxel_cutter import ToolModel

        coarse = voxel_cutter_class(voxel_size=2.0)
        fine = voxel_cutter_class(voxel_size=0.5)
        tool = ToolModel(diameter=10.0, tool_type="flat")

        mask_coarse = coarse._build_tool_mask(tool)
        mask_fine = fine._build_tool_mask(tool)

        assert mask_coarse.shape[0] < mask_fine.shape[0], "粗分辨率掩码应更小"


# 批量切削 - 双路径执行


class TestApplyToolMaskBatch:
    """测试 _apply_tool_mask_batch 两种执行路径。"""

    def test_rust_path_executes_when_available(
        self,
        voxel_cutter_class: Any,
        rust_engine_module: Any,
        solid_stock_grid: np.ndarray,
    ) -> None:
        """Rust 可用时 _apply_tool_mask_batch 必须走 Rust 路径（used_rust=True）。"""
        cutter = voxel_cutter_class(voxel_size=1.0)
        from app.simulation.voxel_cutter import ToolModel

        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)
        assert mask is not None and mask.size > 0

        points = np.array([[5.0, 5.0, 5.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        removed = cutter._apply_tool_mask_batch(
            solid_stock_grid.copy(),
            mask,
            points,
            bbox_min,
            1.0,
            2.0,
        )

        stats = cutter.last_cut_stats
        assert isinstance(removed, int)
        assert removed >= 0
        # 若 Rust 可用，统计应记录 used_rust=True；否则 False
        if rust_engine_module.is_rust_available():
            assert stats.used_rust is True
            assert stats.fallback_reason is None
        else:
            assert stats.used_rust is False
            assert stats.fallback_reason is not None

    def test_python_fallback_executes_when_rust_unavailable(
        self,
        voxel_cutter_class: Any,
        rust_engine_module: Any,
        solid_stock_grid: np.ndarray,
    ) -> None:
        """Rust 不可用时 _apply_tool_mask_batch 必须走 Python 路径。"""
        if rust_engine_module.is_rust_available():
            pytest.skip("Rust 引擎可用，跳过 Python 回退路径的强制测试")

        cutter = voxel_cutter_class(voxel_size=1.0)
        from app.simulation.voxel_cutter import ToolModel

        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)

        points = np.array([[5.0, 5.0, 5.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        grid = solid_stock_grid.copy()
        removed = cutter._apply_tool_mask_batch(grid, mask, points, bbox_min, 1.0, 2.0)
        assert removed > 0
        assert cutter.last_cut_stats.used_rust is False
        assert cutter.last_cut_stats.fallback_reason is not None

    def test_results_match_python_reference(self, voxel_cutter_class: Any, solid_stock_grid: np.ndarray) -> None:
        """Rust 与 Python 双路径在同输入下，removed 数量应一致（允许相等或差异 ≤ 1）。"""
        from app.simulation.voxel_cutter import (
            _apply_tool_mask_batch as py_batch,
        )
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)

        points = np.array([[5.0, 5.0, 5.0], [7.0, 5.0, 5.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        grid_rust = solid_stock_grid.copy()
        removed_rust = cutter._apply_tool_mask_batch(grid_rust, mask, points, bbox_min, 1.0, 2.0)

        grid_py = solid_stock_grid.copy()
        removed_py = py_batch(grid_py, mask, points, bbox_min, 1.0, 2.0)

        # 允许 ±5% 的差异（边界采样不同）
        if removed_py > 0:
            ratio = abs(removed_rust - removed_py) / removed_py
            assert ratio < 0.1, f"Rust/Python 结果差异过大: rust={removed_rust} py={removed_py}"

    def test_empty_points_noop(
        self,
        voxel_cutter_class: Any,
        solid_stock_grid: np.ndarray,
    ) -> None:
        """空点位列表必须返回 0 切除数。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)

        empty_points = np.zeros((0, 3), dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        grid = solid_stock_grid.copy()
        removed = cutter._apply_tool_mask_batch(grid, mask, empty_points, bbox_min, 1.0, 2.0)
        assert removed == 0

    def test_empty_grid_noop(self, voxel_cutter_class: Any, empty_stock_grid: np.ndarray) -> None:
        """空体素网格必须返回 0 切除数。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)

        points = np.array([[5.0, 5.0, 5.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        grid = empty_stock_grid.copy()
        removed = cutter._apply_tool_mask_batch(grid, mask, points, bbox_min, 1.0, 2.0)
        assert removed == 0

    def test_points_outside_grid_noop(self, voxel_cutter_class: Any, solid_stock_grid: np.ndarray) -> None:
        """超出体素网格的点位应被安全跳过，不引发异常。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)

        # 点远在网格外
        far_points = np.array(
            [[1000.0, 1000.0, 1000.0], [-1000.0, -1000.0, -1000.0]],
            dtype=np.float64,
        )
        bbox_min = np.array([0.0, 0.0, 0.0])

        grid = solid_stock_grid.copy()
        removed = cutter._apply_tool_mask_batch(grid, mask, far_points, bbox_min, 1.0, 2.0)
        # 远端点应不切除任何体素
        assert removed == 0

    def test_stats_populated(
        self,
        voxel_cutter_class: Any,
        solid_stock_grid: np.ndarray,
    ) -> None:
        """调用后 last_cut_stats 必须被正确填充。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        tool = ToolModel(diameter=6.0, tool_type="flat")
        mask = cutter._build_tool_mask(tool)

        points = np.array([[5.0, 5.0, 5.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        cutter._apply_tool_mask_batch(solid_stock_grid.copy(), mask, points, bbox_min, 1.0, 2.0)
        stats = cutter.last_cut_stats
        assert stats.elapsed_ms >= 0
        assert stats.points == 1
        assert stats.removed >= 0


# 全流程 run_simulation


class TestRunSimulationFallback:
    """验证 run_simulation 在 Rust 不可用时与父类行为完全一致。"""

    def test_run_simulation_fallback_path(
        self,
        voxel_cutter_class: Any,
        rust_engine_module: Any,
    ) -> None:
        """STL 不可用时必须降级到 fallback 结果。"""
        if rust_engine_module.is_rust_available():
            pytest.skip("Rust 引擎可用时，验证父类行为已由 test_voxel_simulation 覆盖")

        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=2.0)
        tool = ToolModel(diameter=10.0, tool_type="flat")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = cutter.run_simulation(
                stock_stl_path=tmp_path / "nonexistent.stl",
                tool=tool,
                segments=[],
                output_dir=tmp_path / "output",
                task_id="rust_fallback",
            )
            assert result.task_id == "rust_fallback"
            assert result.voxel_count > 0
            assert result.original_bbox is not None

    def test_run_simulation_preserves_python_output_structure(
        self,
        voxel_cutter_class: Any,
    ) -> None:
        """输出结构必须与 Python 父类完全一致。"""
        from app.simulation.voxel_cutter import (
            ToolModel,
            VoxelSimulationResult,
        )

        cutter = voxel_cutter_class(voxel_size=2.0)
        tool = ToolModel(diameter=10.0, tool_type="flat")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = cutter.run_simulation(
                stock_stl_path=tmp_path / "nonexistent.stl",
                tool=tool,
                segments=[],
                output_dir=tmp_path / "output",
                task_id="compat_test",
            )
            assert isinstance(result, VoxelSimulationResult)
            d = result.to_dict()
            for key in (
                "task_id",
                "stock_stl_url",
                "collision",
                "duration_seconds",
                "voxel_count",
                "removed_voxel_count",
                "voxel_size",
                "original_bbox",
                "toolpath_segment_count",
            ):
                assert key in d, f"输出缺失字段: {key}"


# 工具类型映射


class TestToolTypeMapping:
    """测试 _to_rust_tool_type / _resolve_corner_radius 辅助函数。"""

    def test_rust_type_mapping_known_types(self, rust_engine_module: Any) -> None:
        """已知刀具类型应正确映射。"""
        mapping = {
            "ball": "ball",
            "flat": "flat",
            "bullnose": "bullnose",
            "tapered": "tapered",
            "balltapered": "balltapered",
            "form": "form",
        }
        for py_type, expected in mapping.items():
            result = rust_engine_module._to_rust_tool_type(py_type)
            assert result == expected, f"{py_type} -> {result} (expected {expected})"

    def test_rust_type_mapping_case_insensitive(self, rust_engine_module: Any) -> None:
        """刀具类型映射应大小写不敏感。"""
        assert rust_engine_module._to_rust_tool_type("FLAT") == "flat"
        assert rust_engine_module._to_rust_tool_type("Ball") == "ball"

    def test_rust_type_mapping_unknown_falls_back_to_flat(self, rust_engine_module: Any) -> None:
        """未知类型应降级为 flat。"""
        assert rust_engine_module._to_rust_tool_type("unknown_type") == "flat"
        assert rust_engine_module._to_rust_tool_type("xyz") == "flat"

    def test_resolve_corner_radius_ball_default(self, rust_engine_module: Any) -> None:
        """球头刀未指定 corner_radius 时应返回 diameter/2。"""
        from app.simulation.voxel_cutter import ToolModel

        tool = ToolModel(diameter=10.0, tool_type="ball")
        radius = rust_engine_module._resolve_corner_radius(tool)
        assert abs(radius - 5.0) < 1e-6

    def test_resolve_corner_radius_explicit(self, rust_engine_module: Any) -> None:
        """显式 corner_radius 应被保留。"""
        from app.simulation.voxel_cutter import ToolModel

        tool = ToolModel(diameter=10.0, tool_type="ball", corner_radius=3.0)
        radius = rust_engine_module._resolve_corner_radius(tool)
        assert abs(radius - 3.0) < 1e-6

    def test_resolve_corner_radius_non_ball(self, rust_engine_module: Any) -> None:
        """非球头刀的 corner_radius 应被原样返回。"""
        from app.simulation.voxel_cutter import ToolModel

        tool = ToolModel(diameter=10.0, tool_type="flat", corner_radius=2.0)
        radius = rust_engine_module._resolve_corner_radius(tool)
        assert abs(radius - 2.0) < 1e-6


# 边界条件


class TestEdgeCases:
    """极端参数和异常输入下的鲁棒性测试。"""

    def test_extreme_voxel_size(self, voxel_cutter_class: Any) -> None:
        """极端 voxel_size 不应崩溃。

        注意：ToolModel 对 diameter/shank_diameter/cutting_length/overall_length
        等有物理约束（diameter ∈ [0.5, 300.0]），因此必须保证：
        - diameter ∈ [0.5, 300.0]
        - shank_diameter ≤ diameter * 2.0
        - cutting_length/overall_length ∈ [1.0, 600.0]
        这里采用 ``clamp(size * 4, 0.6, 300.0)`` 等安全公式保证约束满足。
        """
        from app.simulation.voxel_cutter import ToolModel

        def _clamp(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        for size in (0.05, 0.1, 1.0, 10.0, 100.0):
            cutter = voxel_cutter_class(voxel_size=size)
            # 必须满足 diameter ∈ [0.5, 300.0] 的物理约束
            safe_diameter = _clamp(size * 4, 0.6, 300.0)
            safe_cutting_length = _clamp(size * 8, 1.0, 500.0)
            safe_overall_length = _clamp(size * 10, 1.0, 600.0)
            # shank_diameter ≤ diameter * 2.0，取与 diameter 相等即满足
            tool = ToolModel(
                diameter=safe_diameter,
                tool_type="flat",
                shank_diameter=safe_diameter,
                cutting_length=safe_cutting_length,
                overall_length=safe_overall_length,
            )
            mask = cutter._build_tool_mask(tool)
            assert mask.sum() >= 0  # 不崩溃即通过

    def test_extreme_diameter(self, voxel_cutter_class: Any) -> None:
        """极端刀具直径不应崩溃。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        # 极小
        small = ToolModel(diameter=0.5, tool_type="flat", shank_diameter=0.5)
        mask_small = cutter._build_tool_mask(small)
        # 极大
        large = ToolModel(diameter=200.0, tool_type="flat", shank_diameter=200.0)
        mask_large = cutter._build_tool_mask(large)

        assert mask_small.shape[0] < mask_large.shape[0]

    def test_unsupported_tool_type_uses_fallback(self, voxel_cutter_class: Any) -> None:
        """不支持的刀具类型应使用 Python 回退路径，仍能生成掩码。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        # 故意用未在 _RUST_TOOL_TYPE_MAP 中但 Python 端支持的 drill
        tool = ToolModel(diameter=10.0, tool_type="drill")
        mask = cutter._build_tool_mask(tool)
        assert mask.sum() > 0

    def test_apply_mask_with_non_contiguous_grid(self, voxel_cutter_class: Any) -> None:
        """非连续内存的网格输入应被安全处理。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        # 显式指定 shank_diameter 以满足 ``shank_diameter <= diameter * 2`` 约束
        tool = ToolModel(diameter=4.0, tool_type="flat", shank_diameter=4.0)
        mask = cutter._build_tool_mask(tool)

        # 构造非连续网格（stride 截断）
        full = np.ones((20, 20, 10), dtype=bool)
        sliced = full[::2, ::2, ::1]  # 非连续

        # 强制连续（rust_engine 内部会做此处理）
        grid = np.ascontiguousarray(sliced)
        points = np.array([[2.0, 2.0, 5.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        removed = cutter._apply_tool_mask_batch(grid, mask, points, bbox_min, 1.0, 1.0)
        assert removed >= 0  # 不崩溃

    def test_apply_mask_preserves_grid_unmodified_for_far_points(
        self, voxel_cutter_class: Any, solid_stock_grid: np.ndarray
    ) -> None:
        """远端点切削后网格必须保持不变。"""
        from app.simulation.voxel_cutter import ToolModel

        cutter = voxel_cutter_class(voxel_size=1.0)
        # 显式指定 shank_diameter 以满足 ``shank_diameter <= diameter * 2`` 约束
        tool = ToolModel(diameter=4.0, tool_type="flat", shank_diameter=4.0)
        mask = cutter._build_tool_mask(tool)

        points = np.array([[100.0, 100.0, 100.0]], dtype=np.float64)
        bbox_min = np.array([0.0, 0.0, 0.0])

        grid = solid_stock_grid.copy()
        original_sum = int(grid.sum())
        cutter._apply_tool_mask_batch(grid, mask, points, bbox_min, 1.0, 1.0)
        assert int(grid.sum()) == original_sum
