"""知识图谱极简本体 Pydantic 模型（M1.1）

设计目标
--------
为后续知识图谱构建提供结构化、可校验的极简本体 schema。
本模块仅实现任务 M1.1 指定的 **4 类核心实体** 与 **4 类关系**，
严格遵循"极简"原则，不做扩展。

实体（4 类）
    - Material  刀具所加工的工件材料
    - Tool      加工所使用的刀具
    - Feature   工件上需要加工的几何特征（孔、型腔、平面、轮廓等）
    - Process   加工工艺步骤/规则

关系（4 类）
    - (Tool)      -[SUITABLE_FOR]-> (Material)
    - (Tool)      -[SUITABLE_FOR]-> (Feature)
    - (Process)   -[APPLIED_TO]  -> (Feature)
    - (Process)   -[USED]        -> (Tool)

所有关系必须携带以下两个公共属性：
    - confidence : float   可信度，取值 [0, 1]
    - source     : str     关系来源，枚举：rule / llm / 实测 / manual

参考：
    - docs/OPTIMIZATION_BLUEPRINT.md 第 3.2.1 节
    - python/app/data/materials.json
    - python/app/data/tools.json
    - python/app/data/process_rules.json
    - python/app/database/data/machines.json
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# 关系来源枚举


class RelationSource(str, Enum):
    """关系来源枚举。"""

    RULE = "rule"  # 由规则推导（如 process_rules.json）
    LLM = "llm"  # 由大模型抽取
    MEASURED = "实测"  # 由车间实测数据统计得到
    MANUAL = "manual"  # 由人工录入


# 实体模型（4 类）


class _EntityBase(BaseModel):
    """实体模型公共基类：禁用未知字段，校验赋值。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Material(_EntityBase):
    """Material（材料）实体。

    描述工件材料的物理与加工属性。
    """

    id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="材料唯一标识，对应 materials.json 中的 id",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="材料名称（如 45#钢 / 铝合金6061）",
    )
    category: str = Field(
        default="",
        max_length=64,
        description="材料类别（如 carbon_steel / aluminum / stainless_steel / alloy_steel）",
    )
    density_gcm3: float | None = Field(
        default=None,
        ge=0.0,
        description="密度，单位 g/cm³",
    )
    hardness_hb: float | None = Field(
        default=None,
        ge=0.0,
        description="布氏硬度 HB",
    )
    tensile_strength_mpa: float | None = Field(
        default=None,
        ge=0.0,
        description="抗拉强度，单位 MPa",
    )
    cutting_performance: str = Field(
        default="",
        max_length=64,
        description="切削加工性能评价（excellent / good / fair / poor）",
    )
    description: str = Field(
        default="",
        max_length=512,
        description="材料描述",
    )


class Tool(_EntityBase):
    """Tool（工具）实体。

    描述加工用刀具的几何与用途属性。
    """

    id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="刀具唯一标识，对应 tools.json 中的 id",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="刀具名称（如 麻花钻 φ3mm / 立铣刀 φ6mm）",
    )
    series: str = Field(
        default="",
        max_length=64,
        description="刀具系列（如 twist_drill / endmill / face_mill / center_drill）",
    )
    diameter_mm: float | None = Field(
        default=None,
        ge=0.0,
        description="刀具直径，单位 mm",
    )
    material: str = Field(
        default="",
        max_length=64,
        description="刀具材料（如 HSS / carbide / 涂层硬质合金）",
    )
    application: str = Field(
        default="",
        max_length=128,
        description="典型应用场景（如 钻孔 / 型腔加工 / 平面加工）",
    )
    description: str = Field(
        default="",
        max_length=512,
        description="刀具描述",
    )


class Feature(_EntityBase):
    """Feature（特征）实体。

    描述工件上需要加工的几何特征，是 Process 与 Tool 共同作用的目标。
    """

    id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="特征唯一标识",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="特征名称（如 孔 / 型腔 / 平面 / 轮廓 / 槽 / 螺纹）",
    )
    feature_type: str = Field(
        default="",
        max_length=64,
        description="特征类型（如 hole / pocket / face / contour / slot / thread）",
    )
    tolerance_mm: float | None = Field(
        default=None,
        ge=0.0,
        description="尺寸公差，单位 mm",
    )
    surface_roughness_ra: float | None = Field(
        default=None,
        ge=0.0,
        description="表面粗糙度 Ra，单位 μm",
    )
    description: str = Field(
        default="",
        max_length=512,
        description="特征描述",
    )


class Process(_EntityBase):
    """Process（工艺）实体。

    描述加工工艺步骤/规则，对应 process_rules.json。
    """

    id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="工艺唯一标识，对应 process_rules.json 中的 id",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="工艺名称（如 先粗后精 / 先面后孔）",
    )
    category: str = Field(
        default="",
        max_length=64,
        description="工艺类别（如 sequence / parameter / fixture）",
    )
    description: str = Field(
        default="",
        max_length=512,
        description="工艺描述",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="工艺细节参数（如余量、依据等结构化字段）",
    )


# 关系模型（4 类）


class _RelationBase(BaseModel):
    """关系模型公共基类：所有关系必须包含可信度与来源。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="关系可信度，取值 [0, 1]，默认 0.5",
    )
    source: RelationSource = Field(
        default=RelationSource.RULE,
        description="关系来源：rule / llm / 实测 / manual",
    )
    evidence: str = Field(
        default="",
        max_length=512,
        description="关系证据描述（出处、统计样本、人工备注等）",
    )


class ToolSuitableForMaterial(_RelationBase):
    """(Tool) -[SUITABLE_FOR]-> (Material)

    表达"某刀具适用于加工某种材料"的关系。
    """

    tool_id: str = Field(..., min_length=1, max_length=128, description="起始端 Tool 实体 id")
    material_id: str = Field(..., min_length=1, max_length=128, description="目标端 Material 实体 id")


class ToolSuitableForFeature(_RelationBase):
    """(Tool) -[SUITABLE_FOR]-> (Feature)

    表达"某刀具适用于加工某种几何特征"的关系。
    """

    tool_id: str = Field(..., min_length=1, max_length=128, description="起始端 Tool 实体 id")
    feature_id: str = Field(..., min_length=1, max_length=128, description="目标端 Feature 实体 id")


class ProcessAppliedToFeature(_RelationBase):
    """(Process) -[APPLIED_TO]-> (Feature)

    表达"某工艺用于加工某种几何特征"的关系。
    """

    process_id: str = Field(..., min_length=1, max_length=128, description="起始端 Process 实体 id")
    feature_id: str = Field(..., min_length=1, max_length=128, description="目标端 Feature 实体 id")


class ProcessUsesTool(_RelationBase):
    """(Process) -[USED]-> (Tool)

    表达"某工艺使用某刀具"的关系。
    """

    process_id: str = Field(..., min_length=1, max_length=128, description="起始端 Process 实体 id")
    tool_id: str = Field(..., min_length=1, max_length=128, description="目标端 Tool 实体 id")


__all__ = [
    # 实体
    "Material",
    "Tool",
    "Feature",
    "Process",
    # 关系
    "ToolSuitableForMaterial",
    "ToolSuitableForFeature",
    "ProcessAppliedToFeature",
    "ProcessUsesTool",
    # 枚举
    "RelationSource",
]
