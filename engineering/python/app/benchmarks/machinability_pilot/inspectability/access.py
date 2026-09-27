"""可检性（inspectability）几何层：由名义 CAD/mesh 自身算出"这一点有没有量具验得了"。

定位与边界（诚实声明）
----------------------
本模块**不**评估"造不造得出来"（那是 S4 三轴可达性 / S5 加工物理链的职责），也**不**
做测量仿真。它回答一个正交问题：认证该名义几何所需的测量不确定度是多少——由几何
本身（视线可达性、入射角、测头球与柄部间隙）决定。

主指标是无量纲的**不确定度预算**::

    U95_required / T = 1 / (k * P)

* ``T`` 公差带宽度（双边带，±0.10 mm → T = 0.20 mm）
* ``k`` 护栏因子，取 3（判据见 criteria_frozen_v1.json → decision_rule.derivation）
* ``P`` 几何惩罚 = 面积分位意义上的最大可达 sec(入射角)，纯由名义几何算出

P 越大（越斜、越遮挡），允许的 U95 越小，即"设计越难被验证"。**零自由参数**：量具
指标表只用于把 U95/T 翻译成"哪一档量具够用"，不进入结论，读者可替换自己的厂商值重算。

近似与失效模式记在 criteria_frozen_v1.json 的 assumptions 里。错误消息遵循仓库约定：
``[错误类型] 描述。建议操作：[具体步骤]``。

已知边界（实测得出，别绕过）
----------------------------
``penalty`` 通道被模型自身的上界锁死：只有入射角 ≤ θ_max 的视线才计入
候选，故 ``P = sec(theta_min) ≤ sec(theta_max)``——θ_max=60° 时 P 上界恰为 2.0，
``U95_required/T = 1/(kP)`` 的下界被钉在 1/6。也就是说**惩罚通道无法把"极难检"与
"较难检"分开**，遮挡信息全部落在 ``frac_no_optic_view`` / ``frac_no_tactile_reach``
两个面积分数通道里。用惩罚做闸门会恒不触发（v1.1 的 M2 就是这么判 0 的，已记入
criteria 的 revisions）。要扩大惩罚动态范围只能放宽 θ_max，但那同时会接受更多掠射
视线、低估惩罚——两难是结构性的，需在判据层面显式取舍。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

SCHEMA = "inspectability-criteria"
_CRITERIA_PATH = Path(__file__).parent / "criteria_frozen_v1.json"


class CriteriaError(ValueError):
    """冻结判据文件缺失、schema 不符或参数越界。"""


@dataclass(frozen=True)
class GeometryCriteria:
    """冻结的几何评估参数（一次加载，全程只读）。"""

    guard_factor_k: float
    surface_samples: int
    optic_max_incidence_deg: float
    ray_epsilon_mm: float
    ray_length_mm: float
    collision_samples: int
    ball_radius_mm: float
    holder_radius_mm: float
    holder_length_mm: float
    tangent_tolerance_mm: float
    collision_tolerance_mm: float
    seed: int
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @staticmethod
    def load(path: Path | None = None) -> GeometryCriteria:
        """读入并校验冻结判据。"""
        src = path or _CRITERIA_PATH
        if not src.exists():
            raise CriteriaError(
                f"[判据缺失] 冻结判据文件不存在 path={src}。建议操作：从版本库恢复 criteria_frozen_v1.json。"
            )
        doc = json.loads(src.read_text(encoding="utf-8"))
        if doc.get("schema") != SCHEMA:
            raise CriteriaError(
                f"[判据不符] schema={doc.get('schema')!r} != {SCHEMA!r}。建议操作：核对判据版本，勿混用。"
            )
        geo = doc["geometry_model"]
        crit = GeometryCriteria(
            guard_factor_k=float(doc["decision_rule"]["guard_factor_k"]),
            surface_samples=int(geo["surface_samples"]),
            optic_max_incidence_deg=float(geo["optic_max_incidence_deg"]),
            ray_epsilon_mm=float(geo["ray_epsilon_mm"]),
            ray_length_mm=float(geo["ray_length_mm"]),
            collision_samples=int(geo["density_samples_for_collision"]),
            ball_radius_mm=float(geo["ball_radius_mm"]),
            holder_radius_mm=float(geo["holder_radius_mm"]),
            holder_length_mm=float(geo["holder_length_mm"]),
            tangent_tolerance_mm=float(geo["tangent_tolerance_mm"]),
            collision_tolerance_mm=float(geo["collision_tolerance_mm"]),
            seed=int(geo["seed"]),
            raw=doc,
        )
        _validate_criteria(crit)
        return crit


def _validate_criteria(crit: GeometryCriteria) -> None:
    checks = {
        "guard_factor_k > 0": crit.guard_factor_k > 0,
        "surface_samples >= 50": crit.surface_samples >= 50,
        "0 < optic_max_incidence_deg < 90": 0 < crit.optic_max_incidence_deg < 90,
        "ball_radius_mm > 0": crit.ball_radius_mm > 0,
        "holder_radius_mm > ball_radius_mm": crit.holder_radius_mm > crit.ball_radius_mm,
        "ray_epsilon_mm > 0": crit.ray_epsilon_mm > 0,
        "tangent_tolerance_mm > 0": crit.tangent_tolerance_mm > 0,
        "0 < collision_tolerance_mm < ball_radius_mm": 0 < crit.collision_tolerance_mm < crit.ball_radius_mm,
    }
    bad = [name for name, ok in checks.items() if not ok]
    if bad:
        raise CriteriaError(
            f"[判据越界] 参数非法 {bad}。建议操作：修正 criteria_frozen_v1.json 并升 version，不得回写旧版。"
        )


def view_directions() -> np.ndarray:
    """单位球面近似均匀的候选视线方向（细分二十面体顶点，42 个）。"""
    verts = np.asarray(trimesh.creation.icosphere(subdivisions=1, radius=1.0).vertices, dtype=np.float64)
    return verts / np.linalg.norm(verts, axis=1)[:, None]


def _area_weighted_samples(mesh: trimesh.Trimesh, count: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """按面积均匀采表面点，返回 (点, 该面外法向)。面积均匀 ⟹ 样本等权。"""
    pts, face_idx = trimesh.sample.sample_surface(mesh, count, seed=np.random.default_rng(seed))
    normals = mesh.face_normals[np.asarray(face_idx, dtype=int)]
    return np.asarray(pts, dtype=np.float64), np.asarray(normals, dtype=np.float64)


def _occluded(mesh: trimesh.Trimesh, origins: np.ndarray, dirs: np.ndarray, length: float) -> np.ndarray:
    """逐射线判断"从表面点沿 dirs 出去的路上，会不会先撞到别的皮"。

    方向约定：dirs 是**量具所在方位**（从表面指向工具）。必须沿 +dirs 向前投而不是
    沿 −dirs 向后打——后者会在 eps 处立刻命中采样点自身所在的那一片皮，把每一个
    可见点都判成遮挡（这个坑是实测踩出来的，开阔顶面 900 个采样点全不可见）。
    起点已沿 dirs 外推 ray_epsilon_mm，故自身切平面不会再被命中。
    """
    blocked = np.zeros(len(origins), dtype=bool)
    if not len(origins):
        return blocked
    loc, index_ray, _index_tri = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    if len(index_ray):
        rays = np.asarray(index_ray, dtype=int)
        if rays.max() >= len(origins):  # pragma: no cover - 只可能是 trimesh 语义变更
            raise RuntimeError(
                f"[接口假设失效] index_ray 越界 max={rays.max()} >= n_rays={len(origins)}。"
                "建议操作：核对 trimesh 版本 intersects_location 的返回顺序。"
            )
        dist = np.linalg.norm(np.asarray(loc, dtype=np.float64) - origins[rays], axis=1)
        blocked[rays[dist <= length]] = True
    return blocked


def optic_penalty_field(
    mesh: trimesh.Trimesh,
    pts: np.ndarray,
    normals: np.ndarray,
    crit: GeometryCriteria,
    dirs: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """每点的光学几何惩罚 sec(theta_min) 与可见性。

    可见定义：存在视线方向 v 使 v 落在外法向半球内、入射角 ≤ optic_max_incidence_deg，
    且从 p + eps*v 沿 -v 回退时不先撞到自身几何。找不到这样的 v ⟹ 该工位下光学不可达，
    penalty = inf。

    Returns
    -------
    penalty : (N,) float，不可达处为 np.inf
    reachable : (N,) bool
    """
    v_dirs = view_directions() if dirs is None else dirs
    cos_limit = float(np.cos(np.radians(crit.optic_max_incidence_deg)))
    cos_best = np.zeros(len(pts))
    for v in v_dirs:
        cos_nv = normals @ v
        eligible = np.flatnonzero(cos_nv >= cos_limit)
        if not len(eligible):
            continue
        origins = pts[eligible] + crit.ray_epsilon_mm * v
        rays_v = np.repeat(v[None, :], len(eligible), axis=0)
        free = ~_occluded(mesh, origins, rays_v, crit.ray_length_mm)
        hit_idx = eligible[free]
        better = cos_best[hit_idx] < cos_nv[hit_idx]
        cos_best[hit_idx[better]] = cos_nv[hit_idx][better]
    reachable = cos_best > 0.0
    penalty = np.full(len(pts), np.inf)
    penalty[reachable] = 1.0 / cos_best[reachable]
    return penalty, reachable


def tactile_reach_field(
    mesh: trimesh.Trimesh, pts: np.ndarray, normals: np.ndarray, crit: GeometryCriteria
) -> np.ndarray:
    """每点测头（球 + 柄）能否贴到名义面。返回 (N,) bool。

    物理模型：摆杆式扫描测头——球心落在 c = p + r·n，柄部沿**接触法向 n** 外伸。
    两条判据：(1) 球不与别处的皮干涉；(2) 柄部柱段与面保持 holder_radius 间隙。

    近似 A3：用密集面采样的 KDTree 距离代替精确偏置面求交，误差量级 ~ 采样间距，
    故留 collision_tolerance_mm 余量。
    近似 A3b：切平面后侧（(q−p)·n ≤ tangent_tol）的采样点一律不参与球干涉——球整个
    落在接触点的前半空间里，后侧的几何不可能撞到它。少了这一步，"薄壁件顶盖内表面"
    会被自己的外壳误判成不可达（实测踩过）。
    未建模（已知局限）：摆杆进入封闭型腔所需的全局路径（那需要开孔）。叶片是外露件，
    此项次要；若要评内腔件，必须补。
    """
    from scipy.spatial import cKDTree

    dense, _ = _area_weighted_samples(mesh, crit.collision_samples, crit.seed + 1)
    tree = cKDTree(dense)
    r = crit.ball_radius_mm
    centers = pts + r * normals

    near = dense[np.asarray(tree.query(centers, k=32)[1], dtype=int)]  # (N,K,3)
    in_front = np.einsum("nkj,nj->nk", near - pts[:, None, :], normals) > crit.tangent_tolerance_mm
    hits_ball = np.linalg.norm(near - centers[:, None, :], axis=2) < r - crit.collision_tolerance_mm
    ok_ball = ~(in_front & hits_ball).any(axis=1)

    # 柄部间隙：沿 n 自 3r 起、长 holder_length 的柱段，要求离面 ≥ 柄半径
    stations = np.linspace(3 * r, 3 * r + crit.holder_length_mm, 5)
    axis_pts = pts[:, None, :] + normals[:, None, :] * stations[None, :, None]
    dist, idx = tree.query(axis_pts.reshape(-1, 3), k=1)
    dist = np.asarray(dist, dtype=np.float64).reshape(len(pts), len(stations))
    ok_holder = (dist >= crit.holder_radius_mm - crit.collision_tolerance_mm).all(axis=1)
    return ok_ball & ok_holder


def assess_case(
    mesh: trimesh.Trimesh,
    crit: GeometryCriteria,
    *,
    tolerance_band_mm: float,
    label: str = "",
) -> dict[str, Any]:
    """单个名义模型的可检性评定（S6）。

    ``penalty_p99`` 取面积分位而非最大值：单个掠射采样点不该决定整件的可判定性；
    ``penalty_max`` 一并落盘，供敏感性检查。
    """
    pts, normals = _area_weighted_samples(mesh, crit.surface_samples, crit.seed)
    penalty, reachable = optic_penalty_field(mesh, pts, normals, crit)
    tactile_ok = tactile_reach_field(mesh, pts, normals, crit)

    finite = np.isfinite(penalty)
    p_p99 = float(np.percentile(penalty[finite], 99)) if finite.any() else float("inf")
    p_max = float(penalty[finite].max()) if finite.any() else float("inf")
    budget = 1.0 / (crit.guard_factor_k * p_p99) if np.isfinite(p_p99) else 0.0

    def area_fraction(mask: np.ndarray) -> float:
        # 面积均匀采样 ⟹ 计数比即面积比
        return float(mask.mean()) if len(mask) else 0.0

    return {
        "label": label,
        "tolerance_band_mm": tolerance_band_mm,
        "guard_factor_k": crit.guard_factor_k,
        "penalty_p99": p_p99,
        "penalty_max": p_max,
        "uncertainty_budget_U95_over_T": budget,
        "U95_required_mm": budget * tolerance_band_mm,
        "frac_no_optic_view": area_fraction(~reachable),
        "frac_no_tactile_reach": area_fraction(~tactile_ok),
        "surface_area_mm2": float(mesh.area),
        "n_samples": int(len(pts)),
        "criteria_version": crit.raw.get("version"),
    }
