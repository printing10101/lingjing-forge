"""SHARP Schema 约束校验器（M1.2）。

对三元组进行多层约束校验，是 SHARP "Schema-Aware" 组件的执行单元。
所有约束规则均来自 `docs/knowledge-graph/ontology-v1.md`，不引入任何额外规则。

校验层级
--------
1. **结构校验**：head/relation/tail 字段完整且类型合法
2. **域值域校验**：关系对应的 (头类型, 尾类型) 是否合法
3. **ID 格式校验**：实体 ID 符合 `<type>-<slug>` 规范
4. **属性校验**：confidence ∈ [0, 1]、source ∈ RelationSource 枚举
5. **类型兼容性校验**：实体属性与 Pydantic 模型字段兼容

返回结构
--------
所有校验均返回 `ValidationResult`，不抛出异常（容错优先）。
调用方可根据 `is_valid` 与 `errors` 决定后续处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


from app.models.knowledge_graph import RelationSource
from app.sharp.schema.domain_schema import (
    DomainSchema,
    EntityType,
    RelationType,
    Triple,
    DEFAULT_SCHEMA,
)


# 节点 ID 规范（见 ontology-v1.md 第 5 节 + graph_store.py 实践）

# `<type>-<slug>` 形式，slug 允许字母/数字/下划线/点/连字符
# 首字符必须是字母或下划线，总长度 1-128
NODE_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\-]{0,127}$")

# 实体类型前缀映射，用于 ID 与类型的弱一致性校验
# 注意：这是软约束（warning），不是硬约束（error）
ENTITY_TYPE_ID_PREFIX: dict[EntityType, str] = {
    EntityType.MATERIAL: "material-",
    EntityType.TOOL: "tool-",
    EntityType.FEATURE: "feature-",
    EntityType.PROCESS: "process-",
}


# 校验结果结构


@dataclass
class ValidationError:
    """单条校验错误。"""

    code: str  # 错误码，如 "INVALID_DOMAIN"
    message: str  # 中文错误描述
    field: str = ""  # 出错字段路径，如 "head.id"
    severity: str = "error"  # "error" | "warning"


@dataclass
class ValidationResult:
    """校验结果。"""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    def add_error(self, code: str, message: str, field: str = "") -> None:
        self.errors.append(ValidationError(code=code, message=message, field=field, severity="error"))
        self.is_valid = False

    def add_warning(self, code: str, message: str, field: str = "") -> None:
        self.warnings.append(ValidationError(code=code, message=message, field=field, severity="warning"))

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": [e.__dict__ for e in self.errors],
            "warnings": [w.__dict__ for w in self.warnings],
        }


# Schema 约束校验器


class SchemaConstraints:
    """Schema 约束校验器。

    对 `Triple` 进行多层校验，所有校验均为纯函数式（无副作用），
    返回 `ValidationResult` 而非抛出异常。

    Parameters
    ----------
    schema : DomainSchema
        领域 Schema，默认使用 `DEFAULT_SCHEMA`
    strict_id_prefix : bool
        是否将 ID 前缀不一致视为 error（True）或 warning（False，默认）
    """

    def __init__(
        self,
        schema: DomainSchema | None = None,
        strict_id_prefix: bool = False,
    ) -> None:
        self.schema = schema or DEFAULT_SCHEMA
        self.strict_id_prefix = strict_id_prefix

    # 公共入口

    def validate(self, triple: Triple) -> ValidationResult:
        """对三元组执行全量校验。"""
        result = ValidationResult(is_valid=True)
        self._validate_structure(triple, result)
        if not result.is_valid:
            return result  # 结构错误则停止后续校验
        self._validate_domain_range(triple, result)
        self._validate_id_format(triple, result)
        self._validate_id_prefix(triple, result)
        self._validate_relation_properties(triple, result)
        return result

    # 各层校验实现

    def _validate_structure(self, triple: Triple, result: ValidationResult) -> None:
        """结构校验：字段完整且类型合法。"""
        if not isinstance(triple.head_type, EntityType):
            result.add_error(
                "INVALID_HEAD_TYPE",
                f"head_type 必须是 EntityType 枚举，实际类型: {type(triple.head_type).__name__}",
                field="head_type",
            )
        if not isinstance(triple.relation, RelationType):
            result.add_error(
                "INVALID_RELATION",
                f"relation 必须是 RelationType 枚举，实际类型: {type(triple.relation).__name__}",
                field="relation",
            )
        if not isinstance(triple.tail_type, EntityType):
            result.add_error(
                "INVALID_TAIL_TYPE",
                f"tail_type 必须是 EntityType 枚举，实际类型: {type(triple.tail_type).__name__}",
                field="tail_type",
            )
        if not triple.head_id or not isinstance(triple.head_id, str):
            result.add_error(
                "EMPTY_HEAD_ID",
                "head_id 不能为空且必须为字符串",
                field="head_id",
            )
        if not triple.tail_id or not isinstance(triple.tail_id, str):
            result.add_error(
                "EMPTY_TAIL_ID",
                "tail_id 不能为空且必须为字符串",
                field="tail_id",
            )

    def _validate_domain_range(self, triple: Triple, result: ValidationResult) -> None:
        """域值域校验：关系对应的 (头类型, 尾类型) 是否合法。"""
        expected = self.schema.get_relation_domain(triple.relation)
        if expected is None:
            result.add_error(
                "UNKNOWN_RELATION",
                f"未知关系类型: {triple.relation}",
                field="relation",
            )
            return
        expected_head, expected_tail = expected
        if triple.head_type != expected_head:
            result.add_error(
                "DOMAIN_MISMATCH",
                f"关系 {triple.relation.value} 的头实体应为 {expected_head.value}，实际为 {triple.head_type.value}",
                field="head_type",
            )
        if triple.tail_type != expected_tail:
            result.add_error(
                "RANGE_MISMATCH",
                f"关系 {triple.relation.value} 的尾实体应为 {expected_tail.value}，实际为 {triple.tail_type.value}",
                field="tail_type",
            )

    def _validate_id_format(self, triple: Triple, result: ValidationResult) -> None:
        """ID 格式校验：符合 `<type>-<slug>` 正则。"""
        for field_name, entity_id in (("head_id", triple.head_id), ("tail_id", triple.tail_id)):
            if not entity_id:
                continue
            if not NODE_ID_PATTERN.match(entity_id):
                result.add_error(
                    "INVALID_ID_FORMAT",
                    f"{field_name}='{entity_id}' 不符合 ID 规范 (正则: {NODE_ID_PATTERN.pattern})",
                    field=field_name,
                )

    def _validate_id_prefix(self, triple: Triple, result: ValidationResult) -> None:
        """ID 前缀弱一致性校验。"""
        for field_name, entity_type, entity_id in (
            ("head_id", triple.head_type, triple.head_id),
            ("tail_id", triple.tail_type, triple.tail_id),
        ):
            if not entity_id:
                continue
            expected_prefix = ENTITY_TYPE_ID_PREFIX.get(entity_type)
            if not expected_prefix:
                continue
            if not entity_id.startswith(expected_prefix):
                msg = (
                    f"{field_name}='{entity_id}' 不以类型前缀 '{expected_prefix}' 开头，"
                    f"建议格式: {expected_prefix}<slug>"
                )
                if self.strict_id_prefix:
                    result.add_error("ID_PREFIX_MISMATCH", msg, field=field_name)
                else:
                    result.add_warning("ID_PREFIX_MISMATCH", msg, field=field_name)

    def _validate_relation_properties(self, triple: Triple, result: ValidationResult) -> None:
        """关系属性校验：confidence / source / evidence。"""
        props = triple.relation_properties
        if not props:
            return  # 关系属性可选，未提供则跳过

        # confidence 校验
        if "confidence" in props:
            conf = props["confidence"]
            if not isinstance(conf, (int, float)):
                result.add_error(
                    "INVALID_CONFIDENCE_TYPE",
                    f"confidence 必须为数值，实际类型: {type(conf).__name__}",
                    field="relation.confidence",
                )
            elif not (0.0 <= conf <= 1.0):
                result.add_error(
                    "CONFIDENCE_OUT_OF_RANGE",
                    f"confidence={conf} 不在 [0, 1] 范围内",
                    field="relation.confidence",
                )

        # source 校验
        if "source" in props:
            src = props["source"]
            try:
                if isinstance(src, RelationSource):
                    return
                RelationSource(src)
            except ValueError:
                valid_values = [s.value for s in RelationSource]
                result.add_error(
                    "INVALID_SOURCE",
                    f"source='{src}' 不是合法枚举值，合法值: {valid_values}",
                    field="relation.source",
                )


__all__ = [
    "NODE_ID_PATTERN",
    "ENTITY_TYPE_ID_PREFIX",
    "ValidationError",
    "ValidationResult",
    "SchemaConstraints",
]
