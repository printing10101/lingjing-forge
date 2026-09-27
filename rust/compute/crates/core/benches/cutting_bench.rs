//! 体素切削核心算法基准测试。
//!
//! 使用 `cargo bench` 运行。
//! 典型输出：100×100×100 网格、100 刀位点、voxel=1.0、刀具直径=8mm。

use compute_core::{
    apply_tool_mask_batch, build_tool_mask, discretize_linear_segment, ToolGeometry, ToolType,
    VoxelGrid, VoxelGridShape,
};
use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};

fn build_solid_grid(n: usize) -> VoxelGrid {
    let shape = VoxelGridShape::new(n, n, n).unwrap();
    let mut g = VoxelGrid::new(shape);
    for i in 0..shape.total() {
        g.set_linear(i, true);
    }
    g
}

fn build_horizontal_line_points(n: usize) -> Vec<f64> {
    let mut pts = Vec::with_capacity(n * 3);
    let stride = (n as f64) / ((n as f64).sqrt() + 1.0);
    let mut k = 0;
    let mut x = stride;
    while x < n as f64 && k < n {
        let mut y = stride;
        while y < n as f64 && k < n {
            pts.push(x);
            pts.push(y);
            pts.push(n as f64 * 0.5);
            y += stride;
            k += 1;
        }
        x += stride;
    }
    pts
}

fn bench_mask_cut(c: &mut Criterion) {
    let mut group = c.benchmark_group("mask_cut");
    for &grid_size in &[10usize, 30, 50] {
        let mut grid = build_solid_grid(grid_size);
        let geom = ToolGeometry::new(ToolType::Flat, 8.0, 0.0, 8.0);
        let (t_shape, t_bits) = build_tool_mask(&geom, 1.0).unwrap();
        let pts = build_horizontal_line_points(50);

        group.bench_with_input(
            BenchmarkId::from_parameter(grid_size),
            &grid_size,
            |b, _| {
                b.iter(|| {
                    let mut g = grid.clone();
                    let r = apply_tool_mask_batch(
                        &mut g,
                        t_shape,
                        &t_bits,
                        &pts,
                        [0.0, 0.0, 0.0],
                        1.0,
                        0.0,
                    )
                    .unwrap();
                    criterion::black_box(r.removed);
                });
            },
        );
    }
    group.finish();
}

fn bench_build_mask(c: &mut Criterion) {
    let mut group = c.benchmark_group("build_mask");
    for &diameter in &[4.0f64, 8.0, 16.0] {
        group.bench_with_input(BenchmarkId::from_parameter(diameter), &diameter, |b, &d| {
            let geom = ToolGeometry::new(ToolType::Ball, d, d * 0.5, d * 5.0);
            b.iter(|| {
                let (s, bits) = build_tool_mask(&geom, 0.5).unwrap();
                criterion::black_box((s.total(), bits.len()));
            });
        });
    }
    group.finish();
}

fn bench_discretize(c: &mut Criterion) {
    c.bench_function("discretize_long_line", |b| {
        b.iter(|| {
            let pts = discretize_linear_segment([0.0, 0.0, 0.0], [1000.0, 1000.0, 0.0], 0.5);
            criterion::black_box(pts.len());
        });
    });
}

criterion_group!(benches, bench_mask_cut, bench_build_mask, bench_discretize);
criterion_main!(benches);
