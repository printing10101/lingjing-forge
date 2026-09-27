# Vue 巨型组件拆分计划 V1.0

## 目标：能效最大化

| 能效维度 | 现状 | 目标 | 手段 |
|---|---|---|---|
| 首屏加载 | 1 个 1906 行 chunk 全量加载 | 子组件按需懒加载 | `defineAsyncComponent` + 路由级拆分 |
| 响应式精度 | 14 个 ref 共享一个作用域（全量重渲染） | 子组件独立响应式作用域 | props/dependency injection 传递 |
| 复用率 | 0 子组件可跨页面复用 | >10 个可复用子组件 | 提取到 `components/` 通用层 |
| 开发者效率 | 1906 行单文件，改一行要滚动全文件 | <400 行/文件，单责 | 按区块边界拆分 |
| 架构一致性 | 7 组件 feat=0（未接 features/ 层） | 7/7 接入 features/ 层 | 拆分时同步接入 |
| 测试覆盖 | 前端 0 单元测试 | 核心子组件 >50% 覆盖 | vitest + @vue/test-utils |
| Git 冲突面 | 7 个 1000+ 行文件 = 高频冲突 | 30-40 个 <400 行文件 = 低冲突 | 文件粒度缩小 |

## 当前基线

| 组件 | 行数 | template | script | style | store | 路由 |
|---|---|---|---|---|---|---|
| Simulation.vue | 1906 | 373 | 413 | 542 | projectStore | /simulation |
| TaskBoard.vue | 1213 | 280 | 269 | 457 | tasksStore | /task-board |
| WorkflowPanel.vue | 1161 | 332 | 530 | 288 | useWorkflow | /workflow-panel |
| WorkflowGuide.vue | 1146 | 180 | 278 | 345 | 无（纯 NLP 输入） | 子路由 /nl-modeling |
| Workspace.vue | 1102 | 12 | 472 | 141 | settingsStore | /workspace |
| RLAgent.vue | 1068 | 79 | 245 | 302 | rlAgentStore | /rl-agent |
| Explainability.vue | 1055 | 36 | 377 | 220 | explainabilityStore | /explainability |

共同特征：vimp=0（零子组件 import）、components_block=0、defineComponent=0、style scoped、路由级已 `() => import()` 懒加载。

## 拆分策略总览

```
第一档（优先拆）：Workspace > RLAgent > Explainability > WorkflowGuide
    理由：区块边界清晰（tab/card/步骤）、复用潜力高、feat=0 消除孤岛

第二档（逻辑优先）：WorkflowPanel
    理由：script 最重(530行)，问题在业务逻辑→优先抽 composable

第三档（降体量）：Simulation > TaskBoard
    理由：巨型但模糊、复用低→只拆分最大最独立的区块
```

## 阶段 P0：基础设施准备

### P0.1 磁盘空间释放
- 当前状态：0G 可用（df -h 确认）
- 需要：至少 2GB 用于 ~40 个新文件 + node_modules 重建
- 建议：清理 `node_modules/.cache`、`output/` 旧文件、浏览器缓存、Windows 临时文件

### P0.2 建立拆分模板
- 子组件文件夹结构：`components/{domain}/` 下建子目录
- 命名规范：`{Parent}{Section}.vue`（如 `SimulationParamsPanel.vue`）
- 拆分检查清单（文件模板）

### P0.3 features/ 层审计
- 审计 7 个组件各自调用的 API 端点
- 确认 features/ 层对应模块是否已完备
- 缺失则补充

---

## 阶段 P1：第一档——高 ROI 拆分（预计 12-20h）

### P1.1 Workspace.vue（1102行 → 目标 <200行主文件 + 8子组件）

**结构**：7 个 el-tab-pane + 2 个 el-card 的主控面板，template 仅 12 行（动态渲染 tab）

**拆分策略**：路由级拆分——把 7 个 tab 做成 Workspace 下的嵌套子路由

```
路由变更：
  /workspace          → Workspace.vue（仅 tab 导航壳子 + <router-view>）
  /workspace/overview → WorkspaceOverview.vue
  /workspace/projects → WorkspaceProjects.vue
  /workspace/data     → WorkspaceData.vue
  /workspace/monitor  → WorkspaceMonitor.vue
  /workspace/tools    → WorkspaceTools.vue
  /workspace/plugins  → WorkspacePlugins.vue
  /workspace/settings → WorkspaceSettings.vue
```

**能效收益**：
- 每个 tab 独立懒加载 chunk（首屏仅加载第一个 tab）
- tab 间独立 `keep-alive`，切换 tab 不销毁状态
- 每个 tab 子组件 <200 行，可独立测试

**接入 features/ 层**：每个 tab 子组件 import 对应 `@/features/workspace` API 模块

