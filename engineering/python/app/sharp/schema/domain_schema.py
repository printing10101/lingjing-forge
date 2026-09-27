"""SHARP 领域 Schema 定义（M1.1）。

基于 `docs/knowledge-graph/ontology-v1.md` 的 4 实体 + 4 关系本体，
为 SHARP 三元组验证提供类型化的 Schema 抽象。

本模块不重复定义 Pydantic 实体/关系模型，而是直接复用
`app.models.knowledge_graph` 中的现有模型，仅补充 SHARP 所需的：
- 实体类型枚举（EntityType）
- 关系类型枚举（RelationType）
- 三元组结构（Triple）
- 领域 Schema（DomainSchema）：实体/关系集合 + 关系→(头类型, 尾类型)映射

设计原则
--------
- **零冗余**：不复用 ontology-v1.md 已有的 Pydantic 模型定义，仅做类型化封装。
- **类型安全**：所有枚举均继承 `str`，便于 JSON 序列化与 LLM prompt 引用。
- **容错**：Triple 的 head/tail 字段允许仅给出 id（验证服务场景），不必携带完整属性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.models.knowledge_graph import (
    Material,
    Tool,
    Feature,
    Process,
    ToolSuitableForMaterial,
    ToolSuitableForFeature,
    ProcessAppliedToFeature,
    ProcessUsesTool,
)


# 实体类型枚举


class EntityType(str, Enum):
    """SHARP 验证支持的实体类型，对齐 ontology-v1.md 的 4 类核心实体。"""

    MATERIAL = "Material"
    TOOL = "Tool"
    FEATURE = "Feature"
    PROCESS = "Process"


# 实体类型 Pydantic 模型映射，用于属性校验与实例化
ENTITY_TYPE_TO_MODEL: dict[EntityType, type] = {
    EntityType.MATERIAL: Material,
    EntityType.TOOL: Tool,
    EntityType.FEATURE: Feature,
    EntityType.PROCESS: Process,
}


# 关系类型枚举


class RelationType(str, Enum):
    """SHARP 验证支持的关系类型，对齐 ontology-v1.md 的 4 类关系。

    命名规则：`<HEAD>_<RELATION>_<TAIL>` 的简化形式，便于在 prompt 中引用。
    """

    SUITABLE_FOR_MATERIAL = "SUITABLE_FOR_MATERIAL"  # (Tool) -[SUITABLE_FOR]-> (Material)
    SUITABLE_FOR_FEATURE = "SUITABLE_FOR_FEATURE"  # (Tool) -[SUITABLE_FOR]-> (Feature)
    APPLIED_TO = "APPLIED_TO"  # (Process) -[APPLIED_TO]-> (Feature)
    USED = "USED"  # (Process) -[USED]-> (Tool)


# 关系类型 Pydantic 关系模型映射
RELATION_TYPE_TO_MODEL: dict[RelationType, type] = {
    RelationType.SUITABLE_FOR_MATERIAL: ToolSuitableForMaterial,
    RelationType.SUITABLE_FOR_FEATURE: ToolSuitableForFeature,
    RelationType.APPLIED_TO: ProcessAppliedToFeature,
    RelationType.USED: ProcessUsesTool,
}


# 三元组结构


@dataclass
class Triple:
    """待验证的知识三元组。

    SHARP 的核心输入。head/tail 仅需给出 `entity_type` 与 `entity_id`，
    不要求携带完整属性——验证服务会在需要时通过 KG 工具拉取属性。

    Attributes
    ----------
    head_type : EntityType
        头实体类型
    head_id : str
        头实体 ID（符合 `<type>-<slug>` 规范，见 ontology-v1.md）
    relation : RelationType
        关系类型
    tail_type : EntityType
        尾实体类型
    tail_id : str
        尾实体 ID
    head_properties : Optional[dict]
        头实体已知属性（可选，由调用方填充，用于减少工具调用）
    tail_properties : Optional[dict]
        尾实体已知属性（可选）
    relation_properties : Optional[dict]
        关系已知属性（confidence / source / evidence，可选）
    """

    head_type: EntityType
    head_id: str
    relation: RelationType
    tail_type: EntityType
    tail_id: str
    head_properties: dict | None = None
    tail_properties: dict | None = None
    relation_properties: dict | None = None

    def as_dict(self) -> dict:
        """序列化为可 JSON 化的字典。"""
        return {
            "head": {
                "type": self.head_type.value,
                "id": self.head_id,
                "properties": self.head_properties or {},
            },
            "relation": {
                "type": self.relation.value,
                "properties": self.relation_properties or {},
            },
            "tail": {
                "type": self.tail_type.value,
                "id": self.tail_id,
                "properties": self.tail_properties or {},
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Triple":
        """从字典反序列化，容错处理字符串形式的枚举。"""
        head = data.get("head", {})
        tail = data.get("tail", {})
        rel = data.get("relation", {})
        return cls(
            head_type=EntityType(head["type"]),
            head_id=str(head["id"]),
            relation=RelationType(rel["type"]),
            tail_type=EntityType(tail["type"]),
            tail_id=str(tail["id"]),
            head_properties=head.get("properties"),
            tail_properties=tail.get("properties"),
            relation_properties=rel.get("properties"),
        )

    def short_repr(self) -> str:
        """三元组的简短文本表示，用于 LLM prompt。"""
        return (
            f"({self.head_type.value}:{self.head_id})-[{self.relation.value}]->({self.tail_type.value}:{self.tail_id})"
        )


# 领域 Schema


@dataclass
class DomainSchema:
    """领域 Schema：封装实体类型集合、关系类型集合、关系域/值域映射。

    这是 SHARP "Schema-Aware" 组件的基础数据结构，被
    `SchemaConstraints` 和 `StrategicPlanner` 共同依赖。
    """

    # 实体类型集合
    entity_types: frozenset[EntityType] = field(default_factory=lambda: frozenset(ENTITY_TYPE_TO_MODEL.keys()))
    # 关系类型集合
    relation_types: frozenset[RelationType] = field(default_factory=lambda: frozenset(RELATION_TYPE_TO_MODEL.keys()))
    # 关系 (头实体类型, 尾实体类型) 的合法映射
    relation_domains: dict[RelationType, tuple[EntityType, EntityType]] = field(
        default_factory=lambda: {
            RelationType.SUITABLE_FOR_MATERIAL: (EntityType.TOOL, EntityType.MATERIAL),
            RelationType.SUITABLE_FOR_FEATURE: (EntityType.TOOL, EntityType.FEATURE),
            RelationType.APPLIED_TO: (EntityType.PROCESS, EntityType.FEATURE),
            RelationType.USED: (EntityType.PROCESS, EntityType.TOOL),
        }
    )

    def is_valid_relation(
        self,
        head_type: EntityType,
        relation: RelationType,
        tail_type: EntityType,
    ) -> bool:
        """校验三元组的类型组合是否符合本体约束。"""
        expected = self.relation_domains.get(relation)
        if expected is None:
            return False
        return (head_type, tail_type) == expected

    def get_entity_model(self, entity_type: EntityType) -> type | None:
        """获取实体类型对应的 Pydantic 模型类。"""
        return ENTITY_TYPE_TO_MODEL.get(entity_type)

    def get_relation_model(self, relation: RelationType) -> type | None:
        """获取关系类型对应的 Pydantic 关系模型类。"""
        return RELATION_TYPE_TO_MODEL.get(relation)

    def get_relation_domain(self, relation: RelationType) -> tuple[EntityType, EntityType] | None:
        """获取关系的合法 (头类型, 尾类型)。"""
        return self.relation_domains.get(relation)

    def to_prompt_text(self) -> str:
        """生成 Schema 的自然语言描述，用于 LLM prompt 注入。"""
        lines = [
            "## 领域 Schema（4 实体 + 4 关系）",
            "",
            "### 实体类型",
        ]
        for et in self.entity_types:
            lines.append(f"- {et.value}")
        lines.extend(["", "### 关系类型（头实体 → 尾实体）"])
        for rel, (h, t) in self.relation_domains.items():
            lines.append(f"- ({h.value}) -[{rel.value}]-> ({t.value})")
        return "\n".join(lines)


# 全局默认 Schema 实例（不可变，线程安全）
DEFAULT_SCHEMA: DomainSchema = DomainSchema()


__all__ = [
    "EntityType",
    "RelationType",
    "ENTITY_TYPE_TO_MODEL",
    "RELATION_TYPE_TO_MODEL",
    "Triple",
    "DomainSchema",
    "DEFAULT_SCHEMA",
]
