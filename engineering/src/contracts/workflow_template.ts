/**
 * 工作流模板市场契约（Workflow Template Marketplace Contract）
 *
 * 对应后端 app/contracts/workflow_template.py。
 * 详见 docs/adr/ADR-010-工作流模板市场.md。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 *
 * 设计要点：
 *   1. 复用现有 WorkflowSpec（src/contracts/task.ts），不修改其定义
 *   2. 模板 = WorkflowSpec + 市场元数据（category / tags / inputs_schema / parameters）
 *   3. 模板多版本管理（semver），每版本对应独立 manifest 快照
 *   4. 市场统计字段（downloads / avg_rating / rating_count）由服务层维护，
 *      不在 manifest 中持久化
 *   5. 通过 BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE 扩展点接入插件系统
 */

/** 模板 ID 正则：小写字母/数字/下划线，开头非数字。 */
export const TEMPLATE_ID_PATTERN = /^[a-z][a-z0-9_]*$/;

/** semver 正则（简化版，允许 pre-release 后缀）。 */
export const TEMPLATE_SEMVER_PATTERN =
  /^\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

/**
 * 推荐的工作流模板分类常量（与后端 TEMPLATE_CATEGORIES 对齐）.
 *
 * 分类不强制枚举，用户可自定义，但市场 UI 按这些推荐分类过滤。
 */
export const TEMPLATE_CATEGORIES = {
  GENERAL: 'general',
  TRAINING: 'training',
  EVALUATION: 'evaluation',
  ITERATION: 'iteration',
  PREPROCESS: 'preprocess',
  INFERENCE: 'inference',
  ANALYSIS: 'analysis',
} as const;

export type TemplateCategory =
  (typeof TEMPLATE_CATEGORIES)[keyof typeof TEMPLATE_CATEGORIES];

/** 所有推荐分类列表（用于 UI 过滤器渲染）。 */
export const TEMPLATE_CATEGORY_VALUES: readonly TemplateCategory[] = [
  TEMPLATE_CATEGORIES.GENERAL,
  TEMPLATE_CATEGORIES.TRAINING,
  TEMPLATE_CATEGORIES.EVALUATION,
  TEMPLATE_CATEGORIES.ITERATION,
  TEMPLATE_CATEGORIES.PREPROCESS,
  TEMPLATE_CATEGORIES.INFERENCE,
  TEMPLATE_CATEGORIES.ANALYSIS,
] as const;

/** 模板分类中文标签（用于 UI 展示）。 */
export const TEMPLATE_CATEGORY_LABELS: Record<TemplateCategory, string> = {
  general: '通用',
  training: '训练',
  evaluation: '评估',
  iteration: '迭代（数据飞轮）',
  preprocess: '预处理',
  inference: '推理',
  analysis: '分析',
};

/** 模板状态（主表 status 字段）。 */
export type TemplateStatus = 'active' | 'unpublished' | 'banned';

/** 模板排序字段。 */
export type TemplateSortBy =
  | 'downloads'
  | 'avg_rating'
  | 'created_at'
  | 'updated_at';

/**
 * 工作流模板清单契约（workflow_template.yaml 的 TypeScript 投影）.
 *
 * 一个模板 = 模板元数据 + 一个 WorkflowSpec（不可变）。
 * 模板的多个版本通过 (template_id, version) 唯一标识。
 *
 * 与 PluginManifest 的关系：
 *   - 插件可通过 plugin.yaml 的 `workflow_templates` 字段声明贡献的模板
 *   - 模板也可独立存在（无 plugin_id），用户手写 YAML 发布到市场
 */
export interface WorkflowTemplateManifest {
  /** 模板 ID（小写字母/数字/下划线，开头非数字）。 */
  id: string;
  /** 显示名。 */
  name: string;
  /** semver 版本号。 */
  version: string;
  /** 描述。 */
  description: string;
  /** 作者。 */
  author: string;
  /** 许可证（如 "MIT" / "Apache-2.0"）。 */
  license: string;
  /** WorkflowSpec dict（含 name / version / nodes / edges / inputs / outputs / metadata）。 */
  spec: WorkflowSpecPayload;
  /** 模板分类（见 TEMPLATE_CATEGORIES）。 */
  category: TemplateCategory;
  /** 自由标签列表。 */
  tags: string[];
  /** 输入参数 JSON Schema dict，覆盖 spec.inputs 的默认值。 */
  inputs_schema: Record<string, unknown>;
  /** 可调参数声明 dict（参数名 → JSON Schema 片段）。 */
  parameters: Record<string, unknown>;
  /** 依赖的契约及版本约束，如 ["task@>=1.0", "dataset@>=1.0"]。 */
  required_contracts: string[];
  /** 必须授权的能力（与 PluginManifest 同规范）。 */
  required_capabilities: string[];
  /** 贡献此模板的插件 id。空字符串表示独立模板。 */
  plugin_id: string;
  /** 主页 URL（可选）。 */
  homepage: string;
}

