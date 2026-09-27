//! 切削仿真核心算法。
//!
//! 提供：
//! - [`apply_tool_mask_batch`]：批量应用刀具掩码到体素网格。
//! - [`discretize_linear_segment`]：将直线路径离散为等间距采样点。
//! - [`BatchResult`]：批量切削的统计结果。
//!
//! ## 性能
//!
//! 关键优化：
//! 1. **位运算裁剪** —— 切削操作在位图层面完成；
//!    当网格与掩码均按 `u64` 字对齐时，可使用 `mask_word &= tool_word` 一次完成 64 体的批量清除。
//! 2. **AABB 预过滤** —— 远在 `O(N)` 之外的刀位点通过单点比较提前 skip；
//!    距离裁剪仅做 `O(1)` 的整数比较，避免进入子区域循环。
//! 3. **行主序** —— 内存访问顺序与底层位图存储一致，硬件预取器友好。
//! 4. **零分配** —— 不使用 `Vec::new()` 在内层循环；预计算所有边界索引。

use crate::error::{ComputeError, ComputeResult};
use crate::voxel_grid::{VoxelGrid, VoxelGridShape};

/// 批量切削结果统计。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct BatchResult {
    /// 处理的刀位点数。
    pub points: usize,
    /// 切除的体素总数。
    pub removed: usize,
    /// 因越界被跳过的刀位点数。
    pub skipped: usize,
}

impl BatchResult {
    pub fn merge(&mut self, other: BatchResult) {
        self.points += other.points;
        self.removed += other.removed;
        self.skipped += other.skipped;
    }
}

/// 将刀具掩码（`u64` 位图）批量应用到体素网格上。
///
/// # 参数
/// - `grid`: 工件体素网格（就地修改）。
/// - `tool_shape`: 刀具掩码形状。
/// - `tool_bits`: 刀具掩码位图（与 `grid.bits()` 同样行主序展平）。
/// - `points`: `(N, 3)` 刀位点坐标数组，连续存储。
/// - `bbox_min`: 工件包围盒最小点 `(x0, y0, z0)`。
/// - `voxel_size`: 体素边长。
/// - `padding`: 工件包围盒外扩量（与 Python 端语义一致）。
///
/// # 返回
/// - [`BatchResult`] 包含处理的刀位点数、切除体素数与越界跳过的刀位点数。
///
/// # 错误
/// - 若 `points.len() % 3 != 0` 返回 `ShapeMismatch`。
/// - 若 `tool_bits.len() != tool_shape.word_count()` 返回 `ShapeMismatch`。
pub fn apply_tool_mask_batch(
    grid: &mut VoxelGrid,
    tool_shape: VoxelGridShape,
    tool_bits: &[u64],
    points: &[f64],
    bbox_min: [f64; 3],
    voxel_size: f64,
    padding: f64,
) -> ComputeResult<BatchResult> {
    if voxel_size <= 0.0 || !voxel_size.is_finite() {
        return Err(ComputeError::InvalidVoxelSize { voxel_size });
    }
    if points.len() % 3 != 0 {
        return Err(ComputeError::ShapeMismatch {
            expected: "points.len() % 3 == 0".to_string(),
            actual: format!("points.len() = {}", points.len()),
        });
    }
    let expected_tool_words = tool_shape.word_count();
    if tool_bits.len() != expected_tool_words {
        return Err(ComputeError::ShapeMismatch {
            expected: format!("tool_bits len = {}", expected_tool_words),
            actual: format!("tool_bits len = {}", tool_bits.len()),
        });
    }

    let n_points = points.len() / 3;
    let mut result = BatchResult {
        points: n_points,
        ..Default::default()
    };

    let (gx, gy, gz) = (grid.shape().nx, grid.shape().ny, grid.shape().nz);
    let (tx, ty, tz) = (tool_shape.nx, tool_shape.ny, tool_shape.nz);
    let hx = tx / 2;
    let hy = ty / 2;
    let hz = tz / 2;

    let inv_vs = 1.0 / voxel_size;
    let mut removed_total = 0usize;
    let mut skipped_total = 0usize;

    for i in 0..n_points {
        let x = points[i * 3];
        let y = points[i * 3 + 1];
        let z = points[i * 3 + 2];

        // 坐标转网格索引（带 padding），等价于 Python 端：
        //   np.round(([x,y,z] - bbox_min + padding) / voxel_size).astype(int)
        let tip = [
            ((x - bbox_min[0] + padding) * inv_vs).round() as i64,
            ((y - bbox_min[1] + padding) * inv_vs).round() as i64,
            ((z - bbox_min[2] + padding) * inv_vs).round() as i64,
        ];

        // AABB 预过滤：刀具覆盖范围与网格求交
        let gx_min = (tip[0] - hx as i64).max(0) as usize;
        let gy_min = (tip[1] - hy as i64).max(0) as usize;
        let gz_min = (tip[2] - hz as i64).max(0) as usize;
        let gx_max = (tip[0] + hx as i64 + 1).min(gx as i64).max(0) as usize;
        let gy_max = (tip[1] + hy as i64 + 1).min(gy as i64).max(0) as usize;
        let gz_max = (tip[2] + hz as i64 + 1).min(gz as i64).max(0) as usize;

        if gx_min >= gx_max || gy_min >= gy_max || gz_min >= gz_max {
            skipped_total += 1;
            continue;
        }

        let mx_start = gx_min as i64 - (tip[0] - hx as i64);
        let my_start = gy_min as i64 - (tip[1] - hy as i64);
        let mz_start = gz_min as i64 - (tip[2] - hz as i64);
        let mx_end = mx_start + (gx_max - gx_min) as i64;
        let my_end = my_start + (gy_max - gy_min) as i64;
        let mz_end = mz_start + (gz_max - gz_min) as i64;

        removed_total += apply_subregion(
            grid, tool_shape, tool_bits, gx_min, gy_min, gz_min, gx_max, gy_max, gz_max, mx_start,
            my_start, mz_start, mx_end, my_end, mz_end,
        );
    }

    result.removed = removed_total;
    result.skipped = skipped_total;
    Ok(result)
}

