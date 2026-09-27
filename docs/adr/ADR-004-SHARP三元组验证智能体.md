# ADR-004: 引入 SHARP 三元组验证智能体

**日期**: 2026-07-05  
**状态**: 已接受  
**决策者**: 灵境制造架构组、知识图谱模块负责人

---

## 背景

灵境制造 V2.5.0 已有的知识图谱（`KnowledgeGraphQueryAPI` + `GraphStore` + NetworkX MultiDiGraph）
存储了大量由 RAG 文献抽取、用户手工录入、实验数据沉淀得到的三元组，包括：

- 4 类实体：`Material` / `Tool` / `Feature` / `Process`
- 4 类关系：`SUITABLE_FOR_MATERIAL` / `SUITABLE_FOR_FEATURE` / `APPLIED_TO` / `USED`

随着图谱规模扩大，出现了三类问题：

1. **冲突三元组并存**：同一 `(head, relation, tail)` 可能来自文献 A（confidence=0.7）
   与文献 B（confidence=0.9），但缺乏统一的仲裁机制。
2. **新增三元组质量参差**：LLM 抽取的三元组置信度难以解释，缺乏可追溯的证据链。
3. **冷启动困难**：用户提问"6061-T6 适不适合用 ϕ6 立铣刀加工？"时，即使图谱中已有
   相关节点，也无法给出带证据支持的判定。

哈工大 SCIR + 华为联合发表的 SHARP 论文（arXiv:2604.04190,
*Schema-Hybrid Agent for Reliable Prediction*）提出了一种 training-free 的智能体范式，
通过 Schema-Aware 规划 + Hybrid Knowledge Toolset + Memory-Augmented + ReAct 循环
对三元组进行验证，在 FB15K-237 上 +4.2%、Wikidata5M-Ind 上 +12.9%。
该范式与灵境制造现有基础设施（KG + RAG + LLM Router）天然契合，因此决定工程化落地。

## 决策

在 `python/app/sharp/` 下新增完整 SHARP 模块，作为 V2.6.0 的核心增量。
**严格遵循 training-free 原则**——不训练任何新模型，全部复用现有 KG / RAG / LLM Router
基础设施，将 SHARP 论文的 4 大组件完整落地：

1. **Schema-Aware 战略规划器**（`sharp/schema/strategic_planner.py`）
2. **Hybrid Knowledge Toolset**（`sharp/tools/`，8 个工具 + 注册表 + 证据重排器）
3. **ReAct 增强循环**（`sharp/react/`，主循环 + 轨迹记录 + 6 重终止条件 + Prompt 模板）
4. **Memory-Augmented 机制**（`sharp/memory/`，JSONL 轨迹存储 + 4 维相似度检索 + Prompt 注入）

对外通过 `/api/v1/sharp/*` 共 8 个 REST 端点暴露能力，全部走 `SharpService` 单例，
懒加载 LLMRouter / KnowledgeGraphQueryAPI / RagRetrievalEngine。

## 理由

### 考虑的方案

1. **方案 A：仅做 LLM 单次推理验证**
   - 优点：实现简单，1 次 LLM 调用即可返回 verdict
   - 缺点：缺乏证据链；无法解释；与论文 baseline 相当，准确率上限低；
     无法利用现有 KG / RAG 沉淀；冷启动场景易产生幻觉

2. **方案 B：基于规则的传统三元组验证**
   - 优点：可解释性强，无 LLM 成本
   - 缺点：无法处理语义层面的冲突；规则维护成本高；
     无法利用 LLM 的常识推理能力；扩展到新关系类型需重写规则

3. **方案 C：完整 SHARP 落地（已选）**
   - 优点：training-free，不增加训练成本；4 组件可独立消融，便于学术对照；
     产出结构化证据链 JSON，可直接驱动前端可视化；复用现有 KG/RAG/LLM Router，
     无新基础设施依赖；与项目"机械 + AI"研究方向一致，可作为论文工程附录
   - 缺点：单次验证需多次 LLM 调用（ReAct 循环），延迟较高（典型 3-8 秒）；
     代码复杂度显著上升（约 2500 行）；Memory-Augmented 需要磁盘存储，
     多用户共享轨迹可能存在隐私考量

### 选型权衡

- 方案 A 无法满足"可解释 + 可追溯"的核心诉求，且放弃了已有 KG/RAG 资产
- 方案 B 在 4 类关系 + 4 类实体的领域本体下规则爆炸，且无法处理 LLM 抽取的
  带置信度三元组
- 方案 C 虽然复杂度上升，但 4 组件解耦清晰，可通过 `ablation_mode` 配置降级；
  `no_react` 模式即等价于方案 A，`no_toolset` 模式可作规则基线对照

## 后果

### 积极影响

- 三元组验证准确率预期提升（参考论文 FB15K-237 +4.2% 基线）
- 每次验证产出结构化证据链（含 KG/文本/LLM 三源加权），
  可直接驱动前端"为什么 supported"的可视化
- Memory-Augmented 让相似三元组验证越来越快、越来越准（轨迹复用）
- 为后续撰写 LTC + 颤振预测论文提供工程附录素材（"知识图谱验证模块"）

### 消极影响

- 单次验证延迟从 "<1s（直接 KG 查询）" 上升到 "3-8s（ReAct 循环）"
- 增加 ~2500 行代码，需要持续维护
- 轨迹文件 `~/.lingjing/sharp/trajectories.jsonl` 默认上限 1000 条，
  超过后淘汰旧记录，可能丢失长期记忆（已在 `TrajectoryStore` 文档中说明）

### 技术影响

- **新增依赖**：无（全部复用现有 LLMRouter / KG / RAG / NetworkX / Pydantic）
- **配置变更**：`config.py` 新增 `SharpConfig` dataclass，10 个字段全部支持
  `LNN_SHARP_*` 环境变量覆盖；`AppConfig.sharp` 字段
