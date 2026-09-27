# 前端全面重构方案（简洁而高效）

> 状态：**已落地（2026-09-17）** ｜ 日期：2026-09-16 ｜ 范围：`engineering/src/`
> 目标一句话：把 37 条路由收敛为约 20 个有真实入口的页面，删除约 1 万行死代码与克隆代码，统一"列表页骨架 / 统计卡 / 图表 / 详情抽屉"四套基础设施。

## 落地结果（2026-09-17，4 个 commit：3cef54a6 / 16d28fdf / 4a0e5e77 / d5d6f197）

- **体量**：源码 76,784 → 70,215 行（净删 ~6,600 行；Phase 0 单次提交删除 7,125 行）
- **路由**：38 条实路由 → 25 条（其余为兼容 redirect）；侧边栏 30 → 25 项
- **验证**：`pnpm build`（vue-tsc + vite）通过；vitest 82 文件 / 1866 用例全部通过
- **回滚点**：tag `backup/frontend-pre-refactor`

已完成项与偏差说明：
1. Phase 0 全部完成（UXDemo/CommandPalette/examples/4 死 store/3 死依赖/cutting-experience 整链删除）。
2. Phase 1 五组合并全部完成；Tab 懒挂载最终用**显式 v-if + visitedTabs** 实现，而非 el-tabs 的 `lazy`——测试环境 `el-*` 不会解析为组件（vitest.config 有意不引 ElementPlusResolver），`lazy` 行为不可测且会静默多发请求。
3. Phase 3 四项全部完成（Goals 空壳砍除、设备"参数设置"改行内入口修 bug、物料伪造卡删除、NL 建模→仿真经 sessionStorage 交接 G 代码）。
4. Phase 2 完成：图表接入路径归一 useEChart；四个命名陷阱目录改名收编（组件名不变）。
   **未做**：StatsCards 8→1 收编与"模型实验室"共享组件（涉及视觉回归，建议配手测/截图流程再做）；ListPageTemplate 大迁移（useDataTable 仍保留待启用）。
5. Phase 4 部分完成：5 个死 locale 区块已删（中英各 -250 行）+ 清理脚本 `scripts/cleanup_i18n_keys.mjs`；en 缺失 key 的补齐、knip CI 门禁为后续事项。

---以下为原始方案---

---

## 一、诊断结论

全前端（不含测试）约 **76,784 行**：views 544KB、components 1.8MB、stores 500KB。
臃肿不是"页面写多了"这么简单，而是四类病灶叠加：

### 病灶 1：死代码（可直接删，约 5,000 行）

| 对象 | 行数 | 证据 |
|---|---|---|
| `views/UXDemo.vue` + 路由 | 322 | 全局无任何入口，路由标题即"UX 功能演示"，8 个命令全部只弹 toast 不干活 |
| `components/CommandPalette/` | ~590 | 唯一挂载点在孤儿页 UXDemo；且 `CommandPalette.vue:177-181` 把 `state` 定义为 computed 硬编码 `visible:false`，`open()` 赋值不生效——面板永远打不开（双重死亡） |
| `examples/`（ExampleGallery + data + types） | ~1,880 | 唯一消费者 UXDemo |
| `stores/workflowTemplate.ts` | 519 | 全局零引用 |
| `stores/projectPackage.ts` | 604 | 无 import，仅 contracts 文档注释提及 |
| `stores/projectSync.ts` | 779 | 同上 |
| `stores/resourceCard.ts` | 727 | 同上 |
| 依赖 `markdown-it`、`pinia-plugin-persistedstate`、`@types/markdown-it` | — | src 无任何 import；settings 持久化是手写的 |
| `stores/cutting-experience.ts` + 孤儿路由 `/cutting-experience` | ~325 | 页面无任何导航入口（注意：与科研侧实测数据方向相关，见决策点） |

### 病灶 2：同一数据源拆成多个顶级页面

