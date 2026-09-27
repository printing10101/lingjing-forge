//! 刀具几何模块。
//!
//! 支持 6 种刀具：
//! - [`ToolType::Ball`] 球头刀
//! - [`ToolType::Flat`] 平底刀
//! - [`ToolType::BullNose`] 圆角平底刀（带拐角圆角的平底刀）
//! - [`ToolType::Tapered`] 锥度刀（圆锥形）
//! - [`ToolType::BallTapered`] 球头锥度刀（圆锥 + 球头尖）
//! - [`ToolType::Form`] 特殊成形刀（自定义包络曲线）
//!
//! 全部以解析形式给出体素掩码；通过 [`build_tool_mask`] 入口构造。
//!
//! ## 局部坐标系
//!
//! 刀具局部坐标系：刀尖位于原点 `(0, 0, 0)`，**Z 轴正方向朝向工件表面**。
//! 有效工作区为 `z ∈ [-cutting_length, 0]`、径向 `√(x² + y²) ≤ r`。
//!
//! ## 输出
//!
//! 刀具掩码使用 [`VoxelGrid`] 表示，三维 `bool` 位图。
//! 中心位置 `(cx, cy, cz)` 对应刀尖。

use crate::error::{ComputeError, ComputeResult};
use crate::voxel_grid::VoxelGridShape;

/// 刀具类型枚举。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ToolType {
    /// 球头刀：完整半球形刀尖。
    Ball,
    /// 平底刀：纯圆柱。
    Flat,
    /// 圆角平底刀：圆柱 + 底部圆角。
    BullNose,
    /// 锥度刀：圆锥（圆台）。
    Tapered,
    /// 球头锥度刀：圆锥 + 半球形刀尖。
    BallTapered,
    /// 成形刀：自定义轮廓（线性插值）。
    Form,
}

impl ToolType {
    /// 从字符串解析（大小写不敏感），失败返回 `InvalidTool`。
    pub fn parse(s: &str) -> ComputeResult<Self> {
        match s.to_ascii_lowercase().as_str() {
            "ball" | "ballnose" | "ball_nose" => Ok(ToolType::Ball),
            "flat" | "flatend" | "flat_end" => Ok(ToolType::Flat),
            "bullnose" | "bull_nose" | "bull" | "corner_radius" => Ok(ToolType::BullNose),
            "tapered" | "taper" | "cone" => Ok(ToolType::Tapered),
            "balltapered" | "ball_tapered" | "taperedball" | "tapered_ball" => {
                Ok(ToolType::BallTapered)
            }
            "form" | "formed" | "profile" => Ok(ToolType::Form),
            other => Err(ComputeError::InvalidTool {
                message: format!(
                    "unknown tool_type '{}' (valid: ball, flat, bullnose, tapered, balltapered, form)",
                    other
                ),
            }),
        }
    }
}

/// 刀具几何参数。
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ToolGeometry {
    pub tool_type: ToolType,
    /// 刀具直径 (mm)。
    pub diameter: f64,
    /// 拐角圆角半径 (mm)。`Flat` 类型为 0，`Ball` 类型通常等于半径。
    pub corner_radius: f64,
    /// 切削刃长 (mm)。
    pub cutting_length: f64,
    /// 锥度半角（度）。仅 `Tapered`/`BallTapered` 使用。
    pub taper_angle_deg: f64,
    /// 成形轮廓半径序列（mm）。仅 `Form` 使用；至少 2 个采样点。
    pub form_profile: &'static [(f64, f64)], // (z_offset_from_tip, radius)
}

impl ToolGeometry {
    /// 构造基础几何参数（带默认 `taper_angle_deg = 0.0`、`form_profile = &[]`）。
    pub fn new(
        tool_type: ToolType,
        diameter: f64,
        corner_radius: f64,
        cutting_length: f64,
    ) -> Self {
        Self {
            tool_type,
            diameter,
            corner_radius,
            cutting_length,
            taper_angle_deg: 0.0,
            form_profile: &[],
        }
    }

    /// 锥度刀构造。
    pub fn tapered(diameter: f64, cutting_length: f64, taper_angle_deg: f64) -> Self {
        Self {
            tool_type: ToolType::Tapered,
            diameter,
            corner_radius: 0.0,
            cutting_length,
            taper_angle_deg,
            form_profile: &[],
        }
    }

    /// 球头锥度刀构造。
    pub fn ball_tapered(
        diameter: f64,
        corner_radius: f64,
        cutting_length: f64,
        taper_angle_deg: f64,
    ) -> Self {
        Self {
            tool_type: ToolType::BallTapered,
            diameter,
            corner_radius,
            cutting_length,
            taper_angle_deg,
            form_profile: &[],
        }
    }

