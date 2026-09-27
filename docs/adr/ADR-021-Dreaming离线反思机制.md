# ADR-021: Dreaming 离线反思机制

**日期**: 2026-07-15  
**状态**: 已接受  
**决策者**: 项目独立开发者

---

## 背景

Dreaming（离线反思）机制借鉴神经科学"记忆巩固"理论：

- **Memory**：Agent 在工作中学习到的知识，存储在 Memory Store
- **Dreaming**：Agent 在 Session 间隙离线审查 Memory Store，执行去重合并、过时更新、跨 Session 洞察浮现
- **Outcomes**：Dream 浮现的洞察反馈到下一轮工作，形成闭环

若依赖外部托管服务实现，则需要云端 Memory Store 与 Session 存储，模型与调用协议均由服务方绑定，无法满足本项目"数据完全本地化"与离线运行的硬约束。

"灵境制造"项目需要离线反思能力以：
1. 从历史实验（MLflow）+ CAM 验证 + 审计日志中沉淀工艺知识
2. 跨 Session 发现切削参数推荐失败的潜在规律
3. 将洞察转化为可执行规则，反馈到下一轮推荐
4. 建立完整的 Memory + Dreaming + Outcomes 闭环

但项目硬约束：
- 数据完全本地化（无云端依赖）
- 本地 LLM（Ollama/LM Studio）替代云端模型
- CAM 二次验证始终 True、SUCCEEDED 任务禁删、HRC52 pending_calibration 强制降低置信度
- 学术诚信（D-2）：AR-02 修复前数据排除出论文

---

## 决策

在 `python/app/dreaming/` 目录下构建本地化 Dreaming 模块，将各概念组件映射到项目已有基础设施：

| 概念组件 | 本地实现 |
|---------------|-----------|
| Memory Store（工作区级记忆文档） | `LocalMemoryStore` → GraphStore + Git 版本管理 |
| Sessions（工作会话历史） | `SessionExtractor` → MLflow runs + CAM report + audit_log + cutting_store |
| LLM 反思 | `DreamReflector` → `ProviderRouter` → 本地 LLM（Ollama/LM Studio） |
| 异步 Dream Job | 后续 `HeartbeatScheduler` cron 调度（P1 阶段） |
| Dream 输出（新 Memory Store） | Git commit hash 作为 `MemoryVersion`（不可变快照） |
| Outcomes 反馈 | `RuleSynthesizer` → 规则草稿 → 沙箱验证 → 灰度应用（P2 阶段） |
| Reflection Report | `ReportGenerator` → Markdown 文档（含审稿人复核信息） |

### 模块结构

```
python/app/dreaming/
├── __init__.py                  # 模块入口（PEP 562 延迟导入，导出 P0/P1/P2 全部组件）
├── memory_store.py              # LocalMemoryStore + Git 版本管理（P0）
├── session_extractor.py         # 4 数据源提取 + ProjectSession 归一化（P0）
├── reflector.py                 # 反思核心（去重/更新/洞察浮现）（P0）
├── rule_synthesizer.py          # 洞察 → 规则草稿 + 硬约束校验（P0）
├── report_generator.py          # Markdown 反思报告（P0）
├── cli.py                       # reflect/extract/report/version 子命令（P0）
├── audit_integration.py         # 反思决策写入 audit_log 哈希链（P1）
├── scheduler_adapter.py         # HeartbeatScheduler cron 调度接入（P1）
├── rule_validator.py            # 规则草稿沙箱验证器（P1）
├── apply_rules.py               # 规则应用入口 + 回滚（P1）
├── progressive_publisher.py     # 规则灰度发布（shadow→canary→rolling→full）（P2）
├── effectiveness_metrics.py     # 规则效果度量（准确率/召回率/误报率）（P2）
├── rollback_manager.py          # 异常检测与自动回滚（含冷却期）（P2）
└── closed_loop.py               # Outcomes 反馈闭环（DSmF + TaskRouter）（P2）
```

