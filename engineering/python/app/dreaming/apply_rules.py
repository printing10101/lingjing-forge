"""规则应用入口。

规则经沙箱验证后方可应用到系统：
    - 通过 RuleValidator 验证的规则草稿可应用
    - 应用 = 将规则持久化到知识图谱（GraphStore）+ 写入审计日志
    - 应用后规则状态从 validated 转为 applied
    - 支持回滚（rollback）已应用的规则

设计原则：
    - 应用前必须通过 RuleValidator 验证（双重校验）
    - 应用操作写入审计日志哈希链（AIModule.DREAMING）
    - 回滚不删除审计记录，仅标记规则状态为 deprecated
    - 应用与回滚都是幂等操作

硬约束对齐：
    - 应用规则时不修改 cam_validation_required（始终 True）
    - 不解锁 SUCCEEDED 任务
    - 不降低 HRC52 pending_calibration 安全阈值
    - 所有应用决策写入审计日志

用法：
    applicator = RuleApplicator()
    result = applicator.apply(validated_rule)
    if result.success:
        logger.info(f"规则已应用：{result.rule_id}")
    else:
        logger.error(f"规则应用失败：{result.error}")

    # 回滚
    rollback_result = applicator.rollback(rule_id)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming.rule_validator import RuleValidator

logger = logging.getLogger(__name__)

# 规则应用状态机：draft validated applied deprecated（或 rejected）
# 回滚后规则状态变为 deprecated，保留审计记录但不生效
RULE_STATUS_APPLIED = "applied"
RULE_STATUS_DEPRECATED = "deprecated"
RULE_STATUS_REJECTED = "rejected"

# 应用规则持久化目录
APPLIED_RULES_DIR = "python/outputs/dreaming/applied_rules"


@dataclass
class ApplyResult:
    """规则应用结果。

    Attributes:
        success: 是否应用成功。
        rule_id: 规则 ID。
        applied_at: 应用时间戳。
        node_id: 知识图谱节点 ID（若应用成功）。
        audit_entry_seq: 审计日志条目序号（若写入成功）。
        error: 失败时的错误信息。
    """

    success: bool
    rule_id: str
    applied_at: str = ""
    node_id: str | None = None
    audit_entry_seq: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RollbackResult:
    """规则回滚结果。

    Attributes:
        success: 是否回滚成功。
        rule_id: 规则 ID。
        rolled_back_at: 回滚时间戳。
        previous_status: 回滚前的规则状态。
        error: 失败时的错误信息。
    """

    success: bool
    rule_id: str
    rolled_back_at: str = ""
    previous_status: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuleApplicator:
    """规则应用器。

    负责将通过验证的规则草稿应用到系统中：
        1. 双重校验（RuleValidator）
        2. 持久化到知识图谱（GraphStore）
        3. 写入审计日志（audit_integration.py）
        4. 更新规则状态为 applied
        5. 持久化到本地 JSON 文件（便于追踪）
    """

    def __init__(
        self,
        output_dir: str | None = None,
        graph_store: Any | None = None,
    ) -> None:
        """初始化规则应用器。

        Args:
            output_dir: 应用规则持久化目录。默认 python/outputs/dreaming/applied_rules
            graph_store: GraphStore 实例。None 表示按需初始化。
        """
        self.output_dir = Path(output_dir or APPLIED_RULES_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._graph_store = graph_store
        self._validator = RuleValidator()

    def _get_graph_store(self):
        """延迟获取 GraphStore 实例。"""
        if self._graph_store is None:
            from app.knowledge_graph.graph_store import GraphStore

            self._graph_store = GraphStore(auto_load=True)
        return self._graph_store

    def _get_audit_recorder(self):
        """延迟获取审计记录器。"""
        from app.dreaming.audit_integration import get_audit_recorder

        return get_audit_recorder()

    def apply(
        self,
        rule: RuleDraft,
        skip_validation: bool = False,
    ) -> ApplyResult:
        """应用规则到系统。

        Args:
            rule: 待应用的规则草稿。必须已通过 RuleValidator 验证。
            skip_validation: 是否跳过双重校验。默认 False。
                设置为 True 时跳过验证直接应用（仅用于测试，生产环境禁用）。

        Returns:
            ApplyResult 实例。
        """
        applied_at = datetime.now(timezone.utc).isoformat()

        # 阶段 1：双重校验
        if not skip_validation:
            validation = self._validator.validate(rule)
            if not validation.passed:
                error_msg = f"规则验证失败：{validation.errors}"
                logger.warning(error_msg)
                rule.status = RULE_STATUS_REJECTED
                self._get_audit_recorder().record_rule_application(
                    rule_id=rule.rule_id,
                    rule_description=rule.description,
                    validation_passed=False,
                    applied=False,
                )
                return ApplyResult(
                    success=False,
                    rule_id=rule.rule_id,
                    applied_at=applied_at,
                    error=error_msg,
                )

        # 阶段 2：持久化到知识图谱
        node_id = None
        try:
            graph = self._get_graph_store()
            node_id = f"rule_{rule.rule_id}"

            # 检查节点是否已存在（幂等性）
            if graph.has_node(node_id):
                logger.info("规则节点已存在，更新属性：node_id=%s", node_id)
                graph.update_node_properties(
                    node_id,
                    {
                        "description": rule.description,
                        "condition": rule.condition,
                        "action": rule.action,
                        "confidence": rule.confidence,
                        "status": RULE_STATUS_APPLIED,
                        "applied_at": applied_at,
                        "rule_type": rule.rule_type,
                        "source_insight_category": rule.source_insight_category,
                        "supporting_sessions_count": len(rule.supporting_sessions),
                        "respects_cam_validation": rule.respects_cam_validation,
                        "respects_succeeded_lock": rule.respects_succeeded_lock,
                        "adr": "ADR-021",
                    },
                )
            else:
                graph.add_node(
                    node_type="dreaming_rule",
                    node_id=node_id,
                    properties={
                        "description": rule.description,
                        "condition": rule.condition,
                        "action": rule.action,
                        "confidence": rule.confidence,
                        "status": RULE_STATUS_APPLIED,
                        "applied_at": applied_at,
                        "rule_type": rule.rule_type,
                        "source_insight_category": rule.source_insight_category,
                        "supporting_sessions_count": len(rule.supporting_sessions),
                        "supporting_sessions": json.dumps(rule.supporting_sessions[:10]),
                        "respects_cam_validation": rule.respects_cam_validation,
                        "respects_succeeded_lock": rule.respects_succeeded_lock,
                        "created_at": rule.created_at,
                        "adr": "ADR-021",
                    },
                )
            logger.info("规则已持久化到知识图谱：node_id=%s", node_id)
        except Exception as e:
            error_msg = f"知识图谱持久化失败：{type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            return ApplyResult(
                success=False,
                rule_id=rule.rule_id,
                applied_at=applied_at,
                error=error_msg,
            )

        # 阶段 3：更新规则状态
        rule.status = RULE_STATUS_APPLIED
        rule.validated_at = applied_at

        # 阶段 4：持久化到本地 JSON 文件
        try:
            rule_file = self.output_dir / f"{rule.rule_id}.json"
            with open(rule_file, "w", encoding="utf-8") as f:
                json.dump(rule.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("规则已持久化到本地文件：%s", rule_file)
        except OSError as e:
            logger.warning("规则本地持久化失败（不影响应用）：%s", e)

        # 阶段 5：写入审计日志
        audit_seq = None
        try:
            entry = self._get_audit_recorder().record_rule_application(
                rule_id=rule.rule_id,
                rule_description=rule.description,
                validation_passed=True,
                applied=True,
            )
            audit_seq = getattr(entry, "chain_seq", None)
        except Exception as e:
            logger.error("审计日志写入失败（不影响应用）：%s", e)

        return ApplyResult(
            success=True,
            rule_id=rule.rule_id,
            applied_at=applied_at,
            node_id=node_id,
            audit_entry_seq=audit_seq,
        )

    def rollback(self, rule_id: str) -> RollbackResult:
        """回滚已应用的规则。

        回滚操作：
            1. 将规则状态从 applied 改为 deprecated
            2. 更新知识图谱节点属性
            3. 写入审计日志
            4. 不删除本地 JSON 文件（保留审计记录）

        Args:
            rule_id: 规则 ID（不含 rule_ 前缀）。

        Returns:
            RollbackResult 实例。
        """
        rolled_back_at = datetime.now(timezone.utc).isoformat()
        node_id = f"rule_{rule_id}"

        # 获取当前状态
        previous_status = None
        try:
            graph = self._get_graph_store()
            if graph.has_node(node_id):
                node = graph.get_node(node_id)
                if node:
                    previous_status = node.properties.get("status", RULE_STATUS_APPLIED)

                # 更新状态为 deprecated
                graph.update_node_properties(
                    node_id,
                    {
                        "status": RULE_STATUS_DEPRECATED,
                        "rolled_back_at": rolled_back_at,
                    },
                )
                logger.info("规则已回滚（知识图谱）：node_id=%s", node_id)
            else:
                return RollbackResult(
                    success=False,
                    rule_id=rule_id,
                    rolled_back_at=rolled_back_at,
                    error=f"规则节点不存在：{node_id}",
                )
        except Exception as e:
            error_msg = f"知识图谱回滚失败：{type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            return RollbackResult(
                success=False,
                rule_id=rule_id,
                rolled_back_at=rolled_back_at,
                previous_status=previous_status,
                error=error_msg,
            )

        # 更新本地 JSON 文件状态
        try:
            rule_file = self.output_dir / f"{rule_id}.json"
            if rule_file.exists():
                with open(rule_file, "r", encoding="utf-8") as f:
                    rule_data = json.load(f)
                rule_data["status"] = RULE_STATUS_DEPRECATED
                rule_data["rolled_back_at"] = rolled_back_at
                with open(rule_file, "w", encoding="utf-8") as f:
                    json.dump(rule_data, f, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("本地 JSON 回滚更新失败（不影响回滚）：%s", e)

        # 写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule_id,
                rule_description=f"规则回滚：{rule_id}",
                validation_passed=True,
                applied=False,
                rollback_triggered=True,
            )
        except Exception as e:
            logger.error("审计日志写入失败（不影响回滚）：%s", e)

        return RollbackResult(
            success=True,
            rule_id=rule_id,
            rolled_back_at=rolled_back_at,
            previous_status=previous_status,
        )

    def list_applied_rules(self) -> list[dict[str, Any]]:
        """列出所有已应用的规则。

        Returns:
            规则信息列表。
        """
        rules: list[dict[str, Any]] = []
        try:
            graph = self._get_graph_store()
            nodes = graph.list_nodes_by_type("dreaming_rule")
            for node in nodes:
                rules.append(
                    {
                        "rule_id": node.node_id.replace("rule_", ""),
                        "description": node.properties.get("description", ""),
                        "status": node.properties.get("status", ""),
                        "applied_at": node.properties.get("applied_at", ""),
                        "confidence": node.properties.get("confidence", 0.0),
                        "rule_type": node.properties.get("rule_type", ""),
                    }
                )
        except Exception as e:
            logger.error("列出已应用规则失败：%s", e)
        return rules

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        """查询指定规则的详情。

        Args:
            rule_id: 规则 ID（不含 rule_ 前缀）。

        Returns:
            规则详情字典，若不存在则 None。
        """
        node_id = f"rule_{rule_id}"
        try:
            graph = self._get_graph_store()
            if not graph.has_node(node_id):
                return None
            node = graph.get_node(node_id)
            if node is None:
                return None
            return {
                "rule_id": rule_id,
                "node_id": node_id,
                "properties": node.properties,
            }
        except Exception as e:
            logger.error("查询规则失败：%s", e)
            return None


def apply_validated_rules(
    rules: list[RuleDraft],
    skip_validation: bool = False,
) -> list[ApplyResult]:
    """批量应用已验证的规则。

    便捷函数，按顺序应用规则列表。任一规则应用失败不影响后续规则。

    Args:
        rules: 规则草稿列表。
        skip_validation: 是否跳过双重校验。默认 False。

    Returns:
        应用结果列表（与输入 rules 一一对应）。
    """
    applicator = RuleApplicator()
    results: list[ApplyResult] = []
    for rule in rules:
        result = applicator.apply(rule, skip_validation=skip_validation)
        results.append(result)
        if result.success:
            logger.info("规则应用成功：%s", rule.rule_id)
        else:
            logger.warning("规则应用失败：%s - %s", rule.rule_id, result.error)
    return results
