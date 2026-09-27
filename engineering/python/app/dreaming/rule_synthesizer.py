"""规则合成器：将洞察转化为可执行规则草稿。

Outcomes 反馈链路：
    Dream 浮现的洞察 → 规则候选 → 沙箱验证 → 灰度应用 → 持久化

本地化实现：
    - 规则草稿以 JSON Schema 描述，便于机器校验
    - 每条规则记录 source_insight_id 和 supporting_sessions（学术诚信 D-2）
    - 规则不直接生效，需经过 RuleValidator 沙箱验证后才能应用
    - 硬约束：规则不得绕过 CAM 二次验证、不得解锁 SUCCEEDED 任务

规则分类（对齐项目已有规则系统）：
    - parameter_adjustment：切削参数调整规则
    - confidence_threshold：置信度阈值规则
    - validation_requirement：验证要求规则
    - warning_rule：警告规则（仅提示，不改变执行流）
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dreaming.reflector import InsightItem, ReflectionResult

logger = logging.getLogger(__name__)


# 规则数据结构


# 规则状态机：draft validated applied deprecated
RULE_STATUS_DRAFT = "draft"
RULE_STATUS_VALIDATED = "validated"
RULE_STATUS_APPLIED = "applied"
RULE_STATUS_DEPRECATED = "deprecated"
RULE_STATUS_REJECTED = "rejected"  # 沙箱验证失败


@dataclass
class RuleDraft:
    """规则草稿：从洞察合成而来，尚未验证。"""

    rule_id: str  # 唯一 ID
    rule_type: str  # parameter_adjustment | confidence_threshold | ...
    description: str  # 人类可读描述
    condition: dict[str, Any]  # 触发条件（JSON Schema 片段）
    action: dict[str, Any]  # 执行动作
    confidence: float = 0.5  # 规则置信度
    status: str = RULE_STATUS_DRAFT
    # 学术诚信追溯
    source_insight_category: str = ""
    supporting_sessions: list[str] = field(default_factory=list)
    source_insight_content: str = ""
    # 硬约束标记
    respects_cam_validation: bool = True  # 是否遵守 CAM 二次验证
    respects_succeeded_lock: bool = True  # 是否遵守 SUCCEEDED 禁删
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    validated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "description": self.description,
            "condition": self.condition,
            "action": self.action,
            "confidence": self.confidence,
            "status": self.status,
            "source_insight_category": self.source_insight_category,
            "supporting_sessions": self.supporting_sessions,
            "source_insight_content": self.source_insight_content,
            "respects_cam_validation": self.respects_cam_validation,
            "respects_succeeded_lock": self.respects_succeeded_lock,
            "created_at": self.created_at,
            "validated_at": self.validated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleDraft":
        return cls(
            rule_id=data["rule_id"],
            rule_type=data["rule_type"],
            description=data["description"],
            condition=data.get("condition", {}),
            action=data.get("action", {}),
            confidence=float(data.get("confidence", 0.5)),
            status=data.get("status", RULE_STATUS_DRAFT),
            source_insight_category=data.get("source_insight_category", ""),
            supporting_sessions=data.get("supporting_sessions", []),
            source_insight_content=data.get("source_insight_content", ""),
            respects_cam_validation=data.get("respects_cam_validation", True),
            respects_succeeded_lock=data.get("respects_succeeded_lock", True),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            validated_at=data.get("validated_at"),
            metadata=data.get("metadata", {}),
        )


# RuleSynthesizer


class RuleSynthesizer:
    """将洞察合成为规则草稿。

    用法：
        synthesizer = RuleSynthesizer(output_dir="python/outputs/dreaming/rules")
        rules = synthesizer.synthesize(reflection_result)
        # rules 是 List[RuleDraft]，状态均为 draft
    """

    def __init__(
        self,
        output_dir: str | None = None,
    ) -> None:
        """初始化规则合成器。

        Args:
            output_dir: 规则草稿持久化目录。默认 python/outputs/dreaming/rules
        """
        self.output_dir = Path(output_dir or "python/outputs/dreaming/rules")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # 主入口

    def synthesize(
        self,
        reflection: ReflectionResult,
    ) -> list[RuleDraft]:
        """从反思结果合成规则草稿。

        Args:
            reflection: DreamReflector 的反思结果

        Returns:
            RuleDraft 列表，状态均为 draft，未经沙箱验证
        """
        rules: list[RuleDraft] = []

        for insight in reflection.insights:
            # 根据 insight.category 分派到不同的合成策略
            if insight.category == "pattern":
                rule = self._synthesize_pattern_rule(insight)
            elif insight.category == "anomaly":
                rule = self._synthesize_anomaly_rule(insight)
            elif insight.category == "rule_candidate":
                rule = self._synthesize_rule_candidate(insight)
            elif insight.category == "warning":
                rule = self._synthesize_warning_rule(insight)
            else:
                logger.debug("未知 insight category: %s", insight.category)
                continue

            if rule is not None:
                # C4 bug 修复：原代码定义了 _validate_hard_constraints 但从未调用，
                # 导致硬约束（CAM 二次验证、SUCCEEDED 锁）被完全绕过。这是项目级
                # 红线（见 project_memory 中的 CAM 二次验证约束）。
                if not self._validate_hard_constraints(rule):
                    logger.warning(
                        "规则 %s 违反硬约束，已拒绝（不进入草稿列表）",
                        rule.rule_id,
                    )
                    continue
                rules.append(rule)

        # 持久化规则草稿
        self._persist_rules(rules)

        logger.info(
            "规则合成完成：%d 条草稿（来自 %d 条洞察）",
            len(rules),
            len(reflection.insights),
        )
        return rules

    # 合成策略

    def _synthesize_pattern_rule(self, insight: InsightItem) -> RuleDraft | None:
        """从 pattern 类洞察合成规则。

        例如：洞察 "材料 HRC52 出现 3 次失败"
        → 规则：当 material=HRC52 时，降低置信度阈值
        """
        content = insight.content
        material = self._extract_material(content)

        if material is None:
            # 无法识别材料，生成通用警告规则
            return RuleDraft(
                rule_id=self._gen_rule_id(),
                rule_type="warning_rule",
                description=f"模式洞察：{content[:100]}",
                condition={
                    "pattern_matched": True,
                    "min_failure_count": len(insight.supporting_sessions),
                },
                action={
                    "type": "log_warning",
                    "message": content,
                },
                confidence=insight.confidence * 0.8,  # 规则置信度略低于洞察
                source_insight_category=insight.category,
                supporting_sessions=insight.supporting_sessions,
                source_insight_content=insight.content,
            )

        return RuleDraft(
            rule_id=self._gen_rule_id(),
            rule_type="confidence_threshold",
            description=f"材料 {material} 失败率较高，降低推荐置信度阈值",
            condition={
                "field": "material_type",
                "operator": "equals",
                "value": material,
                "min_failure_count": len(insight.supporting_sessions),
            },
            action={
                "type": "adjust_confidence_threshold",
                "field": "chatter_confidence",
                "adjustment": -0.1,
                "min_threshold": 0.3,  # 硬约束：不得低于 0.3
            },
            confidence=insight.confidence * 0.8,
            source_insight_category=insight.category,
            supporting_sessions=insight.supporting_sessions,
            source_insight_content=insight.content,
        )

    def _synthesize_anomaly_rule(self, insight: InsightItem) -> RuleDraft | None:
        """从 anomaly 类洞察合成规则。"""
        return RuleDraft(
            rule_id=self._gen_rule_id(),
            rule_type="validation_requirement",
            description=f"异常检测：{insight.content[:100]}",
            condition={
                "anomaly_detected": True,
                "supporting_session_count": len(insight.supporting_sessions),
            },
            action={
                "type": "require_additional_validation",
                "validation_type": "human_review",  # 异常必须人工复核
            },
            confidence=insight.confidence * 0.7,
            source_insight_category=insight.category,
            supporting_sessions=insight.supporting_sessions,
            source_insight_content=insight.content,
        )

    def _synthesize_rule_candidate(self, insight: InsightItem) -> RuleDraft | None:
        """从 rule_candidate 类洞察合成规则。

        例如：洞察 "3 个 Session 成功，对应 memory 应提升 validation_count"
        → 规则：成功的 Session 对应 memory validation_count +1
        """
        return RuleDraft(
            rule_id=self._gen_rule_id(),
            rule_type="parameter_adjustment",
            description=f"规则候选：{insight.content[:100]}",
            condition={
                "session_outcome": "success",
                "session_count": len(insight.supporting_sessions),
            },
            action={
                "type": "increment_validation_count",
                "target": "memory_store",
                # 硬约束：不得修改 SUCCEEDED 任务的锁定状态
                "respects_succeeded_lock": True,
            },
            confidence=insight.confidence,
            source_insight_category=insight.category,
            supporting_sessions=insight.supporting_sessions,
            source_insight_content=insight.content,
        )

    def _synthesize_warning_rule(self, insight: InsightItem) -> RuleDraft | None:
        """从 warning 类洞察合成警告规则。"""
        return RuleDraft(
            rule_id=self._gen_rule_id(),
            rule_type="warning_rule",
            description=f"警告：{insight.content[:100]}",
            condition={
                "warning_triggered": True,
                "supporting_session_count": len(insight.supporting_sessions),
            },
            action={
                "type": "log_warning",
                "message": insight.content,
                "level": "warning",
            },
            confidence=insight.confidence,
            source_insight_category=insight.category,
            supporting_sessions=insight.supporting_sessions,
            source_insight_content=insight.content,
        )

    # 硬约束校验

    def _validate_hard_constraints(self, rule: RuleDraft) -> bool:
        """校验规则是否违反硬约束。

        返回 True 表示合规，False 表示违规。
        """
        # 检查是否试图绕过 CAM 验证
        action_type = rule.action.get("type", "")
        if action_type in ("skip_cam_validation", "force_pass"):
            logger.warning("规则 %s 试图绕过 CAM 验证，已拒绝", rule.rule_id)
            rule.respects_cam_validation = False
            rule.status = RULE_STATUS_REJECTED
            return False

        # 检查是否试图解锁 SUCCEEDED 任务
        if action_type in ("unlock_succeeded", "delete_succeeded"):
            logger.warning("规则 %s 试图解锁 SUCCEEDED 任务，已拒绝", rule.rule_id)
            rule.respects_succeeded_lock = False
            rule.status = RULE_STATUS_REJECTED
            return False

        return True

    # 工具方法

    def _extract_material(self, content: str) -> str | None:
        """从洞察文本中提取材料类型。

        支持的项目材料：TC4 / HRC52 / 6061-T6 / 45钢 / AL7075 等
        """
        materials = [
            "TC4",
            "HRC52",
            "HRC_52",
            "6061-T6",
            "6061T6",
            "45钢",
            "45_steel",
            "AL7075",
            "Al7075",
        ]
        for mat in materials:
            if mat in content:
                return mat
        return None

    def _gen_rule_id(self) -> str:
        """生成规则 ID。"""
        return f"rule_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

    def _persist_rules(self, rules: list[RuleDraft]) -> None:
        """将规则草稿持久化到 JSON 文件。"""
        if not rules:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = self.output_dir / f"rules_draft_{timestamp}.json"

        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "rule_count": len(rules),
                        "rules": [r.to_dict() for r in rules],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info("规则草稿已持久化：%s", output_file)
        except OSError as e:
            logger.warning("规则持久化失败：%s", e)

    # 规则加载（供 RuleValidator 使用）

    def load_rules(self, status: str | None = None) -> list[RuleDraft]:
        """加载已持久化的规则草稿。

        Args:
            status: 按状态过滤（如 "draft"），None 表示全部
        """
        rules: list[RuleDraft] = []

        for rule_file in self.output_dir.glob("rules_draft_*.json"):
            try:
                with open(rule_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rule_data in data.get("rules", []):
                    rule = RuleDraft.from_dict(rule_data)
                    if status is None or rule.status == status:
                        rules.append(rule)
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning("加载规则文件 %s 失败：%s", rule_file, e)

        return rules
