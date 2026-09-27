"""切削力约束自适应求解 API。

落地竞品分析中识别的 NX Adaptive Milling 补强点：

1. **POST /api/cutting-force/adaptive/solve-segment**
   单段刀路自适应求解：给定目标切削力，反求 (ap, fz, vf)
2. **POST /api/cutting-force/adaptive/solve-segments**
   批量多段刀路求解：输入各段剩余材料量，输出整条刀路参数序列
3. **POST /api/cutting-force/adaptive/preview**
   快速预览：用默认参数演示求解器效果，无需构造完整请求体
4. **GET /api/cutting-force/kienzle/coefficients/{material}**
   查询材料 Kienzle 系数，便于前端动态提示
5. **POST /api/cutting-force/kienzle/compute**
   正向 Kienzle 切削力计算（三向力 + 比切削力）

端点前缀：/api/cutting-force
"""

import asyncio
import logging


from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.response import success, error, ErrorCode
from app.simulation.cutting_force.adaptive_milling import (
    AdaptiveMillingParams,
    AdaptiveMillingSolver,
    DEFAULT_MAX_AXIAL_DEPTH_MM,
    DEFAULT_MAX_FEED_MM_PER_MIN,
    DEFAULT_MAX_FZ_MM,
    DEFAULT_MIN_AXIAL_DEPTH_MM,
    DEFAULT_MIN_FZ_MM,
    DEFAULT_TARGET_FORCE_N,
)
from app.simulation.cutting_force.kienzle import (
    DEFAULT_MATERIAL_COEFFICIENTS,
    compute_cutting_forces,
    get_kienzle_coefficients,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cutting-force", tags=["CuttingForce"])


def _resolve_stability_limit(
    spindle_rpm: float,
    material: str,
    cutter_diameter: float,
    explicit_limit: float | None = None,
) -> float | None:
    """解析稳定性叶图极限切深（LTC → 自适应铣削 桥接）。

    优先使用显式传入的 explicit_limit；若为 None，则尝试调用 ChatterPredictor
    （LTC 颤振预测模型）自动获取极限切深，实现"LTC 颤振预测 → NX Adaptive Milling"
    闭环——这是论文中宣称的核心集成链路。

    桥接失败（模型未加载/依赖缺失/预测异常）时降级到 None，不施加约束，
    保证求解器在 LTC 模型不可用时仍能正常运行（软依赖设计）。
    """
    if explicit_limit is not None:
        return explicit_limit

    try:
        from app.simulation.chatter.predictor import predict_stability

        tool_id = f"endmill_d{int(cutter_diameter)}"
        result = predict_stability(
            spindle_rpm=spindle_rpm,
            machine="vmc_850",
            tool=tool_id,
            workpiece=material,
        )
        limit_depth = result.get("limit_depth")
        if limit_depth is not None and float(limit_depth) > 0:  # type: ignore[arg-type]
            logger.info(
                "LTC 颤振预测桥接: spindle_rpm=%s, tool=%s → limit_depth=%.3f mm",
                spindle_rpm,
                tool_id,
                float(limit_depth),  # type: ignore[arg-type]
            )
            return float(limit_depth)  # type: ignore[arg-type]
    except Exception as e:
        logger.debug("ChatterPredictor 桥接失败，跳过稳定性约束: %s", e)

    return None


# 请求/响应模型


class AdaptiveSolveSegmentRequest(BaseModel):
    """单段自适应求解请求。"""

    material: str = Field(default="45steel", description="材料标识")
    cutter_diameter: float = Field(default=10.0, gt=0, description="刀具直径 mm")
    flute_count: int = Field(default=4, ge=1, le=20, description="刃数")
    target_force_n: float = Field(default=DEFAULT_TARGET_FORCE_N, gt=0, description="目标切削力 N")
    radial_depth_ae: float = Field(default=5.0, gt=0, description="径向切宽 mm")
    axial_depth_ap_init: float = Field(default=5.0, gt=0, description="初始轴向切深 mm（求解起点）")
    max_axial_depth: float = Field(default=DEFAULT_MAX_AXIAL_DEPTH_MM, gt=0, description="最大轴向切深 mm")
    min_axial_depth: float = Field(default=DEFAULT_MIN_AXIAL_DEPTH_MM, gt=0, description="最小轴向切深 mm")
    max_fz: float = Field(default=DEFAULT_MAX_FZ_MM, gt=0, description="最大每齿进给 mm/tooth")
    min_fz: float = Field(default=DEFAULT_MIN_FZ_MM, gt=0, description="最小每齿进给 mm/tooth")
    max_feed: float = Field(default=DEFAULT_MAX_FEED_MM_PER_MIN, gt=0, description="机床最大进给 mm/min")
    min_feed: float = Field(default=100.0, gt=0, description="机床最小进给 mm/min")
    spindle_rpm: float = Field(default=6000.0, gt=0, description="主轴转速 rpm")
    stability_limit_ap: float | None = Field(default=None, gt=0, description="稳定性叶图极限切深 mm（可选约束）")
    kc1_1: float | None = Field(default=None, gt=0, description="比切削力 N/mm²（覆盖材料库）")
    mc: float | None = Field(default=None, gt=0, description="切削力指数（覆盖材料库）")
    safety_margin: float = Field(default=0.85, gt=0, le=1.0, description="安全裕度 (0,1]")

    # 单段专用
    material_remainder_mm: float | None = Field(default=None, gt=0, description="该段剩余材料厚度 mm（可选约束）")
    force_override_n: float | None = Field(default=None, gt=0, description="该段目标力覆盖 N（可选）")


class AdaptiveSolveSegmentsRequest(AdaptiveSolveSegmentRequest):
    """批量多段自适应求解请求。"""

    material_remainders: list[float] = Field(default_factory=list, description="每段剩余材料厚度列表 mm")
    force_overrides: list[float] = Field(default_factory=list, description="每段目标力覆盖列表 N")
    num_segments: int | None = Field(default=None, ge=1, le=1000, description="段数（仅当两列表为空时使用）")


class KienzleComputeRequest(BaseModel):
    """Kienzle 正向切削力计算请求。"""

    material: str = Field(default="45steel")
    width: float = Field(default=10.0, gt=0, description="切削宽度 b mm")
    chip_thickness: float = Field(default=0.1, gt=0, description="未变形切屑厚度 h mm")
    kc1_1: float | None = Field(default=None, gt=0)
    mc: float | None = Field(default=None, gt=0)


# 1. 单段自适应求解


@router.post("/adaptive/solve-segment")
async def adaptive_solve_segment(req: AdaptiveSolveSegmentRequest):
    """单段刀路自适应求解。

    算法：给定目标切削力 F_target，Kienzle 反求最大 ap，依次施加
    max_ap → stability → material_remainder → min_ap 约束，
    再反向校核 fz 与进给速度上限。
    """
    try:
        params = AdaptiveMillingParams(
            material=req.material,
            cutter_diameter=req.cutter_diameter,
            flute_count=req.flute_count,
            target_force_n=req.target_force_n,
            radial_depth_ae=req.radial_depth_ae,
            axial_depth_ap_init=req.axial_depth_ap_init,
            max_axial_depth=req.max_axial_depth,
            min_axial_depth=req.min_axial_depth,
            max_fz=req.max_fz,
            min_fz=req.min_fz,
            max_feed=req.max_feed,
            min_feed=req.min_feed,
            spindle_rpm=req.spindle_rpm,
            stability_limit_ap=_resolve_stability_limit(
                req.spindle_rpm, req.material, req.cutter_diameter, req.stability_limit_ap
            ),
            kc1_1=req.kc1_1,
            mc=req.mc,
            safety_margin=req.safety_margin,
        )
        solver = AdaptiveMillingSolver(params)
        segment = await asyncio.to_thread(
            solver.solve_segment,
            0,
            req.material_remainder_mm,
            req.force_override_n,
        )
        return success(
            data=segment.to_dict(),
            message="自适应求解完成",
        )
    except ValueError as e:
        logger.warning("自适应求解参数错误: %s", e)
        return error(
            ErrorCode.INVALID_REQUEST,
            message=f"参数错误: {e}",
            recoverable=True,
        )
    except Exception as e:
        logger.exception("自适应求解失败: %s", e)
        return error(
            ErrorCode.INTERNAL_ERROR,
            message=f"求解失败: {e}",
        )


# 2. 批量多段求解


@router.post("/adaptive/solve-segments")
async def adaptive_solve_segments(req: AdaptiveSolveSegmentsRequest):
    """批量多段刀路自适应求解。

    输入每段剩余材料量与可选的力覆盖，输出整条刀路的参数序列与统计汇总。
    """
    try:
        params = AdaptiveMillingParams(
            material=req.material,
            cutter_diameter=req.cutter_diameter,
            flute_count=req.flute_count,
            target_force_n=req.target_force_n,
            radial_depth_ae=req.radial_depth_ae,
            axial_depth_ap_init=req.axial_depth_ap_init,
            max_axial_depth=req.max_axial_depth,
            min_axial_depth=req.min_axial_depth,
            max_fz=req.max_fz,
            min_fz=req.min_fz,
            max_feed=req.max_feed,
            min_feed=req.min_feed,
            spindle_rpm=req.spindle_rpm,
            stability_limit_ap=_resolve_stability_limit(
                req.spindle_rpm, req.material, req.cutter_diameter, req.stability_limit_ap
            ),
            kc1_1=req.kc1_1,
            mc=req.mc,
            safety_margin=req.safety_margin,
        )
        solver = AdaptiveMillingSolver(params)

        material_remainders = req.material_remainders if req.material_remainders else None
        force_overrides = req.force_overrides if req.force_overrides else None

        result = await asyncio.to_thread(
            solver.solve_segments,
            material_remainders,
            force_overrides,
            req.num_segments,
        )
        return success(
            data=result.to_dict(),
            message=f"批量求解完成：共 {len(result.segments)} 段",
        )
    except ValueError as e:
        logger.warning("批量求解参数错误: %s", e)
        return error(
            ErrorCode.INVALID_REQUEST,
            message=f"参数错误: {e}",
            recoverable=True,
        )
    except Exception as e:
        logger.exception("批量求解失败: %s", e)
        return error(
            ErrorCode.INTERNAL_ERROR,
            message=f"求解失败: {e}",
        )


# 3. 快速预览


@router.post("/adaptive/preview")
async def adaptive_preview():
    """用默认参数演示求解器效果。

    使用 45steel + D10×4 刀具 + 800N 目标力 + 5 段刀路演示。
    """
    try:
        # 桥接 LTC 颤振预测获取稳定性极限切深（闭环演示）
        stability_limit = _resolve_stability_limit(spindle_rpm=6000.0, material="45steel", cutter_diameter=10.0)
        params = AdaptiveMillingParams(stability_limit_ap=stability_limit)
        solver = AdaptiveMillingSolver(params)
        # 模拟 5 段刀路，剩余材料量递减
        material_remainders = [10.0, 8.0, 5.5, 3.0, 1.2]
        result = await asyncio.to_thread(
            solver.solve_segments,
            material_remainders,
            None,
            None,
        )
        return success(
            data=result.to_dict(),
            message="预览完成（5 段示例刀路）",
        )
    except Exception as e:
        logger.exception("预览失败: %s", e)
        return error(
            ErrorCode.INTERNAL_ERROR,
            message=f"预览失败: {e}",
        )


# 4. Kienzle 系数查询


@router.get("/kienzle/coefficients/{material}")
async def get_kienzle_coeffs(material: str):
    """查询材料的 Kienzle 系数。"""
    try:
        coeffs = get_kienzle_coefficients(material)
        return success(
            data={
                "material": material,
                "kc1_1": coeffs["kc1_1"],
                "mc": coeffs["mc"],
                "available_materials": list(DEFAULT_MATERIAL_COEFFICIENTS.keys()),
            }
        )
    except ValueError as e:
        logger.warning("Kienzle material not found: %s", e)
        return error(
            ErrorCode.NOT_FOUND,
            message="未找到指定材料，请检查材料名或使用可用材料列表",
            recoverable=True,
        )


# 5. Kienzle 正向切削力计算


@router.post("/kienzle/compute")
async def compute_kienzle_force(req: KienzleComputeRequest):
    """正向 Kienzle 切削力计算。"""
    try:
        forces = compute_cutting_forces(
            material=req.material,
            width=req.width,
            chip_thickness=req.chip_thickness,
            kc1_1=req.kc1_1,
            mc=req.mc,
        )
        return success(
            data={
                "material": req.material,
                "width_mm": req.width,
                "chip_thickness_mm": req.chip_thickness,
                "forces_n": forces,
                "formula": "Fz = kc1.1 * b * h^(1-mc)",
            }
        )
    except ValueError as e:
        logger.warning("Kienzle compute invalid input: %s", e)
        return error(
            ErrorCode.INVALID_REQUEST,
            message="切削力计算参数无效，请检查材料、宽度或切削厚度",
            recoverable=True,
        )
