/**
 * 项目级 Git 同步契约（Project-Level Git Sync Contract）
 *
 * 对应后端 app/contracts/project_sync.py。
 * 详见 docs/adr/ADR-011-项目级Git同步.md。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 *
 * 设计要点：
 *   1. 不修改现有 ProjectStore（.vrm ZIP 包保留给离线 CAD 工程包），
 *      新建独立的 ProjectSyncService 管理可同步项目
 *   2. 资源引用通过 URI + content_hash 实现内容寻址同步
 *   3. 同步策略（sync_strategy）根据资源类型与大小自动选择
 *   4. 同步状态机：clean / dirty / ahead / behind / conflict / error
 *   5. 前端通过 Pinia Store（useProjectSyncStore）调用 REST API，
 *      不直接持有 Git 仓库状态
 *
 * 资源 URI 体系（与 ADR-005 对齐）：
 *   dataset://<dataset_id>/<version>
 *   model://<model_name>/<version>
 *   workflow://<run_id>
 *   config://<spec_name>
 *   snapshot://<snapshot_id>
 *   template://<template_id>/<version>
 */

// 资源类型常量（与后端 RESOURCE_TYPES 对齐）

/** 项目同步支持的资源类型。 */
export const RESOURCE_TYPES = {
  DATASET: 'dataset',
  MODEL: 'model',
  WORKFLOW: 'workflow',
  CONFIG: 'config',
  SNAPSHOT: 'snapshot',
  TEMPLATE: 'template',
} as const;

export type ResourceType = (typeof RESOURCE_TYPES)[keyof typeof RESOURCE_TYPES];

/** 所有资源类型列表（用于 UI 选择器渲染）。 */
export const RESOURCE_TYPE_VALUES: readonly ResourceType[] = [
  RESOURCE_TYPES.DATASET,
  RESOURCE_TYPES.MODEL,
  RESOURCE_TYPES.WORKFLOW,
  RESOURCE_TYPES.CONFIG,
  RESOURCE_TYPES.SNAPSHOT,
  RESOURCE_TYPES.TEMPLATE,
] as const;

/** 资源类型中文标签（用于 UI 展示）。 */
export const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  dataset: '数据集',
  model: '模型产物',
  workflow: '工作流运行',
  config: '配置规格',
  snapshot: '实验快照',
  template: '工作流模板',
};

// 同步策略常量（与后端 SYNC_STRATEGIES 对齐）

/** 资源同步策略。 */
export const SYNC_STRATEGIES = {
  GIT_TRACKED: 'git_tracked',
  HASH_REFERENCED: 'hash_referenced',
  GIT_LFS: 'git_lfs',
} as const;

export type SyncStrategy = (typeof SYNC_STRATEGIES)[keyof typeof SYNC_STRATEGIES];

/** 所有同步策略列表。 */
export const SYNC_STRATEGY_VALUES: readonly SyncStrategy[] = [
  SYNC_STRATEGIES.GIT_TRACKED,
  SYNC_STRATEGIES.HASH_REFERENCED,
  SYNC_STRATEGIES.GIT_LFS,
] as const;

/** 同步策略中文标签与说明（用于 UI 展示与帮助提示）。 */
export const SYNC_STRATEGY_LABELS: Record<SyncStrategy, string> = {
  git_tracked: 'Git 跟踪（文本入 Git）',
  hash_referenced: '哈希引用（仅记录 content_hash）',
  git_lfs: 'Git LFS（大文件）',
};

// 同步状态常量（与后端 SYNC_STATUS 对齐）

/** 项目同步状态机。 */
export const SYNC_STATUS = {
  CLEAN: 'clean',
  DIRTY: 'dirty',
  AHEAD: 'ahead',
  BEHIND: 'behind',
  CONFLICT: 'conflict',
  ERROR: 'error',
} as const;

export type SyncStatus = (typeof SYNC_STATUS)[keyof typeof SYNC_STATUS];

