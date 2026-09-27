"""G 代码生成模块 API 请求 / 响应模型（从 routes.py 拆分）。

将原 ``routes.py`` 中内联定义的 Pydantic BaseModel 类集中到本模块，
便于：

1. routes.py 仅保留路由处理逻辑，单文件行数从 1468 行降至 ~960 行；
2. 前端 / 测试代码可直接 ``from app.api.v1.gcode_generation.schemas import ...``
   导入模型进行类型校验，无需触发 routes.py 模块导入副作用（如
   pipeline 单例初始化、GCodeGenerator 加载等）；
3. 后续若需 OpenAPI schema 离线生成，可仅依赖本模块。

设计约束（项目记忆）：
- ``cam_validation_required`` 始终 True，不可关闭；
- SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）；
- ``allow_delete_succeeded`` 强制 False，不可由环境变量开启；
- 复用现有 GCodeGenerator（212 个测试用例覆盖），不重写后处理器。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建 G 代码生成任务请求体。

    输入是阶段 5 ChatterReport JSON 路径 + 阶段 3 OperationPlan JSON 路径
    + 控制器类型 + 材料名称 + 程序号 + 安全 Z + 毛坯顶面 Z。

    若 source_chatter_prediction_task_id / source_parametric_geometry_task_id
    存在且上游任务已 SUCCEEDED，本模块会自动从上游任务读取对应路径，
    调用方可不显式提供这些字段。
    """

    source_chatter_prediction_task_id: str = Field(
        default="",
        description=(
            "阶段 5 chatter_prediction 任务 ID（用于追溯 ChatterReport 路径）。为空时必须显式提供 chatter_report_path。"
        ),
    )
    source_parametric_geometry_task_id: str = Field(
        default="",
        description=(
            "阶段 3 parametric_geometry 任务 ID（用于追溯 OperationPlan 路径）。"
            "为空时必须显式提供 operation_plan_path。"
        ),
    )
    chatter_report_path: str = Field(
        default="",
        description=(
            "阶段 5 输出的 ChatterReport JSON 路径。"
            "为空时自动从 source_chatter_prediction_task_id 任务中读取。"
            "通常位于 output/chatter_prediction/{cp_task_id}/{cp_task_id}_chatter_report.json。"
        ),
    )
    operation_plan_path: str = Field(
        default="",
        description=(
            "阶段 3 输出的 OperationPlan JSON 路径。为空时自动从 source_parametric_geometry_task_id 任务中读取。"
        ),
    )
    controller_type: str = Field(
        default="fanuc_0i",
        description=(
            "目标 CNC 控制器类型：fanuc_0i / siemens_840d / heidenhain_tnc / "
            "xmachine_xm100。决定 G 代码文件扩展名（.nc / .mpf / .h）。"
        ),
    )
    material_name: str = Field(
        default="45#钢",
        description=(
            "材料名称（用于 G 代码注释 + 精度告知）。"
            "为空时自动从阶段 5 任务中读取 material_id。"
            "HRC52 触发 pending_calibration 标注。"
        ),
    )
    program_number: int = Field(
        default=1000,
        ge=1,
        le=9999,
        description="G 代码程序号（O 号，Fanuc 习惯 1-9999）。",
    )
    safe_z: float = Field(
        default=80.0,
        description="安全 Z 高度 (mm)，G 代码在特征切换时抬至此高度。",
    )
    stock_top_z: float = Field(
        default=50.0,
        description="毛坯顶面 Z (mm)，G 代码起刀点参考。",
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    source_chatter_report_path: str
    source_operation_plan_path: str
    controller_type: str
    material_name: str
    program_number: int
    safe_z: float
    stock_top_z: float
    cam_validation_required: bool
    gcode_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    """任务状态响应（含审核进度 + 生成统计）。"""

    task_id: str
    status: str
    source_chatter_report_path: str
    source_operation_plan_path: str
    controller_type: str
    material_name: str
    program_number: int
    safe_z: float
    stock_top_z: float
    feature_count: int
    stable_features: int
    unstable_features: int
    pending_calibration: bool
    prediction_method: str
    pending_review_count: int
    confirmed_count: int
    rejected_count: int
    edited_count: int
    cam_validation_required: bool
    gcode_file_path: str
    gcode_report_path: str
    error_message: str
    started_at: float
    completed_at: float
    reviewed_by: str
    reviewed_at: float
    warnings: list[str]
    errors: list[str]
    gcode_disclaimer: dict[str, Any]


class FeatureGCodeResultResponse(BaseModel):
    """单条 G 代码生成结果的响应。"""

    feature_id: str
    feature_type: str
    material_id: str
    spindle_rpm: float
    axial_depth_mm: float
    limit_depth_mm: float
    stable: bool
    safety_margin_ratio: float
    gcode_lines: list[str]
    line_range: list[int]
    warning: str
    review_status: str
    edited_params: dict[str, Any]
    effective_params: dict[str, Any]


class TaskResultResponse(BaseModel):
    """G 代码生成任务结果摘要（含全部特征 G 代码段列表）。"""

    task_id: str
    status: str
    controller_type: str
    material_name: str
    program_number: int
    total_features: int
    stable_features: int
    unstable_features: int
    pending_calibration: bool
    prediction_method: str
    cam_validation_required: bool
    gcode_file_path: str | None
    gcode_report_path: str | None
    error_message: str | None
    feature_results: list[FeatureGCodeResultResponse]
    gcode_disclaimer: dict[str, Any]


class ReviewRequest(BaseModel):
    """工程师审核请求体。"""

    action: str = Field(
        ...,
        description=(
            "审核动作：confirmed（确认 G 代码段无误）/ "
            "rejected（拒绝该特征，不进入最终 G 代码）/ "
            "edited（参数需修正，需同时提供 edited_params）"
        ),
    )
    edited_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "工程师编辑后的参数。仅 action=edited 时必须提供。"
            "字段可为 axial_depth_mm / limit_depth_mm / stable（bool）的子集。"
            "edited 仅记录修改意图，不触发 G 代码重新生成。"
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
    effective_params: dict[str, Any]
    all_reviewed: bool
    task_status: str
    gcode_disclaimer: dict[str, Any]


class ConfirmTaskResponse(BaseModel):
    """确认任务响应（导出 G 代码 + 报告 JSON，状态置为 SUCCEEDED）。"""

    task_id: str
    status: str
    controller_type: str
    material_name: str
    program_number: int
    total_features: int
    exported_features: int
    gcode_file_path: str
    gcode_report_path: str
    download_url: str
    report_download_url: str
    cam_validation_required: bool
    gcode_disclaimer: dict[str, Any]
