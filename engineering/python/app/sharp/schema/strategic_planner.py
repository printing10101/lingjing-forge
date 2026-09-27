"""SHARP Schema-Aware 战略规划器（M1.3）。

对应论文 §4.2 "Schema-Aware Strategic Planning" 组件。

核心思想
--------
传统 KG 验证方法对所有三元组采用统一的验证流程，忽略了三元组的
领域特征差异。SHARP 提出"Schema-Aware 战略规划"，根据三元组的：
- 实体类型组合（Tool-Material / Tool-Feature / Process-Feature / Process-Tool）
- 关系类型
- 已知 confidence 与 source
- 是否携带属性
- 领域先验（如 SUITABLE_FOR_MATERIAL 关系通常有丰富的文献支撑）

生成定制化的 `VerificationStrategy`，指导后续 ReAct 循环：
- 调用哪些工具，调用顺序
- 最大步数
- 重点关注方向
- 终止条件阈值

本规划器是 **training-free** 的，所有策略规则均基于 ontology-v1.md
的本体约束与灵境制造领域专家经验硬编码，不引入任何学习参数。

配置驱动
--------
支持通过 `ablation_mode` 切换消融模式：
- `None`：完整 SHARP（默认）
- `"no_schema"`：禁用 Schema 规划，回退到统一策略
- `"no_memory"`：禁用 Memory 增强（在 M4 中使用）
- `"no_react"`：禁用 ReAct 循环（在 M3 中使用）
- `"no_toolset"`：禁用 Hybrid 工具集（在 M2 中使用）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.knowledge_graph import RelationSource
from app.sharp.schema.domain_schema import (
    DomainSchema,
    RelationType,
    Triple,
    DEFAULT_SCHEMA,
)


# 验证策略结构


@dataclass
class VerificationStrategy:
    """单次三元组验证的执行策略。

    由 `StrategicPlanner.plan()` 生成，被 ReAct 循环消费。

    Attributes
    ----------
    triple : Triple
        待验证的三元组
    tool_sequence : list[str]
        工具调用优先级序列，如 ["kg.query_entity", "text.retrieve", "llm.reason"]
        ReAct 循环会按此顺序尝试，但 LLM 可根据观察结果跳过/重试
    max_steps : int
        最大 ReAct 步数
    focus_dimensions : list[str]
        重点关注维度，如 ["entity_match", "evidence_sufficiency", "source_reliability"]
        用于指导 LLM 在推理时分配注意力
    confidence_threshold : float
        终止阈值：当聚合置信度 >= 该值时提前终止
    evidence_convergence_window : int
        证据收敛窗口：连续 N 步置信度变化 < 0.05 则认为收敛
    require_external_evidence : bool
        是否强制要求外部证据（KG 或文本）支撑，用于低置信度场景
    require_cross_validation : bool
        是否强制多源交叉验证，用于 LLM 抽取的关系
    rationale : str
        策略生成的理由（自然语言，便于 prompt 注入与调试）
    """

    triple: Triple
    tool_sequence: list[str] = field(default_factory=list)
    max_steps: int = 6
    focus_dimensions: list[str] = field(default_factory=list)
    confidence_threshold: float = 0.85
    evidence_convergence_window: int = 2
    require_external_evidence: bool = False
    require_cross_validation: bool = False
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "triple": self.triple.short_repr(),
            "tool_sequence": self.tool_sequence,
            "max_steps": self.max_steps,
            "focus_dimensions": self.focus_dimensions,
            "confidence_threshold": self.confidence_threshold,
            "evidence_convergence_window": self.evidence_convergence_window,
            "require_external_evidence": self.require_external_evidence,
            "require_cross_validation": self.require_cross_validation,
            "rationale": self.rationale,
        }


# 工具名常量（与 M2 tool_registry 对齐）

# KG 工具（基于 KnowledgeGraphQueryAPI）
TOOL_KG_QUERY_ENTITY = "kg.query_entity"  # 查询实体属性
TOOL_KG_QUERY_RELATION = "kg.query_relation"  # 查询关系是否存在
TOOL_KG_QUERY_NEIGHBORS = "kg.query_neighbors"  # 查询邻居（多跳）
TOOL_KG_QUERY_PATH = "kg.query_path"  # 查询路径

# 文本工具（基于 RagRetrievalEngine）
TOOL_TEXT_RETRIEVE = "text.retrieve"  # 文档检索
TOOL_TEXT_ENTITY_LOOKUP = "text.entity_lookup"  # 实体倒排索引查询

# LLM 工具（基于 LLM Router）
TOOL_LLM_REASON = "llm.reason"  # LLM 推理
TOOL_LLM_EXTRACT = "llm.extract"  # LLM 实体/关系抽取

# 聚合工具
TOOL_AGGREGATE_EVIDENCE = "aggregate.evidence"  # 证据聚合


# 战略规划器


class StrategicPlanner:
    """Schema-Aware 战略规划器。

    根据三元组特征生成 `VerificationStrategy`，所有规则均为 training-free
    的硬编码策略，基于 ontology-v1.md 本体约束与灵境制造领域经验。

    Parameters
    ----------
    schema : DomainSchema
        领域 Schema，默认 `DEFAULT_SCHEMA`
    max_react_steps : int
        默认最大 ReAct 步数（可被策略覆盖）
    confidence_threshold : float
        默认终止置信度阈值
    evidence_convergence_window : int
        默认证据收敛窗口
    ablation_mode : Optional[str]
        消融模式：None / "no_schema" / "no_memory" / "no_react" / "no_toolset"
    """

    def __init__(
        self,
        schema: DomainSchema | None = None,
        max_react_steps: int = 8,
        confidence_threshold: float = 0.85,
        evidence_convergence_window: int = 2,
        ablation_mode: str | None = None,
    ) -> None:
        self.schema = schema or DEFAULT_SCHEMA
        self.default_max_steps = max_react_steps
        self.default_confidence_threshold = confidence_threshold
        self.default_evidence_window = evidence_convergence_window
        self.ablation_mode = ablation_mode

        # 消融模式校验
        valid_modes = {None, "no_schema", "no_memory", "no_react", "no_toolset"}
        if ablation_mode not in valid_modes:
            raise ValueError(f"ablation_mode 必须是 {valid_modes} 之一，实际: {ablation_mode}")

    # 公共入口

    def plan(self, triple: Triple) -> VerificationStrategy:
        """为三元组生成验证策略。"""
        # 消融模式：no_schema 直接返回统一策略
        if self.ablation_mode == "no_schema":
            return self._plan_uniform(triple)

        # 分层策略生成
        strategy = VerificationStrategy(
            triple=triple,
            max_steps=self.default_max_steps,
            confidence_threshold=self.default_confidence_threshold,
            evidence_convergence_window=self.default_evidence_window,
        )

        # 依次应用各维度规则
        self._apply_relation_rule(triple, strategy)
        self._apply_confidence_rule(triple, strategy)
        self._apply_source_rule(triple, strategy)
        self._apply_property_rule(triple, strategy)
        self._apply_ablation_rule(triple, strategy)

        # 生成策略理由
        strategy.rationale = self._generate_rationale(triple, strategy)
        return strategy

    # 分维度规则

    def _apply_relation_rule(self, triple: Triple, strategy: VerificationStrategy) -> None:
        """根据关系类型决定工具优先级与关注维度。"""
        rel = triple.relation

        if rel == RelationType.SUITABLE_FOR_MATERIAL:
            # (Tool)-[SUITABLE_FOR]->(Material)
            # 领域先验：刀具-材料适配有丰富的工艺手册与文献支撑
            strategy.tool_sequence = [
                TOOL_KG_QUERY_ENTITY,  # 先拉取 Tool/Material 属性
                TOOL_KG_QUERY_RELATION,  # 查询 KG 中是否已存在该关系
                TOOL_TEXT_RETRIEVE,  # 检索材料加工性能文献
                TOOL_LLM_REASON,  # LLM 综合推理
                TOOL_AGGREGATE_EVIDENCE,  # 证据聚合
            ]
            strategy.focus_dimensions.extend(
                [
                    "tool_material_compatibility",  # 刀具材料与工件材料兼容性
                    "cutting_performance_match",  # 切削性能匹配度
                ]
            )

        elif rel == RelationType.SUITABLE_FOR_FEATURE:
            # (Tool)-[SUITABLE_FOR]->(Feature)
            strategy.tool_sequence = [
                TOOL_KG_QUERY_ENTITY,
                TOOL_KG_QUERY_RELATION,
                TOOL_TEXT_RETRIEVE,
                TOOL_LLM_REASON,
                TOOL_AGGREGATE_EVIDENCE,
            ]
            strategy.focus_dimensions.extend(
                [
                    "tool_geometry_match",  # 刀具几何与特征匹配
                    "application_scenario",  # 应用场景一致性
                ]
            )

        elif rel == RelationType.APPLIED_TO:
            # (Process)-[APPLIED_TO]->(Feature)
            strategy.tool_sequence = [
                TOOL_KG_QUERY_ENTITY,
                TOOL_KG_QUERY_RELATION,
                TOOL_TEXT_RETRIEVE,  # 检索工艺规则文档
                TOOL_LLM_REASON,
                TOOL_AGGREGATE_EVIDENCE,
            ]
            strategy.focus_dimensions.extend(
                [
                    "process_feature_applicability",  # 工艺对特征的适用性
                    "process_rule_validity",  # 工艺规则有效性
                ]
            )

        elif rel == RelationType.USED:
            # (Process)-[USED]->(Tool)
            strategy.tool_sequence = [
                TOOL_KG_QUERY_ENTITY,
                TOOL_KG_QUERY_RELATION,
                TOOL_TEXT_RETRIEVE,
                TOOL_LLM_REASON,
                TOOL_AGGREGATE_EVIDENCE,
            ]
            strategy.focus_dimensions.extend(
                [
                    "process_tool_usage",  # 工艺是否实际使用该刀具
                    "tool_capability_match",  # 刀具能力匹配
                ]
            )

    def _apply_confidence_rule(self, triple: Triple, strategy: VerificationStrategy) -> None:
        """根据已知 confidence 调整验证深度。"""
        props = triple.relation_properties or {}
        conf = props.get("confidence")

        if conf is None:
            # 未提供 confidence，按标准验证
            strategy.max_steps = min(strategy.max_steps, self.default_max_steps)
            return

        if conf >= 0.8:
            # 高置信度：快速验证
            strategy.max_steps = min(strategy.max_steps, 4)
            strategy.confidence_threshold = max(strategy.confidence_threshold, 0.9)
            strategy.focus_dimensions.append("high_confidence_quick_check")
        elif conf >= 0.5:
            # 中置信度：标准验证
            strategy.max_steps = min(strategy.max_steps, 6)
        else:
            # 低置信度：深度验证
            strategy.max_steps = max(strategy.max_steps, 8)
            strategy.require_external_evidence = True
            strategy.focus_dimensions.append("low_confidence_deep_check")

    def _apply_source_rule(self, triple: Triple, strategy: VerificationStrategy) -> None:
        """根据 source 决定是否需要交叉验证。"""
        props = triple.relation_properties or {}
        src = props.get("source")

        if src is None:
            return

        # 字符串/枚举均兼容
        src_value = src.value if isinstance(src, RelationSource) else src

        if src_value == RelationSource.LLM.value:
            # LLM 抽取的关系需多源交叉验证
            strategy.require_cross_validation = True
            strategy.focus_dimensions.append("llm_extraction_verification")
            # 增加文本检索步骤权重（已在 tool_sequence 中）
        elif src_value == RelationSource.MANUAL.value:
            # 人工录入需关注依据
            strategy.focus_dimensions.append("manual_input_evidence")
        elif src_value == RelationSource.MEASURED.value:
            # 实测数据需关注统计显著性
            strategy.focus_dimensions.append("measured_statistical_significance")
        elif src_value == RelationSource.RULE.value:
            # 规则推导需关注规则是否仍然有效
            strategy.focus_dimensions.append("rule_still_valid")

    def _apply_property_rule(self, triple: Triple, strategy: VerificationStrategy) -> None:
        """根据是否携带属性决定是否需要先拉取属性。"""
        # 若 head/tail 无属性，强制第一步调用 KG 拉取属性
        # （tool_sequence 已默认将 kg.query_entity 放在首位，这里仅补充 rationale）
        if not triple.head_properties or not triple.tail_properties:
            strategy.focus_dimensions.append("entity_property_fetch_required")

    def _apply_ablation_rule(self, triple: Triple, strategy: VerificationStrategy) -> None:
        """根据消融模式调整策略。"""
        if self.ablation_mode == "no_toolset":
            # 禁用 Hybrid 工具集：仅保留 LLM 推理
            strategy.tool_sequence = [TOOL_LLM_REASON, TOOL_AGGREGATE_EVIDENCE]
            strategy.max_steps = min(strategy.max_steps, 3)
        elif self.ablation_mode == "no_react":
            # 禁用 ReAct 循环：仅单步工具调用
            strategy.tool_sequence = strategy.tool_sequence[:1]
            strategy.max_steps = 1
        elif self.ablation_mode == "no_memory":
            # Memory 消融在 M4 处理，这里不调整策略
            pass

    # 辅助方法

    def _plan_uniform(self, triple: Triple) -> VerificationStrategy:
        """消融模式 no_schema 的统一策略。"""
        return VerificationStrategy(
            triple=triple,
            tool_sequence=[
                TOOL_KG_QUERY_RELATION,
                TOOL_TEXT_RETRIEVE,
                TOOL_LLM_REASON,
                TOOL_AGGREGATE_EVIDENCE,
            ],
            max_steps=6,
            focus_dimensions=["generic_verification"],
            confidence_threshold=self.default_confidence_threshold,
            evidence_convergence_window=self.default_evidence_window,
            rationale="消融模式 no_schema：采用统一策略，不基于 Schema 特征定制。",
        )

    def _generate_rationale(self, triple: Triple, strategy: VerificationStrategy) -> str:
        """生成策略理由（自然语言，用于 prompt 注入与调试）。"""
        parts = [
            f"针对三元组 {triple.short_repr()} 生成验证策略：",
            f"关系类型={triple.relation.value}，工具序列长度={len(strategy.tool_sequence)}；",
        ]

        props = triple.relation_properties or {}
        conf = props.get("confidence")
        if conf is not None:
            parts.append(f"已知置信度={conf}，最大步数={strategy.max_steps}；")

        src = props.get("source")
        if src is not None:
            src_value = src.value if isinstance(src, RelationSource) else src
            parts.append(f"来源={src_value}，需交叉验证={strategy.require_cross_validation}；")

        if strategy.focus_dimensions:
            parts.append(f"关注维度={strategy.focus_dimensions}。")

        return "".join(parts)


__all__ = [
    "VerificationStrategy",
    "StrategicPlanner",
    # 工具名常量
    "TOOL_KG_QUERY_ENTITY",
    "TOOL_KG_QUERY_RELATION",
    "TOOL_KG_QUERY_NEIGHBORS",
    "TOOL_KG_QUERY_PATH",
    "TOOL_TEXT_RETRIEVE",
    "TOOL_TEXT_ENTITY_LOOKUP",
    "TOOL_LLM_REASON",
    "TOOL_LLM_EXTRACT",
    "TOOL_AGGREGATE_EVIDENCE",
]