| 同源数据 | 被拆成的页面 | 结论 |
|---|---|---|
| `/api/v1/jobs` | TaskBoard（看板+列表双视图）+ TaskHistory（表格+分页+重跑） | TaskHistory 只是 TaskBoard 的另一种视图，合并 |
| `/production/dashboard` + `/production/stats` | Home（4 张 KPI 卡）+ ProductionReport（4 张汇总卡） | 4 个指标 3 个相同、读同一接口；Home"查看详情"就是跳 ProductionReport。典型"总览+报表"应为主从两 Tab |
| `/api/v1/plugins` | PluginMarket + PluginManager + PluginLogs | 三条路由同一实体：市场/已装/日志。日志是单个插件的子资源（`/plugins/{id}/logs`），做成并列顶级页 |
| `/templates/branches` + `/template_market` | TemplateMarket + TemplateDetail + BranchManager + UpdateCenter | 一个"模板分支"实体占 4 个顶级路由；且 TemplateMarket 的"模板列表"Tab 数据从未请求（死 UI） |
| agent 详情 | AgentDashboard（内嵌 AgentDetailPanel 弹窗）+ AgentDetail 独立页 | 同一详情两套 UI 两个入口 |

### 病灶 3：组件层重复

- **统计卡 8 种实现**：`base/StatsCards.vue`（通用版）+ `home/KpiCards.vue` + 4 个域"兼容壳"（approval/material/quality/equipment 的 *StatsCards，自注"兼容层"）+ `SimulationStatsRow` 与 `AgentDashboard` 内联手写（两者 CSS 类名完全同名，纯复制）+ `CostBudgetCards`/`ProductionSummaryCards` 手写卡片行。可收敛为 1 种。
- **9 个页面同构**："统计卡 + 筛选栏 + el-table + CRUD 弹窗"的骨架被手写了 9 遍（Material、PluginMarket、TemplateMarket、TaskBoard、Approval、Quality、TaskHistory、DialectManager、ProcessPlanning），全仓 7 个手写表格只有 1 个有分页；`composables/headless/useDataTable.ts` 写好了却 0 消费；base/ 目录没有 FilterBar/列表页模板。
- **三页克隆**：RLAgent / WorldModel / Explainability 是"360px 左列表 + 右详情"版式的复制改名（VersionList/VersionDetail 逐段对应，仅 CSS 前缀 wm-/rl- 不同）。
- **图表三条路径**：echarts 是唯一图表库，但 `ProductionTrendChart` 绕过 composable 直接 `echarts.init`，`WorkspaceTrainMonitor` 又 canvas 手绘 loss 曲线；`useEChart.ts` 只有 cost 目录在用。
- **命名陷阱目录**：`rule_edit` vs `rule_editor`（前者是后者的弹窗子件）、`simulation` vs `simulation_nc`（后者是前者 NC Tab 的内部子件）、`nl_input` vs `nl2cad`（前者是后者的聊天子件）——均非重复但极易误判，应改名收编。

### 病灶 4：半成品页面（要么修好要么砍）

| 页面 | 问题 |
|---|---|
| Goals.vue | Tab1 真实 API；GoalDetail(19 行)/TaskWizard(17 行)/AlignmentChecker(31 行)全是 `<div><slot/></div>` 空壳，Tab2/3/4 渲染为空白 |
| TemplateMarket.vue | "模板列表"Tab 的 `templates` ref 从未被赋值，永远空表 |
| EquipmentMonitor.vue | alarms/maintenancePlans 拉回后模板从未使用（死数据）；"参数设置"永远只编辑 `devices[0]` |
| MaterialManagement.vue | 第 4 张统计卡"采购中"是前端伪造的 `Math.min(low+out, 8)` |
| WorkflowPanel.vue | 内置模板只有一条硬编码 JSON |
| NLModeling.vue | "开始仿真"跳转丢弃 `currentModelPath`，建模→仿真链路实际未打通 |

### 其他工程问题

- i18n 单文件 3,500 行：zh-CN 2,931 个叶子 key，en 2,845 个——**存在 key 漂移**（部分 key 缺英文）。
- `router/createAppRouter.ts` 仅测试引用（可保留为测试基建）。
- 大文件：LayoutHeader 544 行（菜单+搜索+语言+用户区一体）、HealthCheck 536、WorkspaceTrainMonitor 422（轮询+canvas+UI 混杂）。

---

## 二、目标信息架构

### 侧边栏：6 组 30 项 → 6 组 17 项

