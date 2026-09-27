//! 统一错误类型与 Result 别名。
//!
//! 所有对外 API 都返回 `Result<T, ComputeError>`，避免 panic 上浮污染上层调用栈。
//! 错误可通过 `thiserror` 自动派生 `Display` 与 `Error` trait。

use thiserror::Error;

/// 计算引擎统一错误类型。
#[derive(Debug, Error)]
pub enum ComputeError {
    /// 体素网格维度非法（任意维度为 0）。
    #[error("invalid voxel grid shape: dimensions must be > 0, got [{nx}, {ny}, {nz}]")]
    InvalidShape { nx: usize, ny: usize, nz: usize },

    /// 体素尺寸非法（≤ 0 或非有限）。
    #[error("invalid voxel_size: {voxel_size} (must be positive finite)")]
    InvalidVoxelSize { voxel_size: f64 },

    /// 工具几何参数非法。
    #[error("invalid tool geometry: {message}")]
    InvalidTool { message: String },

    /// 输入数组形状不匹配。
    #[error("shape mismatch: expected {expected}, got {actual}")]
    ShapeMismatch { expected: String, actual: String },

    /// 内部缓冲区容量不足（理论上不应触发；如出现表明上游数据流异常）。
    #[error("internal buffer overflow: required {required} > capacity {capacity}")]
    BufferOverflow { required: usize, capacity: usize },
}

/// Result 简写。
pub type ComputeResult<T> = Result<T, ComputeError>;
