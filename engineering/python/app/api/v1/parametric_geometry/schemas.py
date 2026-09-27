"""参数化几何输出模块 API 请求 / 响应模型（从 routes.py 拆分）。

将原 ``routes.py`` 中内联定义的 Pydantic BaseModel 类集中到本模块，
便于：

1. routes.py 仅保留路由处理逻辑，单文件行数从 1161 行降至 ~840 行；
2. 前端 / 测试代码可直接 ``from app.api.v1.parametric_geometry.schemas import ...``
   导入模型进行类型校验，无需触发 routes.py 模块导入副作用（如
   pythonOCC/FreeCAD 可选依赖加载、pipeline 单例初始化等）；
3. 后续若需 OpenAPI schema 离线生成，可仅依赖本模块。

设计约束（项目记忆）：
- mesh → 参数化 CAD 自动转换工业上未解决，本模块输出「算法建议 STEP」；
- 工程师必须两轮审核（STEP_GENERATED + finalize）后才允许下载最终 STEP；
- 最终 STEP 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床；
- 系统定位「工程师助手」，非「全自动生产线」。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建参数化几何任务请求体。

    输入是阶段 2 feature_extraction 模块导出的 confirmed_features.json 路径，
    而非 mesh 文件（阶段 3 不再处理 mesh，仅处理已审核的特征参数）。
    """

    source_feature_extraction_task_id: str = Field(
        ...,
        description=(
            "阶段 2 feature_extraction 任务 ID（用于追溯 confirmed_features.json 来源 "
            "及查询上游 mesh_calibrated 状态）。"
            "若上游任务不存在或未完成，按未标定 mesh 处理。"
        ),
    )
    input_features_path: str = Field(
        ...,
        description=(
            "阶段 2 导出的 confirmed_features.json 文件路径。"
            "通常位于 output/feature_extraction/{fe_task_id}/confirmed_features_{fe_task_id}.json。"
        ),
    )
    precision_tier: str = Field(
        default="standard",
        description=("精度档位（继承自阶段 1/2）：coarse / standard / high。本模块不引入新档位，仅用于显示告知。"),
    )
    mesh_calibrated: bool | None = Field(
        default=None,
        description=(
            "上游 mesh 是否已做尺度归一化。"
            "若不提供（None），系统自动通过 source_feature_extraction_task_id "
            "查询阶段 1 image_to_3d 任务的 calibrated 字段。"
            "若查询失败，按未标定处理。"
        ),
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    source_feature_extraction_task_id: str
    input_features_path: str
    precision_tier: str
    mesh_calibrated: bool
    step_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    """任务状态响应（含完整审核进度）。"""

    task_id: str
    status: str
    source_feature_extraction_task_id: str
    input_features_path: str
    precision_tier: str
    mesh_calibrated: bool
    feature_count: int
    pending_count: int
    confirmed_count: int
    rejected_count: int
    edited_count: int
    step_output_path: str | None
    final_step_path: str | None
    engine_used: str | None
    cam_validation_required: bool
    error_message: str | None
    created_at: float
    updated_at: float
    step_disclaimer: dict[str, Any]


class FeatureRefResponse(BaseModel):
    """单条已审核特征引用的响应。"""

    feature_id: str
    feature_type: str
    source_params: dict[str, Any]
    review_status: str
    edited_params: dict[str, Any] | None
    effective_params: dict[str, Any]
    engineer_notes: str | None
    reviewed_by: str | None
    reviewed_at: float | None


class TaskResultResponse(BaseModel):
    """参数化几何任务结果摘要（含装配信息与特征列表）。"""

    task_id: str
    status: str
    source_feature_extraction_task_id: str
    feature_count: int
    brep_shape_count: int
    engine_used: str | None
    step_output_path: str | None
    final_step_path: str | None
    precision_tier: str
    mesh_calibrated: bool
    cam_validation_required: bool
    error_message: str | None
    assembly_summary: dict[str, Any] | None
    features: list[FeatureRefResponse]
    step_disclaimer: dict[str, Any]


class ReviewRequest(BaseModel):
    """工程师审核请求体（第一轮：审核 STEP 中的特征表达）。"""

    action: str = Field(
        ...,
        description=(
            "审核动作：confirmed（确认 STEP 表达正确）/ "
            "rejected（误识别，从最终 STEP 中移除）/ "
            "edited（参数需修正，需同时提供 edited_params）"
        ),
    )
    edited_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "工程师编辑后的参数。仅 action=edited 时必须提供，"
            "字段结构需与 source_params 一致（如 cylinder: center/axis/radius_mm/height_mm）。"
        ),
    )
    engineer_notes: str = Field(
        default="",
        description="工程师备注（可选，便于审计追溯）。",
    )
    reviewed_by: str = Field(
        default="engineer",
        description="审核人标识（默认 'engineer'，便于多工程师协同场景区分）。",
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
    step_disclaimer: dict[str, Any]


class FinalizeResponse(BaseModel):
    """最终化 STEP 响应（第二轮 STEP 生成结果）。"""

    task_id: str
    status: str
    source_feature_extraction_task_id: str
    feature_count: int
    brep_shape_count: int
    engine_used: str | None
    step_output_path: str | None
    final_step_path: str | None
    precision_tier: str
    mesh_calibrated: bool
    assembly_summary: dict[str, Any] | None
    download_url: str
    step_disclaimer: dict[str, Any]
