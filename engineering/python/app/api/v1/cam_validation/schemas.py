"""CAM 校验 API — 请求/响应 Pydantic 模型。

V3.0 split: 从 ``routes.py`` 提取，保持文件聚焦。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# 请求 / 响应模型


class TaskCreateRequest(BaseModel):
    """创建 CAM 校验任务请求体。

    输入是阶段 6 G 代码报告 JSON 路径 + G 代码文件路径
    + 控制器类型 + 材料名称 + 安全 Z + 毛坯顶面 Z + CAM 后端。

    若 source_gcode_generation_task_id 存在且上游任务已 SUCCEEDED，
    本模块会自动从上游任务读取对应路径 + 上下文，调用方可不显式提供这些字段。
    """

    source_gcode_generation_task_id: str | None = Field(
        default=None,
        description="上游 gcode_generation 任务 ID（可选）",
    )
    source_gcode_report_path: str | None = Field(
        default=None,
        description="阶段 6 G 代码报告 JSON 路径",
    )
    source_gcode_file_path: str | None = Field(
        default=None,
        description="阶段 6 生成的 G 代码文件路径",
    )
    controller_type: str = Field(
        default="fanuc",
        description="控制器类型（fanuc / siemens / heidenhain / haas / okuma / mazak / ...）",
    )
    material_name: str | None = Field(
        default=None,
        description="材料名称（默认从上游 ChatterReport 推断）",
    )
    safety_z_mm: float | None = Field(
        default=None,
        description="安全 Z 平面高度（默认从上游 G 代码报告推断）",
    )
    stock_top_z_mm: float | None = Field(
        default=None,
        description="毛坯顶面 Z 高度（默认从上游 G 代码报告推断）",
    )
    cam_backend: str | None = Field(
        default=None,
        description="CAM 后端名称（默认自动检测或使用 PyCAM）",
    )


class TaskCreateResponse(BaseModel):
    """任务创建成功响应。"""

    task_id: str
    status: str
    cam_backend: str
    cam_backend_version: str
    precision_tier: str
    pending_calibration: bool
    disclaimer: dict[str, str]


class TaskStatusResponse(BaseModel):
    """任务状态查询响应。"""

    task_id: str
    status: str
    cam_backend: str
    cam_backend_version: str
    controller_type: str
    material_name: str
    precision_tier: str
    pending_calibration: bool
    validation_progress: dict[str, int] = Field(default_factory=lambda: {"pending": 0, "validated": 0, "reviewed": 0})
    validation_summary: dict[str, int] = Field(
        default_factory=lambda: {
            "pass": 0,
            "fail": 0,
            "warning": 0,
            "error": 0,
        }
    )
    disclaimer: dict[str, str] = Field(default_factory=dict)
    source_gcode_report_path: str = ""
    source_gcode_file_path: str = ""
    prediction_method: str = "analytical"
    num_features: int = 0


class FeatureValidationResultResponse(BaseModel):
    """单个特征校验结果响应。"""

    feature_id: str
    feature_type: str
    gcode_segment_ids: list[str]
    nc_file: list[str]
    internal_error_info: dict[str, Any] | None = None
    out_of_gouge: bool = True
    gouge_details: list[dict[str, Any]] = Field(default_factory=list)
    out_of_collision: bool = True
    collision_details: list[dict[str, Any]] = Field(default_factory=list)
    out_of_travel: bool = True
    travel_details: list[dict[str, Any]] = Field(default_factory=list)
    feed_rate_ok: bool = True
    feed_rate_details: list[dict[str, Any]] = Field(default_factory=list)
    safety_z_ok: bool = True
    safety_z_details: list[dict[str, Any]] = Field(default_factory=list)
    review_status: str = "pending"
    corrected_params: dict[str, Any] | None = None
    review_notes: str = ""
    reviewer: str = ""


class TaskResultResponse(BaseModel):
    """任务校验结果响应。"""

    task_id: str
    results: list[FeatureValidationResultResponse]
    total: int
    pass_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    error_count: int = 0


class ReviewRequest(BaseModel):
    """审核请求体。"""

    review_status: str = Field(
        ...,
        description="审核结果：confirmed / rejected / edited / reviewed",
    )
    corrected_params: dict[str, Any] | None = Field(
        default=None,
        description="修正后的参数（review_status=edited 时需提供）",
    )
    notes: str = Field(default="", description="审核批注")


class ReviewResponse(BaseModel):
    """审核结果响应。"""

    feature_id: str
    review_status: str
    corrected_params: dict[str, Any] | None = None
    message: str


class ConfirmTaskResponse(BaseModel):
    """任务确认响应。"""

    task_id: str
    status: str
    cam_report_path: str | None = None
    internal_report_path: str | None = None
    message: str