    /// 成形刀构造。
    pub fn form(diameter: f64, cutting_length: f64, profile: &'static [(f64, f64)]) -> Self {
        Self {
            tool_type: ToolType::Form,
            diameter,
            corner_radius: 0.0,
            cutting_length,
            taper_angle_deg: 0.0,
            form_profile: profile,
        }
    }

    /// 校验参数合法性。
    pub fn validate(&self) -> ComputeResult<()> {
        if !self.diameter.is_finite() || self.diameter <= 0.0 {
            return Err(ComputeError::InvalidTool {
                message: format!("diameter must be positive finite, got {}", self.diameter),
            });
        }
        if !self.cutting_length.is_finite() || self.cutting_length <= 0.0 {
            return Err(ComputeError::InvalidTool {
                message: format!(
                    "cutting_length must be positive finite, got {}",
                    self.cutting_length
                ),
            });
        }
        if self.corner_radius < 0.0 || !self.corner_radius.is_finite() {
            return Err(ComputeError::InvalidTool {
                message: format!(
                    "corner_radius must be non-negative finite, got {}",
                    self.corner_radius
                ),
            });
        }
        if self.corner_radius > self.diameter {
            return Err(ComputeError::InvalidTool {
                message: format!(
                    "corner_radius ({}) cannot exceed diameter ({})",
                    self.corner_radius, self.diameter
                ),
            });
        }
        if matches!(self.tool_type, ToolType::Tapered | ToolType::BallTapered) {
            if self.taper_angle_deg <= 0.0 || self.taper_angle_deg >= 90.0 {
                return Err(ComputeError::InvalidTool {
                    message: format!(
                        "taper_angle_deg must be in (0, 90), got {}",
                        self.taper_angle_deg
                    ),
                });
            }
        }
        if matches!(self.tool_type, ToolType::Form) {
            if self.form_profile.len() < 2 {
                return Err(ComputeError::InvalidTool {
                    message: "form profile requires at least 2 sample points".to_string(),
                });
            }
        }
        Ok(())
    }
}

/// 根据刀具参数生成体素掩码（位存储）。
///
/// # 参数
/// - `geom`: 刀具几何参数（须先 [`ToolGeometry::validate`]）。
/// - `voxel_size`: 体素边长 (mm)，须为正有限值。
///
/// # 返回
/// - `(shape, bits)`：`shape` 是掩码的维度，`bits` 是 `u64` 位图（与 `VoxelGrid::bits()` 兼容）。
///
/// # 性能
/// - 时间复杂度：`O(M)`，其中 `M = nx*ny*nz` 为掩码体素数。
/// - 内存复杂度：`O(M/64)` 字节。
pub fn build_tool_mask(
    geom: &ToolGeometry,
    voxel_size: f64,
) -> ComputeResult<(VoxelGridShape, Vec<u64>)> {
    geom.validate()?;
    if !voxel_size.is_finite() || voxel_size <= 0.0 {
        return Err(ComputeError::InvalidVoxelSize { voxel_size });
    }

    // 选用工具最大半径作为网格半径，确保覆盖所有形状。
    let max_r = geom.diameter * 0.5;
    let grid_half = (max_r / voxel_size).ceil() as i32 + 1;
    let n = (2 * grid_half + 1) as usize;
    let shape = VoxelGridShape::new(n, n, n)?;

    // 仅在 (z ∈ [-cutting_length, 0]) 内生成有效位；
    // 但保持形状对称便于 PyO3 端做掩码卷积。
    let mut bits = vec![0u64; shape.word_count()];
    let total = shape.total();

    // 预计算成形刀轮廓查找表（z 升序，r 单调），供 Form 类型使用。
    // 出于 API 简洁考虑，Form 在调用方传入 `form_profile` 时是 `&'static [(f64, f64)]`。
    // 我们直接在内部把 (z, r) 转为闭式判定。

    for linear in 0..total {
        let (xi, yi, zi) = {
            let z = linear % shape.nz;
            let yz = linear / shape.nz;
            let y = yz % shape.ny;
            let x = yz / shape.ny;
            (x as i32, y as i32, z as i32)
        };
        let gx = (xi - grid_half) as f64 * voxel_size;
        let gy = (yi - grid_half) as f64 * voxel_size;
        let gz = (zi - grid_half) as f64 * voxel_size; // z 局部坐标，0 = 中心

        // 注意：上面 `grid_range` 的中心是刀尖，但我们 grid_half 是从 (max_r + 1) 算的，
        // 实际刀尖应在 (grid_half, grid_half, grid_half) — 即坐标 z=0。
        // 因此 `gz` 即为相对刀尖的 Z 偏移（0 = 刀尖，负值 = 刀尖下方即工件内部）。
        if !inside_geometry(geom, gx, gy, gz) {
            continue;
        }
        // 设置位
        let word = linear >> 6;
        let bit = linear & 63;
        bits[word] |= 1u64 << bit;
    }

    Ok((shape, bits))
}