/**
 * WorkflowSpec 的 dict 投影（用于模板 manifest 的 spec 字段）.
 *
 * 与 src/contracts/task.ts 的 WorkflowSpec 对齐，但模板市场的 spec 字段
 * 允许任意结构（nodes 为必填），由 WorkflowTemplateManifest 校验。
 */
export interface WorkflowSpecPayload {
  name: string;
  version: string;
  nodes: Record<string, unknown>[];
  edges?: Record<string, unknown>[];
  inputs?: Record<string, unknown>;
  outputs?: Record<string, string>;
  metadata?: Record<string, unknown>;
}

/**
 * 模板市场统计快照（运行时聚合，不在 manifest 中持久化）.
 *
 * 与后端 TemplateMarketStats 对齐：downloads + avg_rating + rating_count
 * 三维度驱动优质内容浮现。
 */
export interface TemplateMarketStats {
  template_id: string;
  version: string;
  downloads: number;
  avg_rating: number;
  rating_count: number;
  published_at: string;
}

/**
 * 模板列表项（主表 to_dict() 投影，含反范式市场统计字段）.
 *
 * 对应后端 WorkflowTemplate ORM 的 to_dict() 输出。
 */
export interface WorkflowTemplateSummary {
  /** 主表主键（UUID）。 */
  id: string;
  /** 模板业务 ID。 */
  template_id: string;
  name: string;
  author: string;
  license: string;
  category: TemplateCategory;
  plugin_id: string | null;
  homepage: string | null;
  /** 当前最新版本号。 */
  latest_version: string;
  description: string;
  tags: string[] | null;
  /** 累计下载量（反范式）。 */
  downloads: number;
  /** 平均评分（0-5，反范式）。 */
  avg_rating: number;
  /** 评分人数（反范式）。 */
  rating_count: number;
  /** 首次发布时间（ISO 8601）。 */
  published_at: string;
  /** 模板状态。 */
  status: TemplateStatus;
  created_at: string;
  updated_at: string;
}

/** 模板版本记录（WorkflowTemplateVersion ORM 的 to_dict() 投影）。 */
export interface WorkflowTemplateVersionSummary {
  id: string;
  template_id: string;
  version: string;
  /** 完整 manifest 快照（JSON）。 */
  manifest_snapshot: WorkflowTemplateManifest;
  spec: WorkflowSpecPayload;
  inputs_schema: Record<string, unknown>;
  parameters: Record<string, unknown>;
  required_contracts: string[];
  required_capabilities: string[];
  /** 版本变更说明。 */
  changelog: string | null;
  /** 该版本的下载量。 */
  version_downloads: number;
  created_at: string;
}

/** 列表查询参数。 */
export interface ListTemplatesParams {
  category?: TemplateCategory;
  tag?: string;
  author?: string;
  limit?: number;
  offset?: number;
  sort_by?: TemplateSortBy;
}

/** 列表响应。 */
export interface ListTemplatesResponse {
  items: WorkflowTemplateSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** 搜索响应。 */
export interface SearchTemplatesResponse {
  items: WorkflowTemplateSummary[];
  total: number;
  query: string;
}

/** 模板详情响应（含主表 + 版本 + manifest 三层）。 */
export interface GetTemplateResponse {
  template: WorkflowTemplateSummary;
  version: WorkflowTemplateVersionSummary;
  manifest: WorkflowTemplateManifest;
}

/** 下载响应（与详情结构相同，但会自增下载计数）。 */
export type DownloadTemplateResponse = GetTemplateResponse;

/** 版本列表响应。 */
export interface ListVersionsResponse {
  template_id: string;
  latest_version: string;
  versions: WorkflowTemplateVersionSummary[];
}

/** 市场全局统计响应。 */
export interface MarketStatsResponse {
  total_templates: number;
  total_downloads: number;
  avg_rating: number;
}

/** 发布请求体。 */
export interface PublishTemplateRequest {
  /** 模板 manifest 字典（template.yaml 的反序列化形式）。 */
  template_dict: Record<string, unknown>;
  /** 版本变更说明。 */
  changelog?: string;
}

/** 发布响应。 */
export interface PublishTemplateResponse {
  template_id: string;
  version: string;
  /** 是否首次发布（True = 新模板，False = 新版本）。 */
  is_new_template: boolean;
  /** 发布时间（ISO 8601）。 */
  published_at: string;
}

/** 评分请求体。 */
export interface RateTemplateRequest {
  /** 评分（1.0 - 5.0）。 */
  rating: number;
}

/** 评分响应。 */
export interface RateTemplateResponse {
  template_id: string;
  avg_rating: number;
  rating_count: number;
}

/** 下架响应。 */
export interface UnpublishTemplateResponse {
  template_id: string;
  status: 'unpublished';
}

/** 模板 manifest 校验错误（与后端 TemplateValidationError 对齐）。 */
export interface TemplateValidationErrorResponse {
  code: 1002;
  message: string;
  /** 校验错误列表。 */
  detail?: string[];
  request_id: string;
}
