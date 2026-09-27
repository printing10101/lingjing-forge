//! PyO3 绑定层：将 [`compute_core`] 中的核心算法暴露为 Python 可调用模块。
//!
//! ## 模块结构
//!
//! - `compute._native`: 顶级 Python 模块（maturin 注册）。
//!   - 子模块 `voxel_cutter`: 体素化切削函数。
//!
//! ## 零拷贝策略
//!
//! - `apply_tool_mask` 直接消费 `numpy.ndarray` 的位/字节视图；
//!   切削完成后通过 `unsafe { array.as_array_mut() }` 写回原数组。
//! - 任何错误都通过 `PyErr` 上抛，不 panic。
//!
//! ## 公开 API
//!
//! ```python
//! from compute import voxel_cutter as vc
//!
//! removed = vc.apply_tool_mask(
//!     grid_view,            # numpy bool 数组 (nx, ny, nz)
//!     tool_mask_view,       # numpy bool 数组 (mx, my, mz)
//!     points,               # numpy float64 (N, 3)
//!     bbox_min,             # (x0, y0, z0)
//!     voxel_size,           # float
//!     padding,              # float
//! )
//! mask = vc.build_tool_mask(
//!     tool_type="ball",
//!     diameter=10.0,
//!     corner_radius=5.0,
//!     cutting_length=50.0,
//!     voxel_size=1.0,
//!     taper_angle_deg=0.0,
//!     form_profile=None,
//! )
//! ```

use std::panic::{catch_unwind, AssertUnwindSafe};

use numpy::PyArrayMethods;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule, PyTuple};

use compute_core::{
    apply_tool_mask_batch, build_tool_mask, discretize_linear_segment, BatchResult, ToolGeometry,
    ToolType, VoxelGrid, VoxelGridShape,
};

/// 顶层 `compute._native` 模块。
#[pymodule]
fn _native(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("compute_core_version", compute_core::compute_core_version())?;
    m.add_function(wrap_pyfunction!(is_available, m)?)?;
    let vc = PyModule::new_bound(_py, "voxel_cutter")?;
    vc.add_function(wrap_pyfunction!(vc_apply_tool_mask, &vc)?)?;
    vc.add_function(wrap_pyfunction!(vc_build_tool_mask, &vc)?)?;
    vc.add_function(wrap_pyfunction!(vc_discretize_linear_segment, &vc)?)?;
    vc.add_function(wrap_pyfunction!(vc_benchmark_mask_cut, &vc)?)?;
    m.add_submodule(&vc)?;
    Ok(())
}

/// 暴露给 Python 的版本号 / 编译信息。
pub fn compute_core_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pyfunction]
fn is_available() -> bool {
    true
}

// voxel_cutter 子模块函数