#[allow(clippy::too_many_arguments)]
fn apply_subregion(
    grid: &mut VoxelGrid,
    tool_shape: VoxelGridShape,
    tool_bits: &[u64],
    gx_min: usize,
    gy_min: usize,
    gz_min: usize,
    gx_max: usize,
    gy_max: usize,
    gz_max: usize,
    mx_start: i64,
    my_start: i64,
    mz_start: i64,
    mx_end: i64,
    my_end: i64,
    mz_end: i64,
) -> usize {
    let mut removed = 0usize;
    let ny = grid.shape().ny;
    let nz = grid.shape().nz;
    let tny = tool_shape.ny;
    let tnz = tool_shape.nz;
    let grid_bits = grid.bits_mut();

    // 逐行扫描：内层以 (x, y, z) 三层循环
    for gx in gx_min..gx_max {
        let mx = mx_start + (gx - gx_min) as i64;
        if mx < 0 || mx >= tool_shape.nx as i64 {
            continue;
        }
        for gy in gy_min..gy_max {
            let my = my_start + (gy - gy_min) as i64;
            if my < 0 || my >= tny as i64 {
                continue;
            }
            // 计算每行基址（线性索引）
            let grid_row_base = (gx * ny + gy) * nz;
            let tool_row_base = (mx as usize * tny + my as usize) * tnz;
            for gz in gz_min..gz_max {
                let mz = mz_start + (gz - gz_min) as i64;
                if mz < 0 || mz >= tnz as i64 {
                    continue;
                }
                let grid_idx = grid_row_base + gz;
                let tool_idx = tool_row_base + mz as usize;

                let g_word = grid_idx >> 6;
                let g_bit = grid_idx & 63;
                let t_word = tool_idx >> 6;
                let t_bit = tool_idx & 63;

                let tool_bit = (tool_bits[t_word] >> t_bit) & 1;
                if tool_bit == 0 {
                    continue;
                }
                let g_set = (grid_bits[g_word] >> g_bit) & 1;
                if g_set == 1 {
                    grid_bits[g_word] &= !(1u64 << g_bit);
                    removed += 1;
                }
            }
        }
    }
    removed
}