/** 所有同步状态列表。 */
export const SYNC_STATUS_VALUES: readonly SyncStatus[] = [
  SYNC_STATUS.CLEAN,
  SYNC_STATUS.DIRTY,
  SYNC_STATUS.AHEAD,
  SYNC_STATUS.BEHIND,
  SYNC_STATUS.CONFLICT,
  SYNC_STATUS.ERROR,
] as const;

/** 同步状态中文标签（用于 UI 状态徽章渲染）。 */
export const SYNC_STATUS_LABELS: Record<SyncStatus, string> = {
  clean: '已同步',
  dirty: '有未提交变更',
  ahead: '本地领先远端',
  behind: '本地落后远端',
  conflict: '存在冲突',
  error: 'Git 错误',
};

/** 同步状态对应的 UI 颜色类型（与 element-plus tag type 对齐）。 */
export const SYNC_STATUS_TAG_TYPE: Record<SyncStatus, 'success' | 'warning' | 'info' | 'danger'> = {
  clean: 'success',
  dirty: 'warning',
  ahead: 'info',
  behind: 'info',
  conflict: 'danger',
  error: 'danger',
};

// 同步方向常量（与后端 SYNC_DIRECTIONS 对齐）

/** 同步方向（用于 SyncRecord.direction 字段）。 */
export const SYNC_DIRECTIONS = {
  INIT: 'init',
  COMMIT: 'commit',
  PUSH: 'push',
  PULL: 'pull',
  CLONE: 'clone',
} as const;

export type SyncDirection = (typeof SYNC_DIRECTIONS)[keyof typeof SYNC_DIRECTIONS];

/** 同步方向中文标签。 */
export const SYNC_DIRECTION_LABELS: Record<SyncDirection, string> = {
  init: '初始化',
  commit: '提交',
  push: '推送',
  pull: '拉取',
  clone: '克隆',
};

// 资源类型 → 默认同步策略映射（与后端 DEFAULT_SYNC_STRATEGY 对齐）

export const DEFAULT_SYNC_STRATEGY: Record<ResourceType, SyncStrategy> = {
  dataset: SYNC_STRATEGIES.HASH_REFERENCED,
  model: SYNC_STRATEGIES.HASH_REFERENCED,
  workflow: SYNC_STRATEGIES.GIT_TRACKED,
  config: SYNC_STRATEGIES.GIT_TRACKED,
  snapshot: SYNC_STRATEGIES.HASH_REFERENCED,
  template: SYNC_STRATEGIES.GIT_TRACKED,
};

// URI 解析工具（与后端 parse_resource_uri / build_resource_uri 对齐）

/**
 * 解析资源 URI，返回 [resourceType, path] 元组.
 *
 * @throws Error URI 格式无效或 scheme 不在 RESOURCE_TYPES 中
 */
export function parseResourceUri(uri: string): [ResourceType, string] {
  if (!uri.includes('://')) {
    throw new Error(`资源 URI 格式无效（缺少 scheme://）: ${uri}`);
  }
  const [scheme, path] = uri.split('://', 2);
  if (!isResourceType(scheme)) {
    throw new Error(`资源 URI scheme 不支持: ${scheme}`);
  }
  if (!path) {
    throw new Error(`资源 URI path 为空: ${uri}`);
  }
  return [scheme, path];
}

/** 构造资源 URI. */
export function buildResourceUri(resourceType: ResourceType, ...pathParts: string[]): string {
  const path = pathParts
    .map((p) => p.replace(/^\/+|\/+$/g, ''))
    .filter((p) => p.length > 0)
    .join('/');
  if (!path) {
    throw new Error('path_parts 拼接后为空');
  }
  return `${resourceType}://${path}`;
}

/** 类型守卫：判断字符串是否为合法 ResourceType. */
export function isResourceType(value: string): value is ResourceType {
  return RESOURCE_TYPE_VALUES.includes(value as ResourceType);
}

/** 类型守卫：判断字符串是否为合法 SyncStrategy. */
export function isSyncStrategy(value: string): value is SyncStrategy {
  return SYNC_STRATEGY_VALUES.includes(value as SyncStrategy);
}

