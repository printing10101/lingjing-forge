"""CAM 校验 API 请求/响应模型（从 routes.py 拆分，D5）。

Pydantic 模型；端点实现见 routes.py。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建 CAM 校验任务请求体。

    输入是阶段 6 G 代码报告 JSON 路径 + G 代码文件路径
    + 控制器类型 + 材料名称 + 安全 Z + 毛坯顶面 Z + CAM 后端。

    若 source_gcode_generation_task_id 存在且上游任务已 SUCCEEDED，
    本模块会自动从上游任务读取对应路径 + 上下文，调用方可不显式提供这些字段。
    """

    source_gcode_generation_task_id: str = Field(
        default="",
        description=(
            "阶段 6 gcode_generation 任务 ID（用于追溯 G 代码报告 + 文件路径 + "
            "控制器 / 材料 / safe_z / stock_top_z）。为空时必须显式提供 "
            "gcode_report_path。"
        ),
    )
    gcode_report_path: str = Field(
        default="",
        description=(
            "阶段 6 输出的 G 代码审核记录 JSON 路径。"
            "为空时自动从 source_gcode_generation_task_id 任务中读取。"
            "通常位于 output/gcode_generation/{gcode_task_id}/{gcode_task_id}_report.json。"
        ),
    )
    gcode_file_path: str = Field(
        default="",
        description=(
            "阶段 6 输出的 G 代码文件路径。为空时自动从上游任务读取，"
            "或从 gcode_report_path 的 gcode_file_path 字段读取。"
        ),
    )
    controller_type: str = Field(
        default="fanuc_0i",
        description=(
            "目标 CNC 控制器类型：fanuc_0i / siemens_840d / heidenhain_tnc / "
            "xmachine_xm100。仅用于 disclaimer 显示，不影响校验逻辑。"
        ),
    )
    material_name: str = Field(
        default="45#钢",
        description=(
            "材料名称（用于 disclaimer 显示 + 校准状态判断）。HRC52 触发 pending_calibration 标注（继承自阶段 5/6）。"
        ),
    )
    safe_z: float = Field(
        default=80.0,
        description="安全 Z 高度 (mm)，CollisionDetector 用于碰撞检测。",
    )
    stock_top_z: float = Field(
        default=50.0,
        description="毛坯顶面 Z (mm)，CollisionDetector 用于 AABB 包围盒计算。",
    )
    cam_backend: str = Field(
        default="internal_only",
        description=(
            "CAM 后端：internal_only（仅内部预校验）/ pycam / nx_open / "
            "powermill / manual。CAM 软件不可用时自动降级到 manual。"
        ),
    )
    tool_diameter_mm: float | None = Field(
        default=None,
        description=(
            "实际装刀直径 (mm，可选)。提供时体素材料去除仿真基于该直径执行"
            "（tool_source=actual）；缺省回退配置默认值并向任务写警告，"
            "提示工程师对照刀具表确认。"
        ),
    )
    tool_type: str = Field(
        default="",
        description="实际刀具类型（可选，如 end_mill / ball_end_mill），随体素仿真报告落盘。",
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    source_gcode_report_path: str
    source_gcode_file_path: str
    controller_type: str
    material_name: str
    safe_z: float
    stock_top_z: float
    cam_backend_requested: str
    cam_validation_required: bool
    cam_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    """任务状态响应（含审核进度 + 校验统计）。"""

    task_id: str
    status: str
    source_gcode_report_path: str
    source_gcode_file_path: str
    controller_type: str
    material_name: str
    safe_z: float
    stock_top_z: float
    gcode_total_lines: int
    total_features: int
    passed_features: int
    failed_features: int
    pending_calibration: bool
    prediction_method: str
    cam_backend_requested: str
    cam_backend_used: str
    cam_backend_fallback_reason: str
    pending_review_count: int
    confirmed_count: int
    rejected_count: int
    edited_count: int
    cam_validation_required: bool
    cam_report_path: str
    internal_report_path: str
    error_message: str
    started_at: float
    completed_at: float
    reviewed_by: str
    reviewed_at: float
    warnings: list[str]
    errors: list[str]
    cam_disclaimer: dict[str, Any]


class FeatureValidationResultResponse(BaseModel):
    """单条 CAM 校验结果的响应。"""

    feature_id: str
    feature_type: str
    line_range: list[int]
    internal_check_passed: bool
    internal_events: list[dict[str, Any]]
    cam_check_passed: bool
    cam_messages: list[str]
    cam_backend_used: str
    review_status: str
    edited_params: dict[str, Any]
    spindle_rpm: float
    axial_depth_mm: float
    limit_depth_mm: float
    stable: bool
    safety_margin_ratio: float
    warning: str


class TaskResultResponse(BaseModel):
    """CAM 校验任务结果摘要（含全部特征校验结果列表）。"""

    task_id: str
    status: str
    controller_type: str
    material_name: str
    gcode_total_lines: int
    total_features: int
    passed_features: int
    failed_features: int
    pending_calibration: bool
    prediction_method: str
    cam_backend_requested: str
    cam_backend_used: str
    cam_backend_fallback_reason: str
    cam_validation_required: bool
    cam_report_path: str | None
    internal_report_path: str | None
    error_message: str | None
    feature_results: list[FeatureValidationResultResponse]
    cam_disclaimer: dict[str, Any]


class ReviewRequest(BaseModel):
    """工程师审核请求体。"""

    action: str = Field(
        ...,
        description=(
            "审核动作：confirmed（确认特征校验结论）/ "
            "rejected（拒绝该特征，需阶段 6 重新生成 G 代码）/ "
            "edited（编辑校验参数，需同时提供 edited_params）"
        ),
    )
    edited_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "工程师编辑后的参数。仅 action=edited 时必须提供。"
            "字段可为 safe_z / cam_backend / stock_top_z 的子集。"
            "edited 仅记录修改意图，不触发流水线重新执行。"
        ),
    )
    engineer_notes: str = Field(
        default="",
        description="工程师备注（可选，便于审计追溯）。",
    )
    reviewed_by: str = Field(
        default="engineer",
        description="审核人标识。",
    )


class ReviewResponse(BaseModel):
    """审核结果响应。"""

    task_id: str
    feature_id: str
    feature_type: str
    review_status: str
    edited_params: dict[str, Any]
    all_reviewed: bool
    task_status: str
    cam_disclaimer: dict[str, Any]


class ConfirmTaskResponse(BaseModel):
    """确认任务响应（导出 cam_report + internal_report JSON，状态置为 SUCCEEDED）。"""

    task_id: str
    status: str
    controller_type: str
    material_name: str
    total_features: int
    passed_features: int
    failed_features: int
    cam_backend_used: str
    cam_report_path: str
    internal_report_path: str
    report_download_url: str
    internal_report_download_url: str
    cam_validation_required: bool
    cam_disclaimer: dict[str, Any]
