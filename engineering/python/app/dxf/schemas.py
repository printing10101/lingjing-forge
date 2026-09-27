"""DXF API Pydantic 模型定义。

从 ``app.dxf.api`` 外移的请求/响应模型，保持路由文件只放路由的工程规范。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DxfParseResponse(BaseModel):
    file_name: str = ""
    file_size: int = 0
    dxf_version: str = ""
    parse_time_ms: float = 0.0
    entity_counts: dict[str, int] = Field(default_factory=dict)
    total_entities: int = 0
    lines_count: int = 0
    circles_count: int = 0
    arcs_count: int = 0
    texts_count: int = 0
    dimensions_count: int = 0
    extents: dict[str, float] = Field(default_factory=dict)
    lines: list[dict] = Field(default_factory=list)
    circles: list[dict] = Field(default_factory=list)
    dimensions: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DxfFeatureResponse(BaseModel):
    hole_count: int = 0
    plane_count: int = 0
    overall_length: float = 0.0
    overall_width: float = 0.0
    overall_height: float = 10.0
    height_inferred: bool = True
    holes: list[dict] = Field(default_factory=list)
    planes: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DxfPipelineResponse(BaseModel):
    success: bool = False
    parse_result: dict = Field(default_factory=dict)
    feature_result: dict = Field(default_factory=dict)
    model_result: dict = Field(default_factory=dict)
    process_result: dict = Field(default_factory=dict)
    total_duration_ms: float = 0.0
    summary: str = ""
    stages: list[dict] = Field(default_factory=list)


__all__ = [
    "DxfParseResponse",
    "DxfFeatureResponse",
    "DxfPipelineResponse",
]