/** 类型守卫：判断字符串是否为合法 SyncStatus. */
export function isSyncStatus(value: string): value is SyncStatus {
  return SYNC_STATUS_VALUES.includes(value as SyncStatus);
}

// 数据接口：资源引用

/** 资源引用附加元数据（文件大小、来源插件 id、自定义标签等）。 */
export type ResourceRefMetadata = Record<string, unknown>;

/**
 * 资源引用契约：项目中的一个资源引用（不存储内容，仅记录 hash）.
 *
 * 对应后端 ResourceRef dataclass。
 */
export interface ResourceRef {
  /** 所属项目 ID。 */
  project_id: string;
  /** 资源类型。 */
  resource_type: ResourceType;
  /** 资源 URI（如 "dataset://phm2010/v3"）。 */
  resource_uri: string;
  /** 内容哈希（sha256 hex，64 字符；空字符串表示未计算）。 */
  content_hash: string;
  /** 同步策略。 */
  sync_strategy: SyncStrategy;
  /** 附加元数据。 */
  metadata: ResourceRefMetadata;
}

/** 资源引用 ORM 投影（含 id / created_at / updated_at 等数据库字段）。 */
export interface ResourceRefRecord extends ResourceRef {
  /** 主键 ID。 */
  id: string;
  /** 创建时间（ISO 8601）。 */
  created_at: string;
  /** 最后更新时间（ISO 8601）。 */
  updated_at: string;
}

// 数据接口：项目同步清单

/**
 * 项目同步清单契约：一个可同步项目的元数据 + 当前状态.
 *
 * 对应后端 ProjectSyncManifest dataclass，以及 ProjectRepo ORM 的 to_dict() 投影。
 */
export interface ProjectSyncManifest {
  /** 项目 ID（prj_ 前缀，全局唯一）。 */
  project_id: string;
  /** 项目显示名。 */
  name: string;
  /** 仓库本地路径。 */
  repo_path: string;
  /** 远端仓库 URL（空字符串表示纯本地仓库）。 */
  remote_url: string;
  /** 当前分支名。 */
  current_branch: string;
  /** 当前 HEAD commit sha（空字符串表示未提交）。 */
  current_commit: string;
  /** 同步状态。 */
  status: SyncStatus;
  /** 项目描述。 */
  description: string;
  /** 项目作者。 */
  author: string;
  /** 创建时间（ISO 8601）。 */
  created_at: string;
  /** 最后更新时间（ISO 8601）。 */
  updated_at: string;
}

/** 项目详情响应（含主表 + 可选资源引用 / 同步记录）。 */
export interface GetProjectResponse extends ProjectSyncManifest {
  /** 资源引用列表（include_refs=true 时返回）。 */
  resource_refs?: ResourceRefRecord[];
  /** 资源引用数量。 */
  resource_count?: number;
  /** 同步记录列表（include_records=true 时返回）。 */
  sync_records?: SyncRecord[];
}

// 数据接口：同步记录

/** 同步记录详情。 */
export interface SyncRecord {
  /** 记录 ID（psr_ 前缀）。 */
  record_id: string;
  /** 所属项目 ID。 */
  project_id: string;
  /** 同步方向。 */
  direction: SyncDirection;
  /** 涉及的 commit sha（push/pull/commit 时填写）。 */
  commit_sha: string;
  /** 操作结果状态：success / failed / conflict。 */
  status: 'success' | 'failed' | 'conflict';
  /** 操作消息（commit message 或错误描述）。 */
  message: string;
  /** 操作时间戳（ISO 8601）。 */
  timestamp: string;
  /** 附加详情（变更文件数、字节数、远端 URL 等）。 */
  details: Record<string, unknown>;
}

// 数据接口：项目状态查询

