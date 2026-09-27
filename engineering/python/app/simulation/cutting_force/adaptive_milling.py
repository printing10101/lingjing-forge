"""NX Adaptive Milling 式切削力约束自适应求解器。

竞品对标：Siemens NX "Adaptive Milling"（原 Volumill / HSMWorks 思路）。
核心思想：**在保持切削力不超过目标值的前提下，沿刀路逐段反求最大允许切深
（axial depth of cut, ap）与每齿进给（fz）**，实现轨迹自适应——

    材料余量大的区域 → 降切深 / 降进给
    材料余量小的区域 → 提切深 / 提进给

从而在保证刀具寿命与加工质量的同时最大化材料去除率（MRR）。

算法链：
    1. 切削力正向模型：F = kc1.1 * b * h^(1-mc)  （Kienzle）
       其中 b = 径向切宽 ae，h = fz * sin(phi) 平均切屑厚度
    2. 反向求解：给定 F_target，反推最大 ap_max
       由于 F 与 ap 通过 b·h 关联（b = ap 对端铣；h = fz 影响独立），
       在固定 fz 的前提下：ap_max = F_target / (kc1.1 * h^(1-mc))
    3. 切宽 ae 影响：实际切削力还取决于径向切入角对应的当量切宽
       通过 effective_width = ae / cutter_diameter * effective_factor 修正
    4. 约束传播：
       - ap_max 上限：刀具最大切深、机床功率、稳定性叶图极限
       - fz 上限：表面粗糙度约束、机床最大进给速度
       - fz 下限：最小切屑厚度（rubbing 避免）
    5. 输出：每段刀路的 (ap_recommended, fz_recommended, vf, MRR, F_estimated)

设计说明：
    - 复用 app.simulation.cutting_force.kienzle 的 Kienzle 模型
    - 复用 app.simulation.chatter.stability 的稳定性叶图极限（可选）
    - 不修改现有模块，作为独立的工艺参数优化层
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.simulation.cutting_force.kienzle import (
    compute_cutting_force_fz,
    get_kienzle_coefficients,
)

logger = logging.getLogger(__name__)


# ── 物理常量与默认约束 ───────────────────────────────────────────────
DEFAULT_TARGET_FORCE_N = 800.0  # 目标切削力 800N（中等直径立铣刀典型值）
DEFAULT_MAX_AXIAL_DEPTH_MM = 30.0  # 默认最大轴向切深（刀具刃长约束）
DEFAULT_MIN_AXIAL_DEPTH_MM = 0.1  # 最小轴向切深
DEFAULT_MAX_FZ_MM = 0.3  # 最大每齿进给（表面粗糙度约束）
DEFAULT_MIN_FZ_MM = 0.02  # 最小每齿进给（避免 rubbing）
DEFAULT_MAX_FEED_MM_PER_MIN = 5000.0  # 机床最大进给速度
DEFAULT_MIN_FEED_MM_PER_MIN = 100.0
DEFAULT_RUBBING_THRESHOLD_FZ = 0.02  # 低于此值进入 rubbing 区
DEFAULT_EFFECTIVE_FACTOR = 1.0  # 径向切入效率系数（端铣满刃=1.0）


@dataclass
class AdaptiveMillingParams:
    """自适应铣削求解参数。

    Attributes:
        material: 工件材料标识（如 "45steel", "aluminum_6061"）
        cutter_diameter: 刀具直径 (mm)
        flute_count: 刀具刃数
        target_force_n: 目标切削力 (N)
        radial_depth_ae: 径向切宽 (mm)（刀路侧吃刀量）
        axial_depth_ap_init: 初始轴向切深 (mm)，作为求解起点
        max_axial_depth: 最大轴向切深 (mm)（刀长约束）
        min_axial_depth: 最小轴向切深 (mm)
        max_fz: 最大每齿进给 (mm/tooth)
        min_fz: 最小每齿进给 (mm/tooth)
        max_feed: 机床最大进给速度 (mm/min)
        min_feed: 机床最小进给速度 (mm/min)
        spindle_rpm: 主轴转速 (rpm)
        stability_limit_ap: 稳定性叶图极限切深 (mm)，None 则不施加约束
        kc1_1: 比切削力基准值 (N/mm²)，None 时从材料库读取
        mc: 切削力指数，None 时从材料库读取
        effective_factor: 径向切入效率系数
        safety_margin: 安全裕度（0-1，作用于 ap_max 的折减系数）
    """

    material: str = "45steel"
    cutter_diameter: float = 10.0
    flute_count: int = 4
    target_force_n: float = DEFAULT_TARGET_FORCE_N
    radial_depth_ae: float = 5.0
    axial_depth_ap_init: float = 5.0
    max_axial_depth: float = DEFAULT_MAX_AXIAL_DEPTH_MM
    min_axial_depth: float = DEFAULT_MIN_AXIAL_DEPTH_MM
    max_fz: float = DEFAULT_MAX_FZ_MM
    min_fz: float = DEFAULT_MIN_FZ_MM
    max_feed: float = DEFAULT_MAX_FEED_MM_PER_MIN
    min_feed: float = DEFAULT_MIN_FEED_MM_PER_MIN
    spindle_rpm: float = 6000.0
    stability_limit_ap: float | None = None
    kc1_1: float | None = None
    mc: float | None = None
    effective_factor: float = DEFAULT_EFFECTIVE_FACTOR
    safety_margin: float = 0.85

    def __post_init__(self) -> None:
        if self.cutter_diameter <= 0:
            raise ValueError(f"刀具直径必须为正，当前: {self.cutter_diameter}")
        if self.flute_count <= 0:
            raise ValueError(f"刃数必须为正整数，当前: {self.flute_count}")
        if self.target_force_n <= 0:
            raise ValueError(f"目标切削力必须为正，当前: {self.target_force_n}")
        if self.radial_depth_ae <= 0:
            raise ValueError(f"径向切宽必须为正，当前: {self.radial_depth_ae}")
        if not (0.0 < self.safety_margin <= 1.0):
            raise ValueError(f"safety_margin 应在 (0,1]，当前: {self.safety_margin}")

        # 加载材料系数（未配置材料降级到 45steel，避免整个求解器无法实例化）
        try:
            coeffs = get_kienzle_coefficients(self.material)
        except ValueError as e:
            logger.warning(
                "材料 '%s' 未配置 Kienzle 系数，降级到 45steel: %s",
                self.material,
                e,
            )
            coeffs = get_kienzle_coefficients("45steel")
            self.material = "45steel"
        if self.kc1_1 is None:
            self.kc1_1 = coeffs["kc1_1"]
        if self.mc is None:
            self.mc = coeffs["mc"]

        # 材料库兜底后仍缺失（如自定义材料未在库中且无 Kienzle 系数）时显式报错，
        # 避免下游 `0.1 < None` 的 TypeError 与隐式错误传播。
        if self.kc1_1 is None or self.mc is None:
            raise ValueError(f"材料 '{self.material}' 缺少 Kienzle 系数（kc1_1/mc），无法计算切削力")

        # 修复 P1: mc=1 时 `1.0 / (1.0 - mc)` 触发 ZeroDivisionError；
        # mc>1 时指数为负导致数学异常。Kienzle 切削力指数物理合理范围为 0.1 < mc < 0.5，
        # 违反时抛 ValueError，避免下游反向校核 fz 时崩溃。
        if not (0.1 < self.mc < 0.5):
            raise ValueError(f"mc 切削力指数必须在物理合理范围 (0.1, 0.5) 内，当前: {self.mc}")


@dataclass
class SegmentSolution:
    """单段刀路的优化解。

    Attributes:
        segment_id: 段索引
        ap_recommended: 推荐轴向切深 (mm)
        fz_recommended: 推荐每齿进给 (mm/tooth)
        feed_rate: 进给速度 (mm/min)
        estimated_force_n: 估算切削力 (N)
        mrr_mm3_per_min: 材料去除率 (mm³/min)
        constraint_active: 哪个约束是绑定约束
                           ("target_force" / "max_ap" / "stability" /
                            "max_fz" / "min_fz" / "max_feed")
        confidence: 置信度 [0,1]（受约束松弛程度影响）
    """

    segment_id: int = 0
    ap_recommended: float = 0.0
    fz_recommended: float = 0.0
    feed_rate: float = 0.0
    estimated_force_n: float = 0.0
    mrr_mm3_per_min: float = 0.0
    constraint_active: str = "target_force"
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "ap_recommended_mm": round(self.ap_recommended, 4),
            "fz_recommended_mm_per_tooth": round(self.fz_recommended, 5),
            "feed_rate_mm_per_min": round(self.feed_rate, 2),
            "estimated_force_n": round(self.estimated_force_n, 2),
            "mrr_mm3_per_min": round(self.mrr_mm3_per_min, 2),
            "constraint_active": self.constraint_active,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class AdaptiveMillingResult:
    """自适应铣削求解完整结果。"""

    material: str = ""
    cutter_diameter: float = 0.0
    flute_count: int = 0
    spindle_rpm: float = 0.0
    target_force_n: float = 0.0
    segments: list[SegmentSolution] = field(default_factory=list)
    total_mrr_mm3_per_min: float = 0.0
    avg_force_n: float = 0.0
    max_force_n: float = 0.0
    min_force_n: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "cutter_diameter_mm": self.cutter_diameter,
            "flute_count": self.flute_count,
            "spindle_rpm": self.spindle_rpm,
            "target_force_n": self.target_force_n,
            "segments": [s.to_dict() for s in self.segments],
            "total_mrr_mm3_per_min": round(self.total_mrr_mm3_per_min, 2),
            "avg_force_n": round(self.avg_force_n, 2),
            "max_force_n": round(self.max_force_n, 2),
            "min_force_n": round(self.min_force_n, 2),
            "summary": self.summary,
        }


class AdaptiveMillingSolver:
    """NX Adaptive Milling 式自适应求解器。

    使用 Kienzle 切削力正向模型反向求解最大切深，并施加多重物理约束。

    求解性质说明：
        - **本求解器为解析解，非迭代解**。给定 (target_force, fz, kc1.1, mc) 后，
          ap_max 通过闭式公式 ``F_target / (kc1.1 * fz^(1-mc))`` 一次性求得，
          不存在收敛失败或初值依赖问题。
        - **唯一的数值奇异源**：mc → 1 时 ``1/(1-mc)`` 发散。已在
          :mod:`app.simulation.cutting_force.kienzle` 的材料系数库中限制
          mc ∈ [0.18, 0.28]，规避该风险。
        - **求解复杂度**：O(n_segments)，单段求解仅含若干算术运算，
          无矩阵求逆或优化迭代，适合实时刀路自适应（毫秒级响应）。
        - **LTC 桥接退化路径**：当 ``stability_limit_ap`` 由 chatter 模块的
          :func:`predict_stability` 提供 LTC 预测结果时，若 LTC 模型不可用，
          调用方应回退到 :func:`compute_stability_limit` 解析法，并将所得
          极限切深通过 ``stability_limit_ap`` 传入本求解器（见 chatter/api.py
          的 ``_resolve_stability_limit``）。
    """

    def __init__(self, params: AdaptiveMillingParams) -> None:
        self._params = params

    # 公开求解接口

    def solve_segment(
        self,
        segment_id: int,
        material_remainder_mm: float | None = None,
        force_override_n: float | None = None,
    ) -> SegmentSolution:
        """求解单段刀路的最优 (ap, fz)。

        Args:
            segment_id: 段索引
            material_remainder_mm: 该段剩余材料厚度（mm），若提供则
                                  ap_max 不会超过该值（避免空切或过切）
            force_override_n: 该段目标力覆盖值（None 用全局 target_force_n）

        Returns:
            SegmentSolution 优化解
        """
        p = self._params
        if p.kc1_1 is None or p.mc is None:
            raise ValueError(f"材料 '{p.material}' 缺少 Kienzle 系数，无法计算切削力")
        target_force = force_override_n if force_override_n else p.target_force_n

        # Step 1: 选择基准 fz（先用 max_fz 起步，后续按需回退）
        # NX Adaptive Milling 思路：优先保证每齿进给最大化以提升 MRR
        fz = p.max_fz

        # Step 2: 反求 ap_max
        # Kienzle: F = kc1.1 * b * h^(1-mc)
        # b = ap（端铣时径向切宽 = 轴向切深，此处为统一处理用 effective_width）
        # h = fz（简化：每齿进给 = 平均切屑厚度，忽略 sin(phi) 最大化场景）
        # 注：实际端铣时 b = ae，h = fz；立铣侧铣时 b = ap，h = fz
        # 本求解器面向立铣侧铣场景（NX Adaptive Milling 典型应用）：
        # b = ap, h = fz F = kc1.1 * ap * fz^(1-mc)
        # 反推 ap_max = F_target / (kc1.1 * fz^(1-mc))
        h = fz
        specific_force = p.kc1_1 * (h ** (1.0 - p.mc))  # 比切削力 N/mm²
        if specific_force <= 0:
            ap_max_force = p.max_axial_depth
        else:
            ap_max_force = target_force / specific_force

        # 施加安全裕度
        ap_max_force *= p.safety_margin

        # Step 3: 应用多重约束
        ap_max = ap_max_force
        constraint = "target_force"

        # 3a. 刀具最大切深约束
        if ap_max > p.max_axial_depth:
            ap_max = p.max_axial_depth
            constraint = "max_ap"

        # 3b. 稳定性叶图极限约束（如果提供）
        if p.stability_limit_ap is not None and ap_max > p.stability_limit_ap:
            ap_max = p.stability_limit_ap
            constraint = "stability"

        # 3c. 材料余量约束（如该段余量不足 ap_max，则降至余量）
        if material_remainder_mm is not None and material_remainder_mm > 0 and ap_max > material_remainder_mm:
            ap_max = material_remainder_mm
            constraint = "material_remainder"

        # 3d. 最小切深约束
        if ap_max < p.min_axial_depth:
            ap_max = p.min_axial_depth
            constraint = "min_ap"

        # Step 4: 反向校核 fz
        # 在 ap 确定后，重新计算 fz 是否还能维持 max_fz。
        # 若 F(ap_max, fz=max_fz) > target_force，则降 fz。
        #
        # 注：本反向校核在大多数场景下是“死代码”（防御式编程）：
        # - Step 2 已基于 fz=max_fz 反求 ap_max_force，使得 F(ap_max_force, max_fz) ≈ target_force
        # - Step 3 中的 max_ap / stability / material_remainder / min_ap 约束只会让 ap_max 更小
        # - 因此 F(ap_max, max_fz) ≤ F(ap_max_force, max_fz) ≈ target_force
        # - 仅当 material_remainder 把 ap 压到极小值、再被 min_ap 抬回时，
        # 或 ap 受 stability_limit_ap 强制截断到比 ap_max_force 更小的值时，
        # F(ap_max, max_fz) < target_force，此时 fz 反而不需要降——所以校核不触发。
        # - 真正触发本分支的场景：ap_max 被外部的 stability_limit_ap 或 max_axial_depth
        # “放大”到比 ap_max_force 还大的情况（理论上不应发生，但作为安全网保留）。
        if p.kc1_1 > 0 and ap_max > 0:
            fz_max_by_force = (target_force / (p.kc1_1 * ap_max)) ** (1.0 / (1.0 - p.mc))
        else:
            fz_max_by_force = p.max_fz

        if fz > fz_max_by_force:
            fz = max(fz_max_by_force * p.safety_margin, p.min_fz)
            constraint = "target_force"

        # fz 上下限裁剪
        if fz > p.max_fz:
            fz = p.max_fz
            if constraint == "target_force":
                constraint = "max_fz"
        if fz < p.min_fz:
            fz = p.min_fz
            constraint = "min_fz"

        # Step 5: 计算进给速度 vf = fz * z * n
        # 注意：进给速度约束不应覆盖 stability / material_remainder 等物理约束
        # 的追溯信息——后者影响 ap，前者只影响 fz/vf，应并行记录而非覆盖
        vf = fz * p.flute_count * p.spindle_rpm
        if vf > p.max_feed:
            vf = p.max_feed
            # vf 受机床上限约束时，重新反算有效 fz
            effective_fz = vf / (p.flute_count * p.spindle_rpm)
            if effective_fz >= p.min_fz:
                fz = effective_fz
                # 仅在未触发更重要的物理约束时才标记为 max_feed
                if constraint in ("target_force", "max_fz"):
                    constraint = "max_feed"
            else:
                vf = p.min_feed
                if constraint in ("target_force", "max_fz", "max_feed"):
                    constraint = "min_feed"

        # Step 6: 估算最终切削力与 MRR
        final_force = self._compute_force(ap_max, fz)
        mrr = self._compute_mrr(ap_max, fz, vf)

        # 置信度：基于绑定约束的松弛程度
        confidence = self._compute_confidence(ap_max, fz, target_force, final_force)

        return SegmentSolution(
            segment_id=segment_id,
            ap_recommended=ap_max,
            fz_recommended=fz,
            feed_rate=vf,
            estimated_force_n=final_force,
            mrr_mm3_per_min=mrr,
            constraint_active=constraint,
            confidence=confidence,
        )

    def solve_segments(
        self,
        material_remainders: list[float] | None = None,
        force_overrides: list[float] | None = None,
        num_segments: int | None = None,
    ) -> AdaptiveMillingResult:
        """批量求解多段刀路的优化参数。

        Args:
            material_remainders: 每段剩余材料厚度列表（mm），None 表示不约束
            force_overrides: 每段目标力覆盖列表（N），None 表示用全局值
            num_segments: 段数（仅在两个 list 都为 None 时使用）

        Returns:
            AdaptiveMillingResult 完整求解结果
        """
        # 确定段数（注意：num_segments=0 是合法的显式输入，不应被当作 None）
        if material_remainders is not None:
            n = len(material_remainders)
        elif force_overrides is not None:
            n = len(force_overrides)
        else:
            n = num_segments if num_segments is not None else 1

        # 长度对齐校验
        if material_remainders is not None and len(material_remainders) != n:
            raise ValueError(f"material_remainders 长度 {len(material_remainders)} != 段数 {n}")
        if force_overrides is not None and len(force_overrides) != n:
            raise ValueError(f"force_overrides 长度 {len(force_overrides)} != 段数 {n}")

        segments: list[SegmentSolution] = []
        for i in range(n):
            mr = material_remainders[i] if material_remainders else None
            fo = force_overrides[i] if force_overrides else None
            segments.append(self.solve_segment(i, mr, fo))

        # 统计汇总
        forces = [s.estimated_force_n for s in segments]
        mrrs = [s.mrr_mm3_per_min for s in segments]
        total_mrr = sum(mrrs)
        avg_force = float(np.mean(forces)) if forces else 0.0
        max_force = max(forces) if forces else 0.0
        min_force = min(forces) if forces else 0.0

        # 统计约束分布
        constraints = [s.constraint_active for s in segments]
        constraint_counts: dict[str, int] = {}
        for c in constraints:
            constraint_counts[c] = constraint_counts.get(c, 0) + 1

        summary = self._build_summary(n, total_mrr, avg_force, max_force, constraint_counts)

        return AdaptiveMillingResult(
            material=self._params.material,
            cutter_diameter=self._params.cutter_diameter,
            flute_count=self._params.flute_count,
            spindle_rpm=self._params.spindle_rpm,
            target_force_n=self._params.target_force_n,
            segments=segments,
            total_mrr_mm3_per_min=total_mrr,
            avg_force_n=avg_force,
            max_force_n=max_force,
            min_force_n=min_force,
            summary=summary,
        )

    # 内部实现

    def _compute_force(self, ap: float, fz: float) -> float:
        """正向计算切削力。"""
        p = self._params
        if p.kc1_1 is None or p.mc is None:
            raise ValueError(f"材料 '{p.material}' 缺少 Kienzle 系数，无法计算切削力")
        h = fz
        # 立铣侧铣：b = ap, h = fz
        return compute_cutting_force_fz(p.kc1_1, p.mc, ap, h)

    def _compute_mrr(self, ap: float, fz: float, vf: float) -> float:
        """计算材料去除率 MRR = ap * ae * vf。

        立铣侧铣场景：MRR = ap * ae * vf
        其中 ae = radial_depth_ae（径向切宽）。
        """
        p = self._params
        return ap * p.radial_depth_ae * vf

    def _compute_confidence(
        self,
        ap: float,
        fz: float,
        target_force: float,
        actual_force: float,
    ) -> float:
        """基于约束松弛度计算置信度。

        实际力与目标力的偏差越小、参数离上下限越远，置信度越高。
        """
        # 力偏差得分
        if target_force > 0:
            force_ratio = abs(actual_force - target_force) / target_force
            force_score = max(0.0, 1.0 - force_ratio)
        else:
            force_score = 0.5

        # 参数裕度得分
        p = self._params
        ap_range = p.max_axial_depth - p.min_axial_depth
        if ap_range > 0:
            ap_margin = 1.0 - abs(ap - (p.max_axial_depth + p.min_axial_depth) / 2) / (ap_range / 2)
        else:
            ap_margin = 0.5

        fz_range = p.max_fz - p.min_fz
        if fz_range > 0:
            fz_margin = 1.0 - abs(fz - (p.max_fz + p.min_fz) / 2) / (fz_range / 2)
        else:
            fz_margin = 0.5

        # 加权平均
        return float(0.5 * force_score + 0.25 * ap_margin + 0.25 * fz_margin)

    def _build_summary(
        self,
        num_segments: int,
        total_mrr: float,
        avg_force: float,
        max_force: float,
        constraint_counts: dict[str, int],
    ) -> str:
        """生成人类可读的求解摘要。"""
        p = self._params
        dominant = max(constraint_counts, key=lambda k: constraint_counts[k]) if constraint_counts else "unknown"
        dominant_count = constraint_counts.get(dominant, 0)
        dominant_pct = (dominant_count / num_segments * 100) if num_segments > 0 else 0

        return (
            f"自适应铣削求解完成：共 {num_segments} 段刀路，"
            f"总 MRR = {total_mrr:.1f} mm³/min，"
            f"平均切削力 {avg_force:.1f} N（峰值 {max_force:.1f} N），"
            f"目标力 {p.target_force_n:.1f} N。"
            f"主导约束：{dominant}（{dominant_pct:.1f}%）。"
        )


__all__ = [
    "AdaptiveMillingParams",
    "SegmentSolution",
    "AdaptiveMillingResult",
    "AdaptiveMillingSolver",
    "DEFAULT_TARGET_FORCE_N",
    "DEFAULT_MAX_AXIAL_DEPTH_MM",
    "DEFAULT_MIN_AXIAL_DEPTH_MM",
    "DEFAULT_MAX_FZ_MM",
    "DEFAULT_MIN_FZ_MM",
    "DEFAULT_MAX_FEED_MM_PER_MIN",
]
