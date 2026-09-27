/**
 * API 路径配置
 * 统一管理所有 API 基础路径，避免硬编码
 */

export const API_CONFIG = {
  /** 版本 1 基础路径 */
  V1: "/api/v1",

  /** 成本预算模块 */
  COST_BUDGET: "/api/v1/cost-budget",

  /** 设备监控模块 */
  EQUIPMENT: "/api/v1/equipment",

  /** 物料管理模块 */
  MATERIALS: "/api/v1/materials",

  /** 质量检验模块 */
  QUALITY: "/api/v1/quality",

  /** 生产报表模块 */
  PRODUCTION: "/api/v1/production",

  /** 工艺路线模块 */
  PROCESS_ROUTES: "/api/v1/process-routes",

  /** 代理管理模块（后端注册于 /agents，无 /api/v1 段） */
  AGENTS: "/agents",

  /** 模板更新模块 */
  TEMPLATES_UPDATES: "/api/v1/templates/updates",

  /** 项目管理模块（后端注册于 /api/projects，无 v1 段） */
  PROJECTS: "/api/projects",

  /** 任务管理模块（通用任务创建/查询使用 jobs 接口） */
  TASKS: "/api/v1/jobs",

  /** 模型管理模块 */
  MODELS: "/api/v1/models",

  /** 知识库模块 */
  KNOWLEDGE: "/api/v1/knowledge",

  /** 仿真模块 */
  SIMULATION: "/api/simulation",

  /** 系统配置 */
  SYSTEM: "/api/v1/system",

  /** 用户认证 */
  AUTH: "/api/v1/auth",

  /** LNN 模型服务 */
  LNN: "/api/v1/lnn",

  /** 插件管理 */
  PLUGINS: "/api/v1/plugins",

  /** 用户主权审计日志 */
  USER_SOVEREIGNTY: "/api/v1/user-sovereignty",

  /** 治理审批 */
  GOVERNANCE: "/api/v1/governance",

  /** 任务作业流 */
  JOBS: "/api/v1/jobs",

  /** DAG 工作流编排（ADR-005 阶段 1） */
  WORKFLOWS: "/api/v1/workflows",

  /** 数据集 / 版本 / 血缘（ADR-005 阶段 2） */
  DATASETS: "/api/v1/datasets",

  /** 实验快照 / 一键复现（ADR-005 阶段 2） */
  SNAPSHOTS: "/api/v1/snapshots",

  /** 推理服务 */
  REASONING: "/api/v1/reasoning",

  /** 健康检查 */
  HEALTH: "/api/health",

  /** 指标监控 */
  METRICS: "/api/metrics",

  /** 工艺规则管理（后端注册于 /api/rules，无 v1 段） */
  RULES: "/api/rules",

  /** STEP 文件导入（后端注册于 /api/import/step，无 v1 段） */
  IMPORT: "/api/import",

  /** 目标对齐管理 */
  GOAL_ALIGNMENT: "/api/v1/goal-alignment",

  /** 目标对齐管理（features/goals 使用，与 GOAL_ALIGNMENT 同后端） */
  GOALS: "/api/v1/goal-alignment",

  /** DXF 文件导入 */
  DXF: "/api/dxf",

  /** NL-to-CAD 自然语言建模 */
  NL2CAD: "/api/v1/nl2cad",

  /** LLM Provider 网关（多后端 LLM 管理） */
  LLM_PROVIDERS: "/api/v1/llm-providers",

  /** 工艺理解与知识问答（基于 LLM 的工艺咨询/故障诊断/方案生成） */
  PROCESS_UNDERSTANDING: "/api/process-understanding",

  /** 数据飞轮（ADR-005 阶段 4：反馈闭环 + 模型热更新） */
  FLYWHEEL: "/api/v1/flywheel",

  /** 工作流模板市场（ADR-010 阶段 6 p6-1：发布 / 列表 / 搜索 / 下载 / 评分 / 下架） */
  WORKFLOW_TEMPLATES: "/api/v1/workflow-templates",

  /** 模板市场（features/template-market 使用，后端注册于 /api/v1/template_market） */
  TEMPLATES: "/api/v1/template_market",

  /** 项目级 Git 同步（ADR-011 阶段 6 p6-2：项目 / 资源引用 / commit/push/pull/clone / 同步记录） */
  PROJECT_SYNC: "/api/v1/project-sync",

  /** 资源卡片（ADR-012 阶段 6 p6-3：模型产物 + 数据集 README + 卡片聚合 + lineage 摘要） */
  RESOURCE_CARDS: "/api/v1/resource-cards",

  /** 项目导入导出（ADR-015 阶段 6 p6-4：.lomo 包格式导出/导入/校验/预览 + 记录列表） */
  PROJECT_PACKAGES: "/api/v1/project-packages",

  /** 可解释性可视化（ADR-016 阶段 7 p7：隐状态投影 + 门控动力学 + 反事实 + 置信度 + 对比） */
  EXPLAINABILITY: "/api/v1/explainability",

  /** 世界模型（ADR-017 阶段 8 p8：轨迹预测 + 版本管理） */
  WORLD_MODEL: "/api/v1/world-model",

  /** RL Agent（ADR-017 阶段 8 p8：决策推理 + 训练控制） */
  RL_AGENT: "/api/v1/rl-agent",

  /** Agent Gateway（Token/预测/执行/审计日志，后端注册于 /api/agent/v1） */
  AGENT_GATEWAY: "/api/agent/v1",
} as const;

/**
 * 构建完整的 API 路径
 * @param base - 基础路径（从 API_CONFIG 中选择）
 * @param path - 相对路径
 * @returns 完整的 API 路径
 */
export function buildApiPath(base: string, path: string): string {
  // 移除 path 开头的斜杠
  const cleanPath = path.startsWith("/") ? path.slice(1) : path;
  return `${base}/${cleanPath}`;
}
