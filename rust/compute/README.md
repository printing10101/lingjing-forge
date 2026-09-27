# 灵境制造 - 体素仿真计算引擎（Rust + PyO3）

本目录为灵境制造（**LingJing Manufacturing**）系统的核心计算引擎实现。

## 模块职责

- **crates/core** — 纯 Rust 核心库，体素网格、刀具几何、切削算法与数据结构，无任何 Python 依赖。
- **crates/pyo3_bindings** — 通过 PyO3 暴露核心算法为 Python 扩展模块 `compute._native`。
- **python/compute/** — 纯 Python 适配包，向上层 (`python/app/simulation/rust_engine.py`) 提供统一接口。

## 构建与测试

```bash
# 安装 maturin
pip install maturin

# 开发模式构建（自动注册到当前 Python 环境）
maturin develop --release

# 运行 Rust 单元测试
cargo test -p compute-core -p compute-pyo3-bindings

# 运行基准测试
cargo bench -p compute-core
```

## 设计目标

- **性能**：将 Python 三重循环 `O(N*V*T)` 替换为基于向量化（SIMD 风格块运算）的 Rust 实现，
  在 100×100×100 网格上预期取得 ≥ 50% 的加速比。
- **正确性**：与现有 Python `VoxelCutter` 在相同的输入下应得到位级一致的体素差异。
- **可回退**：当 Rust 模块不可用时，Python 适配层自动无缝回退到原 NumPy 实现，上层零修改。
- **可测试**：Rust 端单测覆盖核心路径（≥80%），Python 端覆盖 Rust 路径与回退路径。

## API 入口

```python
from compute import voxel_cutter
import numpy as np

# 体素化切削
removed = voxel_cutter.apply_tool_mask(
    voxel_grid, tool_mask, points, bbox_min, voxel_size, padding,
)

# 构建刀具掩码
mask = voxel_cutter.build_tool_mask(
    tool_type="ball", diameter=10.0, corner_radius=5.0,
    cutting_length=50.0, voxel_size=1.0,
)
```

详细接口见 `python/compute/voxel_cutter.py` 与 `crates/pyo3_bindings/src/lib.rs`。