/** 项目 Git 状态查询响应（含 ahead/behind 计数与变更文件列表）。 */
export interface ProjectStatusResponse {
  /** 项目 ID。 */
  project_id: string;
  /** 同步状态。 */
  status: SyncStatus;
  /** 当前分支。 */
  current_branch: string;
  /** 当前 HEAD commit sha。 */
  current_commit: string;
  /** 领先远端的 commit 数（ahead 状态时 > 0）。 */
  ahead_count: number;
  /** 落后远端的 commit 数（behind 状态时 > 0）。 */
  behind_count: number;
  /** 变更文件列表（dirty 状态时非空）。 */
  changed_files: ChangedFileEntry[];
}

/** 变更文件条目（git status --porcelain 解析结果）。 */
export interface ChangedFileEntry {
  /** 文件路径（相对仓库根目录）。 */
  path: string;
  /** 变更类型（M=modified / A=added / D=deleted / R=renamed / ?=untracked）。 */
  change_type: string;
}

// 请求 / 响应接口：项目 CRUD

/** 创建项目请求体。 */
export interface CreateProjectRequest {
  name: string;
  description?: string;
  author?: string;
  /** 远端仓库 URL（空表示纯本地仓库）。 */
  remote_url?: string;
  /** 初始分支名（默认 main）。 */
  branch?: string;
  /** 是否在创建时生成首个 commit（默认 true）。 */
  initial_commit?: boolean;
}

/** 克隆远端项目请求体。 */
export interface CloneProjectRequest {
  remote_url: string;
  name: string;
  branch?: string;
  description?: string;
  author?: string;
}

/** 列出项目查询参数。 */
export interface ListProjectsParams {
  /** 按状态过滤。 */
  status?: SyncStatus;
  /** 按作者过滤。 */
  author?: string;
  limit?: number;
  offset?: number;
}

/** 列出项目响应。 */
export interface ListProjectsResponse {
  items: ProjectSyncManifest[];
  total: number;
  limit: number;
  offset: number;
}

/** 提交变更请求体。 */
export interface CommitProjectRequest {
  message: string;
}

/** 提交变更响应。 */
export interface CommitProjectResponse {
  project_id: string;
  commit_sha: string;
  /** 是否实际产生了 commit（false 表示无变更可提交）。 */
  committed: boolean;
  /** 变更的资源引用数。 */
  changed_refs: number;
  status: SyncStatus;
}

/** push / pull 响应。 */
export interface SyncOperationResponse {
  project_id: string;
  direction: SyncDirection;
  commit_sha: string;
  status: SyncStatus;
  message: string;
}

/** 删除项目响应。 */
export interface DeleteProjectResponse {
  project_id: string;
  /** 是否物理删除了仓库目录。 */
  purged: boolean;
  deleted: boolean;
}

// 请求 / 响应接口：资源引用管理

/** 添加资源引用请求体。 */
export interface AddResourceRefRequest {
  resource_type: ResourceType;
  resource_uri: string;
  sync_strategy?: SyncStrategy;
  metadata?: ResourceRefMetadata;
}

/** 添加资源引用响应。 */
export interface AddResourceRefResponse extends ResourceRefRecord {
  /** 是否立即计算了 content_hash（false 表示该资源类型暂不支持 hash 计算）。 */
  hash_computed: boolean;
}

/** 列出资源引用查询参数。 */
export interface ListResourceRefsParams {
  /** 按资源类型过滤。 */
  resource_type?: ResourceType;
}

/** 列出资源引用响应。 */
export interface ListResourceRefsResponse {
  items: ResourceRefRecord[];
  total: number;
}

/** 删除资源引用响应。 */
export interface RemoveResourceRefResponse {
  project_id: string;
  resource_uri: string;
  deleted: boolean;
}

// 请求 / 响应接口：同步记录查询

/** 列出同步记录查询参数。 */
export interface ListSyncRecordsParams {
  /** 按同步方向过滤。 */
  direction?: SyncDirection;
  limit?: number;
  offset?: number;
}

/** 列出同步记录响应。 */
export interface ListSyncRecordsResponse {
  items: SyncRecord[];
  total: number;
  limit: number;
  offset: number;
}