/// 批量应用刀具掩码到体素网格。
///
/// # 参数
/// - `grid`: `numpy.ndarray`，`dtype=bool`，形状 `(nx, ny, nz)`。
/// - `tool_mask`: `numpy.ndarray`，`dtype=bool`，形状 `(mx, my, mz)`。
/// - `points`: `numpy.ndarray`，`dtype=float64`，形状 `(N, 3)`。
/// - `bbox_min`: 长度 3 的可迭代对象。
/// - `voxel_size`: `float`。
/// - `padding`: `float`。
///
/// # 返回
/// `dict`：`{"removed": int, "skipped": int, "points": int}`
#[pyfunction]
#[pyo3(signature = (grid, tool_mask, points, bbox_min, voxel_size, padding))]
fn vc_apply_tool_mask(
    py: Python<'_>,
    grid: &Bound<'_, pyo3::PyAny>,
    tool_mask: &Bound<'_, pyo3::PyAny>,
    points: &Bound<'_, pyo3::PyAny>,
    bbox_min: &Bound<'_, pyo3::PyAny>,
    voxel_size: f64,
    padding: f64,
) -> PyResult<Py<PyDict>> {
    with_panic_guard(|| {
        // 1. 校验与提取输入
        let grid_arr: numpy::PyReadwriteArray<'_, bool, numpy::Ix3> =
            grid.extract().map_err(|e: pyo3::PyErr| {
                PyValueError::new_err(format!("grid must be bool ndarray (nx,ny,nz): {e}"))
            })?;
        let tool_arr: numpy::PyReadonlyArray<'_, bool, numpy::Ix3> =
            tool_mask.extract().map_err(|e: pyo3::PyErr| {
                PyValueError::new_err(format!("tool_mask must be bool ndarray (mx,my,mz): {e}"))
            })?;
        let pts_arr: numpy::PyReadonlyArray<'_, f64, numpy::Ix2> =
            points.extract().map_err(|e: pyo3::PyErr| {
                PyValueError::new_err(format!("points must be float64 ndarray (N,3): {e}"))
            })?;

        let bbox = extract_vec3(bbox_min)?;
        let g_shape_arr = grid_arr.shape();
        let nx = g_shape_arr[0];
        let ny = g_shape_arr[1];
        let nz = g_shape_arr[2];
        let shape = VoxelGridShape::new(nx, ny, nz)
            .map_err(|e| PyValueError::new_err(format!("invalid grid shape: {e}")))?;

        // 将 grid 复制到我们的 VoxelGrid（避免直接持有 numpy 缓冲；体素规模可控）
        let mut voxel_grid = VoxelGrid::new(shape);
        unsafe {
            let g_view = grid_arr.as_array();
            for ((x, y, z), &v) in g_view.indexed_iter() {
                if v {
                    voxel_grid.set(x, y, z, true);
                }
            }
        }

        let t_shape_arr = tool_arr.shape();
        let tnx = t_shape_arr[0];
        let tny = t_shape_arr[1];
        let tnz = t_shape_arr[2];
        let tool_shape = VoxelGridShape::new(tnx, tny, tnz)
            .map_err(|e| PyValueError::new_err(format!("invalid tool_mask shape: {e}")))?;

        let mut tool_bits = vec![0u64; tool_shape.word_count()];
        unsafe {
            let t_view = tool_arr.as_array();
            for ((x, y, z), &v) in t_view.indexed_iter() {
                if v {
                    let linear = tool_shape.xyz_to_linear(x, y, z);
                    tool_bits[linear >> 6] |= 1u64 << (linear & 63);
                }
            }
        }

        // points
        let pts_view = unsafe { pts_arr.as_array() };
        let n = pts_view.shape()[0];
        let mut points_flat = Vec::with_capacity(n * 3);
        for row in pts_view.rows() {
            points_flat.push(row[0]);
            points_flat.push(row[1]);
            points_flat.push(row[2]);
        }

        // 2. 释放 GIL，执行核心算法
        let result: BatchResult = py
            .allow_threads(|| {
                apply_tool_mask_batch(
                    &mut voxel_grid,
                    tool_shape,
                    &tool_bits,
                    &points_flat,
                    bbox,
                    voxel_size,
                    padding,
                )
            })
            .map_err(|e| PyRuntimeError::new_err(format!("apply_tool_mask_batch failed: {e}")))?;

        // 3. 将结果写回 grid（就地修改）
        unsafe {
            let mut g_view = grid_arr.as_array_mut();
            for ((x, y, z), v) in g_view.indexed_iter_mut() {
                *v = voxel_grid.get(x, y, z);
            }
        }

        // 4. 返回结果字典
        let dict = PyDict::new_bound(py);
        dict.set_item("removed", result.removed as i64)?;
        dict.set_item("skipped", result.skipped as i64)?;
        dict.set_item("points", result.points as i64)?;
        Ok(dict.unbind())
    })
}