- **API 表面**：`/api/v1/sharp/*` 共 8 端点，权限分为 `sharp:read` / `sharp:write`
- **权限模型**：新增 `sharp:read` 和 `sharp:write` 两个权限点，需在 RBAC seed
  中登记（详见实施计划）
- **存储**：轨迹 JSONL 默认路径 `~/.lingjing/sharp/trajectories.jsonl`，
  可通过 `SHARP_TRAJECTORY_PATH` 环境变量覆盖

### 业务影响

- 用户可在前端直接提问"X 适合 Y 吗？"，得到带证据的可解释答案
- 知识图谱质量得到持续验证，冲突三元组可被 SHARP 自动识别并降权
- 为后续"工艺推荐"功能提供可靠的三元组基础（避免基于错误三元组推荐）

## 实施计划

### 已完成里程碑（M1-M6）

| 里程碑 | 内容 | 关键文件 | 状态 |
|--------|------|----------|------|
| M1 | Schema 基础 | `sharp/schema/{domain_schema,schema_constraints,strategic_planner}.py` | |
| M2 | Hybrid Knowledge Toolset | `sharp/tools/{base,kg_tools,text_tools,llm_tools,reranker,tool_registry}.py` | |
| M3 | ReAct 增强循环 | `sharp/react/{react_loop,trajectory_recorder,stopping_criteria,prompt_templates}.py` | |
| M4 | Memory-Augmented 机制 | `sharp/memory/{trajectory_store,similarity_retriever,memory_augmentor}.py` | |
| M5.1 | Pydantic 请求/响应模型 | `sharp/schemas.py` | |
| M5.2 | SharpService 单例 | `sharp/service.py` | |
| M5.3 | FastAPI 路由（8 端点） | `api/v1/sharp.py` | |
| M5.4 | main.py 注册路由 | `app/main.py` | |
| M5.5 | import 与端到端验证 | 8 路由全部注册，6 项本地测试通过 | |
| M6.1 | SharpConfig 配置块 | `app/config.py` | |
| M6.2 | ADR 文档 | `docs/adr/ADR-004-SHARP三元组验证智能体.md` | |

### 后续运维任务（非阻塞）

- [ ] 在 RBAC seed 脚本中登记 `sharp:read` / `sharp:write` 权限点
- [ ] 在前端权限管理 UI 中暴露 SHARP 相关权限
- [ ] 接入真实 LLM 后端（Ollama / 云端 API）进行端到端 ReAct 循环压测
- [ ] 评估轨迹文件长期增长，必要时引入归档机制

## 关键设计约束

### 接口对齐原则

SHARP 新代码**必须直接复用**现有基础设施的接口，禁止绕开：

- **KG 查询**：`KnowledgeGraphQueryAPI`（来自 `app.api.v1.knowledge_graph._get_query_api`）
- **RAG 检索**：`RagRetrievalEngine`（来自 `app.rag.routes._get_rag_engine`）
- **LLM 调用**：`LLMRouter.chat_completion(...)`（来自 `app.ai.llm.router.get_router`）

这一原则保证了 SHARP 不会引入新的单例争用、不会破坏现有预热逻辑。

### 消融模式语义

5 种消融模式（`None` / `no_schema` / `no_memory` / `no_react` / `no_toolset`）
全部通过 `SharpService.set_ablation_mode()` 在运行时切换，会重建 pipeline：

| 模式 | Schema 规划器 | Memory 增强 | Hybrid Toolset | ReAct 循环 |
|------|---------------|-------------|----------------|------------|
| `None` | | | | |
| `no_schema` | （统一策略） | | | |
| `no_memory` | | | | |
| `no_react` | | （仅查询） | | （单次 LLM 推理） |
| `no_toolset` | | | （仅 LLM 推理工具） | |

### 证据来源加权

`EvidenceReranker` 中的 `SOURCE_WEIGHTS`：

```python
SOURCE_WEIGHTS = {
    "kg": 0.95,        # 知识图谱（人工/RAG 抽取并验证过）
    "experiment": 0.9, # 实测数据（PHM2010 / 6061-T6 自采）
    "literature": 0.75,# 文献检索（RAG 命中）
    "llm": 0.6,        # LLM 推理（无外部证据）
    "unknown": 0.5,    # 未知来源
}
```

权重在 `aggregate_confidence` 中按证据数量加权平均，并在 `react_loop.py`
中被解包为 `confidence` 字段。

### 节点 ID 规范

所有 KG 节点 ID 必须匹配 `^[a-zA-Z_][a-zA-Z0-9_.\-]{0,127}$`，
格式为 `<type>-<slug>`，例如 `material-6061-t6` / `tool-endmill-d6`。
此规范由 `SchemaConstraints` 强制校验，违反时返回 400。

## 相关文档

- 论文：SHARP: Schema-Hybrid Agent for Reliable Prediction（arXiv:2604.04190）
- 上游 KG 模块：`python/app/knowledge_graph/`
- 上游 RAG 模块：`python/app/rag/`
- 上游 LLM Router：`python/app/ai/llm/router.py`
- 相关 ADR：
  - [ADR-001-LNN-AI引擎选型](ADR-001-LNN-AI引擎选型.md)
  - [ADR-002-FastAPI后端框架选型](ADR-002-FastAPI后端框架选型.md)
  - [ADR-003-SQLite主数据库选型](ADR-003-SQLite主数据库选型.md)
- 变更摘要：`docs/变更摘要/`（V2.6.0 待撰写）

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|----------|--------|
| 2026-07-05 | 初始版本（M1-M6 全部完成） | 灵境制造架构组 |