**子组件清单**：
| 文件名 | 预估行数 | 职责 | Store |
|---|---|---|---|
| WorkspaceTabsNav.vue | ~80 | 顶部 tab 导航（从 Workspace.vue 抽） | settingsStore |
| WorkspaceOverview.vue | ~150 | 概览 dashboard | settingsStore |
| WorkspaceProjects.vue | ~150 | 项目管理面板 | settingsStore |
| WorkspaceData.vue | ~120 | 数据浏览/导入 | settingsStore |
| WorkspaceMonitor.vue | ~130 | 机床/任务监控 | settingsStore |
| WorkspaceTools.vue | ~120 | 工具/后处理器 | settingsStore |
| WorkspacePlugins.vue | ~100 | 插件管理面板 | settingsStore |
| WorkspaceSettings.vue | ~90 | 工作区设置 | settingsStore |

### P1.2 RLAgent.vue（1068行 → 目标 <150行主文件 + 7子组件）

**结构**：6 个 el-card + header + section 分块

```
components/rl_agent/
  RlAgentHeader.vue         ~40  头部（标题 + 状态灯 + 操作按钮）
  RlAgentTrainingCard.vue   ~140 训练配置 + 启动
  RlAgentInferenceCard.vue  ~130 推理请求 + 结果
  RlAgentMetricsCard.vue    ~150 指标图表（echarts 复用）
  RlAgentConfigCard.vue     ~120 环境/策略参数
  RlAgentLogCard.vue         ~90 训练日志（虚拟滚动）
  RlAgentStatusBar.vue       ~50 底部状态栏
```

**能效收益**：
- MetricsCard 和 LogCard 可在 Explainability/CostDashboard 复用
- card 组件独立 `Suspense` + `defineAsyncComponent` 懒加载
- 每个 card <150 行，可独立测试

**接入 features/ 层**：引用 `@/features/rl-agent`（阶段 4 已建）

### P1.3 Explainability.vue（1055行 → 目标 <150行主文件 + 5子组件）

**结构**：header + 3 个分析面板

```
components/explainability/
  ExplainabilityHeader.vue          ~50  标题 + 模型/任务选择器
  ExplainabilityFeatureImportance.vue ~180 特征重要性图表 + 表格
  ExplainabilityShapValues.vue       ~200 SHAP 值瀑布图/热力图
  ExplainabilityDecisionPath.vue     ~150 决策路径可视化
  ExplainabilityResultsSummary.vue   ~120 底部汇总
```

**能效收益**：
- SHAP 值组件（echarts 重渲染）独立懒加载
- 与 RLAgent/MetricsCard 可跨页面复用图表组件
- 已存在 `contracts/explainability.ts` 接口定义（前端契约层已有）

**接入 features/ 层**：引用 `@/features/explainability`（阶段 4 已建）

### P1.4 WorkflowGuide.vue（1146行 → 目标 <100行主文件 + 通用 Step 组件）

**结构**：NL2CAD 对话式向导，17 个 div 区块 = 多步骤

**拆分策略**：不同于页面拆分——向导核心是步骤状态机。策略是抽**通用步骤组件**（`<WorkflowStep>`），各步骤内容作 slot/props 注入。

```
components/nl2cad/
  WorkflowProgressBar.vue  ~60  进度条 + 步骤描述
  WorkflowStep.vue         ~80  步骤容器（插槽：输入区/输出区/操作区）
  WorkflowInputPanel.vue   ~150 输入区（已有 NLInputPanel.vue 779行，复用或抽取）
  WorkflowOutputPreview.vue ~130 3D/CAD 预览
  WorkflowCodePanel.vue     ~140 G 代码/NC 预览
  WorkflowActionBar.vue     ~50 上一步/下一步/重置
```

**能效收益**：
- `WorkflowStep` 是通用组件，可用在别的多步流程（RuleEditor、ProcessPlanning）
- 拆分后 WorkflowGuide 主页 <100 行（纯步骤编排）
- 已有 `NLInputPanel.vue`（779 行）也是巨型组件——本次可一并购入拆分

---

## 阶段 P2：第二档——逻辑优先（预计 4-6h）

### P2.1 WorkflowPanel.vue（1161行 → 目标 ~250行 + 3 composables）

**问题**：script 530 行（全组件最大），大量工作流编排逻辑混在 `setup()` 里。

**策略**：先抽 composable，UI 后续再拆（如果必要）。

```
composables/
  useWorkflowState.ts        ~100 工作流状态管理（ref/computed）
  useWorkflowActions.ts      ~120 动作触发、验证、提交
  useWorkflowValidation.ts    ~80 节点/连线校验规则
```

**拆分后 WorkflowPanel.vue**：
- script 从 530 行 → ~200 行（3 个 composable 调用）
- 总行数 1161 → ~600（含 template + style 不变）

**能效收益**：
- 3 个 composable 可复用（useWorkflowState 可在 AgentDashboard 复用）
- 每个 composable 可独立单元测试（不依赖 Vue mount）
- 不涉及 UI 变更，回归风险极低

---

## 阶段 P3：第三档——降体量（预计 6-9h）