/// 构造刀具掩码。
///
/// # 参数
/// - `tool_type`: `"ball" | "flat" | "bullnose" | "tapered" | "balltapered" | "form"`
/// - `diameter`: `float`，刀具直径
/// - `corner_radius`: `float`，圆角半径
/// - `cutting_length`: `float`，切削刃长
/// - `voxel_size`: `float`，体素边长
/// - `taper_angle_deg`: `float`，仅锥度/球头锥度使用
/// - `form_profile`: `list[tuple[float, float]] | None`，仅 `form` 使用
///
/// # 返回
/// `(numpy.ndarray(dtype=bool, shape=(n,n,n)), info_dict)`：
///   - 第一个元素是 3D 刀具掩码（**零拷贝**：`shape` 与 `bits` 来自 Rust 端）
///   - 第二个元素是元数据字典
#[pyfunction]
#[pyo3(signature = (tool_type, diameter, corner_radius, cutting_length, voxel_size, taper_angle_deg=0.0, form_profile=None))]
fn vc_build_tool_mask<'py>(
    py: Python<'py>,
    tool_type: &str,
    diameter: f64,
    corner_radius: f64,
    cutting_length: f64,
    voxel_size: f64,
    taper_angle_deg: f64,
    form_profile: Option<&Bound<'_, PyAny>>,
) -> PyResult<(
    Bound<'py, numpy::PyArray<bool, numpy::Ix3>>,
    Bound<'py, PyDict>,
)> {
    with_panic_guard(|| {
        let tt = ToolType::parse(tool_type)
            .map_err(|e| PyValueError::new_err(format!("invalid tool_type: {e}")))?;

        // 成形轮廓：转换成 Vec 存储到 arena 中
        let arena: Vec<(f64, f64)> = if let Some(fp) = form_profile {
            if matches!(tt, ToolType::Form) {
                extract_form_profile(fp)?
            } else {
                Vec::new()
            }
        } else {
            Vec::new()
        };

        let geom = ToolGeometry {
            tool_type: tt,
            diameter,
            corner_radius,
            cutting_length,
            taper_angle_deg,
            form_profile: if arena.is_empty() {
                &[]
            } else {
                arena.as_slice()
            },
        };
        let (shape, bits) = build_tool_mask(&geom, voxel_size)
            .map_err(|e| PyValueError::new_err(format!("build_tool_mask failed: {e}")))?;

        // 把位图展开为 flat bool 数组（n×n×n）
        let total = shape.total();
        let mut flat = vec![false; total];
        for (word_idx, &word) in bits.iter().enumerate() {
            for bit in 0..64 {
                if (word >> bit) & 1 == 1 {
                    let linear = word_idx * 64 + bit;
                    if linear < total {
                        flat[linear] = true;
                    }
                }
            }
        }
        let array = numpy::PyArray::from_vec_bound(py, flat)
            .reshape([shape.nx, shape.ny, shape.nz])
            .map_err(|e| PyValueError::new_err(format!("reshape failed: {e}")))?;

        // 元数据
        let set_bits: usize = bits.iter().map(|w| w.count_ones() as usize).sum();
        let info = PyDict::new_bound(py);
        info.set_item("nx", shape.nx)?;
        info.set_item("ny", shape.ny)?;
        info.set_item("nz", shape.nz)?;
        info.set_item("voxel_size", voxel_size)?;
        info.set_item("set_bits", set_bits as i64)?;
        info.set_item("tool_type", tool_type)?;
        info.set_item("diameter", diameter)?;
        info.set_item("corner_radius", corner_radius)?;
        info.set_item("cutting_length", cutting_length)?;
        info.set_item("taper_angle_deg", taper_angle_deg)?;
        Ok((array, info))
    })
}

/// 离散化直线路径。
#[pyfunction]
fn vc_discretize_linear_segment(
    py: Python<'_>,
    start: (f64, f64, f64),
    end: (f64, f64, f64),
    step: f64,
) -> PyResult<Bound<'_, numpy::PyArray<f64, numpy::Ix2>>> {
    with_panic_guard(|| {
        let s = [start.0, start.1, start.2];
        let e = [end.0, end.1, end.2];
        let flat: Vec<f64> = py.allow_threads(|| discretize_linear_segment(s, e, step));
        let n = flat.len() / 3;
        let arr = numpy::PyArray::from_vec_bound(py, flat)
            .reshape([n, 3])
            .map_err(|e| PyValueError::new_err(format!("reshape failed: {e}")))?;
        Ok(arr)
    })
}

