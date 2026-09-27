"""切削参数推荐模块 API 请求 / 响应模型（从 routes.py 拆分）。

将原 ``routes.py`` 中内联定义的 Pydantic BaseModel 类集中到本模块，
便于：

1. routes.py 仅保留路由处理逻辑，单文件行数从 1240 行降至 ~870 行；
2. 前端 / 测试代码可直接 ``from app.api.v1.cutting_parameters.schemas import ...``
   导入模型进行类型校验，无需触发 routes.py 模块导入副作用（如
   pipeline 单例初始化、材料库加载等）；
3. 后续若需 OpenAPI schema 离线生成，可仅依赖本模块。

设计约束（项目记忆）：
- 本模块输出 ChatterParams 仅供阶段 5 颤振预测参考，不可直接用于机床；
- 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字；
- 系统定位「工程师助手」，非「全自动切削参数生成器」。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """创建切削参数推荐任务请求体。

    输入是阶段 3 STEP 文件路径 + 阶段 2 confirmed_features.json 路径 + 材料 ID。
    STEP 文件路径仅作追溯用，本模块不重新解析 STEP，
    实际特征参数取自阶段 2 confirmed_features.json。
    """

    source_parametric_geometry_task_id: str = Field(
        ...,
        description=(
            "阶段 3 parametric_geometry 任务 ID（用于追溯 STEP 文件来源 "
            "及查询上游 mesh_calibrated 状态）。"
            "若上游任务不存在或未完成，按未标定 mesh 处理。"
        ),
    )
    step_file_path: str = Field(
        ...,
        description=(
            "阶段 3 输出的 STEP 文件路径（仅作追溯用，本模块不解析 STEP）。"
            "通常位于 output/parametric_geometry/{pg_task_id}/{pg_task_id}_final.step。"
        ),
    )
    input_features_path: str = Field(
        ...,
        description=(
            "阶段 2 导出的 confirmed_features.json 路径。切削参数推荐基于其中的 feature_id / feature_type 字段。"
        ),
    )
    material_id: str = Field(
        ...,
        description=(
            "材料 ID：al_6061 / ti_tc4 / steel_hrc52 等。"
            "材料数据库中 17 种材料可用，HRC52 通过内存补充数据（待自采校准）。"
        ),
    )
    precision_tier: str = Field(
        default="standard",
        description="精度档位（继承自阶段 1/2/3）：coarse / standard / high。",
    )
    mesh_calibrated: bool | None = Field(
        default=None,
        description=(
            "上游 mesh 是否已做尺度归一化。None 时通过 source_parametric_geometry_task_id 自动查询阶段 3 任务。"
        ),
    )
    machine_type: str = Field(
        default="vmc_850",
        description="机床类型标识（仅供追溯，不直接影响推荐算法）。",
    )
    tool_diameter_mm: float = Field(
        default=10.0,
        description="刀具直径 (mm)，影响主轴转速与径向切深计算。",
    )
    num_flutes: int = Field(
        default=4,
        description="齿数，影响进给速度计算 feed_rate = spindle_rpm * num_flutes * feed_per_tooth。",
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    source_parametric_geometry_task_id: str
    step_file_path: str
    input_features_path: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    machine_type: str
    tool_diameter_mm: float
    num_flutes: int
    cutting_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    """任务状态响应（含审核进度）。"""

    task_id: str
    status: str
    source_parametric_geometry_task_id: str
    step_file_path: str
    input_features_path: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    machine_type: str
    tool_diameter_mm: float
    num_flutes: int
    feature_count: int
    recommended_count: int
    pending_count: int
    confirmed_count: int
    rejected_count: int
    edited_count: int
    cam_validation_required: bool
    chatter_params_path: str
    error_message: str
    created_at: float
    started_at: float
    completed_at: float
    cutting_disclaimer: dict[str, Any]


class RecommendedParamsResponse(BaseModel):
    """单条推荐切削参数的响应。"""

    feature_id: str
    feature_type: str
    operation: str
    spindle_speed_rpm: float
    feed_rate_mm_per_min: float
    feed_per_tooth_mm: float
    cutting_speed_m_per_min: float
    axial_depth_mm: float
    radial_depth_mm: float
    estimated_cutting_time_s: float
    tool_life_estimate_min: float
    warnings: list[str]
    review_status: str
    edited_params: dict[str, Any]
    effective_params: dict[str, Any]
    reviewed_by: str
    reviewed_at: float
    engineer_notes: str
    material_id: str
    tool_diameter_mm: float
    num_flutes: int


class TaskResultResponse(BaseModel):
    """切削参数任务结果摘要（含全部推荐参数列表）。"""

    task_id: str
    status: str
    source_parametric_geometry_task_id: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    feature_count: int
    recommended_count: int
    cam_validation_required: bool
    chatter_params_path: str
    error_message: str | None
    recommended_params: list[RecommendedParamsResponse]
    cutting_disclaimer: dict[str, Any]


class ReviewRequest(BaseModel):
    """工程师审核请求体。"""

    action: str = Field(
        ...,
        description=(
            "审核动作：confirmed（确认推荐参数无误）/ "
            "rejected（拒绝该特征，不进入最终 ChatterParams）/ "
            "edited（参数需修正，需同时提供 edited_params）"
        ),
    )
    edited_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "工程师编辑后的参数。仅 action=edited 时必须提供。"
            "字段可为 spindle_speed_rpm / feed_rate_mm_per_min / feed_per_tooth_mm "
            "/ cutting_speed_m_per_min / axial_depth_mm / radial_depth_mm 的子集。"
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
    cutting_disclaimer: dict[str, Any]


class ExportChatterParamsResponse(BaseModel):
    """导出 ChatterParams 响应（阶段 5 输入）。"""

    task_id: str
    status: str
    source_parametric_geometry_task_id: str
    material_id: str
    feature_count: int
    chatter_params_path: str
    download_url: str
    chatter_params_ready: bool
    cutting_disclaimer: dict[str, Any]
