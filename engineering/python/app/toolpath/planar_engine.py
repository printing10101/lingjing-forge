"""平面刀轨引擎（2.5D）：从真实几何计算铣削刀心轨迹。

定位与边界（诚实声明）
----------------------
- 本引擎填补的是此前缺失的"几何刀轨层"：给定封闭多边形轮廓与刀具直径，
  生成材料去除意义上正确的 G01 刀心轨迹——外环贴壁、向内步距环切（contour-parallel）、
  Z 分层（stepdown）、斜坡下刀（ramp plunge）、端面蛇形 raster。
- 仅支持直线段组成的简单多边形（无岛屿、无内孔）。圆弧轮廓请由调用方离散化后传入；
  带旋转的矩形轮廓可由调用方（G 代码 mixin）构造。
- 偏置自交清理采用"最早交叉分裂 + 环向分解"算法，并对每个环做偏置有效性校验
  （环上采样点到原始轮廓的距离 ≥ |偏置量|）。轮廓过窄、刀轨规模超限等情况会抛出
  :class:`PlanarToolpathError`，调用方必须回退到保守策略（模板走线/报人工）。
- 本引擎不处理曲面刀轨（cutter-location）、机床运动学仿真与刀柄/夹具干涉——
  这些属于 app/simulation/（体素切削、碰撞预筛）与外部 CAM 二次校验的职责；
  生成的 G 代码仍须通过 cam_validation 流程后方可上机。

算法要点
--------
1. 偏置：对多边形逐边沿法线偏置 d（正=向内收缩，负=向外扩张），相邻偏置线求交
   得到候选顶点；尖角处 miter 长度超过 ``miter_limit * |d|`` 时按比例截断。
2. 自交清理：沿环行走，遇到最早的线段交叉点 X 时，把 X 后的闭合子环拆出分别继续
   分解；拆完后按取向与偏置有效性过滤（顺时针伪环丢弃，CCW 合法环保留——
   这同时覆盖了"偏置导致区域分裂"的场景：每个子区域各自成为一个加工环）。
3. 退化环：挖槽偏置恰好使区域退化为线段时（如 40×20 槽、φ10 刀、d=10），
   保留为"中线清底刀轨"（面积≈0 但含 2 个相异顶点）。
4. 环切终止：从 d=刀具半径 开始，每次递增 stepover（始终相对原始轮廓计算，
   避免误差累积），偏置无有效环即停。stepover ≤ 刀具半径时环间材料完全覆盖
   （默认 stepover_ratio=0.5）。
5. 斜坡下刀：沿本环/本行路径以 ramp_angle 螺旋下降（整圈数均分深度），绕行圈数
   超过 max_laps 仍不足时退化为垂直下刀（调用方可依据返回值打注释）。

错误消息遵循仓库约定：``[错误类型] 描述。建议操作：[具体步骤]``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from app.core.error_taxonomy import ErrorCategory, ManufacturingError

__all__ = [
    "PlanarToolpathError",
    "PocketTooNarrowError",
    "ToolpathScaleExceededError",
    "ToolpathMove",
    "MillingToolpath",
    "PlanarToolpathEngine",
]

_EPS = 1e-9


class PlanarToolpathError(ManufacturingError):
    """平面刀轨计算失败（刀轨几何不可用，调用方应回退保守策略）。"""

    def __init__(self, detail: str, suggestion: str | None = None) -> None:
        super().__init__(
            ErrorCategory.TOOLPATH_GENERATION_FAILED,
            detail=detail,
            suggestion=suggestion or "建议：检查轮廓尺寸与刀具直径的匹配关系，或改用保守走刀策略。",
            recoverable=True,
        )


class PocketTooNarrowError(PlanarToolpathError):
    """加工区域窄于刀具直径，无法铣削。"""


class ToolpathScaleExceededError(PlanarToolpathError):
    """刀轨规模超出安全上限（防止病态轮廓拖垮管线）。"""


MoveKind = Literal["rapid_z", "rapid_xy", "plunge", "cut"]


@dataclass(frozen=True)
class ToolpathMove:
    """单条刀具运动指令。

    Attributes:
        kind: rapid_z=Z 轴快移；rapid_xy=XY 快移（仅在清除平面高度发生）；
              plunge=带进给下刀（垂直或斜坡，用下刀进给）；cut=切削进给。
        x/y/z: 目标点绝对坐标（mm）。
        feed: 进给速度（mm/min）；快移为 None。
    """

    kind: MoveKind
    x: float
    y: float
    z: float
    feed: float | None = None


@dataclass
class MillingToolpath:
    """一次铣削工序的完整刀心轨迹与统计。"""

    strategy: Literal["pocket_contour_parallel", "profile_offset", "face_raster"]
    moves: list[ToolpathMove] = field(default_factory=list)
    z_levels: list[float] = field(default_factory=list)
    ring_count: int = 0
    cut_length_mm: float = 0.0
    plunge_length_mm: float = 0.0
    est_cut_time_min: float = 0.0
    tool_diameter: float = 0.0
    stepover_mm: float = 0.0
    stepdown_mm: float = 0.0

    def summary(self) -> dict[str, object]:
        """返回可写进 G 代码注释/日志的摘要字典。"""
        return {
            "strategy": self.strategy,
            "moves": len(self.moves),
            "z_levels": len(self.z_levels),
            "rings": self.ring_count,
            "cut_length_mm": round(self.cut_length_mm, 2),
            "est_cut_time_min": round(self.est_cut_time_min, 2),
            "tool_diameter": self.tool_diameter,
            "stepover_mm": round(self.stepover_mm, 2),
            "stepdown_mm": round(self.stepdown_mm, 2),
        }


# ---------------------------------------------------------------------------
# 几何内核（纯 numpy，直线段多边形）
# ---------------------------------------------------------------------------


def _signed_area(pts: np.ndarray) -> float:
    """鞋带公式有向面积（CCW 为正）。pts 形状 (n,2)。"""
    x = pts[:, 0]
    y = pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _ensure_ccw(pts: np.ndarray) -> np.ndarray:
    return pts if _signed_area(pts) > 0 else pts[::-1].copy()


def _dedupe_closed(pts: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """去除相邻重复点与首尾闭合重复点。"""
    if len(pts) == 0:
        return pts
    out: list[np.ndarray] = [pts[0]]
    for p in pts[1:]:
        if float(np.hypot(*(p - out[-1]))) > tol:
            out.append(p)
    while len(out) > 1 and float(np.hypot(*(out[0] - out[-1]))) <= tol:
        out.pop()
    return np.asarray(out, dtype=float)


def _seg_intersect(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, p4: np.ndarray) -> tuple[float, float] | None:
    """线段 p1p2 与 p3p4 的严格内部相交检测，返回 (t, u)（均在开区间）。"""
    d1 = p2 - p1
    d2 = p4 - p3
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < _EPS:
        return None
    diff = p3 - p1
    t = (diff[0] * d2[1] - diff[1] * d2[0]) / denom
    u = (diff[0] * d1[1] - diff[1] * d1[0]) / denom
    if _EPS < t < 1 - _EPS and _EPS < u < 1 - _EPS:
        return t, u
    return None


def _find_self_intersections(pts: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
    """闭合折线的所有严格自交，按行走顺序排序。返回 (边i, 边j>i, 交点)。"""
    n = len(pts)
    out: list[tuple[int, int, np.ndarray]] = []
    for i in range(n):
        p1, p2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # 首尾边相邻
            p3, p4 = pts[j], pts[(j + 1) % n]
            hit = _seg_intersect(p1, p2, p3, p4)
            if hit is not None:
                t, _ = hit
                out.append((i, j, p1 + t * (p2 - p1)))
    out.sort(key=lambda e: (e[0], float(np.hypot(*(e[2] - pts[e[0]])))))
    return out


def _decompose_at_intersections(pts: np.ndarray) -> list[np.ndarray]:
    """把自交闭合折线在交叉点处递归分解为若干简单闭合环。

    每次取行走顺序最早的交叉 (i,j,X)：主链在 X 处拼接（去掉 i+1..j 顶点），
    子环 X→i+1..j→X 单独入表继续分解。每次拆分严格减少主链顶点数，必然终止。
    """
    work: list[np.ndarray] = [pts]
    simple: list[np.ndarray] = []
    guard = 0
    while work:
        guard += 1
        if guard > 10000:
            raise PlanarToolpathError(
                "[刀轨规划] 轮廓偏置自交分解超过安全迭代上限。"
                "建议操作：简化轮廓顶点（当前输入可能含重复/抖动顶点）后重试。"
            )
        cur = work.pop()
        xs = _find_self_intersections(cur)
        if not xs:
            simple.append(cur)
            continue
        i, j, x_pt = xs[0]
        main = np.vstack([cur[: i + 1], x_pt.reshape(1, 2), cur[j + 1 :]])
        bubble = np.vstack([x_pt.reshape(1, 2), cur[i + 1 : j + 1], x_pt.reshape(1, 2)])
        work.append(main)
        work.append(bubble)
    return simple


def _min_dist_to_polygon(p: np.ndarray, poly: np.ndarray) -> float:
    """点 p 到多边形（闭合折线）各线段的最小距离。"""
    n = len(poly)
    d2min = float("inf")
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        ab = b - a
        denom = float(ab @ ab)
        t = 0.0 if denom < _EPS else float(np.clip((p - a) @ ab / denom, 0.0, 1.0))
        d2 = float(np.sum((p - (a + t * ab)) ** 2))
        d2min = min(d2min, d2)
    return math.sqrt(d2min)


def _offset_polygon(pts: np.ndarray, d: float, miter_limit: float) -> np.ndarray:
    """简单多边形偏置 |d|：d>0 向内收缩，d<0 向外扩张。返回候选顶点环（可能自交）。"""
    n = len(pts)
    u = pts[np.arange(1, n)] - pts[np.arange(0, n - 1)]
    u = np.vstack([u, pts[0] - pts[n - 1]])  # 边 i: pts[i] -> pts[(i+1) % n]
    seg_len = np.hypot(u[:, 0], u[:, 1])
    if float(seg_len.min()) < _EPS:
        raise PlanarToolpathError("[刀轨规划] 轮廓含零长度边。建议操作：调用前对轮廓做去重与简化。")
    u = u / seg_len[:, None]
    n_left = np.stack([-u[:, 1], u[:, 0]], axis=1)  # CCW 左法线指向内部
    offset_pts = pts + d * n_left

    out: list[np.ndarray] = []
    for j in range(n):
        a0 = offset_pts[(j - 1) % n]
        ua = u[(j - 1) % n]
        b0 = offset_pts[j]
        ub = u[j]
        denom = ua[0] * ub[1] - ua[1] * ub[0]
        if abs(denom) < 1e-12:
            out.append(offset_pts[j])
            continue
        t = ((b0 - a0)[0] * ub[1] - (b0 - a0)[1] * ub[0]) / denom
        x_pt = a0 + t * ua
        miter = float(np.hypot(*(x_pt - pts[j])))
        max_miter = miter_limit * abs(d)
        if miter > max_miter:
            x_pt = pts[j] + (x_pt - pts[j]) * (max_miter / miter)
        out.append(x_pt)
    return _dedupe_closed(np.asarray(out, dtype=float))


def _offset_valid_loops(poly: np.ndarray, d: float, miter_limit: float, allow_slit: bool) -> list[np.ndarray]:
    """偏置 d（正内负外）后返回全部有效环（CCW）。

    校验：取向 CCW（或退化 slit——面积≈0 且恰含 2 个相异顶点，仅挖槽清底场景允许）、
    环上顶点与边中点到原始轮廓的距离 ≥ |d| - 容差。无有效环返回空列表。
    """
    if len(poly) < 3:
        return []
    bbox = poly.max(axis=0) - poly.min(axis=0)
    scale = float(np.hypot(*bbox))
    tol = max(1e-6, scale * 1e-6, abs(d) * 1e-3)
    try:
        candidate = _offset_polygon(poly, d, miter_limit)
    except PlanarToolpathError:
        return []
    if len(candidate) < 2:
        return []

    loops: list[np.ndarray] = []
    parts = _decompose_at_intersections(candidate) if _find_self_intersections(candidate) else [candidate]
    for loop in parts:
        loop = _dedupe_closed(loop)
        if len(loop) < 2:
            continue
        area = _signed_area(loop) if len(loop) >= 3 else 0.0
        tol_area = max(1e-6, (abs(d) * 1e-3) ** 2)
        is_slit = abs(area) <= tol_area and len(loop) >= 2
        if area < -tol_area and not (allow_slit and is_slit):
            continue  # 顺时针伪环（偏置越界的回卷）
        if area <= tol_area and not (allow_slit and is_slit):
            continue  # 无意义退化
        slack = tol
        ok = True
        for k in range(len(loop)):
            a = loop[k]
            if _min_dist_to_polygon(a, poly) < abs(d) - slack:
                ok = False
                break
            b = loop[(k + 1) % len(loop)]
            mid = 0.5 * (a + b)
            if _min_dist_to_polygon(mid, poly) < abs(d) - slack:
                ok = False
                break
        if ok:
            loops.append(loop)
    return loops


class PlanarToolpathEngine:
    """2.5D 平面刀轨引擎：挖槽环切 / 外形偏置 / 端面 raster。

    Usage:
        engine = PlanarToolpathEngine(tool_diameter=10.0, feed_rate=300)
        tp = engine.pocket(polygon, z_top=50.0, z_bottom=42.0)
    """

    def __init__(
        self,
        tool_diameter: float,
        feed_rate: float,
        plunge_feed: float | None = None,
        stepover_ratio: float = 0.5,
        stepdown: float = 2.0,
        ramp_angle_deg: float = 3.0,
        rapid_rate: float = 3000.0,
        miter_limit: float = 4.0,
        max_moves: int = 50000,
        max_rings: int = 500,
        clearance: float = 2.0,
    ) -> None:
        if tool_diameter <= 0:
            raise ValueError("[刀轨规划] 刀具直径必须为正数。建议操作：检查刀具参数。")
        if feed_rate <= 0:
            raise ValueError("[刀轨规划] 进给速度必须为正数。建议操作：检查切削参数库。")
        if not 0 < stepover_ratio <= 1.0:
            raise ValueError(
                "[刀轨规划] stepover_ratio 必须在 (0,1] 区间。"
                "建议操作：使用默认 0.5（50% 刀具直径），以保证环间全覆盖。"
            )
        if stepdown <= 0:
            raise ValueError("[刀轨规划] stepdown 必须为正数。建议操作：检查切削参数库。")
        if not 0 < ramp_angle_deg <= 30:
            raise ValueError("[刀轨规划] 斜坡角度须在 (0,30] 度。建议操作：使用默认 3 度。")

        self.tool_diameter = float(tool_diameter)
        self.tool_radius = self.tool_diameter / 2.0
        self.feed_rate = float(feed_rate)
        self.plunge_feed = float(plunge_feed if plunge_feed is not None else feed_rate * 0.5)
        self.stepover = self.tool_diameter * float(stepover_ratio)
        self.stepdown = float(stepdown)
        self.ramp_angle = math.radians(float(ramp_angle_deg))
        self.rapid_rate = float(rapid_rate)
        self.miter_limit = float(miter_limit)
        self.max_moves = int(max_moves)
        self.max_rings = int(max_rings)
        self.clearance = float(clearance)

    # ---------------- 公共入口 ----------------

    def pocket(self, polygon: np.ndarray, z_top: float, z_bottom: float) -> MillingToolpath:
        """挖槽（contour-parallel 环切 + Z 分层 + 斜坡下刀）。

        Args:
            polygon: (n,2) 简单多边形顶点（方向不限），待去除材料区域。
            z_top: 槽顶面绝对 Z（mm）。
            z_bottom: 槽底绝对 Z（mm），须低于 z_top。

        Raises:
            PocketTooNarrowError: 槽体内切尺寸小于刀具直径。
            ToolpathScaleExceededError: 刀轨规模超限。
        """
        poly = self._prepare_polygon(polygon)
        self._check_z(z_top, z_bottom)
        rings = self._pocket_rings(poly)
        return self._build_toolpath(rings, z_top, z_bottom, "pocket_contour_parallel")

    def profile(
        self,
        polygon: np.ndarray,
        z_top: float,
        z_bottom: float,
        finish_allowance: float = 0.0,
    ) -> MillingToolpath:
        """外形铣削：沿轮廓外侧单环偏置（刀具半径 + 精加工余量），Z 分层。

        Args:
            polygon: (n,2) 外轮廓顶点（材料在多边形内部）。
            finish_allowance: 精加工单边余量（mm，≥0）。
        """
        poly = self._prepare_polygon(polygon)
        self._check_z(z_top, z_bottom)
        if finish_allowance < 0:
            raise ValueError("[刀轨规划] 精加工余量不能为负。建议操作：余量取 0 或正值。")
        loops = _offset_valid_loops(poly, -(self.tool_radius + finish_allowance), self.miter_limit, allow_slit=False)
        if not loops:
            raise PlanarToolpathError(
                "[刀轨规划] 外形偏置失败（轮廓可能过尖锐或自交）。建议操作：简化轮廓尖锐角后重试，或回退保守走刀。"
            )
        return self._build_toolpath(loops, z_top, z_bottom, "profile_offset")

    def face_raster(
        self,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        z_top: float,
        z_bottom: float,
    ) -> MillingToolpath:
        """端面/平面铣削：矩形区域蛇形（serpentine）raster。

        刀心路径在矩形内缩刀具半径后按 stepover 布线，行末以切削进给横向转入下一行。
        """
        self._check_z(z_top, z_bottom)
        r = self.tool_radius
        x0, y0 = x_min + r, y_min + r
        x1, y1 = x_max - r, y_max - r
        if x1 - x0 < -_EPS or y1 - y0 < -_EPS:
            raise PocketTooNarrowError(
                f"[刀轨规划] 平面区域 ({x_max - x_min:.2f}×{y_max - y_min:.2f}mm) "
                f"小于刀具直径 {self.tool_diameter:.2f}mm，无法端面铣削。"
                "建议操作：更换小直径铣刀，或改用其他加工方式。"
            )
        x1, y1 = max(x1, x0), max(y1, y0)
        ys = self._raster_ys(y0, y1)
        z_levels = self._z_levels(z_top, z_bottom)

        moves: list[ToolpathMove] = []
        cut_len = 0.0
        plunge_len = 0.0
        current_z = z_top + self.clearance
        for lv in z_levels:
            for row, y in enumerate(ys):
                x_start, x_end = (x0, x1) if row % 2 == 0 else (x1, x0)
                moves.append(ToolpathMove("rapid_z", x_start, y, z_top + self.clearance))
                moves.append(ToolpathMove("rapid_xy", x_start, y, z_top + self.clearance))
                ramp = self._ramp_along([(x_start, y), (x_end, y)], current_z, lv)
                if ramp is not None:
                    ramp_pts = ramp[0]
                    for mx, my, mz in ramp_pts:
                        moves.append(ToolpathMove("plunge", mx, my, mz, self.plunge_feed))
                    plunge_len += current_z - lv
                else:
                    moves.append(ToolpathMove("plunge", x_start, y, lv, self.plunge_feed))
                    plunge_len += current_z - lv
                current_z = lv
                moves.append(ToolpathMove("cut", x_end, y, lv, self.feed_rate))
                cut_len += abs(x_end - x_start)

        tp = MillingToolpath(
            strategy="face_raster",
            moves=moves,
            z_levels=z_levels,
            ring_count=len(ys),
            cut_length_mm=cut_len,
            plunge_length_mm=plunge_len,
            tool_diameter=self.tool_diameter,
            stepover_mm=self.stepover,
            stepdown_mm=self.stepdown,
        )
        self._check_scale(tp)
        tp.est_cut_time_min = self._est_time(tp)
        return tp

    # ---------------- 内部实现 ----------------

    def _prepare_polygon(self, polygon: np.ndarray) -> np.ndarray:
        pts = np.asarray(polygon, dtype=float)
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise PlanarToolpathError(
                "[刀轨规划] 轮廓必须是 (n,2) 坐标数组。建议操作：检查几何数据来源（DXF 轮廓/矩形构造）。"
            )
        pts = _dedupe_closed(pts)
        if len(pts) < 3:
            raise PlanarToolpathError("[刀轨规划] 轮廓有效顶点不足 3 个。建议操作：检查几何提取结果是否为空或退化。")
        if len(pts) > 2000:
            raise ToolpathScaleExceededError(
                f"[刀轨规划] 轮廓顶点数 {len(pts)} 超过上限 2000。建议操作：先对轮廓做抽稀/圆弧拟合再生成刀轨。"
            )
        if not np.all(np.isfinite(pts)):
            raise PlanarToolpathError(
                "[刀轨规划] 轮廓坐标含非有限值（NaN/Inf）。建议操作：检查上游几何计算是否出现除零或未初始化。"
            )
        if _signed_area(pts) == 0.0:
            raise PlanarToolpathError(
                "[刀轨规划] 轮廓面积为零（共线退化）。建议操作：检查轮廓是否为直线段而非封闭区域。"
            )
        return _ensure_ccw(pts)

    @staticmethod
    def _check_z(z_top: float, z_bottom: float) -> None:
        if z_bottom >= z_top:
            raise PlanarToolpathError(
                f"[刀轨规划] 槽底 Z ({z_bottom:.3f}) 不低于顶面 Z ({z_top:.3f})。"
                "建议操作：检查 depth/stock_top_z 语义（深度应为正值，向下铣削）。"
            )

    def _pocket_rings(self, poly: np.ndarray) -> list[np.ndarray]:
        """环切环序列：d 从刀具半径起按 stepover 递增（始终相对原始轮廓计算）。"""
        rings: list[np.ndarray] = []
        d = self.tool_radius
        while len(rings) < self.max_rings:
            loops = _offset_valid_loops(poly, d, self.miter_limit, allow_slit=True)
            if not loops:
                break
            rings.extend(sorted(loops, key=_signed_area, reverse=True))
            d += self.stepover
        if not rings:
            raise PocketTooNarrowError(
                f"[刀轨规划] 挖槽区域过窄（内切尺寸 < 刀具直径 {self.tool_diameter:.2f}mm），"
                "无法环切。建议操作：更换小直径刀具，或对该特征改用钻削/电加工工艺。"
            )
        return rings

    def _raster_ys(self, y0: float, y1: float) -> list[float]:
        """行距布线：等距 stepover，末行贴近 y1（余量小于半行距时合并到末行）。"""
        span = y1 - y0
        if span < _EPS:
            return [y0]
        count = int(math.floor(span / self.stepover + 1e-9)) + 1
        ys = [y0 + k * self.stepover for k in range(count)]
        remainder = y1 - ys[-1]
        if remainder > _EPS:
            if remainder < 0.5 * self.stepover or len(ys) < 2:
                ys[-1] = y1
            else:
                ys.append(y1)
        return ys

    def _z_levels(self, z_top: float, z_bottom: float) -> list[float]:
        """Z 分层：从 z_top-stepdown 起等距向下，末层精确落在 z_bottom。"""
        levels: list[float] = []
        z = z_top - self.stepdown
        while z > z_bottom + 1e-9:
            levels.append(z)
            z -= self.stepdown
        levels.append(z_bottom)
        return levels

    def _ramp_along(
        self,
        path_pts: list[tuple[float, float]],
        z_from: float,
        z_to: float,
        max_laps: int = 4,
    ) -> tuple[list[tuple[float, float, float]], int] | None:
        """沿路径斜坡下刀，返回 ((x,y,z) 序列, 结束段索引)。

        序列首点=路径起点、末点 z=z_to；结束段索引指明末点位于
        path_pts[索引]→path_pts[索引+1] 段上（循环计数），调用方据此从该段
        继续切削以完成整圈。需要的斜坡长度超过路径单圈长度时循环绕行
        （开放路径即往复拉锯，正好是 raster 行内的斜坡进入方式），最多
        max_laps 圈；仍不足则返回 None（调用方退化为垂直下刀）。
        """
        drop_total = z_from - z_to
        if drop_total <= 1e-9:
            return [], 0
        segs: list[tuple[np.ndarray, np.ndarray]] = []
        for i in range(len(path_pts) - 1):
            a = np.asarray(path_pts[i], dtype=float)
            b = np.asarray(path_pts[i + 1], dtype=float)
            if float(np.hypot(*(b - a))) > _EPS:
                segs.append((a, b))
        if not segs:
            return None
        closed = float(np.hypot(*(segs[0][0] - segs[-1][1]))) < 1e-9
        perimeter = sum(float(np.hypot(*(b - a))) for a, b in segs)
        need_len = drop_total / math.tan(self.ramp_angle)
        laps = max(1, int(math.ceil(need_len / perimeter - 1e-9)))
        if laps > max_laps:
            return None
        total = laps * perimeter

        # 展开 lap 段序列：闭合路径循环绕圈；开放路径逐圈翻转方向（真实折返走线）
        seg_seq: list[tuple[np.ndarray, np.ndarray]] = []
        for lap in range(laps):
            if closed or lap % 2 == 0:
                seg_seq.extend(segs)
            else:
                seg_seq.extend([(b, a) for (a, b) in reversed(segs)])

        moves: list[tuple[float, float, float]] = []
        walked = 0.0
        end_seg = 0
        for k, (a, b) in enumerate(seg_seq):
            seg_len = float(np.hypot(*(b - a)))
            if walked + seg_len >= total - _EPS:
                t = (total - walked) / seg_len
                px = a + (b - a) * t
                moves.append((float(px[0]), float(px[1]), z_to))
                end_seg = k % len(segs) if closed else 0
                break
            walked += seg_len
            moves.append((float(b[0]), float(b[1]), z_from - drop_total * (walked / total)))
            if closed:
                end_seg = (k + 1) % len(segs)
        return moves, end_seg

    def _build_toolpath(
        self,
        rings: list[np.ndarray],
        z_top: float,
        z_bottom: float,
        strategy: Literal["pocket_contour_parallel", "profile_offset"],
    ) -> MillingToolpath:
        clearance_z = z_top + self.clearance
        z_levels = self._z_levels(z_top, z_bottom)
        moves: list[ToolpathMove] = []
        cut_len = 0.0
        plunge_len = 0.0
        current_z = clearance_z

        for lv in z_levels:
            for ring in rings:
                start = ring[0]
                moves.append(ToolpathMove("rapid_z", float(start[0]), float(start[1]), clearance_z))
                moves.append(ToolpathMove("rapid_xy", float(start[0]), float(start[1]), clearance_z))
                n = len(ring)
                path = [(float(p[0]), float(p[1])) for p in ring]
                path.append((float(start[0]), float(start[1])))  # 闭合段
                ramp = self._ramp_along(path, current_z, lv)
                if ramp is not None:
                    ramp_pts, end_seg = ramp
                    for mx, my, mz in ramp_pts:
                        moves.append(ToolpathMove("plunge", mx, my, mz, self.plunge_feed))
                    plunge_len += current_z - lv
                    current_z = lv
                    # 从斜坡结束段继续：先补完该段，再走完整一圈（共 n 段）
                    prev = np.asarray(path[end_seg], dtype=float)
                    for j in range(1, n + 2):
                        p = np.asarray(path[(end_seg + j) % n], dtype=float)
                        moves.append(ToolpathMove("cut", float(p[0]), float(p[1]), lv, self.feed_rate))
                        cut_len += float(np.hypot(*(p - prev)))
                        prev = p
                else:
                    moves.append(ToolpathMove("plunge", float(start[0]), float(start[1]), lv, self.plunge_feed))
                    plunge_len += current_z - lv
                    current_z = lv
                    for k in range(1, n + 1):
                        p = ring[k % n]
                        prev = ring[(k - 1) % n]
                        moves.append(ToolpathMove("cut", float(p[0]), float(p[1]), lv, self.feed_rate))
                        cut_len += float(np.hypot(*(p - prev)))

        tp = MillingToolpath(
            strategy=strategy,
            moves=moves,
            z_levels=z_levels,
            ring_count=len(rings),
            cut_length_mm=cut_len,
            plunge_length_mm=plunge_len,
            tool_diameter=self.tool_diameter,
            stepover_mm=self.stepover,
            stepdown_mm=self.stepdown,
        )
        self._check_scale(tp)
        tp.est_cut_time_min = self._est_time(tp)
        return tp

    def _check_scale(self, tp: MillingToolpath) -> None:
        if len(tp.moves) > self.max_moves:
            raise ToolpathScaleExceededError(
                f"[刀轨规划] 刀轨指令数 {len(tp.moves)} 超过上限 {self.max_moves}。"
                "建议操作：增大 stepdown/stepover，或拆分加工区域。"
            )

    def _est_time(self, tp: MillingToolpath) -> float:
        """估算切削时间（min）：切削+下刀按进给，快移按 5mm/条 均长估计。"""
        cut_t = tp.cut_length_mm / self.feed_rate
        plunge_t = tp.plunge_length_mm / max(self.plunge_feed, _EPS)
        rapids = sum(1 for m in tp.moves if m.feed is None)
        return cut_t + plunge_t + (rapids * 5.0) / self.rapid_rate
