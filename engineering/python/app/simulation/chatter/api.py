"""颤振稳定性可视化与模态辨识 API。

落地竞品分析中识别的三个补强点：
1. CutPro 式稳定性叶图（SLD）可视化输出
   - ``compute_stability_lobe`` 已存在于 stability.py，本模块将其暴露为 API
   - 返回 ECharts 友好格式（lobe 序列 + 最佳工作点 + 不稳定区域）
2. 模态参数输入接口（用户上传锤击测试 FRF 数据）
   - 接收 CSV/JSON 格式的频响函数数据
   - 解析为多模态参数，覆盖默认单自由度假设
3. 在线模态辨识（清华深圳思路）
   - 基于频响函数曲线拟合多自由度模态参数
   - 单点最小二乘 + 多模态峰值拾取混合算法

端点前缀：/api/chatter
"""

import csv
import io
import json
import logging
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import success
from app.core.safe_errors import safe_error_message
from app.simulation.chatter.stability import (
    MachineParams,
    ToolParams,
    DEFAULT_TOOL_PARAMS,
    compute_stability_lobe,
    get_machine_params,
)
from app.simulation.chatter.predictor import predict_stability
from app.utils.upload_security import validate_upload

# 颤振 FRF 数据上传限制：CSV/JSON 文本数据，100MB 上限
_CHATTER_UPLOAD_MAX_SIZE = 100 * 1024 * 1024
_CHATTER_ALLOWED_EXTENSIONS = {".csv", ".json"}
_CHATTER_ALLOWED_MIMES = {"text/csv", "application/json"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chatter", tags=["Chatter"])


# 请求/响应模型


class SLDRequest(BaseModel):
    """稳定性叶图请求。"""

    machine_id: str = Field(default="vmc_850", description="机床标识")
    tool_id: str = Field(default="endmill_d10", description="刀具标识")
    speed_min: float = Field(default=1000.0, gt=0, description="起始转速 rpm")
    speed_max: float = Field(default=10000.0, gt=0, description="终止转速 rpm")
    num_points: int = Field(default=100, ge=20, le=500, description="每叶点数")
    num_lobes: int = Field(default=5, ge=1, le=10, description="叶图数")
    # 自定义模态参数（可选，覆盖默认机床参数）
    custom_modal: dict | None = Field(
        default=None,
        description=("自定义模态参数，覆盖机床默认值。字段：stiffness_z, damping_ratio, natural_freq, modal_mass"),
    )
    # 实际加工切深（可选，用于计算不稳定转速区间）
    # 未传时默认 2.0mm，并在响应中标注 depth_source="default"
    actual_axial_depth: float | None = Field(
        default=None,
        gt=0,
        description="实际加工轴向切深 (mm)，用于精确计算不稳定转速区间",
    )


class ModalIdentificationRequest(BaseModel):
    """在线模态辨识请求。"""

    freqs: list[float] = Field(..., description="频率序列 Hz")
    re_frf: list[float] = Field(..., description="FRF 实部序列 mm/N")
    im_frf: list[float] = Field(..., description="FRF 虚部序列 mm/N")
    max_modes: int = Field(default=3, ge=1, le=8, description="最大辨识模态数")


class PredictRequest(BaseModel):
    """单点稳定性预测请求。"""

    spindle_rpm: float = Field(..., gt=0, description="主轴转速 rpm")
    machine_id: str = Field(default="vmc_850")
    tool_id: str = Field(default="endmill_d10")
    axial_depth: float | None = Field(default=None, gt=0, description="实际轴向切深 mm，用于判定稳定性")


# 1. SLD 稳定性叶图可视化端点


@router.post("/sld", dependencies=[Depends(require_permission("chatter:write"))])
async def get_stability_lobe_diagram(req: SLDRequest):
    """生成稳定性叶图（SLD）可视化数据。

    返回结构适配 ECharts line 系列：
    - ``lobes``: 每个叶图一条曲线（speed vs limit_depth）
    - ``best_points``: 每个叶图中极限切深最大的点（推荐工作点）
    - ``unstable_region``: 当前切深下的不稳定转速区间
    """
    try:
        machine = get_machine_params(req.machine_id)

        # 应用自定义模态参数
        if req.custom_modal:
            machine = _apply_custom_modal(machine, req.custom_modal)

        # 获取刀具参数
        if req.tool_id in DEFAULT_TOOL_PARAMS:
            tool = ToolParams(tool_id=req.tool_id, **DEFAULT_TOOL_PARAMS[req.tool_id])
        else:
            tool = ToolParams(tool_id=req.tool_id)

        # 生成稳定性叶图
        result = compute_stability_lobe(
            machine=machine,
            tool=tool,
            speed_range=(req.speed_min, req.speed_max),
            num_points=req.num_points,
            num_lobes=req.num_lobes,
        )

        # 转换为前端友好的格式
        lobe_series = []
        best_points = []
        for idx, (speeds, depths) in enumerate(result["lobes"]):
            if not speeds:
                continue
            series_data = [{"speed": round(s, 1), "depth": round(d, 3)} for s, d in zip(speeds, depths)]
            lobe_series.append(
                {
                    "name": f"Lobe {idx}",
                    "data": series_data,
                }
            )
            # 找出该叶图中极限切深最大的点
            max_idx = int(np.argmax(depths))
            best_points.append(
                {
                    "lobe": idx,
                    "speed": round(speeds[max_idx], 1),
                    "depth": round(depths[max_idx], 3),
                }
            )

        # 计算不稳定区域：优先使用用户传入的实际切深
        assumed_depth = req.actual_axial_depth if req.actual_axial_depth else 2.0
        depth_source = "actual" if req.actual_axial_depth else "default"
        unstable_ranges = _compute_unstable_ranges(result, assumed_depth)

        return success(
            data={
                "machine_id": req.machine_id,
                "tool_id": req.tool_id,
                "speed_range": [req.speed_min, req.speed_max],
                "lobe_series": lobe_series,
                "best_points": best_points,
                "unstable_ranges": unstable_ranges,
                "assumed_depth_mm": assumed_depth,
                "depth_source": depth_source,
                "modal_params": {
                    "natural_freq_hz": machine.natural_freq,
                    "damping_ratio": machine.damping_ratio,
                    "stiffness_z": machine.stiffness_z,
                    "modal_mass": machine.modal_mass,
                },
                "tool_params": {
                    "diameter": tool.diameter,
                    "num_flutes": tool.num_flutes,
                    "cutting_force_coeff": tool.cutting_force_coeff,
                },
            }
        )

    except (ValueError, KeyError, TypeError) as e:
        # [P0-18] 避免异常详情泄露：safe_error_message 内部已 logger.exception 记录堆栈
        # 并生成 error_id 供报障关联；生产环境仅返回通用提示，不暴露 {e}
        safe = safe_error_message(e, fallback="SLD 生成失败，请检查输入参数", context="chatter.sld")
        raise HTTPException(status_code=400, detail=safe) from e


# 2. 模态参数输入接口（锤击测试数据上传）


@router.post("/modal/upload", dependencies=[Depends(require_permission("chatter:write"))])
async def upload_modal_data(
    file: UploadFile = File(...),
    machine_id: str = "custom",
):
    """上传锤击测试 FRF 数据文件（CSV/JSON）。

    CSV 格式：第一行表头 freq_hz,re_frf,im_frf，后续为数值
    JSON 格式：{"freqs": [...], "re_frf": [...], "im_frf": [...]}

    返回解析后的频响数据 + 自动辨识的模态参数。
    """
    try:
        # P0-11/P0-13 修复：使用统一上传校验（扩展名 + magic bytes + 分块读取 + 大小限制）
        content = await validate_upload(
            file,
            max_size=_CHATTER_UPLOAD_MAX_SIZE,
            allowed_extensions=_CHATTER_ALLOWED_EXTENSIONS,
            allowed_mimes=_CHATTER_ALLOWED_MIMES,
        )
        filename = file.filename or ""

        if filename.lower().endswith(".json"):
            data = json.loads(content.decode("utf-8"))
            freqs = np.asarray(data["freqs"], dtype=float)
            re_frf = np.asarray(data["re_frf"], dtype=float)
            im_frf = np.asarray(data["im_frf"], dtype=float)
        elif filename.lower().endswith(".csv"):
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            freqs_list, re_list, im_list = [], [], []
            for row in reader:
                freqs_list.append(float(row.get("freq_hz", row.get("frequency", 0))))
                re_list.append(float(row.get("re_frf", row.get("re", 0))))
                im_list.append(float(row.get("im_frf", row.get("im", 0))))
            freqs = np.asarray(freqs_list)
            re_frf = np.asarray(re_list)
            im_frf = np.asarray(im_list)
        else:
            # 理论上不会到这里：validate_upload 已校验扩展名
            raise HTTPException(
                status_code=400,
                detail="仅支持 .csv 或 .json 格式的 FRF 数据文件",
            )

        # 在线模态辨识
        modal_params = identify_modal_parameters(
            freqs=freqs,
            re_frf=re_frf,
            im_frf=im_frf,
            max_modes=3,
        )

        return success(
            data={
                "machine_id": machine_id,
                "sample_count": int(len(freqs)),
                "freq_range_hz": [float(freqs.min()), float(freqs.max())],
                "identified_modes": modal_params,
                "raw_frf_preview": {
                    "freqs": freqs[:50].tolist(),
                    "re_frf": re_frf[:50].tolist(),
                    "im_frf": im_frf[:50].tolist(),
                },
            }
        )

    except HTTPException:
        raise
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        # [P0-18] 避免异常详情泄露：文件解析错误不回传原始异常文本
        safe = safe_error_message(e, fallback="模态数据解析失败，请检查文件格式", context="chatter.modal_upload")
        raise HTTPException(status_code=400, detail=safe) from e


# 3. 在线模态辨识（直接基于频响函数序列）


@router.post("/modal/identify", dependencies=[Depends(require_permission("chatter:write"))])
async def identify_modal(req: ModalIdentificationRequest):
    """基于频响函数曲线辨识多模态参数。

    算法流程：
    1. 计算复频响函数幅值 |G(ω)|
    2. 峰值拾取定位模态频率
    3. 半功率带宽法估计阻尼比
    4. 单模态拟合估计刚度和模态质量
    """
    try:
        if len(req.freqs) != len(req.re_frf) or len(req.freqs) != len(req.im_frf):
            raise HTTPException(
                status_code=400,
                detail="freqs / re_frf / im_frf 长度必须一致",
            )

        freqs = np.asarray(req.freqs, dtype=float)
        re_frf = np.asarray(req.re_frf, dtype=float)
        im_frf = np.asarray(req.im_frf, dtype=float)

        modes = identify_modal_parameters(
            freqs=freqs,
            re_frf=re_frf,
            im_frf=im_frf,
            max_modes=req.max_modes,
        )

        return success(
            data={
                "sample_count": int(len(freqs)),
                "freq_range_hz": [float(freqs.min()), float(freqs.max())],
                "identified_modes": modes,
            }
        )

    except HTTPException:
        raise
    except (ValueError, TypeError) as e:
        # [P0-18] 避免异常详情泄露
        safe = safe_error_message(e, fallback="模态辨识失败，请检查频响数据", context="chatter.modal_identify")
        raise HTTPException(status_code=400, detail=safe) from e


# 4. 单点稳定性预测（兼容旧接口，补充切深判定）


@router.post("/predict", dependencies=[Depends(require_permission("chatter:write"))])
async def predict_chatter_stability(req: PredictRequest):
    """单点稳定性预测。

    返回稳定性状态、极限切深、推荐工作点。
    """
    try:
        result = predict_stability(
            spindle_rpm=req.spindle_rpm,
            machine=req.machine_id,
            tool=req.tool_id,
        )

        # 若指定了实际切深，判定稳定性
        if req.axial_depth is not None:
            limit_depth = float(result.get("limit_depth") or 5.0)
            stable = req.axial_depth < limit_depth
            result["actual_depth"] = req.axial_depth
            result["stable"] = stable
            result["safety_margin"] = round((limit_depth - req.axial_depth) / req.axial_depth * 100, 2)

        return success(data=result)

    except (ValueError, KeyError, TypeError) as e:
        # [P0-18] 避免异常详情泄露
        safe = safe_error_message(e, fallback="稳定性预测失败，请检查参数配置", context="chatter.predict")
        raise HTTPException(status_code=400, detail=safe) from e


# 辅助函数


def _apply_custom_modal(machine: MachineParams, custom: dict) -> MachineParams:
    """应用用户自定义模态参数到机床对象。"""
    return MachineParams(
        machine_id=machine.machine_id,
        stiffness_x=custom.get("stiffness_x", machine.stiffness_x),
        stiffness_y=custom.get("stiffness_y", machine.stiffness_y),
        stiffness_z=custom.get("stiffness_z", machine.stiffness_z),
        damping_ratio=custom.get("damping_ratio", machine.damping_ratio),
        natural_freq=custom.get("natural_freq", machine.natural_freq),
        modal_mass=custom.get("modal_mass", machine.modal_mass),
    )


def _compute_unstable_ranges(
    lobe_data: dict[str, Any],
    assumed_depth: float,
) -> list[dict]:
    """计算给定切深下的不稳定转速区间。

    对每个叶图，找出 limit_depth < assumed_depth 的转速区间。
    """
    unstable_ranges = []
    for idx, (speeds, depths) in enumerate(lobe_data["lobes"]):
        if not speeds:
            continue
        in_unstable = False
        range_start: float | None = None
        for s, d in zip(speeds, depths):
            if d < assumed_depth and not in_unstable:
                in_unstable = True
                range_start = s
            elif d >= assumed_depth and in_unstable:
                in_unstable = False
                unstable_ranges.append(
                    {
                        "lobe": idx,
                        "speed_start": round(range_start or 0.0, 1),
                        "speed_end": round(s, 1),
                    }
                )
        if in_unstable:
            unstable_ranges.append(
                {
                    "lobe": idx,
                    "speed_start": round(range_start or 0.0, 1),
                    "speed_end": round(speeds[-1], 1),
                }
            )
    return unstable_ranges


def identify_modal_parameters(
    freqs: np.ndarray,
    re_frf: np.ndarray,
    im_frf: np.ndarray,
    max_modes: int = 3,
) -> list[dict]:
    """基于频响函数曲线辨识多模态参数。

    采用峰值拾取 + 半功率带宽法：
    1. 计算幅值 |G(ω)| = sqrt(re² + im²)
    2. 寻找局部极大值（模态频率）
    3. 在每个峰值附近用半功率带宽估计阻尼比
    4. 单模态拟合：k = 1/|G(ω_n)|, m = k/(2π f_n)²

    Args:
        freqs: 频率序列 (Hz)
        re_frf: FRF 实部 (mm/N)
        im_frf: FRF 虚部 (mm/N)
        max_modes: 最大辨识模态数

    Returns:
        辨识出的模态参数列表，每项含 natural_freq, damping_ratio, stiffness, modal_mass
    """
    if len(freqs) < 5:
        raise ValueError("频率点数过少，至少需要 5 个点进行模态辨识")

    # 幅值谱
    magnitude = np.sqrt(re_frf**2 + im_frf**2)

    # 寻找局部极大值（简单峰值拾取）
    peaks = []
    for i in range(1, len(magnitude) - 1):
        if magnitude[i] > magnitude[i - 1] and magnitude[i] > magnitude[i + 1]:
            peaks.append((i, freqs[i], magnitude[i]))

    # 按幅值降序排序，取前 max_modes 个
    peaks.sort(key=lambda x: x[2], reverse=True)
    peaks = peaks[:max_modes]

    modes = []
    for peak_idx, f_n, peak_mag in peaks:
        # 半功率带宽法估计阻尼比
        half_power = peak_mag / np.sqrt(2)

        # 向左搜索半功率点
        left_idx = peak_idx
        while left_idx > 0 and magnitude[left_idx] > half_power:
            left_idx -= 1

        # 向右搜索半功率点
        right_idx = peak_idx
        while right_idx < len(magnitude) - 1 and magnitude[right_idx] > half_power:
            right_idx += 1

        f1 = freqs[left_idx]
        f2 = freqs[right_idx]
        bandwidth = f2 - f1

        # 阻尼比 ζ = Δf / (2 f_n)
        if f_n > 0 and bandwidth > 0:
            zeta = bandwidth / (2 * f_n)
            zeta = max(0.001, min(0.5, zeta))  # 限制在合理范围
        else:
            zeta = 0.05

        # 单模态拟合：k = 1/|G(ω_n)|（单位 mm/N N/m）
        # peak_mag 单位 mm/N，转换为 m/N：peak_mag / 1000
        peak_mag_m_per_n = peak_mag / 1000.0
        if peak_mag_m_per_n > 0:
            stiffness = 1.0 / peak_mag_m_per_n  # N/m
            # m = k / (2π f_n)²
            modal_mass = stiffness / (2 * np.pi * f_n) ** 2
        else:
            stiffness = 1.5e7
            modal_mass = 50.0

        modes.append(
            {
                "natural_freq_hz": round(float(f_n), 2),
                "damping_ratio": round(float(zeta), 4),
                "stiffness_n_per_m": round(float(stiffness), 1),
                "modal_mass_kg": round(float(modal_mass), 3),
                "peak_magnitude_mm_per_n": round(float(peak_mag), 6),
                "bandwidth_hz": round(float(bandwidth), 2),
            }
        )

    # 按频率升序排序
    modes.sort(key=lambda x: x["natural_freq_hz"])
    return modes