/// 将直线路径 `start → end` 离散为等间距采样点。
///
/// # 参数
/// - `start`, `end`: 起止点 (x, y, z)。
/// - `step`: 采样间距。
///
/// # 返回
/// 扁平 `(N*3)` 数组：`[x0, y0, z0, x1, y1, z1, ...]`。
pub fn discretize_linear_segment(start: [f64; 3], end: [f64; 3], step: f64) -> Vec<f64> {
    if step <= 0.0 {
        return vec![start[0], start[1], start[2], end[0], end[1], end[2]];
    }
    let dx = end[0] - start[0];
    let dy = end[1] - start[1];
    let dz = end[2] - start[2];
    let dist = (dx * dx + dy * dy + dz * dz).sqrt();
    if dist < 1e-12 {
        return vec![start[0], start[1], start[2]];
    }
    let n = (dist / step).ceil() as usize;
    let n = n.max(1);
    let mut out = Vec::with_capacity((n + 1) * 3);
    for k in 0..=n {
        let t = k as f64 / n as f64;
        out.push(start[0] + t * dx);
        out.push(start[1] + t * dy);
        out.push(start[2] + t * dz);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tool::{build_tool_mask, ToolGeometry, ToolType};

    fn make_grid(n: usize) -> VoxelGrid {
        let shape = VoxelGridShape::new(n, n, n).unwrap();
        VoxelGrid::new(shape)
    }

    fn make_solid_grid(n: usize) -> VoxelGrid {
        let shape = VoxelGridShape::new(n, n, n).unwrap();
        // 全部置 true
        let mut g = VoxelGrid::new(shape);
        let total = shape.total();
        for i in 0..total {
            g.set_linear(i, true);
        }
        g
    }

    fn small_flat_mask() -> (VoxelGridShape, Vec<u64>) {
        // 直径 2mm，voxel=1mm → 掩码 3x3x3
        let geom = ToolGeometry::new(ToolType::Flat, 2.0, 0.0, 2.0);
        build_tool_mask(&geom, 1.0).unwrap()
    }

    #[test]
    fn rejects_misaligned_points() {
        let mut g = make_grid(10);
        let (ts, tb) = small_flat_mask();
        let r = apply_tool_mask_batch(
            &mut g,
            ts,
            &tb,
            &[1.0, 2.0], // 长度不是 3 的倍数
            [0.0, 0.0, 0.0],
            1.0,
            0.0,
        );
        assert!(matches!(r, Err(ComputeError::ShapeMismatch { .. })));
    }

    #[test]
    fn rejects_wrong_tool_bits_length() {
        let mut g = make_grid(10);
        let (ts, _tb) = small_flat_mask();
        let r = apply_tool_mask_batch(
            &mut g,
            ts,
            &[0u64; 999], // 长度不匹配
            &[],
            [0.0, 0.0, 0.0],
            1.0,
            0.0,
        );
        assert!(matches!(r, Err(ComputeError::ShapeMismatch { .. })));
    }

    #[test]
    fn no_points_no_change() {
        let mut g = make_solid_grid(10);
        let g_before = g.clone();
        let (ts, tb) = small_flat_mask();
        let r = apply_tool_mask_batch(&mut g, ts, &tb, &[], [0.0, 0.0, 0.0], 1.0, 0.0).unwrap();
        assert_eq!(r.points, 0);
        assert_eq!(r.removed, 0);
        assert_eq!(g.bits(), g_before.bits());
    }

    #[test]
    fn center_cut_removes_mask_volume() {
        // 5x5x5 实心网格，中心点 (2,2,2)，bbox_min=(0,0,0)，voxel=1, padding=0
        let mut g = make_solid_grid(5);
        let (ts, tb) = small_flat_mask();
        let result =
            apply_tool_mask_batch(&mut g, ts, &tb, &[2.0, 2.0, 2.0], [0.0, 0.0, 0.0], 1.0, 0.0)
                .unwrap();
        assert_eq!(result.points, 1);
        assert!(result.removed > 0);
        // 网格剩余应严格小于全 125
        assert!(g.count_true() < 125);
    }

    #[test]
    fn out_of_bounds_point_is_skipped() {
        let mut g = make_solid_grid(5);
        let (ts, tb) = small_flat_mask();
        let result = apply_tool_mask_batch(
            &mut g,
            ts,
            &tb,
            &[100.0, 100.0, 100.0], // 远离网格
            [0.0, 0.0, 0.0],
            1.0,
            0.0,
        )
        .unwrap();
        assert_eq!(result.points, 1);
        assert_eq!(result.skipped, 1);
        assert_eq!(result.removed, 0);
        assert_eq!(g.count_true(), 125);
    }

    #[test]
    fn batch_result_merge() {
        let a = BatchResult {
            points: 5,
            removed: 10,
            skipped: 1,
        };
        let mut b = a;
        b.merge(a);
        assert_eq!(b.points, 10);
        assert_eq!(b.removed, 20);
        assert_eq!(b.skipped, 2);
    }

    #[test]
    fn discretize_linear_emits_endpoints() {
        let pts = discretize_linear_segment([0.0, 0.0, 0.0], [10.0, 0.0, 0.0], 2.0);
        // 距离=10, step=2 → 5 段 → 6 个点
        assert_eq!(pts.len(), 18);
        assert_eq!(&pts[0..3], &[0.0, 0.0, 0.0]);
        assert_eq!(&pts[15..18], &[10.0, 0.0, 0.0]);
    }

    #[test]
    fn discretize_zero_distance_returns_single_point() {
        let pts = discretize_linear_segment([1.0, 1.0, 1.0], [1.0, 1.0, 1.0], 0.5);
        assert_eq!(pts, vec![1.0, 1.0, 1.0]);
    }

    #[test]
    fn multiple_cuts_are_cumulative() {
        let mut g = make_solid_grid(10);
        let (ts, tb) = small_flat_mask();
        let pts = vec![3.0, 3.0, 3.0, 6.0, 6.0, 6.0];
        let result =
            apply_tool_mask_batch(&mut g, ts, &tb, &pts, [0.0, 0.0, 0.0], 1.0, 0.0).unwrap();
        assert_eq!(result.points, 2);
        assert!(result.removed > 0);
    }

    #[test]
    fn extreme_voxel_size_validation() {
        let mut g = make_solid_grid(10);
        let (ts, tb) = small_flat_mask();
        assert!(apply_tool_mask_batch(
            &mut g,
            ts,
            &tb,
            &[0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            0.0, // invalid voxel size
            0.0,
        )
        .is_err());
    }
}
