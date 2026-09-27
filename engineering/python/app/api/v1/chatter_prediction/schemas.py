"""颤振预测模块 API 请求 / 响应模型（从 routes.py 拆分）。

将原 ``routes.py`` 中内联定义的 Pydantic BaseModel 类集中到本模块，
便于：

1. routes.py 仅保留路由处理逻辑，单文件行数从 1258 行降至 ~870 行；
2. 前端 / 测试代码可直接 ``from app.api.v1.chatter_prediction.schemas import ...``
   导入模型进行类型校验，无需触发 routes.py 模块导入副作用（如
   pipeline 单例初始化、LTC 模型探测等）；
3. 后续若需 OpenAPI schema 离线生成，可仅依赖本模块。

设计约束（项目记忆）：
- ``cam_validation_required`` 始终 True，不可关闭；
- ``k_s``（cutting_force_coeff）直接取自阶段 4，不二次拟合；
- SUCCEEDED 状态禁止删除（阶段 6 G 代码生成可能已引用 ChatterReport）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建颤振预测任务请求体。

    输入是阶段 4 任务 ID（追溯用）+ ChatterParams JSON 路径 + 材料 ID。
    若 source_cutting_parameters_task_id 存在且上游任务已 SUCCEEDED，
    本模块会自动从上游任务读取 chatter_params_path / material_id / mesh_calibrated，
    调用方可不显式提供这些字段。
    """

    source_cutting_parameters_task_id: str = Field(
        ...,
        description=(
            "阶段 4 cutting_parameters 任务 ID（用于追溯 ChatterParams 来源 "
            "及查询上游 mesh_calibrated / material_id 状态）。"
            "若上游任务不存在或未完成，必须显式提供 chatter_params_path + material_id。"
        ),
    )
    chatter_params_path: str = Field(
        default="",
        description=(
            "阶段 4 输出的 ChatterParams JSON 路径。"
            "为空时自动从 source_cutting_parameters_task_id 任务中读取。"
            "通常位于 output/cutting_parameters/{cp_task_id}/{cp_task_id}_chatter_params.json。"
        ),
    )
    material_id: str = Field(
        default="",
        description=(
            "材料 ID：al_6061 / ti_tc4 / steel_hrc52 等。"
            "为空时自动从阶段 4 任务中读取。HRC52 触发 pending_calibration 强制降低置信度。"
        ),
    )
    precision_tier: str = Field(
        default="standard",
        description="精度档位（继承自阶段 1/2/3/4）：coarse / standard / high。",
    )
    mesh_calibrated: bool | None = Field(
        default=None,
        description=(
            "上游 mesh 是否已做尺度归一化。None 时通过 source_cutting_parameters_task_id 自动查询阶段 4 任务。"
        ),
    )
    machine_type: str = Field(
        default="vmc_850",
        description="机床类型标识（仅供追溯，不直接影响预测算法）。",
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    source_cutting_parameters_task_id: str
    chatter_params_path: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    machine_type: str
    ltc_model_available: bool
    chatter_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    """任务状态响应（含审核进度 + 预测方法分布）。"""

    task_id: str
    status: str
    source_cutting_parameters_task_id: str
    chatter_params_path: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    machine_type: str
    feature_count: int
    predicted_count: int
    analytical_count: int
    neural_network_count: int
    fallback_count: int
    ltc_model_available: bool
    pending_count: int
    confirmed_count: int
    rejected_count: int
    edited_count: int
    cam_validation_required: bool
    chatter_report_path: str
    error_message: str
    created_at: float
    started_at: float
    completed_at: float
    chatter_disclaimer: dict[str, Any]


class FeatureChatterResultResponse(BaseModel):
    """单条颤振预测结果的响应。"""

    feature_id: str
    feature_type: str
    material_id: str
    spindle_rpm: float
    axial_depth_mm: float
    limit_depth_mm: float
    stable: bool
    stability_margin: float
    method: str
    ltc_active: bool
    confidence: float
    inference_time_ms: float
    warnings: list[str]
    material_calibration_status: str
    review_status: str
    edited_params: dict[str, Any]
    effective_params: dict[str, Any]
    reviewed_by: str
    reviewed_at: float
    engineer_notes: str
    source_cutting_params_task_id: str
    machine_id: str
    tool_id: str
    cutting_force_coeff: float


class TaskResultResponse(BaseModel):
    """颤振预测任务结果摘要（含全部特征预测结果列表）。"""

    task_id: str
    status: str
    source_cutting_parameters_task_id: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    feature_count: int
    predicted_count: int
    analytical_count: int
    neural_network_count: int
    fallback_count: int
    ltc_model_available: bool
    cam_validation_required: bool
    chatter_report_path: str
    error_message: str | None
    feature_results: list[FeatureChatterResultResponse]
    chatter_disclaimer: dict[str, Any]


class ReviewRequest(BaseModel):
    """工程师审核请求体。"""

    action: str = Field(
        ...,
        description=(
            "审核动作：confirmed（确认预测结果无误）/ "
            "rejected（拒绝该特征，不进入最终 ChatterReport）/ "
            "edited（参数需修正，需同时提供 edited_params）"
        ),
    )
    edited_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "工程师编辑后的参数。仅 action=edited 时必须提供。"
            "字段可为 limit_depth_mm / axial_depth_mm / stable（0/1）的子集。"
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
    chatter_disclaimer: dict[str, Any]


class ExportChatterReportResponse(BaseModel):
    """导出 ChatterReport 响应（阶段 6 输入）。"""

    task_id: str
    status: str
    source_cutting_parameters_task_id: str
    material_id: str
    feature_count: int
    chatter_report_path: str
    download_url: str
    chatter_params_ready: bool
    cutting_disclaimer: dict[str, Any]