/// 判断点 `(x, y, z)`（局部坐标，刀尖在原点）是否在刀具占据区域内。
///
/// 坐标系约定：刀尖在 `(0, 0, 0)`，Z 轴正方向指向工件表面（上方），
/// Z 负方向为刀具进入工件的方向。
fn inside_geometry(geom: &ToolGeometry, x: f64, y: f64, z: f64) -> bool {
    let r = geom.diameter * 0.5;
    let r_xy = (x * x + y * y).sqrt();

    // 工作 Z 范围：刀尖以下到 cutting_length 处。
    // z=0 -> 刀尖, z=-cutting_length -> 切削刃末端（深入工件方向）。
    if z > 0.0 || z < -geom.cutting_length {
        return false;
    }
    // 工作半径外直接拒绝（加速大量点）。
    if r_xy > r + 1e-9 {
        return false;
    }

    match geom.tool_type {
        ToolType::Ball => {
            // 完整半球 + 圆柱延伸
            let cr = geom.corner_radius.max(1e-6);
            if z >= -cr + 1e-9 {
                // 半球部分：以 (0, 0, -cr) 为球心
                let dz = z + cr;
                return r_xy * r_xy + dz * dz <= cr * cr + 1e-9;
            }
            // 圆柱部分：z <= -cr
            r_xy <= r + 1e-9
        }
        ToolType::Flat => {
            // 纯圆柱
            r_xy <= r + 1e-9
        }
        ToolType::BullNose => {
            // 拐角圆角 + 圆柱
            let cr = geom.corner_radius;
            if cr <= 1e-9 {
                return r_xy <= r + 1e-9;
            }
            if z >= -cr + 1e-9 {
                // 圆角区域：球心 (0, 0, -cr)
                let dz = z + cr;
                return r_xy * r_xy + dz * dz <= cr * cr + 1e-9;
            }
            r_xy <= r + 1e-9
        }
        ToolType::Tapered => {
            // 锥度刀：从刀尖向 z=-cutting_length 半径线性变化（刀尖=0, 末端=r）
            // 锥度半角 β；r(z) = -z * tan(β)  for z ∈ [-cutting_length, 0]
            let beta = geom.taper_angle_deg.to_radians();
            let tan_b = beta.tan();
            // 刀尖处 r(0)=0; z=-cutting_length 处 r=r_max
            // 强制末端不小于 0、不超过 r：
            let max_r_at_end = (geom.cutting_length * tan_b).min(r);
            if z.abs() < 1e-9 {
                return r_xy <= 1e-9;
            }
            let r_local = (-z) * tan_b;
            if r_local > max_r_at_end + 1e-9 {
                // 已越过末端 → 视为无材料（被夹持，不参与切削）
                return false;
            }
            r_xy <= r_local + 1e-9
        }
        ToolType::BallTapered => {
            // 锥度 + 半球尖
            let cr = geom.corner_radius.max(1e-6);
            if z >= -cr + 1e-9 {
                let dz = z + cr;
                return r_xy * r_xy + dz * dz <= cr * cr + 1e-9;
            }
            // 锥度延伸（z 越深半径越大）
            let beta = geom.taper_angle_deg.to_radians();
            let tan_b = beta.tan();
            let r_local = cr + (-z - cr).max(0.0) * tan_b;
            if r_local > r + 1e-9 {
                return false;
            }
            r_xy <= r_local + 1e-9
        }
        ToolType::Form => {
            // 成形刀：线性插值自定义轮廓 (z, r(z))
            // z ∈ [-cutting_length, 0]，profile 至少 2 个点。
            if geom.form_profile.is_empty() {
                return false;
            }
            // 寻找包围 z 的两个 profile 节点
            let prof = geom.form_profile;
            if z <= prof[0].0 {
                return r_xy <= prof[0].1 + 1e-9;
            }
            if z >= prof[prof.len() - 1].0 {
                return r_xy <= prof[prof.len() - 1].1 + 1e-9;
            }
            // 线性插值
            let mut r_at_z = prof[0].1;
            for w in prof.windows(2) {
                let (z0, r0) = w[0];
                let (z1, r1) = w[1];
                if z >= z0 && z <= z1 {
                    let t = if (z1 - z0).abs() < 1e-12 {
                        0.0
                    } else {
                        (z - z0) / (z1 - z0)
                    };
                    r_at_z = r0 + t * (r1 - r0);
                    break;
                }
            }
            r_xy <= r_at_z + 1e-9
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ball(d: f64) -> ToolGeometry {
        ToolGeometry::new(ToolType::Ball, d, d * 0.5, 50.0)
    }
    fn flat(d: f64) -> ToolGeometry {
        ToolGeometry::new(ToolType::Flat, d, 0.0, 50.0)
    }

    #[test]
    fn parses_all_six_tool_types() {
        for (alias, expected) in [
            ("ball", ToolType::Ball),
            ("BallNose", ToolType::Ball),
            ("flat", ToolType::Flat),
            ("flatend", ToolType::Flat),
            ("bullnose", ToolType::BullNose),
            ("bull", ToolType::BullNose),
            ("tapered", ToolType::Tapered),
            ("taper", ToolType::Tapered),
            ("balltapered", ToolType::BallTapered),
            ("tapered_ball", ToolType::BallTapered),
            ("form", ToolType::Form),
            ("profile", ToolType::Form),
        ] {
            assert_eq!(ToolType::parse(alias).unwrap(), expected);
        }
        assert!(ToolType::parse("unknown_xyz").is_err());
    }

    #[test]
    fn validate_rejects_invalid_params() {
        let g = ToolGeometry::new(ToolType::Ball, -1.0, 0.0, 50.0);
        assert!(g.validate().is_err());
        let g = ToolGeometry::new(ToolType::Ball, 10.0, 0.0, -1.0);
        assert!(g.validate().is_err());
        let g = ToolGeometry::new(ToolType::Flat, 10.0, 11.0, 50.0);
        assert!(g.validate().is_err());
    }

    #[test]
    fn flat_mask_center_is_filled() {
        let g = flat(10.0);
        let (shape, bits) = build_tool_mask(&g, 1.0).unwrap();
        let total = shape.total();
        let set_bits: usize = bits.iter().map(|w| w.count_ones() as usize).sum();
        assert!(set_bits > 0);
        // 中心体素必须被占据
        let (cx, cy, cz) = (shape.nx / 2, shape.ny / 2, shape.nz / 2);
        let idx = (cx * shape.ny + cy) * shape.nz + cz;
        assert!(idx < total);
        let word = idx >> 6;
        let bit = idx & 63;
        assert_ne!(bits[word] & (1u64 << bit), 0);
    }

    #[test]
    fn ball_mask_hemisphere_at_tip() {
        let g = ball(10.0);
        let (shape, bits) = build_tool_mask(&g, 0.5).unwrap();
        // 刀尖半球应被填充：刀尖 (0,0,0) 一定在内部
        let (cx, cy, cz) = (shape.nx / 2, shape.ny / 2, shape.nz / 2);
        let idx = (cx * shape.ny + cy) * shape.nz + cz;
        let word = idx >> 6;
        let bit = idx & 63;
        assert_ne!(bits[word] & (1u64 << bit), 0);
    }

    #[test]
    fn flat_mask_radius_matches_diameter() {
        let g = flat(10.0);
        let voxel_size = 1.0;
        let (shape, bits) = build_tool_mask(&g, voxel_size).unwrap();
        let cx = shape.nx / 2;
        let cy = shape.ny / 2;
        // 在 z=0 平面上，最远被占据的体素距离中心应在 [r - voxel, r + voxel] 区间内
        let r_mm = 5.0;
        let max_r_voxels = (r_mm / voxel_size).ceil() as i32;
        let z = shape.nz / 2;
        for d in 0..=max_r_voxels + 2 {
            let x = cx as i32 + d;
            let y = cy;
            let idx = (x as usize * shape.ny + y) * shape.nz + z;
            let word = idx >> 6;
            let bit = idx & 63;
            let occupied = (bits[word] >> bit) & 1 == 1;
            if d as f64 * voxel_size <= r_mm + 1e-9 {
                assert!(occupied, "d={} should be inside", d);
            } else if d as f64 * voxel_size > r_mm + voxel_size {
                assert!(!occupied, "d={} should be outside", d);
            }
        }
    }

    #[test]
    fn bull_nose_mask_is_smooth_transition() {
        let g = ToolGeometry::new(ToolType::BullNose, 10.0, 1.0, 20.0);
        let (shape, bits) = build_tool_mask(&g, 0.5).unwrap();
        // 圆角区域 z ∈ [-1, 0] 应有逐渐减小的 r
        let cx = shape.nx / 2;
        let cy = shape.ny / 2;
        let cz_top = shape.nz / 2; // z=0 刀尖
        let r_at_top: i32 = (0..shape.nx as i32)
            .rev()
            .find(|&x| {
                let idx = (x as usize * shape.ny + cy) * shape.nz + cz_top;
                ((bits[idx >> 6] >> (idx & 63)) & 1) == 1
            })
            .unwrap_or(0);
        let cz_below = (shape.nz / 2).saturating_sub(2);
        let r_below: i32 = (0..shape.nx as i32)
            .rev()
            .find(|&x| {
                let idx = (x as usize * shape.ny + cy) * shape.nz + cz_below;
                ((bits[idx >> 6] >> (idx & 63)) & 1) == 1
            })
            .unwrap_or(0);
        // 圆柱部分（z 更低）半径应 ≥ 圆角顶端半径
        assert!(
            r_below >= r_at_top - 1,
            "r_below={} r_at_top={}",
            r_below,
            r_at_top
        );
    }

    #[test]
    fn tapered_mask_tip_is_point() {
        // 锥度刀：刀尖处 r=0
        let g = ToolGeometry::tapered(10.0, 30.0, 5.0);
        let (shape, bits) = build_tool_mask(&g, 0.5).unwrap();
        let cx = shape.nx / 2;
        let cy = shape.ny / 2;
        let cz = shape.nz / 2;
        // 紧邻 z=0 平面的下一个体素（z=cz-1）应有非零 r
        let idx_below = ((cx as i32 - 1) as usize * shape.ny + cy) * shape.nz + cz - 1;
        let idx_at_tip = (cx * shape.ny + cy) * shape.nz + cz;
        // 刀尖处被占据（r≈0）
        let w_tip = idx_at_tip >> 6;
        let b_tip = idx_at_tip & 63;
        assert_ne!(bits[w_tip] & (1u64 << b_tip), 0);
        // 略下方 (z=-voxel) 也应被占据
        let w_b = idx_below >> 6;
        let b_b = idx_below & 63;
        assert_ne!(bits[w_b] & (1u64 << b_b), 0);
    }

    #[test]
    fn ball_tapered_combines_hemisphere_and_taper() {
        let g = ToolGeometry::ball_tapered(10.0, 2.0, 30.0, 3.0);
        let (shape, bits) = build_tool_mask(&g, 0.5).unwrap();
        let cx = shape.nx / 2;
        let cy = shape.ny / 2;
        let cz = shape.nz / 2;
        let idx = (cx * shape.ny + cy) * shape.nz + cz;
        let word = idx >> 6;
        let bit = idx & 63;
        assert_ne!(bits[word] & (1u64 << bit), 0);
    }

    #[test]
    fn form_mask_uses_linear_interpolation() {
        // 成形刀轮廓：z=-10 -> r=1; z=-5 -> r=2; z=0 -> r=3
        static PROFILE: &[(f64, f64)] = &[(-10.0, 1.0), (-5.0, 2.0), (0.0, 3.0)];
        let g = ToolGeometry::form(10.0, 10.0, PROFILE);
        let (shape, bits) = build_tool_mask(&g, 0.5).unwrap();
        let cx = shape.nx / 2;
        let cy = shape.ny / 2;
        // z=cz（z=0 位置）：应有 r≈3mm
        let cz = shape.nz / 2;
        let mut max_r_voxels = 0i32;
        for d in 0..(shape.nx as i32 / 2) {
            let idx = ((cx as i32 + d) as usize * shape.ny + cy) * shape.nz + cz;
            let w = idx >> 6;
            let b = idx & 63;
            if (bits[w] >> b) & 1 == 1 {
                max_r_voxels = d;
            }
        }
        let r_mm = max_r_voxels as f64 * 0.5;
        assert!((r_mm - 3.0).abs() < 1.0, "expected ~3mm, got {}", r_mm);
    }

    #[test]
    fn form_rejects_too_few_profile_points() {
        static PROFILE: &[(f64, f64)] = &[(-5.0, 1.0)];
        let g = ToolGeometry::form(10.0, 10.0, PROFILE);
        assert!(g.validate().is_err());
    }

    #[test]
    fn invalid_voxel_size_returns_error() {
        let g = flat(10.0);
        assert!(build_tool_mask(&g, 0.0).is_err());
        assert!(build_tool_mask(&g, -1.0).is_err());
    }
}
