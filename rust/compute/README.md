# 灵境制造 - 体素仿真计算引擎（Rust + PyO3）

本目录为灵境制造（**LingJing Manufacturing**）系统的核心计算引擎实现。

## 模块职责

- **crates/core** — 纯 Rust 核心库，体素网格、刀具几何、切削算法与数据结构，无任何 Python 依赖。
- **crates/pyo3_bindings** — 通过 PyO3 暴露核心算法为 Python 扩展模块 `compute._native`。
- **compute/** — maturin 混合包，导出 `compute._native` 中的版本和 `voxel_cutter` 接口。
- **engineering/python/app/simulation/rust_engine.py**（仓库根路径）— 工程侧适配与 NumPy 回退。

## 构建与测试

```bash
# 安装 maturin
pip install maturin

# 开发模式构建（自动注册到当前 Python 环境）
maturin develop --release

# 运行纯 Rust 单元测试
cargo test -p compute-core

# 编译并验证 Python 绑定（从本目录执行）
cargo build -p compute-pyo3-bindings
python tests/test_native_contract.py

# 运行基准测试
cargo bench -p compute-core
```

Windows 下应将 `PYO3_PYTHON` 指向桌面运行时的 Python 3.12：
`$env:PYO3_PYTHON="D:\灵境制造\engineering\python\desktop_runtime\runtime\python.exe"`。
运行原生边界测试时也使用该解释器；无需安装或替换当前环境中的 `compute` 包。
发布时仍需通过 maturin 重新构建扩展并打入桌面运行时，单独通过 Rust 单测不代表绑定可用。

## 设计目标

- **性能**：将 Python 三重循环 `O(N*V*T)` 替换为基于向量化（SIMD 风格块运算）的 Rust 实现，
  在 100×100×100 网格上预期取得 ≥ 50% 的加速比。
- **正确性**：与现有 Python `VoxelCutter` 在相同的输入下应得到位级一致的体素差异。
- **可回退**：当 Rust 模块不可用时，Python 适配层自动无缝回退到原 NumPy 实现，上层零修改。
- **可测试**：Rust 单测覆盖核心路径；`tests/test_native_contract.py` 直接加载编译产物，验证实际导出名、NumPy 写回、动态轮廓和错误映射。覆盖率须单独测量，测试通过不等于达到覆盖率门槛。

## API 入口

```python
from compute import voxel_cutter
import numpy as np

# 体素化切削
removed = voxel_cutter.apply_tool_mask(
    voxel_grid, tool_mask, points, bbox_min, voxel_size, padding,
)

# 构建刀具掩码
mask, info = voxel_cutter.build_tool_mask(
    tool_type="ball", diameter=10.0, corner_radius=5.0,
    cutting_length=50.0, voxel_size=1.0,
)
```

详细接口见 `crates/pyo3_bindings/src/lib.rs`。掩码为 `(nx, ny, nz)`，
XY 按刀具半径、Z 按完整切削刃长分配，刀尖位于各维度中心；长刀具的掩码不一定是立方体。
