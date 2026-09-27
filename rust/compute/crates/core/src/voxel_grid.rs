//! 3D 体素网格。
//!
//! 内部使用位压缩存储（`u64` 位图），相比 `Vec<bool>` 节省 8× 内存，
//! 批量运算时位运算可被现代 CPU 自动向量化（AVX2 / NEON）。
//!
//! ## 设计目标
//!
//! - **零拷贝切片访问**：`as_bitslice()` / `as_mut_bitslice()` 直接返回 `&[u64]`，
//!   可安全零拷贝包装为 `numpy.ndarray` (PyO3 端)。
//! - **行主序** `(x, y, z)`，与 NumPy 默认布局兼容。
//! - **批量操作**：`remove_if_mask_at()` 一次性应用一个刀具掩码到目标子区域。

use crate::error::{ComputeError, ComputeResult};

/// 体素网格的形状。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VoxelGridShape {
    pub nx: usize,
    pub ny: usize,
    pub nz: usize,
}

impl VoxelGridShape {
    /// 构造新形状，校验维度合法。
    pub fn new(nx: usize, ny: usize, nz: usize) -> ComputeResult<Self> {
        if nx == 0 || ny == 0 || nz == 0 {
            return Err(ComputeError::InvalidShape { nx, ny, nz });
        }
        Ok(Self { nx, ny, nz })
    }

    /// 总元素数。
    #[inline]
    pub const fn total(&self) -> usize {
        self.nx * self.ny * self.nz
    }

    /// `u64` 位图所需的 word 数。
    #[inline]
    pub const fn word_count(&self) -> usize {
        (self.total() + 63) / 64
    }
}

/// 3D 体素网格（紧凑位存储）。
///
/// 行主序布局：`index = (x * ny + y) * nz + z`。
#[derive(Debug, Clone)]
pub struct VoxelGrid {
    shape: VoxelGridShape,
    bits: Vec<u64>,
}

impl VoxelGrid {
    /// 创建全 `false`（空）网格。
    pub fn new(shape: VoxelGridShape) -> Self {
        let n_words = shape.word_count();
        Self {
            shape,
            bits: vec![0u64; n_words],
        }
    }

    /// 从 `bool` 切片构造（按行主序展平）。
    pub fn from_bool_slice(shape: VoxelGridShape, data: &[bool]) -> ComputeResult<Self> {
        let total = shape.total();
        if data.len() != total {
            return Err(ComputeError::ShapeMismatch {
                expected: format!("flat length {}", total),
                actual: format!("flat length {}", data.len()),
            });
        }
        let mut grid = Self::new(shape);
        for (i, &b) in data.iter().enumerate() {
            if b {
                grid.set_linear(i, true);
            }
        }
        Ok(grid)
    }

    /// 形状。
    #[inline]
    pub const fn shape(&self) -> VoxelGridShape {
        self.shape
    }

    /// 位图原始数据。
    #[inline]
    pub fn bits(&self) -> &[u64] {
        &self.bits
    }

    /// 位图原始数据（可变）。
    #[inline]
    pub fn bits_mut(&mut self) -> &mut [u64] {
        &mut self.bits
    }

    /// 计算当前为 `true` 的体素总数。
    pub fn count_true(&self) -> usize {
        self.bits.iter().map(|w| w.count_ones() as usize).sum()
    }

    /// 线性索引转 `(x, y, z)`。
    #[inline]
    pub fn linear_to_xyz(&self, idx: usize) -> (usize, usize, usize) {
        let z = idx % self.shape.nz;
        let yz = idx / self.shape.nz;
        let y = yz % self.shape.ny;
        let x = yz / self.shape.ny;
        (x, y, z)
    }

    /// `(x, y, z)` 转线性索引。
    #[inline]
    pub const fn xyz_to_linear(&self, x: usize, y: usize, z: usize) -> usize {
        (x * self.shape.ny + y) * self.shape.nz + z
    }

    /// 读取某线性位置（带越界保护，越界返回 `false`）。
    #[inline]
    pub fn get_linear(&self, idx: usize) -> bool {
        if idx >= self.shape.total() {
            return false;
        }
        (self.bits[idx >> 6] >> (idx & 63)) & 1 == 1
    }

