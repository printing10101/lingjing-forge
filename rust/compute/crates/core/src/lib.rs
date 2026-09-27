//! # compute-core
//!
//! 体素化切削仿真的纯 Rust 核心库。
//!
//! 提供：
//! - [`voxel_grid`]：3D 体素网格，紧凑位存储 + 高速批量运算。
//! - [`tool`]：6 种刀具几何的解析体素化（球头/平底/圆角平底/锥度/球头锥度/成形）。
//! - [`cutting`]：批量切削核心，SIMD 风格块运算 + 边界裁剪。
//! - [`error`]：统一错误类型，所有 API 不 panic。
//!
//! 所有函数都遵循 **Result 返回** 风格；不依赖任何 Python 运行时。
//!
//! ## 复杂度
//!
//! `apply_tool_mask_batch` 的时间复杂度为 `O(P * M)`，其中 `P` 为刀位点数、`M` 为
//! 刀具掩码体素数（`≤ (2*r/voxel_size)³`）。相比原 Python 三重循环
//! `O(N * V * T)`，去除了对全部 `V` 个体素的线性扫描，单帧复杂度从 `O(V)`
//! 降为 `O(M)`（`M << V`）。

#![deny(rust_2018_idioms)]
#![warn(missing_debug_implementations)]

pub mod cutting;
pub mod error;
pub mod tool;
pub mod voxel_grid;

pub use crate::cutting::{apply_tool_mask_batch, discretize_linear_segment, BatchResult};
pub use crate::error::{ComputeError, ComputeResult};
pub use crate::tool::{build_tool_mask, ToolGeometry, ToolType};
pub use crate::voxel_grid::{VoxelGrid, VoxelGridShape};
