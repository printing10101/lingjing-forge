"""几何特征提取模块 API 请求 / 响应模型（从 routes.py 拆分）。

将原 ``routes.py`` 中内联定义的 Pydantic BaseModel 类集中到本模块，
便于：

1. routes.py 仅保留路由处理逻辑，单文件行数从 1133 行降至 ~870 行；
2. 前端 / 测试代码可直接 ``from app.api.v1.feature_extraction.schemas import ...``
   导入模型进行类型校验，无需触发 routes.py 模块导入副作用（如
   pipeline 单例初始化、mesh 解析依赖加载等）；
3. 后续若需 OpenAPI schema 离线生成，可仅依赖本模块。

设计约束（项目记忆）：
- mesh → 参数化 CAD 自动转换工业上未解决，本模块输出「算法建议特征」；
- 工程师必须审核每个特征（confirmed / rejected / edited）后才允许进入阶段 3；
- 配合面公差 0.01mm（手机摄影测量物理上不可达），系统定位「工程师助手」。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskCreateFromPathRequest(BaseModel):
    """通过 mesh 路径 + 关联重建任务 ID 创建特征提取任务（链路模式）。"""

    mesh_path: str = Field(
        ...,
        description=(
            "输入 mesh 文件路径（PLY/STL/GLB/OBJ）。通常为 image_to_3d 任务的输出 mesh，或用户外部上传的 mesh。"
        ),
    )
    source_reconstruction_task_id: str = Field(
        default="",
        description=(
            "关联的拍照重建任务 ID（可选）。"
            "若提供且上游任务已 SUCCEEDED，则系统自动查询 mesh 是否已做尺度归一化。"
            "若不提供或上游任务不存在，则视为外部上传，按未标定 mesh 处理。"
        ),
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    input_mesh_path: str
    source_reconstruction_task_id: str
    mesh_calibrated: bool
    feature_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    input_mesh_path: str
    source_reconstruction_task_id: str
    vertex_count: int
    face_count: int
    feature_count: int
    plane_count: int
    cylinder_count: int
    hole_count: int
    boss_count: int
    plane_duration_seconds: float
    cylinder_duration_seconds: float
    hole_duration_seconds: float
    total_duration_seconds: float
    error_message: str
    reviewed_by: str
    reviewed_at: float
    exported_features_path: str
    mesh_calibrated: bool
    feature_disclaimer: dict[str, Any]


class FeatureItemResponse(BaseModel):
    """单条特征的简化响应。"""

    feature_id: str
    feature_type: str
    params: dict[str, Any]
    confidence: float
    review_status: str
    engineer_notes: str
    edited: bool
    edited_params: dict[str, Any]


class TaskResultResponse(BaseModel):
    """特征提取结果（含完整特征列表）。"""

    task_id: str
    status: str
    features: list[FeatureItemResponse]
    feature_count: int
    plane_count: int
    cylinder_count: int
    hole_count: int
    boss_count: int
    mesh_calibrated: bool
    feature_disclaimer: dict[str, Any]


class ReviewRequest(BaseModel):
    """工程师审核请求体。"""

    action: str = Field(
        ...,
        description=(
            "审核动作：confirmed（确认无误）/ rejected（误识别，丢弃）/ edited（参数需修正，需同时提供 edited_params）"
        ),
    )
    edited_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "工程师编辑后的参数。仅 action=edited 时必须提供，"
            "字段结构需与原始 params 一致（如 plane: normal/offset/area_mm2）。"
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
    feature_disclaimer: dict[str, Any]


class ExportResponse(BaseModel):
    """导出已确认特征响应。"""

    task_id: str
    status: str
    exported_features_path: str
    confirmed_feature_count: int
    download_url: str
    feature_disclaimer: dict[str, Any]