    /// 写入某线性位置。
    #[inline]
    pub fn set_linear(&mut self, idx: usize, val: bool) {
        let word = idx >> 6;
        let bit = idx & 63;
        if val {
            self.bits[word] |= 1u64 << bit;
        } else {
            self.bits[word] &= !(1u64 << bit);
        }
    }

    /// 读取 `(x, y,, z)`。
    #[inline]
    pub fn get(&self, x: usize, y: usize, z: usize) -> bool {
        self.get_linear(self.xyz_to_linear(x, y, z))
    }

    /// 写入 `(x, y, z)`。
    #[inline]
    pub fn set(&mut self, x: usize, y: usize, z: usize, val: bool) {
        self.set_linear(self.xyz_to_linear(x, y, z), val)
    }

    /// 计算在 `(gx_min..gx_max, gy_min..gy_max, gz_min..gz_max)` 子区域内
    /// `true` 的体素数。
    ///
    /// 该函数用于批量切削前后状态对比。
    pub fn count_in_box(
        &self,
        gx_min: usize,
        gy_min: usize,
        gz_min: usize,
        gx_max: usize,
        gy_max: usize,
        gz_max: usize,
    ) -> usize {
        let mut count = 0usize;
        for x in gx_min..gx_max {
            for y in gy_min..gy_max {
                let base = self.xyz_to_linear(x, y, gz_min);
                for z in gz_min..gz_max {
                    let idx = base + z - gz_min;
                    if (self.bits[idx >> 6] >> (idx & 63)) & 1 == 1 {
                        count += 1;
                    }
                }
            }
        }
        count
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shape_ok() -> VoxelGridShape {
        VoxelGridShape::new(4, 5, 6).unwrap()
    }

    #[test]
    fn new_grid_is_all_false() {
        let g = VoxelGrid::new(shape_ok());
        assert_eq!(g.count_true(), 0);
        assert_eq!(g.bits().len(), shape_ok().word_count());
    }

    #[test]
    fn set_get_round_trip() {
        let mut g = VoxelGrid::new(shape_ok());
        let (nx, ny, nz) = (g.shape().nx, g.shape().ny, g.shape().nz);
        g.set(1, 2, 3, true);
        assert!(g.get(1, 2, 3));
        // 越界
        assert!(!g.get(nx, 0, 0));
        assert!(!g.get(0, ny, 0));
        assert!(!g.get(0, 0, nz));
    }

    #[test]
    fn linear_to_xyz_round_trip() {
        let g = VoxelGrid::new(shape_ok());
        for idx in [0, 1, g.shape().total() - 1, 42, 100] {
            let (x, y, z) = g.linear_to_xyz(idx);
            assert_eq!(g.xyz_to_linear(x, y, z), idx);
        }
    }

    #[test]
    fn count_true_matches_sum() {
        let mut g = VoxelGrid::new(VoxelGridShape::new(3, 3, 3).unwrap());
        g.set(0, 0, 0, true);
        g.set(1, 1, 1, true);
        g.set(2, 2, 2, true);
        assert_eq!(g.count_true(), 3);
    }

    #[test]
    fn from_bool_slice_rejects_wrong_length() {
        let r = VoxelGrid::from_bool_slice(shape_ok(), &[true, false]);
        assert!(matches!(r, Err(ComputeError::ShapeMismatch { .. })));
    }

    #[test]
    fn from_bool_slice_round_trip() {
        let shape = shape_ok();
        let total = shape.total();
        let data: Vec<bool> = (0..total).map(|i| i % 7 == 0).collect();
        let g = VoxelGrid::from_bool_slice(shape, &data).unwrap();
        for i in 0..total {
            assert_eq!(g.get_linear(i), i % 7 == 0);
        }
    }

    #[test]
    fn invalid_shape_returns_error() {
        assert!(VoxelGridShape::new(0, 1, 1).is_err());
        assert!(VoxelGridShape::new(1, 0, 1).is_err());
        assert!(VoxelGridShape::new(1, 1, 0).is_err());
    }

    #[test]
    fn count_in_box_subregion() {
        let mut g = VoxelGrid::new(VoxelGridShape::new(4, 4, 4).unwrap());
        // 在 (1..3, 1..3, 1..3) 区域放 8 个体素
        for x in 1..3 {
            for y in 1..3 {
                for z in 1..3 {
                    g.set(x, y, z, true);
                }
            }
        }
        let c = g.count_in_box(1, 1, 1, 3, 3, 3);
        assert_eq!(c, 8);
    }
}