### 配置层

`DreamingConfig` 已嵌入 `python/app/config/__init__.py` 的 `AppConfig` 顶层聚合（字段 `dreaming`），所有参数支持 `LNN_DREAM_*` 环境变量覆盖，`__post_init__` 强制 3 项硬约束：
- `cam_validation_required` 始终 True
- `allow_delete_succeeded` 始终 False
- `k_s_direct_passthrough` 始终 True

### 反思三阶段

1. **去重（deduplicate）**：按 entity 分组，合并 content 相同的 memory 条目，保留 `validation_count` 最高的节点，其余标记 `deprecated`
2. **过时更新（update stale）**：用失败 Session 修正旧 memory，降低置信度；HRC52 pending_calibration 强制降低至 ≤0.3；CAM 验证失败标记 `requires_revalidation`
3. **洞察浮现（surface insights）**：优先调用 LLM 反思，LLM 不可用时降级为规则统计（材料失败率、CAM 失败聚集、SUCCEEDED 提醒）

### 命令行入口

```bash
# 完整反思流程
python -m app.dreaming.cli reflect --lookback-days 30 --instructions "..."

# 仅提取 Session
python -m app.dreaming.cli extract --output sessions.json

# 从已保存结果生成报告
python -m app.dreaming.cli report --reflection reflection.json
```

---

## 理由

### 考虑的方案

1. **方案 A**：接入外部托管的 Dreaming 云服务
   - 优点：功能完整，无需自行实现反思逻辑
   - 缺点：违反"数据完全本地化"硬约束；依赖云端模型；无法离线运行

2. **方案 B**：不引入 Dreaming，仅依赖 RAG 检索
   - 优点：零新增代码
   - 缺点：无法跨 Session 发现潜在规律；无法自动去重/更新 memory；缺乏完整的自我改进闭环

3. **方案 C（采纳）**：本地化映射，复用项目已有基础设施
   - 优点：数据完全本地化；复用 GraphStore + ProviderRouter + MLflow；硬约束可强制嵌入；LLM 不可用时可降级为规则统计
   - 缺点：本地 LLM 反思质量低于云端旗舰模型；需要 P1/P2 阶段完成调度和闭环

### 选择方案 C 的原因

- **硬约束对齐**：GraphStore 的 RLock 保证并发安全；Git 版本管理提供不可变快照；audit_log 哈希链可记录所有反思决策
- **学术诚信**：`is_ar_02_pre_fix` 标记过滤修复前数据；每条规则记录 `supporting_sessions` 供审稿人复核
- **工程可行性**：本地 LLM（Qwen/Llama）虽反思质量有限，但规则统计降级可保证基础功能；后续可随 Provider 升级自动提升
- **与项目架构一致**：复用 ProviderRouter 路由策略、MLflow 实验追踪、GraphStore 知识图谱，避免重新造轮子

---

## 后果

### 积极影响

- 建立完整的 Memory + Dreaming + Outcomes 自我改进闭环
- 跨 Session 发现切削参数推荐失败的潜在规律
- Memory Store 自动去重和过时更新，避免知识膨胀
- 所有反思决策有 Git 版本 + audit_log 哈希链双重审计
- LLM 不可用时可降级为规则统计，保证可用性

### 消极影响

- 本地 LLM 反思质量低于云端旗舰模型（通过规则统计降级缓解）
- ~~需要定期手动触发反思（P1 阶段接入 HeartbeatScheduler 后可自动化）~~ → 已由 `scheduler_adapter.py` 解决
- ~~规则草稿需经过沙箱验证才能应用（P2 阶段实现 RuleValidator）~~ → 已由 `rule_validator.py` 解决
- Git 操作在无 Git 环境的项目中不可用（降级为内存版本，不持久化）

### 技术影响