```
总览
  └ 生产总览（Home：KPI + 概览 Tab + 报表 Tab + 导出）      ← 吸收 ProductionReport
制造流程（保持既有主线，与流程引导一致）
  ├ 自然语言建模   /nl-modeling
  ├ 工艺规划       /process-planning
  ├ 仿真模拟       /simulation
  └ 刀轨 NC        /toolpath-editor
任务与协作
  ├ 任务中心       /tasks            ← TaskBoard 吸收 TaskHistory（看板/列表双视图保留，列表补分页+重跑）
  ├ 工作流编排     /workflow-panel
  └ 审批中心       /approval-dashboard
资源与设备
  ├ 设备监控       /equipment-monitor
  ├ 物料管理       /material-management
  ├ 质量检测       /quality-inspection
  ├ 工艺规则       /rule-editor
  └ 后处理方言     /dialect-manager
模板与插件
  ├ 模板中心       /templates        ← TemplateMarket+Detail+BranchManager+UpdateCenter 四合一
  └ 插件中心       /plugins          ← Market+Manager+Logs 三合一
智能实验室
  ├ LNN 工作台     /workspace        （补导航入口，现仅深链可达）
  ├ 智能体管理     /agent-dashboard  （删独立详情页，统一内嵌面板）
  ├ 数据飞轮       /flywheel-dashboard （可加"实验快照"Tab 吸收 SnapshotPanel）
  └ 模型实验室     /models           （可选：RL/世界模型/可解释性 三 Tab；保守方案=保留三页但共享组件）
系统
  ├ 系统设置       /settings
  └ 关于           /about
```

**路由数：38 → 24（保守）/ 22（含模型实验室合并）。** 建议先做保守版；`/models` 合并在组件统一完成后按需再评估。

### 关键合并映射

| 旧页面 | 去向 | 处理 |
|---|---|---|
| `/task-history` (393行) | `/tasks` 列表视图 | 并入后删除；"重跑"与跳转 workspace 的深链保留 |
| `/production-report` (390行) | `/` 报表 Tab | 趋势图+记录表+工单表+CSV 导出整体搬入 |
| `/plugin-logs` (372行) | `/plugins` 日志 Tab（或插件详情抽屉） | 并入后删除 |
| `/plugin-market` (302行) | `/plugins` 市场 Tab | 并入后删除 |
| `/agent-detail/:id` (346行) | AgentDashboard 内嵌面板 | 删路由删页面，详情统一走面板/抽屉 |
| `/template-detail/:id` (285行) | `/templates` 详情抽屉 | 抽屉化 |
| `/branch-manager` (405行) | `/templates` 分支 Tab | 并入 |
| `/update-center` | `/templates` 更新入口（按钮/抽屉） | 并入 |
| `/snapshot-panel` | `/flywheel-dashboard` 快照 Tab | 并入（可选） |
| `/ux-demo`、`/cutting-experience` | 删除/下线 | 见决策点 |

### 旧路径兼容

所有被合并的路由在 `router/index.ts` 保留 `redirect`（如 `/task-history → /tasks?view=list`），避免桌面端已存的 localStorage 快捷方式、文档链接失效。一个版本周期后再清理。

---

## 三、组件层重构

1. **统计卡收编 8 → 1**：所有卡片走 `base/StatsCards.vue`（已支持 icon/type/click/subLabel 趋势）。删 5 个兼容壳与手写变体，KPI 字段映射下沉到调用方 props。
2. **列表页骨架**：新建 `components/base/ListPageTemplate.vue`（StatsCards + FilterBar + DataTable + 详情抽屉 + Pagination 槽位），**启用已有但零消费的 `useDataTable`**。9 个同构页按"最复杂的 TaskHistory"为基准迁移，迁移完成即获得统一分页。
3. **详情统一为 Drawer**：AgentDetail、TemplateDetail、TaskDetailDialog、插件详情等从"弹窗或独立页"统一为右侧抽屉，信息密度一致、可保留上下文。
4. **图表归一 `useEChart`**：`ProductionTrendChart`、`WorkspaceTrainMonitor` 迁移；仿真 canvas 渲染（热力/颜色映射）属可视化辅助，保留。
5. **模型实验室共享组件**：抽 `ModelVersionWorkbench`（左版本列表 + 右详情 + 操作区，插槽注入差异字段），RLAgent/WorldModel/Explainability 三页改为配置驱动。
6. **目录改名消除命名陷阱**：`rule_edit/` → `rule_editor/fields/`；`simulation_nc/` → `simulation/nc/`；`nl_input/` → `nl2cad/chat/`；`workflow_guide/` → `nl2cad/steps/`。
7. **i18n 拆域**：`locales/zh-CN.ts`(3,493行) 按导航域拆为模块目录 + 自动聚合入口；写 key 对账脚本修复中英漂移（en 缺 ~86 key），CI 加校验。

