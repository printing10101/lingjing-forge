"""SHARP API 请求/响应 Pydantic 模型（M5.1）。

对应论文 §5 "Engineering" 中描述的 REST API 接口契约。

设计原则
--------
- **零冗余**：直接复用 `app.sharp.schema.domain_schema` 的枚举与 Triple，
  不重复定义业务概念
- **类型安全**：所有字段使用 Pydantic v2 BaseModel，自动生成 OpenAPI schema
- **容错**：枚举字段允许字符串输入，Pydantic 会自动转换与校验
- **可观测**：响应模型包含完整证据链与轨迹，便于前端可视化与调试
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.sharp.schema.domain_schema import EntityType, RelationType


# 共享子模型


class EntityRef(BaseModel):
    """实体引用：类型 + ID + 可选属性。"""

    type: EntityType = Field(..., description="实体类型")
    id: str = Field(..., description="实体 ID，符合 `<type>-<slug>` 规范")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="已知属性（可选，减少工具调用）",
    )


class RelationRef(BaseModel):
    """关系引用：类型 + 可选属性。"""

    type: RelationType = Field(..., description="关系类型")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="关系已知属性（confidence / source / evidence 等）",
    )


class TripleRequest(BaseModel):
    """三元组请求模型，对应 `Triple.from_dict` 输入格式。"""

    head: EntityRef = Field(..., description="头实体")
    relation: RelationRef = Field(..., description="关系")
    tail: EntityRef = Field(..., description="尾实体")

    def to_triple_dict(self) -> dict[str, Any]:
        """转换为 `Triple.from_dict` 接受的 dict 格式。"""
        return {
            "head": {
                "type": self.head.type.value,
                "id": self.head.id,
                "properties": self.head.properties,
            },
            "relation": {
                "type": self.relation.type.value,
                "properties": self.relation.properties,
            },
            "tail": {
                "type": self.tail.type.value,
                "id": self.tail.id,
                "properties": self.tail.properties,
            },
        }


# 验证请求


class VerifyRequest(BaseModel):
    """`POST /sharp/verify` 请求体。"""

    triple: TripleRequest = Field(..., description="待验证的三元组")
    ablation_mode: str | None = Field(
        None,
        description=(
            "消融模式覆盖（None / 'no_schema' / 'no_memory' / "
            "'no_react' / 'no_toolset'）。None 表示使用服务端默认配置。"
        ),
    )
    max_react_steps: int | None = Field(
        None,
        ge=1,
        le=20,
        description="覆盖默认最大 ReAct 步数",
    )
    return_trajectory: bool = Field(
        True,
        description="是否在响应中包含完整 ReAct 轨迹（False 可减小响应体积）",
    )


class BatchVerifyRequest(BaseModel):
    """`POST /sharp/verify/batch` 请求体。"""

    triples: list[TripleRequest] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="待验证的三元组列表（单次最多 50 条）",
    )
    ablation_mode: str | None = Field(
        None,
        description="消融模式（应用于本批所有三元组）",
    )
    max_react_steps: int | None = Field(
        None,
        ge=1,
        le=20,
        description="覆盖默认最大 ReAct 步数",
    )
    return_trajectory: bool = Field(
        False,
        description="批量验证默认不返回轨迹（节省带宽）",
    )


# 验证响应


class EvidenceItem(BaseModel):
    """证据链中的一条证据。"""

    source: str = Field(..., description="证据来源（kg/text/llm）")
    content: str = Field(..., description="证据内容")
    confidence: float = Field(..., description="证据置信度")
    weight: float = Field(..., description="来源权重")
    weighted_score: float = Field(..., description="加权分数")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class TrajectoryStep(BaseModel):
    """ReAct 轨迹中的单步记录。"""

    step: int = Field(..., description="步序号")
    thought: str = Field("", description="LLM 思考")
    action: str | None = Field(None, description="动作名")
    action_input: Any | None = Field(None, description="动作输入")
    observation: str | None = Field(None, description="观察结果")
    elapsed_ms: float = Field(0.0, description="本步耗时（毫秒）")
    success: bool = Field(True, description="是否成功")
    finish_action: dict[str, Any] | None = Field(
        None, description="若为 Finish 步骤，包含 verdict/confidence/reasoning"
    )


class VerifyResponse(BaseModel):
    """单条三元组验证响应。"""

    verification_id: str = Field(..., description="本次验证唯一 ID")
    triple: dict[str, Any] = Field(..., description="被验证的三元组（详细）")
    verdict: str = Field(
        ...,
        description="判定结果：supported / refuted / uncertain",
    )
    confidence: float = Field(..., description="聚合置信度 [0, 1]")
    reasoning: str = Field("", description="LLM 推理依据（自然语言）")
    evidence_chain: list[EvidenceItem] = Field(default_factory=list, description="证据链（按加权分数降序）")
    strategy: dict[str, Any] = Field(default_factory=dict, description="本次使用的验证策略")
    stopping_decision: dict[str, Any] = Field(default_factory=dict, description="终止原因")
    steps_taken: int = Field(0, description="实际执行步数")
    elapsed_ms: float = Field(0.0, description="总耗时（毫秒）")
    trajectory: list[TrajectoryStep] = Field(
        default_factory=list,
        description="完整 ReAct 轨迹（return_trajectory=False 时为空）",
    )

    @classmethod
    def from_result(
        cls,
        result: Any,
        return_trajectory: bool = True,
    ) -> "VerifyResponse":
        """从 `VerificationResult` 构造响应。

        Args:
            result: `VerificationResult` 实例
            return_trajectory: 是否包含完整轨迹
        """
        d = result.to_dict()
        trajectory = [TrajectoryStep(**step) for step in d.get("trajectory", [])] if return_trajectory else []
        evidence = [EvidenceItem(**ev) for ev in d.get("evidence_chain", [])]
        return cls(
            verification_id=d["verification_id"],
            triple=d["triple_detail"],
            verdict=d["verdict"],
            confidence=d["confidence"],
            reasoning=d["reasoning"],
            evidence_chain=evidence,
            strategy=d["strategy"],
            stopping_decision=d["stopping_decision"],
            steps_taken=d["steps_taken"],
            elapsed_ms=d["elapsed_ms"],
            trajectory=trajectory,
        )


class BatchVerifyItem(BaseModel):
    """批量验证中的单条结果（精简版，不含轨迹）。"""

    index: int = Field(..., description="在请求列表中的下标")
    verification_id: str = Field(..., description="本次验证唯一 ID")
    triple: dict[str, Any] = Field(..., description="被验证的三元组")
    verdict: str = Field(..., description="判定结果")
    confidence: float = Field(..., description="聚合置信度")
    reasoning: str = Field("", description="推理依据")
    steps_taken: int = Field(0, description="执行步数")
    elapsed_ms: float = Field(0.0, description="耗时（毫秒）")
    error: str | None = Field(None, description="错误信息（成功为 None）")


class BatchVerifyResponse(BaseModel):
    """`POST /sharp/verify/batch` 响应体。"""

    total: int = Field(..., description="总三元组数")
    succeeded: int = Field(..., description="成功验证数")
    failed: int = Field(..., description="失败数")
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="verdict 统计：{supported: n, refuted: n, uncertain: n}",
    )
    results: list[BatchVerifyItem] = Field(..., description="逐条验证结果")


# 轨迹查询与消融配置


class TrajectoryRecord(BaseModel):
    """历史轨迹记录（来自 M4 TrajectoryStore）。"""

    verification_id: str = Field(..., description="验证 ID")
    triple: dict[str, Any] = Field(..., description="三元组")
    verdict: str = Field(..., description="判定结果")
    confidence: float = Field(..., description="置信度")
    reasoning: str = Field("", description="推理依据")
    stopping_trigger: str = Field("", description="终止触发器")
    steps_taken: int = Field(0, description="步数")
    elapsed_ms: float = Field(0.0, description="耗时")
    timestamp: float = Field(..., description="时间戳")
    key_evidence: list[str] = Field(default_factory=list, description="关键证据片段")


class TrajectoryListResponse(BaseModel):
    """历史轨迹列表响应。"""

    total: int = Field(..., description="总记录数")
    records: list[TrajectoryRecord] = Field(..., description="轨迹列表")


class TrajectoryQueryRequest(BaseModel):
    """`POST /sharp/trajectory/query` 请求体。"""

    limit: int = Field(50, ge=1, le=500, description="返回记录数上限")
    verdict: str | None = Field(None, description="按 verdict 过滤")
    relation: str | None = Field(None, description="按关系类型过滤")


class AblationInfo(BaseModel):
    """消融模式信息。"""

    current_mode: str | None = Field(..., description="当前消融模式")
    available_modes: list[str | None] = Field(..., description="可选消融模式列表（None 表示完整 SHARP）")
    description: str = Field(..., description="当前模式说明")


class AblationUpdateRequest(BaseModel):
    """`POST /sharp/ablation` 请求体：切换消融模式。"""

    mode: str | None = Field(
        None,
        description=("消融模式：None / 'no_schema' / 'no_memory' / 'no_react' / 'no_toolset'"),
    )


class StatusResponse(BaseModel):
    """SHARP 服务状态响应。"""

    version: str = Field(..., description="SHARP 模块版本")
    enabled_components: dict[str, bool] = Field(..., description="各组件启用状态")
    tool_registry_size: int = Field(..., description="已注册工具数")
    trajectory_count: int = Field(..., description="历史轨迹数")
    ablation_mode: str | None = Field(..., description="当前消融模式")


__all__ = [
    "EntityRef",
    "RelationRef",
    "TripleRequest",
    "VerifyRequest",
    "BatchVerifyRequest",
    "EvidenceItem",
    "TrajectoryStep",
    "VerifyResponse",
    "BatchVerifyItem",
    "BatchVerifyResponse",
    "TrajectoryRecord",
    "TrajectoryListResponse",
    "TrajectoryQueryRequest",
    "AblationInfo",
    "AblationUpdateRequest",
    "StatusResponse",
]