- 新增 `python/app/dreaming/` 模块，14 个文件，约 7000 行代码（P0+P1+P2 全量交付）
- 新增 `DreamingConfig` 配置类（嵌入 `AppConfig.dreaming`，3 项硬约束强制）
- 复用 GraphStore / ProviderRouter / MLflow experiment_tracker / DempsterShaferFusion / TaskRouter 五个已有模块
- 新增节点类型 `dreaming_memory` 和关系类型 `CONSOLIDATED_FROM`
- 新增 `AIModule.DREAMING` 审计日志枚举值（写入哈希链）
- Git commit hash 作为 MemoryVersion ID
- 输出目录：`python/outputs/dreaming/{reports,rules,metrics_samples,publication_records,rollback_history,closed_loop_state}/`

### 业务影响

- 提升切削参数推荐准确性（通过跨 Session 学习）
- 降低人工审核负担（自动去重 + 过时更新）
- 为论文提供可追溯的实验反思证据链
- 运营成本：零云端依赖，纯本地运行

---

## 实施计划

### P0 阶段（已完成 2026-07-15）

- [x] `dreaming/__init__.py`：模块入口 + PEP 562 延迟导入
- [x] `memory_store.py`：LocalMemoryStore + Git 版本管理
- [x] `session_extractor.py`：4 数据源提取 + ProjectSession 归一化
- [x] `reflector.py`：三阶段反思核心（去重/更新/洞察浮现）
- [x] `rule_synthesizer.py`：洞察 → 规则草稿 + 硬约束校验
- [x] `report_generator.py`：8 章节 Markdown 报告
- [x] `cli.py`：reflect/extract/report/version 子命令

**验收标准**：
- 能从 MLflow + CAM + audit 提取 ≥10 个 Session
- 生成 Markdown 反思报告
- Git commit 生成 memory version
- LLM 不可用时降级为规则统计

### P1 阶段（已完成 2026-07-15）

- [x] `audit_integration.py`：新增 `AIModule.DREAMING` 枚举值，反思决策写入哈希链
- [x] `scheduler_adapter.py`：接入 HeartbeatScheduler cron 调度（默认 `0 2 * * *` 每日凌晨 2 点）
- [x] `rule_validator.py`：沙箱验证规则草稿（语法 + 硬约束 + 行为模拟）
- [x] `apply_rules.py`：规则应用入口（含 `RuleApplicator` + `RollbackResult` 回滚结构）

**验收标准**：
- 定时自动触发反思（如每日凌晨 2 点）
- 反思决策写入 audit_log 哈希链
- 规则草稿沙箱验证通过率可统计

### P2 阶段（已完成 2026-07-15）

- [x] `progressive_publisher.py`：规则灰度发布（`shadow → canary → rolling_10 → rolling_50 → full` 五级）
- [x] `rollback_manager.py`：异常检测与自动回滚（含 24h 冷却期 + 三级严重级别：硬约束/生产异常/指标恶化）
- [x] `effectiveness_metrics.py`：规则效果度量（accuracy/recall/FPR/error_rate/conflict/insufficient_data 标记）
- [x] `closed_loop.py`：Outcomes 反馈闭环（`DempsterShaferFusion` + `TaskRouter.update_outcome`）

**验收标准**：
- 规则灰度应用（shadow 0% → canary 1% → rolling_10 10% → rolling_50 50% → full 100%）
- 效果度量反馈到下一轮 Dreaming（`record_rule_outcome` 便捷函数）
- 异常规则自动回滚（硬约束违反立即回滚，不等指标窗口）

### 配置层（已完成 2026-07-15）

- [x] `config/__init__.py` 新增 `DreamingConfig` 类
- [x] `AppConfig.dreaming` 字段聚合
- [x] `__post_init__` 强制 3 项硬约束：`cam_validation_required` / `allow_delete_succeeded` / `k_s_direct_passthrough`
- [x] 环境变量前缀 `LNN_DREAM_*`（12-Factor App 一致性）

**验收标准**：
- `python -m py_compile app/config/__init__.py` 通过
- `from app.config import config; config.dreaming.enabled` 可正常读取
- 3 项硬约束在环境变量绕过时强制重置并记录 WARNING