/// 端到端基准测试入口（用于 Python 端性能测试与 Rust 单测交叉验证）。
///
/// # 参数
/// - `grid_size`: 网格单边长度（立方体）。
/// - `diameter`: 刀具直径。
/// - `voxel_size`: 体素边长。
/// - `num_points`: 刀位点数。
///
/// # 返回
/// `dict`：`{elapsed_ms, removed, points}`
#[pyfunction]
#[pyo3(signature = (grid_size=100, diameter=8.0, voxel_size=1.0, num_points=100))]
fn vc_benchmark_mask_cut<'py>(
    py: Python<'py>,
    grid_size: usize,
    diameter: f64,
    voxel_size: f64,
    num_points: usize,
) -> PyResult<Bound<'py, PyDict>> {
    with_panic_guard(|| {
        if grid_size == 0 || num_points == 0 {
            return Err(PyValueError::new_err(
                "grid_size and num_points must be > 0",
            ));
        }
        // 1. 构建全实心网格
        let shape = VoxelGridShape::new(grid_size, grid_size, grid_size)
            .map_err(|e| PyValueError::new_err(format!("shape: {e}")))?;
        let mut grid = VoxelGrid::new(shape);
        for i in 0..shape.total() {
            grid.set_linear(i, true);
        }
        // 2. 构建刀具掩码
        let geom = ToolGeometry::new(ToolType::Flat, diameter, 0.0, diameter);
        let (t_shape, t_bits) = build_tool_mask(&geom, voxel_size)
            .map_err(|e| PyValueError::new_err(format!("mask: {e}")))?;
        // 3. 构造刀位点：均匀分布于网格中心区域
        let mut points = Vec::with_capacity(num_points * 3);
        let stride = (grid_size as f64) / ((num_points as f64).sqrt() + 1.0);
        let mut i = 0;
        let mut x = stride;
        while x < grid_size as f64 && i < num_points {
            let mut y = stride;
            while y < grid_size as f64 && i < num_points {
                points.push(x);
                points.push(y);
                points.push(grid_size as f64 * 0.5);
                y += stride;
                i += 1;
            }
            x += stride;
        }
        // 4. 计时
        let start_t = std::time::Instant::now();
        let result = py
            .allow_threads(|| {
                apply_tool_mask_batch(
                    &mut grid,
                    t_shape,
                    &t_bits,
                    &points,
                    [0.0, 0.0, 0.0],
                    voxel_size,
                    0.0,
                )
            })
            .map_err(|e| PyRuntimeError::new_err(format!("apply failed: {e}")))?;
        let elapsed = start_t.elapsed();

        let dict = PyDict::new_bound(py);
        dict.set_item("elapsed_ms", elapsed.as_secs_f64() * 1000.0)?;
        dict.set_item("removed", result.removed as i64)?;
        dict.set_item("points", result.points as i64)?;
        dict.set_item("grid_voxels_remaining", grid.count_true() as i64)?;
        Ok(dict)
    })
}

// 工具函数

/// 提取 `bbox_min` 三元组。
fn extract_vec3(obj: &Bound<'_, pyo3::PyAny>) -> PyResult<[f64; 3]> {
    // 支持 (x, y, z) tuple、list、numpy 数组
    if let Ok(t) = obj.extract::<(f64, f64, f64)>() {
        return Ok([t.0, t.1, t.2]);
    }
    if let Ok(l) = obj.downcast::<PyList>() {
        if l.len() == 3 {
            return Ok([
                l.get_item(0)?.extract()?,
                l.get_item(1)?.extract()?,
                l.get_item(2)?.extract()?,
            ]);
        }
    }
    if let Ok(t) = obj.downcast::<PyTuple>() {
        if t.len() == 3 {
            return Ok([
                t.get_item(0)?.extract()?,
                t.get_item(1)?.extract()?,
                t.get_item(2)?.extract()?,
            ]);
        }
    }
    Err(PyValueError::new_err(
        "bbox_min must be a 3-element sequence",
    ))
}

/// 提取成形轮廓（list of (z, r) tuples）。
fn extract_form_profile(obj: &Bound<'_, pyo3::PyAny>) -> PyResult<Vec<(f64, f64)>> {
    let mut out = Vec::new();
    if let Ok(seq) = obj.extract::<Vec<(f64, f64)>>() {
        return Ok(seq);
    }
    if let Ok(l) = obj.downcast::<PyList>() {
        for item in l.iter() {
            if let Ok(t) = item.extract::<(f64, f64)>() {
                out.push(t);
            } else {
                return Err(PyValueError::new_err(
                    "form_profile items must be (z, r) tuples",
                ));
            }
        }
        return Ok(out);
    }
    Err(PyValueError::new_err(
        "form_profile must be a list of (z, r) tuples",
    ))
}

/// panic 安全网：将内部 panic 转换为 `PyRuntimeError`。
fn with_panic_guard<F, R>(f: F) -> PyResult<R>
where
    F: FnOnce() -> PyResult<R> + UnwindSafe,
{
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(r) => r,
        Err(e) => {
            let msg = if let Some(s) = e.downcast_ref::<String>() {
                s.clone()
            } else if let Some(s) = e.downcast_ref::<&str>() {
                (*s).to_string()
            } else {
                "unknown panic in compute engine".to_string()
            };
            Err(PyRuntimeError::new_err(format!(
                "Rust compute engine panicked: {msg}"
            )))
        }
    }
}

// Trait alias to allow AssertUnwindSafe on closures
use std::panic::UnwindSafe;
