"""LLM 抽取专用 Prompt 模板（M1.4）。

定义从工艺文档中抽取实体和关系的系统提示词与用户提示词模板。
设计原则：
    - 明确指定 4 类实体类型和 4 类关系类型（与 M1.1 本体一致）
    - 包含示例说明，提高 LLM 理解准确性
    - 定义严格的 JSON 输出格式，便于解析
    - 要求 LLM 为每个抽取结果生成可信度评分（0-100）
"""

from __future__ import annotations

# 系统提示词

EXTRACTION_SYSTEM_PROMPT = """\
你是一个专业的机械加工工艺文档知识抽取专家。你的任务是从给定的工艺文档文本中，\
精准提取结构化的实体和关系数据，用于构建制造工艺知识图谱。

## 实体类型（4 类）

### 1. Material（材料）
工件材料的物理与加工属性。
- id 格式: material-<标识>
- 属性: name(名称), category(类别), density_gcm3(密度), hardness_hb(硬度), \
tensile_strength_mpa(抗拉强度), cutting_performance(切削性能), description(描述)

### 2. Tool（刀具）
加工所使用的刀具。
- id 格式: tool-<标识>
- 属性: name(名称), series(系列), diameter_mm(直径), material(材料), \
application(应用), description(描述)

### 3. Feature（特征）
工件上需要加工的几何特征。
- id 格式: feature-<标识>
- 属性: name(名称), feature_type(类型: hole/pocket/face/contour/slot/thread), \
tolerance_mm(公差), surface_roughness_ra(粗糙度), description(描述)

### 4. Process（工艺）
加工工艺步骤或规则。
- id 格式: process-<标识>
- 属性: name(名称), category(类别: sequence/parameter/fixture), \
description(描述), details(细节参数)

## 关系类型（4 类）

1. SUITABLE_FOR: (Tool) -> (Material)  刀具适用于加工某材料
2. SUITABLE_FOR: (Tool) -> (Feature)  刀具适用于加工某特征
3. APPLIED_TO:   (Process) -> (Feature) 工艺用于加工某特征
4. USED:         (Process) -> (Tool)    工艺使用某刀具

## 输出格式（严格 JSON）

```json
{
  "entities": [
    {
      "entity_type": "Material",
      "id": "material-45steel",
      "name": "45#钢",
      "properties": {"category": "carbon_steel", "hardness_hb": 220},
      "confidence": 90
    }
  ],
  "relations": [
    {
      "relation_type": "SUITABLE_FOR",
      "source_id": "tool-twist-drill-10",
      "target_id": "material-45steel",
      "properties": {"evidence": "文档第2段明确提到"},
      "confidence": 85
    }
  ]
}
```

## 抽取规则

1. 仔细阅读文档，识别所有提到的材料、刀具、特征和工艺
2. 材料属性：从文档中提取明确的物理参数（硬度、密度、强度等）
3. 刀具属性：关注刀具类型、直径、材料、适用场景
4. 特征属性：关注几何类型、公差要求、表面粗糙度
5. 工艺属性：关注加工顺序、参数选择、装夹方式
6. 关系抽取：基于文档上下文判断实体间的适用/使用关系
7. 每个实体和关系必须给出可信度评分（0-100）

## 可信度评分标准

- 90-100: 文档中明确描述的直接信息
- 70-89: 基于文档上下文的强推断
- 50-69: 基于行业知识的合理推断
- 30-49: 较弱的推断，需要人工验证
- 0-29: 非常不确定，仅供参考

## 重要约束

- 仅输出 JSON，不要包含任何其他文字或解释
- 如果文档中没有可抽取的内容，返回空列表
- ID 使用英文小写+连字符格式
- 数值属性使用数字类型，不要加单位后缀
- 如果某个属性值无法从文档中提取，则不要包含该属性"""

# 用户提示词模板

EXTRACTION_USER_PROMPT_TEMPLATE = """\
请从以下工艺文档文本中抽取实体和关系。

## 文档内容（第 {start_page}-{end_page} 页，共 {total_pages} 页）

{document_text}

请按照指定的 JSON 格式输出抽取结果。注意：
1. 仅输出 JSON 数据，不要包含其他文字
2. 为每个实体和关系给出可信度评分（0-100）
3. 确保 ID 格式正确（如 material-xxx, tool-xxx, feature-xxx, process-xxx）
4. 关系中的 source_id 和 target_id 必须引用已抽取的实体 ID"""

# 实体类型到 Pydantic 模型的映射

ENTITY_TYPE_MAP = {
    "Material": {
        "id_prefix": "material",
        "required_fields": ["name"],
        "optional_fields": [
            "category",
            "density_gcm3",
            "hardness_hb",
            "tensile_strength_mpa",
            "cutting_performance",
            "description",
        ],
    },
    "Tool": {
        "id_prefix": "tool",
        "required_fields": ["name"],
        "optional_fields": [
            "series",
            "diameter_mm",
            "material",
            "application",
            "description",
        ],
    },
    "Feature": {
        "id_prefix": "feature",
        "required_fields": ["name"],
        "optional_fields": [
            "feature_type",
            "tolerance_mm",
            "surface_roughness_ra",
            "description",
        ],
    },
    "Process": {
        "id_prefix": "process",
        "required_fields": ["name"],
        "optional_fields": ["category", "description", "details"],
    },
}

# 关系类型定义
RELATION_TYPE_MAP = {
    "SUITABLE_FOR_Tool_Material": {
        "source_type": "Tool",
        "target_type": "Material",
        "edge_type": "SUITABLE_FOR",
    },
    "SUITABLE_FOR_Tool_Feature": {
        "source_type": "Tool",
        "target_type": "Feature",
        "edge_type": "SUITABLE_FOR",
    },
    "APPLIED_TO": {
        "source_type": "Process",
        "target_type": "Feature",
        "edge_type": "APPLIED_TO",
    },
    "USED": {
        "source_type": "Process",
        "target_type": "Tool",
        "edge_type": "USED",
    },
}


def build_user_prompt(
    document_text: str,
    start_page: int,
    end_page: int,
    total_pages: int,
) -> str:
    """构建用户提示词。

    Args:
        document_text: 当前批次的文档文本。
        start_page: 起始页码。
        end_page: 结束页码。
        total_pages: 文档总页数。

    Returns:
        格式化的用户提示词字符串。
    """
    return EXTRACTION_USER_PROMPT_TEMPLATE.format(
        document_text=document_text,
        start_page=start_page,
        end_page=end_page,
        total_pages=total_pages,
    )
