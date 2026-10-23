"""可辨识性诊断探针（2026-10-23）。

背景
----
`seed_sweep.py` 把"最低 RMSE 的 PCA 候选是否回到生成用的 180° 逆变换"记为成功，
据此报告对称叶片 8/10、带弯度叶片 10/10。这个指标在**自对称**（self-symmetric）构件上
不成立：生成变换 G 把形状映到自身，于是"真位姿"与"对称伴位姿"对数据的拟合程度在
采样噪声量级上相同，argmin 只是在这两个等价解之间抛硬币。8/10 不是精度，是抽样波动。

本探针改问四个可测的量：

1. ``symmetry_residual_mm``（形状内在量）
   把形状点云按 G 映射后与自身点云比对的 RMSE。若它与**双采样底噪**（同一形状两次
   独立采样的 RMSE）同量级，则该形状在 G 下自对称，位姿**不由几何唯一决定**。
   **这一判定不需要跑任何配准器**，是设计阶段的判据。
2. ``delta_fixed_mm``（假设检验量）
   两个**无自由参数**的位姿假设下的残差之差：H_truth = 已知的 G⁻¹，H_mate = 恒等。
   两个都是固定变换，不引入 ICP 优化优势，因此差值只反映"数据能否区分两种位姿"。
3. ``D = delta / sigma``（可辨识度）
   ``sigma`` 由**配对自助法**给出——两个假设共用同一份源点，差值方差必须配对估计。
   同时输出 ``P(delta > 0)``：自助重采样中"真位姿更优"的频率。P≈0.5 即抛硬币。
4. ``strict_success`` / ``class_counts``（配准器行为）
   PCA 四符号候选 + ICP 之后，各候选落在哪个等价类。对称件上应看到两类都有候选且
   argmin 随机跳变，这正是 8/10 的成因。

dose 模式（补扫剂量—响应）
--------------------------
只在前缘带（0 ≤ x ≤ le_band_mm）采样 N 点，构造"局部补扫"点云，测 D 随 (弯度 m, N) 的变化。
预期并检验：
* m = 0 时 D 恒为 0——自对称是**几何**性质，补扫再多同形点也救不回来；
* m > 0 时 D 随 N 上升，存在 N* 使 D ≥ 2——补扫把"近似对称"推到可辨阈值之上。
这条曲线才是"补扫规划"的定量依据，不是"多扫点总归更好"的直觉。

用法
----
    python identifiability_probe.py --mode baseline --seeds 20 --workers 3
    python identifiability_probe.py --mode dose --seeds 6 --workers 3

产物：``artifacts/identifiability_<mode>_<YYYYMMDD>/results.json``
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from pca_core import pca_candidates
from registration_demo import icp_point_to_point, pose_errors, sample_surface, step_to_mesh
from symmetry_probe import (
    ARTIFACT_DIR,
    NOISE_MM,
    SEED,
    angle_vs_identity,
    build_case,
    prepare_flip_case,
    rmse_to_target,
)

POINTS = 20_000
LE_BAND_MM = 3.2  # 0.08 × 40 mm 弦长
BOOTSTRAP = 200
POSE_ROT_TOL_DEG = 1.0
POSE_TRANS_TOL_MM = 0.5
DISTINGUISHABLE_D = 2.0

CASES = {
    "symmetric_naca0012": ({"m": 0.0, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0}, "x"),
    "cambered_naca2412": ({"m": 0.02, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0}, "x"),
    "twisted_tapered_naca2412": ({"m": 0.02, "p": 0.4, "t": 0.12, "twist_deg": 20.0, "taper": 0.25}, "x"),
    "cambered_naca2412_yaw_control": ({"m": 0.02, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0}, "z"),
}


# --------------------------------------------------------------------------- #
# 形状内在量
# --------------------------------------------------------------------------- #
def sampling_floor(mesh, seed: int, points: int = POINTS) -> float:
    """同一形状两次独立采样之间的 RMSE——任何"残差"的下界参考。"""
    a = sample_surface(mesh, points, seed=seed)
    b = sample_surface(mesh, points, seed=seed + 7777)
    return rmse_to_target(a, b)


def symmetry_residual(mesh, rotation: np.ndarray, translation: np.ndarray, seed: int, points: int = POINTS) -> float:
    """G 是否把该形状映到自身：映射后与自身点云的 RMSE。"""
    a = sample_surface(mesh, points, seed=seed)
    b = sample_surface(mesh, points, seed=seed + 1)
    return rmse_to_target((rotation @ a.T).T + translation, b)


# --------------------------------------------------------------------------- #
# 假设检验量
# --------------------------------------------------------------------------- #
def _paired_bootstrap(aligned_truth: np.ndarray, aligned_mate: np.ndarray, target: np.ndarray, seed: int) -> dict:
    """配对自助：同一份重采样源点下，两个位姿假设的 RMSE 差分布。"""
    from scipy.spatial import cKDTree

    tree = cKDTree(target)
    rng = np.random.default_rng(seed + 20261023)
    n = len(aligned_truth)
    deltas = np.empty(BOOTSTRAP)
    for i in range(BOOTSTRAP):
        idx = rng.integers(0, n, n)
        dt, _ = tree.query(aligned_truth[idx])
        dm, _ = tree.query(aligned_mate[idx])
        deltas[i] = math.sqrt(float(np.mean(dm**2))) - math.sqrt(float(np.mean(dt**2)))
    mean = float(deltas.mean())
    sigma = float(deltas.std(ddof=1))
    return {
        "delta_mm": mean,
        "sigma_mm": sigma,
        "p_truth_better": float((deltas > 0).mean()),
        "D": float(mean / sigma) if sigma > 0 else float("inf"),
    }


def fixed_pose_test(source: np.ndarray, target: np.ndarray, rotation_gen: np.ndarray, translation_gen: np.ndarray, seed: int) -> dict:
    """H_truth = G⁻¹（已知生成变换的逆） vs H_mate = 恒等；两者均无自由参数。"""
    ideal_rotation = rotation_gen.T
    ideal_translation = -rotation_gen.T @ translation_gen
    aligned_truth = (ideal_rotation @ source.T).T + ideal_translation
    aligned_mate = source  # 恒等假设：保持原位
    return _paired_bootstrap(aligned_truth, aligned_mate, target, seed)


def _classify(rotation_total, translation_total, rotation_gen, translation_gen) -> str:
    rot_err, trans_err = pose_errors(rotation_total, translation_total, rotation_gen, translation_gen)
    if rot_err < POSE_ROT_TOL_DEG and trans_err < POSE_TRANS_TOL_MM:
        return "truth"
    if angle_vs_identity(rotation_total) < POSE_ROT_TOL_DEG and float(np.linalg.norm(translation_total)) < POSE_TRANS_TOL_MM:
        return "mate"
    return "other"


# --------------------------------------------------------------------------- #
# baseline：全点云 + PCA/ICP 行为
# --------------------------------------------------------------------------- #
def run_seed(name: str, axis: str, seed: int, blade_kwargs: dict | None = None) -> dict:
    blade_kwargs = blade_kwargs or CASES[name][0]
    step = build_case(name, **blade_kwargs)
    prepared = prepare_flip_case(name, step, axis, seed=seed)
    mesh = prepared["mesh"]
    target = prepared["target"]
    source = prepared["source"]
    rotation_gen = prepared["rotation_gen"]
    translation_gen = prepared["translation_gen"]

    sym_resid = symmetry_residual(mesh, rotation_gen, translation_gen, seed)
    floor = sampling_floor(mesh, seed)
    fixed = fixed_pose_test(source, target, rotation_gen, translation_gen, seed)

    candidates = []
    for cand in pca_candidates(source, target):
        rotation0 = cand["rotation"]
        translation0 = cand["translation"]
        source_init = (rotation0 @ source.T).T + translation0
        result = icp_point_to_point(source_init, target, max_iter=80, tol=1e-8)
        rotation_total = result.rotation @ rotation0
        translation_total = result.rotation @ translation0 + result.translation
        rot_err, trans_err = pose_errors(rotation_total, translation_total, rotation_gen, translation_gen)
        candidates.append(
            {
                "signs": list(cand["signs"]),
                "rmse_mm": rmse_to_target(result.aligned, target),
                "class": _classify(rotation_total, translation_total, rotation_gen, translation_gen),
                "rotation_error_vs_generating_inverse_deg": rot_err,
                "translation_error_vs_generating_inverse_mm": trans_err,
                "rotation_error_vs_identity_deg": angle_vs_identity(rotation_total),
            }
        )
    candidates.sort(key=lambda item: item["rmse_mm"])
    best = candidates[0]

    return {
        "case": name,
        "axis": axis,
        "seed": seed,
        "symmetry_residual_mm": sym_resid,
        "sampling_floor_mm": floor,
        "symmetry_floor_ratio": sym_resid / floor if floor > 0 else float("inf"),
        "delta_fixed_mm": fixed["delta_mm"],
        "sigma_mm": fixed["sigma_mm"],
        "D": fixed["D"],
        "p_truth_better": fixed["p_truth_better"],
        "distinguishable": fixed["D"] >= DISTINGUISHABLE_D and fixed["p_truth_better"] >= 0.95,
        "best_class": best["class"],
        "best_rmse_mm": best["rmse_mm"],
        "strict_success": best["class"] == "truth",
        "class_counts": {c: sum(1 for k in candidates if k["class"] == c) for c in ("truth", "mate", "other")},
        "candidates": candidates,
    }


# --------------------------------------------------------------------------- #
# dose：前缘带局部补扫
# --------------------------------------------------------------------------- #
def le_band_points(mesh, n: int, seed: int, band: tuple[float, float] | None = None) -> np.ndarray:
    """在 x ∈ [lo, hi] 的带状区域取 n 点。默认前缘带（曲率最高但弯度信息最少，是保守选择）。"""
    lo, hi = band or (0.0, LE_BAND_MM)
    pool = sample_surface(mesh, max(8000, n * 20), seed=seed + 31337)
    band_mask = (pool[:, 0] >= lo) & (pool[:, 0] <= hi)
    band = pool[band_mask]
    if len(band) < n:
        raise RuntimeError(f"前缘带点数不足：需要 {n}，实际 {len(band)}")
    return band[:n]


def _surface_distances(mesh, points: np.ndarray) -> np.ndarray:
    """到名义 CAD 曲面的最近点距离（trimesh 最近点投影，不受参考点云密度影响）。"""
    _, distance, _ = mesh.nearest.on_surface(np.asarray(points, dtype=np.float64))
    return np.asarray(distance, dtype=np.float64)


def _bootstrap_surface_delta(d_truth: np.ndarray, d_mate: np.ndarray, seed: int) -> dict:
    """配对自助（按扫描点重采样）：两个位姿假设下的 RMS 曲面偏差之差。"""
    rng = np.random.default_rng(seed + 20261023)
    n = len(d_truth)
    deltas = np.empty(BOOTSTRAP)
    for i in range(BOOTSTRAP):
        idx = rng.integers(0, n, n)
        deltas[i] = math.sqrt(float(np.mean(d_mate[idx] ** 2))) - math.sqrt(float(np.mean(d_truth[idx] ** 2)))
    mean = float(deltas.mean())
    sigma = float(deltas.std(ddof=1))
    return {
        "delta_mm": mean,
        "sigma_mm": sigma,
        "p_truth_better": float((deltas > 0).mean()),
        "D": float(mean / sigma) if sigma > 0 else float("inf"),
    }


def run_local_scan(
    name: str,
    axis: str,
    seed: int,
    n_points: int,
    blade_kwargs: dict | None = None,
    band: tuple[float, float] | None = None,
) -> dict:
    """局部补扫 N 点：到**名义曲面**的偏差差即"补扫剂量—响应"。

    参考基准是 CAD 曲面本身（最近点投影），不是另一份点云——否则"点少 = 残差大"
    会把采样稀疏误读成位姿不可辨（2026-10-23 首版踩过这个坑，已改）。
    """
    from scipy.spatial.transform import Rotation as Rot

    blade_kwargs = blade_kwargs or CASES[name][0]
    step = build_case(name, **blade_kwargs)
    mesh, _ = step_to_mesh(step, step.parent / "blade_fine.stl")

    if axis == "x":
        rotation_gen = Rot.from_euler("x", 180.0, degrees=True).as_matrix()
        translation_gen = np.array([0.0, 0.0, 60.0])
    elif axis == "z":
        rotation_gen = Rot.from_euler("z", 180.0, degrees=True).as_matrix()
        translation_gen = np.array([40.0, 0.0, 0.0])
    else:
        raise ValueError(axis)

    scan_pts = le_band_points(mesh, n_points, seed, band)
    rng = np.random.default_rng(seed + 100)
    scan = (rotation_gen @ scan_pts.T).T + translation_gen
    scan = scan + rng.normal(0.0, NOISE_MM, scan.shape)

    # H_truth: scan 按已知 G⁻¹ 复原；H_mate: 保持原位（"没翻转"假设）
    aligned_truth = (rotation_gen.T @ scan.T).T + (-rotation_gen.T @ translation_gen)
    d_truth = _surface_distances(mesh, aligned_truth)
    d_mate = _surface_distances(mesh, scan)

    result = _bootstrap_surface_delta(d_truth, d_mate, seed)
    return {
        "case": name,
        "axis": axis,
        "seed": seed,
        "n_points": n_points,
        "band_mm": list(band or (0.0, LE_BAND_MM)),
        "delta_fixed_mm": result["delta_mm"],
        "sigma_mm": result["sigma_mm"],
        "D": result["D"],
        "p_truth_better": result["p_truth_better"],
        "distinguishable": result["D"] >= DISTINGUISHABLE_D and result["p_truth_better"] >= 0.95,
        "rms_truth_mm": float(np.sqrt(np.mean(d_truth**2))),
        "rms_mate_mm": float(np.sqrt(np.mean(d_mate**2))),
    }


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def _binomial_two_sided(successes: int, n: int, p: float = 0.5) -> float:
    def pmf(k):
        return math.comb(n, k) * p**k * (1 - p) ** (n - k)

    observed = pmf(successes)
    return float(min(1.0, sum(pmf(k) for k in range(n + 1) if pmf(k) <= observed + 1e-15)))


def summarize_baseline(rows: list[dict]) -> dict:
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        by_case.setdefault(row["case"], []).append(row)

    summary = {}
    for case, items in by_case.items():
        n = len(items)
        strict = sum(1 for r in items if r["strict_success"])
        summary[case] = {
            "n": n,
            "strict_success": strict,
            "strict_rate": strict / n,
            "binomial_p_vs_coinflip": _binomial_two_sided(strict, n),
            "symmetry_residual_median_mm": float(np.median([r["symmetry_residual_mm"] for r in items])),
            "sampling_floor_median_mm": float(np.median([r["sampling_floor_mm"] for r in items])),
            "symmetry_floor_ratio_median": float(np.median([r["symmetry_floor_ratio"] for r in items])),
            "delta_fixed_median_mm": float(np.median([r["delta_fixed_mm"] for r in items])),
            "D_median": float(np.median([r["D"] for r in items])),
            "p_truth_better_median": float(np.median([r["p_truth_better"] for r in items])),
            "distinguishable_rate": sum(1 for r in items if r["distinguishable"]) / n,
            "mate_candidate_rate": sum(1 for r in items if r["class_counts"]["mate"] > 0) / n,
        }
    return summary


def summarize_dose(rows: list[dict]) -> dict:
    grid: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        grid.setdefault(row["case"], {}).setdefault(str(row["n_points"]), []).append(row)
    out = {}
    for case, by_n in grid.items():
        out[case] = {}
        for n_points, items in sorted(by_n.items(), key=lambda kv: int(kv[0])):
            out[case][n_points] = {
                "n": len(items),
                "delta_median_mm": float(np.median([r["delta_fixed_mm"] for r in items])),
                "D_median": float(np.median([r["D"] for r in items])),
                "p_truth_better_median": float(np.median([r["p_truth_better"] for r in items])),
                "distinguishable_rate": sum(1 for r in items if r["distinguishable"]) / len(items),
            }
    return out


def _print_baseline(summary: dict) -> None:
    header = (
        f"{'case':30} {'n':>3} {'strict':>8} {'p(coin)':>8} {'sym/floor':>9} "
        f"{'Δ(mm)':>9} {'D':>7} {'P(Δ>0)':>7} {'可辨率':>6} {'mate候选':>8}"
    )
    print(header)
    print("-" * len(header))
    for case, s in summary.items():
        print(
            f"{case:30} {s['n']:>3} {s['strict_success']:>3}/{s['n']:<3} "
            f"{s['binomial_p_vs_coinflip']:>8.3f} {s['symmetry_floor_ratio_median']:>9.2f} "
            f"{s['delta_fixed_median_mm']:>9.4f} {s['D_median']:>7.2f} "
            f"{s['p_truth_better_median']:>7.3f} {s['distinguishable_rate']:>6.2f} {s['mate_candidate_rate']:>8.2f}"
        )


def _print_dose(summary: dict) -> None:
    print(f"{'case':28} {'N':>5} {'Δ(mm)':>9} {'D':>7} {'P(Δ>0)':>7} {'可辨率':>6}")
    print("-" * 66)
    for case, by_n in summary.items():
        for n_points, s in by_n.items():
            print(
                f"{case:28} {n_points:>5} {s['delta_median_mm']:>9.5f} {s['D_median']:>7.2f} "
                f"{s['p_truth_better_median']:>7.3f} {s['distinguishable_rate']:>6.2f}"
            )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def _worker_baseline(task):
    name, axis, seed = task[0], task[1], task[2]
    blade_kwargs = task[3] if len(task) > 3 else None
    return run_seed(name, axis, seed, blade_kwargs)


def _worker_dose(task):
    name, axis, seed, n_points, blade_kwargs, band = task
    return run_local_scan(name, axis, seed, n_points, blade_kwargs, band)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "dose"), default="baseline")
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--baseline-cases", default="", help="逗号分隔的 CASES 键；dose_m* 会自动注册")
    parser.add_argument("--dose-m", default="0.0,0.002,0.01,0.02")
    parser.add_argument("--dose-n", default="50,100,200,400,800")
    parser.add_argument("--band-lo", type=float, default=0.0, help="补扫带 x 下界 (mm)")
    parser.add_argument("--band-hi", type=float, default=LE_BAND_MM, help="补扫带 x 上界 (mm)")
    args = parser.parse_args()

    started = time.time()
    if args.mode == "baseline":
        wanted = [c for c in args.baseline_cases.split(",") if c] or list(CASES)
        for case_name in wanted:
            if case_name.startswith("dose_m") and case_name not in CASES:
                m = float(case_name.replace("dose_m", ""))
                CASES[case_name] = ({"m": m, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0}, "x")
                build_case(case_name, **CASES[case_name][0])
        tasks = [
            (case_name, CASES[case_name][1], SEED + i, dict(CASES[case_name][0]))
            for case_name in wanted
            for i in range(args.seeds)
        ]
        worker = _worker_baseline
        tag = "baseline"
    else:
        tasks = []
        for m in (float(x) for x in args.dose_m.split(",")):
            case_name = f"dose_m{m:.3f}"
            CASES[case_name] = ({"m": m, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0}, "x")
            # 串行预生成 STEP：多进程同时导出同一文件会写坏几何
            build_case(case_name, **CASES[case_name][0])
            for n_points in (int(x) for x in args.dose_n.split(",")):
                for i in range(args.seeds):
                    band_range = (args.band_lo, args.band_hi)
                    tasks.append(
                        (case_name, "x", SEED + i, n_points, dict(CASES[case_name][0]), band_range)
                    )
        worker = _worker_dose
        tag = "dose"

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(worker, tasks), 1):
            rows.append(row)
            n_points = row.get("n_points", 0)
            print(
                f"[{i}/{len(tasks)}] {row['case']:26} seed={row['seed']} N={n_points:>4} "
                f"Δ={row['delta_fixed_mm']:+.5f} D={row['D']:6.2f} P={row['p_truth_better']:.3f}"
            )

    summary = summarize_baseline(rows) if tag == "baseline" else summarize_dose(rows)
    print()
    _print_baseline(summary) if tag == "baseline" else _print_dose(summary)

    # 目录名带算例集，避免不同算例组合互相覆盖（2026-10-23 首版踩过覆盖坑）
    cases_slug = "-".join(sorted({r["case"] for r in rows}))
    slug = cases_slug if len(cases_slug) <= 60 else f"{len({r['case'] for r in rows})}cases"
    band_suffix = f"_x{args.band_lo:g}-{args.band_hi:g}" if tag == "dose" else ""
    out_dir = ARTIFACT_DIR.parent / f"identifiability_{tag}_{slug}{band_suffix}_{time.strftime('%Y%m%d')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": tag,
        "config": {
            "points": POINTS,
            "bootstrap": BOOTSTRAP,
            "band_mm": [args.band_lo, args.band_hi],
            "distinguishable_D": DISTINGUISHABLE_D,
            "seeds_per_case": args.seeds,
            "pose_rot_tol_deg": POSE_ROT_TOL_DEG,
            "pose_trans_tol_mm": POSE_TRANS_TOL_MM,
        },
        "summary": summary,
        "rows": rows,
    }
    out = out_dir / "results.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult: {out}  ({time.time() - started:.1f}s)")


if __name__ == "__main__":
    main()