### P2 全链路验证（已完成 2026-07-15）

- [x] P2 类导入验证：`from app.dreaming import ClosedLoop, ProgressivePublisher, EffectivenessMetricsCollector, RollbackManager` → `P2_IMPORT_OK`
- [x] P2 便捷函数导入验证：`run_closed_loop / record_rule_outcome / publish_rule / promote_rule / demote_rule / collect_rule_metrics / record_outcome_sample / rollback_rule / monitor_and_rollback` → `P2_FUNCS_OK`
- [x] DreamingConfig 实例化验证：3 项硬约束强制生效（`cam_validation_required=True / allow_delete_succeeded=False / k_s_direct_passthrough=True`）

### 外部依赖

- Git（MemoryVersion 持久化）
- MLflow（Session 数据源，未安装时降级）
- 本地 LLM Provider（Ollama/LM Studio，未配置时降级为规则统计）
- GraphStore + networkx（必需）

---

## 相关文档

- [ADR-TEMPLATE](./ADR-TEMPLATE.md)：ADR 编写模板
- [ADR-020](./ADR-020-GUSH3R借鉴思路落地实践方案.md)：GUSH3R 借鉴思路（与本 ADR 同期）
- 项目记忆 `project_memory.md`：硬约束清单
- D-2 学术诚信约束：论文实验数据收集模板

---

## 硬约束对齐

本 ADR 实现严格遵守以下项目硬约束：

| 约束 | 实现位置 | 验证方式 |
|------|---------|---------|
| cam_validation_required 始终 True | `rule_synthesizer.py` `_validate_hard_constraints` + `DreamingConfig.__post_init__` | 拒绝 `skip_cam_validation` 动作；环境变量绕过时强制重置 |
| SUCCEEDED 禁删 | `rule_synthesizer.py` `_validate_hard_constraints` + `DreamingConfig.__post_init__` | 拒绝 `unlock_succeeded` 动作；环境变量绕过时强制重置 |
| K_s → cutting_force_coeff 直接传递 | `DreamingConfig.__post_init__` | `k_s_direct_passthrough` 始终 True，不二次拟合 |
| HRC52 pending_calibration 降低置信度 | `reflector.py` `_update_stale_entries` | 强制 confidence ≤ 0.3 |
| 单轮审核状态机 | `reflector.py` 仅修改 memory，不触碰任务状态 | 反思不调用 cutting_store 写接口 |
| 所有反思决策写入 audit_log 哈希链 | `audit_integration.py`（P1 已完成） | 新增 `AIModule.DREAMING` 枚举值 |
| AR-02 修复前数据排除（D-2） | `session_extractor.py` `extract_sessions` | `is_ar_02_pre_fix` 标记 + 默认过滤 |
| 规则不直接生效 | `rule_synthesizer.py` 状态机 `draft → validated → applied → deprecated` | 需沙箱验证 + 灰度发布 |
| 灰度发布硬约束 | `progressive_publisher.py` 五级灰度（shadow→canary→rolling_10→rolling_50→full） | 每级晋级需 accuracy ≥ 0.75 且 sample_size ≥ 10 |
| 硬约束违反立即回滚 | `rollback_manager.py` 三级严重级别 | 硬约束违反不等指标窗口，直接降级到 deprecated |

---

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|----------|--------|
| 2026-07-15 | 初始版本，P0 阶段实现完成 | 项目独立开发者 |
| 2026-07-15 | P1 阶段完成：audit_integration + scheduler_adapter + rule_validator + apply_rules | 项目独立开发者 |
| 2026-07-15 | P2 阶段完成：progressive_publisher + effectiveness_metrics + rollback_manager + closed_loop + __init__.py P2 导出 | 项目独立开发者 |
| 2026-07-15 | 配置层完成：DreamingConfig 嵌入 AppConfig.dreaming，3 项硬约束强制；P2 全链路导入验证通过 | 项目独立开发者 |