### P3.1 Simulation.vue（1906行 → 目标 ~200行主文件 + 6子组件）

**问题**：最大文件，style 542 行，但区块边界模糊（20 个顶层 div）。

**策略**：只拆分最明显的独立区块——不求完美，目标降至 <200 行主文件。

```
components/simulation/
  SimulationParamsPanel.vue   ~200 参数配置表单区（最独立）
  SimulationControls.vue      ~80  运行/暂停/重置按钮 + 进度
  SimulationResultChart.vue   ~250 结果图表（echarts/three.js）
  SimulationResultTable.vue   ~150 数值表格
  SimulationSummary.vue       ~100 结果摘要/导出
  SimulationLogPanel.vue      ~120 运行日志
```

### P3.2 TaskBoard.vue（1213行 → 目标 ~150行主文件 + 5子组件）

```
components/task_board/
  TaskBoardHeader.vue   ~50  标题 + 筛选 + 批量操作
  TaskFilterBar.vue     ~80  状态/优先级/时间筛选
  TaskList.vue          ~200 任务卡片列表（虚拟滚动）
  TaskCard.vue          ~100 单个任务卡片（复用）
  TaskDetailDrawer.vue  ~180 任务详情抽屉
```

---

## 阶段 P4：收尾——性能 + 测试 + 验证（预计 4-6h）

### P4.1 路由优化
- Workspace 改为嵌套子路由（7 个子路由，各自懒加载）
- 其余页面保持单路由，子组件用 `defineAsyncComponent` 懒加载
- 为高频切换的 tab/card 添加 `<keep-alive include="...">` 
- 为低频组件添加 `v-if` + `Suspense` 按需挂载

### P4.2 前端单元测试（对标后端 11 个架构测试）
```
tests/frontend/
  components/explainability/ExplainabilityFeatureImportance.test.ts
  components/rl_agent/RlAgentMetricsCard.test.ts
  components/simulation/SimulationParamsPanel.test.ts
  composables/useWorkflowState.test.ts
  composables/useWorkflowActions.test.ts
  composables/useWorkflowValidation.test.ts
  ... (≥15 测试文件，覆盖核心子组件)
```

### P4.3 回归验证清单
- [ ] 所有页面路由可正常导航
- [ ] 7 个巨型组件页面视觉一致性（截图对比前后）
- [ ] 各 store 数据流正常（Pinia devtools 验证）
- [ ] features/ 层 API 调用正确（Network 面板确认）
- [ ] `npm run build` 无错误
- [ ] `npm run type-check` 无错误
- [ ] `npm run test` 通过（新增 15+ 测试）

---

## 执行顺序与依赖

```
P0（基础设施）
 │
 ├─► P1.2 RLAgent ──► P1.3 Explainability ──► P1.4 WorkflowGuide
 │          │                  │                        │
 │          └──── 共享 MetricsCard 组件 ────────────────┘
 │
 ├─► P1.1 Workspace（独立，可与 P1.2 并行）
 │
 ▼ P1 完成后
 P2 WorkflowPanel（composable 抽离，最低风险）
 │
 ▼ P2 完成后
 P3.1 Simulation ──► P3.2 TaskBoard
 │
 ▼ P3 完成后
 P4 收尾（性能+测试+验证）
```

**P1.2/1.3/1.4 按顺序做**（因为有共享子组件如 MetricsCard）。**P1.1 可与 P1.2 并行**（唯一独立路由页）。

---

## 能效最大化验证指标

| 指标 | 拆分前 | 拆分后目标 | 测量方式 |
|---|---|---|---|
| 最大单文件行数 | 1906 | <400 | `wc -l` |
| >1000 行文件数 | 7 | 0 | find |
| 子组件复用数 | 0 | ≥10 | grep import |
| features/ 接入率 | 0% (0/7) | 100% (7/7) | grep feat |
| 前端测试覆盖 | 0 文件 | ≥15 文件 | find tests/frontend |
| 首屏 chunk 大小 | ~500KB (Simulation) | <150KB | vite build stats |
| fe 构建时间 | 基线 | 不变或减少 | time npm run build |

---

## 总工时估算

| 阶段 | 内容 | 估计工时 |
|---|---|---|
| P0 | 基础设施 + 磁盘释放 + 模板 | 1-2h |
| P1.1 | Workspace 路由级拆分 | 4-5h |
| P1.2 | RLAgent card 拆分 | 3-4h |
| P1.3 | Explainability 面板拆分 | 3-4h |
| P1.4 | WorkflowGuide 步骤拆分 + NLInputPanel | 3-4h |
| P2 | WorkflowPanel composable | 4-5h |
| P3.1 | Simulation 降体量 | 4-5h |
| P3.2 | TaskBoard 降体量 | 2-3h |
| P4 | 收尾（性能+测试+验证） | 4-6h |
| **合计** | | **28-38h** |

---

*制定时间：2026-08-01 | 关联：architecture_refactoring_plan.md（V3.0 架构重构路线图阶段D）*