---

## 四、半成品治理（修复或砍除）

| 项 | 建议 |
|---|---|
| Goals 空 Tab | **砍** Tab2/3/4，只留目标树（真实可用部分），空壳组件删除；未来需要时再实装 |
| TemplateMarket 死列表 | 四合一重构时接 `/templates/branches` 真实数据 |
| EquipmentMonitor | 删死数据请求；参数设置改为按行传入设备 id（修 bug） |
| MaterialManagement | "采购中"卡改为后端真实字段或移除 |
| NLModeling→Simulation 断链 | 跳转时带上 `currentModelPath`，仿真页接收并预填 |
| WorkflowPanel 硬编码模板 | 接后端模板列表（后端已有 workflows API 则小改） |

---

## 五、分阶段实施（每阶段独立可交付、可回滚）

### Phase 0：纯删除（半天，零功能风险）
删 UXDemo+路由、CommandPalette 目录、examples/、4 个死 store、3 个死依赖（`pnpm remove markdown-it @types/markdown-it pinia-plugin-persistedstate`）。
验证：`pnpm build`（含 vue-tsc）+ `pnpm test` + 手测登录/首页/设置。
决策点：`cutting-experience`（孤儿但关联科研方向）——建议保留代码、暂不给入口，等实测数据功能就绪后挂到数据飞轮域。

### Phase 1：页面合并与导航重组（1-2 天）
按"任务中心 → 插件中心 → Home 报表 Tab → 模板中心 → AgentDetail 抽屉化"顺序逐个合并；每合并一个就在 navGroups 删对应入口、加 redirect。
验证：每步 build+test；合并完成后手测清单走一遍 17 个入口。

### Phase 2：组件归一（2-3 天）
StatsCards 收编 → ListPageTemplate + useDataTable 逐页迁移 → useEChart 归一 → 模型实验室共享组件 → 目录改名。
验证：类型检查 + 既有组件测试迁移 + 每页手测。

### Phase 3：半成品治理（1-2 天）
按上表逐项修复/砍除，每项一个 commit（含复现说明）。

### Phase 4：工程化收尾（1 天）
i18n 拆域 + 对账脚本 + CI 校验；引入 `knip`（或 ts-prune）进 CI 防死代码复发；LayoutHeader/HealthCheck/WorkspaceTrainMonitor 拆分；文档同步（AGENTS.md 仓库地图、docs-site）。

### 预期收益

- 路由 38 → 24，侧边栏 30 → 17 项，新用户首屏可理解面积减半
- 删除/合并约 **9,000–11,000 行**（死代码 ~5,000 + 合并冗余 ~3,000 + 兼容壳/克隆 ~1,500）
- 统计卡 8 → 1、图表路径 3 → 1、列表页骨架 9 套 → 1 套
- 构建产物：UXDemo/examples chunk（约 2,800 行源码）不再打包

---

## 六、风险与保障

- **回滚**：每阶段独立 commit 序列；Phase 1 开始前打 tag `backup/frontend-pre-refactor`。
- **兼容**：旧路由全部 redirect 一个版本周期；localStorage 键、Tour 引导选择器（`.sidebar-nav a[href=...]`）随导航重组同步更新——**App.vue 的 flowTourSteps 引用了 6 个侧边栏 href，导航重组时必须同步**。
- **测试**：合并页面时把 TaskHistory 的分页/重跑行为补 vitest 用例；StatsCards/ListPageTemplate 新组件带测试（仓库已有 vitest + @vue/test-utils）。
- **门禁**：Phase 4 把 knip、i18n key 对账加进 CI，防止臃肿回潮。
