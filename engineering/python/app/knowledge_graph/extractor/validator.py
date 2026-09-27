"""抽取结果验证模块（M1.4）。

实现抽取结果的自动验证机制：
    - 结构验证：与 Pydantic 模型校验
    - 一致性检查：实体 ID 引用完整性
    - 关系合理性校验：类型匹配、自环检测
    - 生成验证报告

验证结果默认标记为 "unverified" 状态，需经人工审核后方可写入图谱。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.knowledge_graph import (
    Feature,
    Material,
    Process,
    ProcessAppliedToFeature,
    ProcessUsesTool,
    Tool,
    ToolSuitableForFeature,
    ToolSuitableForMaterial,
    _RelationBase,
)

logger = logging.getLogger(__name__)


# 数据类


@dataclass
class EntityValidationResult:
    """单个实体验证结果。"""

    entity: dict[str, Any]
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RelationValidationResult:
    """单个关系验证结果。"""

    relation: dict[str, Any]
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """完整验证报告。"""

    entity_results: list[EntityValidationResult] = field(default_factory=list)
    relation_results: list[RelationValidationResult] = field(default_factory=list)
    consistency_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overall_valid: bool = True
    accuracy_score: float = 0.0
    recommendation: str = ""

    def summary(self) -> dict[str, Any]:
        """生成验证摘要。"""
        total_entities = len(self.entity_results)
        valid_entities = sum(1 for r in self.entity_results if r.is_valid)
        total_relations = len(self.relation_results)
        valid_relations = sum(1 for r in self.relation_results if r.is_valid)

        entity_pass_rate = valid_entities / total_entities if total_entities > 0 else 0.0
        relation_pass_rate = valid_relations / total_relations if total_relations > 0 else 0.0
        overall_pass_rate = (
            (valid_entities + valid_relations) / (total_entities + total_relations)
            if (total_entities + total_relations) > 0
            else 0.0
        )

        return {
            "total_entities": total_entities,
            "valid_entities": valid_entities,
            "entity_pass_rate": round(entity_pass_rate, 4),
            "total_relations": total_relations,
            "valid_relations": valid_relations,
            "relation_pass_rate": round(relation_pass_rate, 4),
            "overall_pass_rate": round(overall_pass_rate, 4),
            "consistency_issues": len(self.consistency_issues),
            "warnings": len(self.warnings),
            "overall_valid": self.overall_valid,
            "accuracy_score": round(self.accuracy_score, 4),
            "recommendation": self.recommendation,
        }


# 验证器

# 实体类型到 Pydantic 模型的映射
_ENTITY_MODEL_MAP = {
    "Material": Material,
    "Tool": Tool,
    "Feature": Feature,
    "Process": Process,
}

# 关系类型到 Pydantic 模型的映射
# 显式注解 type[_RelationBase]：各关系子类共享公共基类，mypy 不再报 dict-item 不兼容
_RELATION_MODEL_MAP: dict[tuple[str, str, str], type[_RelationBase]] = {
    ("SUITABLE_FOR", "Tool", "Material"): ToolSuitableForMaterial,
    ("SUITABLE_FOR", "Tool", "Feature"): ToolSuitableForFeature,
    ("APPLIED_TO", "Process", "Feature"): ProcessAppliedToFeature,
    ("USED", "Process", "Tool"): ProcessUsesTool,
}

# 实体 ID 前缀规范
_ENTITY_ID_PREFIX = {
    "Material": "material-",
    "Tool": "tool-",
    "Feature": "feature-",
    "Process": "process-",
}


class ExtractionValidator:
    """抽取结果验证器。

    用法::

        validator = ExtractionValidator()
        report = validator.validate(extraction_result)
        if report.overall_valid:
            logger.info("验证通过")
        else:
            logger.info("准确率: %s%%", report.accuracy_score)
    """

    def validate(self, extraction_result: dict[str, Any]) -> ValidationReport:
        """验证完整的抽取结果。

        Args:
            extraction_result: 包含 entities 和 relations 的抽取结果字典。

        Returns:
            ValidationReport 验证报告。
        """
        report = ValidationReport()

        entities = extraction_result.get("entities", [])
        relations = extraction_result.get("relations", [])

        # 1. 结构验证 - 实体
        for entity in entities:
            entity_result = self._validate_entity(entity)
            report.entity_results.append(entity_result)

        # 2. 结构验证 - 关系
        for relation in relations:
            relation_result = self._validate_relation(relation, entities)
            report.relation_results.append(relation_result)

        # 3. 一致性检查
        self._check_consistency(entities, relations, report)

        # 4. 合理性检查
        self._check_reasonableness(entities, relations, report)

        # 5. 计算总体评分
        self._calculate_overall_score(report)

        return report

    def _validate_entity(self, entity: dict[str, Any]) -> EntityValidationResult:
        """验证单个实体的结构。"""
        result = EntityValidationResult(entity=entity, is_valid=True)

        # 检查必填字段
        entity_type = entity.get("entity_type")
        if not entity_type:
            result.is_valid = False
            result.errors.append("缺少 entity_type 字段")
            return result

        entity_id = entity.get("id")
        if not entity_id:
            result.is_valid = False
            result.errors.append("缺少 id 字段")
            return result

        name = entity.get("name")
        if not name:
            result.is_valid = False
            result.errors.append("缺少 name 字段")
            return result

        # 使用 Pydantic 模型验证
        model_class = _ENTITY_MODEL_MAP.get(entity_type)
        if model_class is None:
            result.warnings.append(f"未知实体类型: {entity_type}")
            return result

        try:
            model_data = {"id": entity_id, "name": name}
            properties = entity.get("properties", {})
            if isinstance(properties, dict):
                model_data.update(properties)

            model_class(**model_data)
        except (ValueError, TypeError, KeyError) as exc:
            result.is_valid = False
            result.errors.append(f"Pydantic 验证失败: {exc}")

        return result

    def _validate_relation(
        self,
        relation: dict[str, Any],
        entities: list[dict[str, Any]],
    ) -> RelationValidationResult:
        """验证单个关系的结构。"""
        result = RelationValidationResult(relation=relation, is_valid=True)

        # 检查必填字段
        source_id = relation.get("source_id")
        target_id = relation.get("target_id")
        relation_type = relation.get("relation_type")

        if not source_id:
            result.is_valid = False
            result.errors.append("缺少 source_id 字段")
            return result

        if not target_id:
            result.is_valid = False
            result.errors.append("缺少 target_id 字段")
            return result

        if not relation_type:
            result.is_valid = False
            result.errors.append("缺少 relation_type 字段")
            return result

        # 检查可信度
        confidence = relation.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)):
                result.is_valid = False
                result.errors.append(f"confidence 必须为数值，当前: {type(confidence).__name__}")
            elif not (0 <= float(confidence) <= 100):
                result.is_valid = False
                result.errors.append(f"confidence 必须在 [0, 100] 范围内，当前: {confidence}")

        # 使用 Pydantic 关系模型验证
        entity_type_map = {e["id"]: str(e.get("entity_type") or "") for e in entities}
        source_type = entity_type_map.get(source_id, "")
        target_type = entity_type_map.get(target_id, "")

        model_key = (relation_type, source_type, target_type)
        model_class = _RELATION_MODEL_MAP.get(model_key)

        if model_class is not None:
            try:
                rel_data = {
                    "source_id": source_id,
                    "target_id": target_id,
                }
                # 映射到 Pydantic 模型的字段名
                field_map = self._get_relation_field_map(model_key)
                if field_map:
                    rel_data[field_map["source_field"]] = source_id
                    rel_data[field_map["target_field"]] = target_id

                properties = relation.get("properties", {})
                if isinstance(properties, dict):
                    if "evidence" in properties:
                        rel_data["evidence"] = properties["evidence"]

                if confidence is not None:
                    rel_data["confidence"] = float(confidence) / 100.0

                model_class(**rel_data)
            except (ValueError, TypeError, KeyError) as exc:
                result.warnings.append(f"Pydantic 关系验证提示: {exc}")

        return result

    @staticmethod
    def _get_relation_field_map(
        model_key: tuple[str, str, str],
    ) -> dict[str, str] | None:
        """获取关系模型的字段映射。"""
        field_maps = {
            ("SUITABLE_FOR", "Tool", "Material"): {
                "source_field": "tool_id",
                "target_field": "material_id",
            },
            ("SUITABLE_FOR", "Tool", "Feature"): {
                "source_field": "tool_id",
                "target_field": "feature_id",
            },
            ("APPLIED_TO", "Process", "Feature"): {
                "source_field": "process_id",
                "target_field": "feature_id",
            },
            ("USED", "Process", "Tool"): {
                "source_field": "process_id",
                "target_field": "tool_id",
            },
        }
        return field_maps.get(model_key)

    def _check_consistency(
        self,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        """检查实体-关系一致性。"""
        entity_ids = {e.get("id") for e in entities if e.get("id")}

        for i, rel in enumerate(relations):
            source_id = rel.get("source_id", "")
            target_id = rel.get("target_id", "")

            if source_id and source_id not in entity_ids:
                issue = f"关系 #{i + 1} 的 source_id '{source_id}' 不在已抽取实体列表中"
                report.consistency_issues.append(issue)

            if target_id and target_id not in entity_ids:
                issue = f"关系 #{i + 1} 的 target_id '{target_id}' 不在已抽取实体列表中"
                report.consistency_issues.append(issue)

        # 检查实体 ID 前缀是否与类型匹配
        for entity in entities:
            entity_type = entity.get("entity_type", "")
            entity_id = entity.get("id", "")
            expected_prefix = _ENTITY_ID_PREFIX.get(entity_type)
            if expected_prefix and not entity_id.startswith(expected_prefix):
                report.warnings.append(
                    f"实体 '{entity_id}' 的 ID 前缀与类型 '{entity_type}' 不匹配（期望前缀: '{expected_prefix}'）"
                )

    def _check_reasonableness(
        self,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        """检查关系合理性。"""
        for i, rel in enumerate(relations):
            source_id = rel.get("source_id", "")
            target_id = rel.get("target_id", "")

            # 检查自环
            if source_id == target_id:
                report.warnings.append(f"关系 #{i + 1} 存在自环: {source_id} -> {target_id}")

        # 检查重复关系
        seen_relations: set[tuple[str, str, str]] = set()
        for i, rel in enumerate(relations):
            key = (
                rel.get("source_id", ""),
                rel.get("target_id", ""),
                rel.get("relation_type", ""),
            )
            if key in seen_relations:
                report.warnings.append(f"关系 #{i + 1} 与前面的关系重复: {key}")
            seen_relations.add(key)

    def _calculate_overall_score(self, report: ValidationReport) -> None:
        """计算总体评分。"""
        total_entities = len(report.entity_results)
        valid_entities = sum(1 for r in report.entity_results if r.is_valid)
        total_relations = len(report.relation_results)
        valid_relations = sum(1 for r in report.relation_results if r.is_valid)

        total = total_entities + total_relations
        valid = valid_entities + valid_relations

        if total > 0:
            report.accuracy_score = (valid / total) * 100
        else:
            report.accuracy_score = 0.0

        # 一致性问题和警告扣分
        consistency_penalty = len(report.consistency_issues) * 2
        warning_penalty = len(report.warnings) * 0.5
        report.accuracy_score = max(0, report.accuracy_score - consistency_penalty - warning_penalty)

        # 判定是否通过
        report.overall_valid = report.accuracy_score >= 70

        if report.accuracy_score >= 90:
            report.recommendation = "优秀：可直接入库"
        elif report.accuracy_score >= 70:
            report.recommendation = "良好：建议人工复核后入库"
        elif report.accuracy_score >= 50:
            report.recommendation = "一般：需要较多人工修正"
        else:
            report.recommendation = "较差：建议重新抽取或更换 Prompt"

    def to_dict(self, report: ValidationReport) -> dict[str, Any]:
        """将验证报告转为字典。"""
        return {
            "summary": report.summary(),
            "entity_results": [
                {
                    "entity": r.entity,
                    "is_valid": r.is_valid,
                    "errors": r.errors,
                    "warnings": r.warnings,
                }
                for r in report.entity_results
            ],
            "relation_results": [
                {
                    "relation": r.relation,
                    "is_valid": r.is_valid,
                    "errors": r.errors,
                    "warnings": r.warnings,
                }
                for r in report.relation_results
            ],
            "consistency_issues": report.consistency_issues,
            "warnings": report.warnings,
        }
