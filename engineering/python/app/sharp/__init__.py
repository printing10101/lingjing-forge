"""SHARP - Schema-Hybrid Agent for Reliable Prediction.

本模块实现哈工大 SCIR + 华为联合提出的 SHARP 范式（arXiv:2604.04190），
将其作为通用三元组验证服务落地到「灵境制造」平台。

设计目标
--------
- **Training-Free**：完全复用现有 KG / RAG / LLM Router 基础设施，不引入任何训练参数。
- **配置驱动**：四大组件（Schema 规划器 / Hybrid 工具集 / Memory 机制 / ReAct 循环）
  均可通过配置独立开关，便于消融实验。
- **证据链可追溯**：每次验证产出结构化证据链 JSON，记录每一步推理与工具调用。
- **接口对齐**：直接复用 `app.models.knowledge_graph` 的 4 实体 + 4 关系 Pydantic 模型，
  以及 `KnowledgeGraphQueryAPI` 与 `RagRetrievalEngine` 现有接口。

模块结构
--------
- `sharp.schema`       M1 领域 Schema、约束、战略规划器
- `sharp.tools`        M2 Hybrid Knowledge Toolset（KG + 文本 + 重排序）
- `sharp.react`        M3 ReAct 增强循环
- `sharp.memory`       M4 Memory-Augmented 机制
- `sharp.service`      M5 验证服务与批量验证
- `sharp.evaluation`   M6 FB15K-237 / Wikidata5M 评测与消融

参考
----
- 论文：arXiv:2604.04190（SHARP: Schema-Hybrid Agent for Reliable Prediction）
- 团队：哈工大 SCIR（Xinyan Ma, Ming Liu, Bing Qin）+ 华为（Dandan Tu）+ 北师大湾区
- 实验：FB15K-237 (+4.2%)、Wikidata5M-Ind (+12.9%)
"""

from __future__ import annotations

__version__ = "2.5.0"
__all__ = ["__version__"]
