/**
 * 项目导入导出契约（ADR-015 阶段 6 p6-4：``.lomo`` 包格式）
 *
 * 对应后端 `python/app/contracts/project_package.py`。
 * 详见 `docs/adr/ADR-015-项目导入导出.md`。
 *
 * 设计要点：
 *   1. ``.lomo`` 包本质是 ZIP 归档（扩展名 ``.lomo``），与 ADR-011 Git 同步互补——
 *      Git 同步是"引用同步"（仅 hash），``.lomo`` 包是"内容同步"（含文件）
 *   2. ``manifest.json`` 是包清单，含格式版本 + 项目元数据 + 资源清单 + 校验和
 *   3. ``ContentPolicy`` 决定打包范围：metadata_only / include_content / small_files_only
 *   4. ``ConflictStrategy`` 决定冲突处理：skip / overwrite / rename / fail
 *   5. 资源 URI 体系与 ADR-005 / ADR-011 / ADR-012 对齐：
 *      dataset://<dataset_id>/<version> / model://<name>/<version> /
 *      workflow://<run_id> / config://<spec_name> / snapshot://<snapshot_id>
 *
 * 稳定性：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

// 包格式版本常量

/**
 * ``.lomo`` 包格式版本常量。
 *
 * 遵循 semver：MAJOR.MINOR.PATCH。
 * - MAJOR：不兼容的清单结构变更（如字段重命名 / 删除）
 * - MINOR：向后兼容的字段新增（导入旧版本包仍可用）
 * - PATCH：错误修复与澄清
 *
 * 导入时校验 manifest.format_version 的 MAJOR 是否与当前版本一致；
 * MINOR / PATCH 差异通过兼容性矩阵处理。
 */
export const PACKAGE_FORMAT_VERSION = {
  /** 初始版本：ZIP + manifest.json + 内容寻址 */
  V1_0_0: '1.0.0',
  /** 当前实现版本 */
  CURRENT: '1.0.0',
} as const

/** 包格式版本字面量类型。 */
export type PackageFormatVersion = (typeof PACKAGE_FORMAT_VERSION)[keyof typeof PACKAGE_FORMAT_VERSION]

/** 所有受支持的包格式版本列表。 */
export const PACKAGE_FORMAT_VERSION_VALUES: readonly string[] = [
  PACKAGE_FORMAT_VERSION.V1_0_0,
]

/** 包格式版本 → 中文标签。 */
export const PACKAGE_FORMAT_VERSION_LABELS: Readonly<Record<string, string>> = {
  [PACKAGE_FORMAT_VERSION.V1_0_0]: 'v1.0.0（初始版本）',
}

/**
 * 判断包格式版本是否受支持。
 * @param version - 待校验的版本字符串
 * @returns True 表示当前实现可读取该版本包
 */
export function isPackageFormatVersionSupported(version: string): boolean {
  return PACKAGE_FORMAT_VERSION_VALUES.includes(version)
}

/**
 * 判断包格式版本的 MAJOR 段是否与当前版本一致。
 *
 * 用于导入时兼容性预检：MAJOR 一致表示清单结构兼容，
 * 可尝试导入（MINOR / PATCH 差异由字段缺省值兜底）。
 *
 * @param version - 待校验的版本字符串
 * @returns True 表示 MAJOR 段一致
 */
export function isPackageFormatVersionMajorCompatible(version: string): boolean {
  if (!version || !version.includes('.')) return false
  try {
    const major = parseInt(version.split('.')[0]!, 10)
    const currentMajor = parseInt(PACKAGE_FORMAT_VERSION.CURRENT.split('.')[0]!, 10)
    return major === currentMajor
  } catch {
    return false
  }
}

// 内容策略常量

/**
 * 内容策略常量：决定 ``.lomo`` 包打包资源内容的范围。
 *
 * - METADATA_ONLY：仅打包元数据，不打包资源内容。适用项目结构分享、文档归档场景。
 *   包体积小，但导入后无法直接运行工作流。
 * - INCLUDE_CONTENT：打包所有资源内容（默认）。适用跨机器迁移、完整备份场景。
 *   包体积大，但导入后立即可用。
 * - SMALL_FILES_ONLY：仅打包 ≤ max_file_size_bytes 的资源文件，大文件仅元数据。
 *   适用网络受限场景（如邮件附件），平衡包体积与可用性。
 */
export const CONTENT_POLICY = {
  METADATA_ONLY: 'metadata_only',
  INCLUDE_CONTENT: 'include_content',
  SMALL_FILES_ONLY: 'small_files_only',
} as const

/** 内容策略字面量类型。 */
export type ContentPolicy = (typeof CONTENT_POLICY)[keyof typeof CONTENT_POLICY]

/** 所有内容策略列表。 */
export const CONTENT_POLICY_VALUES: readonly ContentPolicy[] = [
  CONTENT_POLICY.METADATA_ONLY,
  CONTENT_POLICY.INCLUDE_CONTENT,
  CONTENT_POLICY.SMALL_FILES_ONLY,
]

/** 内容策略 → 中文标签。 */
export const CONTENT_POLICY_LABELS: Readonly<Record<ContentPolicy, string>> = {
  [CONTENT_POLICY.METADATA_ONLY]: '仅元数据',
  [CONTENT_POLICY.INCLUDE_CONTENT]: '含内容（默认）',
  [CONTENT_POLICY.SMALL_FILES_ONLY]: '仅小文件',
}

/** 默认内容策略。 */
export const DEFAULT_CONTENT_POLICY: ContentPolicy = CONTENT_POLICY.INCLUDE_CONTENT

/**
 * 判断内容策略是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isContentPolicy(value: string): value is ContentPolicy {
  return (CONTENT_POLICY_VALUES as readonly string[]).includes(value)
}

// 冲突策略常量

/**
 * 冲突策略常量：导入时目标机器已存在同 URI 资源的处理方式。
 *
 * - SKIP：跳过冲突资源（保留目标机器已有版本，默认）。最安全，但可能导致
 *   项目状态与源机器不一致。
 * - OVERWRITE：覆盖目标机器已有版本。危险，需前端二次确认；适用于
 *   目标机器资源明显过时的场景。
 * - RENAME：重命名导入资源（URI 追加 _imported_<timestamp> 后缀）。
 *   保留两份资源，由用户后续手动合并。
 * - FAIL：遇到冲突立即报错，不导入任何资源（事务性）。适用于要求
 *   "全有或全无"的批量导入场景。
 */
export const CONFLICT_STRATEGY = {
  SKIP: 'skip',
  OVERWRITE: 'overwrite',
  RENAME: 'rename',
  FAIL: 'fail',
} as const

/** 冲突策略字面量类型。 */
export type ConflictStrategy = (typeof CONFLICT_STRATEGY)[keyof typeof CONFLICT_STRATEGY]

/** 所有冲突策略列表。 */
export const CONFLICT_STRATEGY_VALUES: readonly ConflictStrategy[] = [
  CONFLICT_STRATEGY.SKIP,
  CONFLICT_STRATEGY.OVERWRITE,
  CONFLICT_STRATEGY.RENAME,
  CONFLICT_STRATEGY.FAIL,
]

/** 冲突策略 → 中文标签。 */
export const CONFLICT_STRATEGY_LABELS: Readonly<Record<ConflictStrategy, string>> = {
  [CONFLICT_STRATEGY.SKIP]: '跳过（默认）',
  [CONFLICT_STRATEGY.OVERWRITE]: '覆盖',
  [CONFLICT_STRATEGY.RENAME]: '重命名',
  [CONFLICT_STRATEGY.FAIL]: '失败即终止',
}

/** 冲突策略 → UI Tag 类型（与 element-plus Tag type 对齐）。 */
export const CONFLICT_STRATEGY_TAG_TYPE: Readonly<Record<ConflictStrategy, string>> = {
  [CONFLICT_STRATEGY.SKIP]: 'info',
  [CONFLICT_STRATEGY.OVERWRITE]: 'danger',
  [CONFLICT_STRATEGY.RENAME]: 'warning',
  [CONFLICT_STRATEGY.FAIL]: 'danger',
}

/** 默认冲突策略。 */
export const DEFAULT_CONFLICT_STRATEGY: ConflictStrategy = CONFLICT_STRATEGY.SKIP

/**
 * 判断冲突策略是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isConflictStrategy(value: string): value is ConflictStrategy {
  return (CONFLICT_STRATEGY_VALUES as readonly string[]).includes(value)
}

// 包任务状态常量（导出 / 导入共用）

/**
 * 包任务状态常量：导出 / 导入任务的异步执行状态。
 *
 * 状态机：
 *   PENDING → RUNNING → COMPLETED
 *           ↘ FAILED
 */
export const PACKAGE_TASK_STATUS = {
  PENDING: 'pending',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
} as const

/** 包任务状态字面量类型。 */
export type PackageTaskStatus = (typeof PACKAGE_TASK_STATUS)[keyof typeof PACKAGE_TASK_STATUS]

/** 所有包任务状态列表。 */
export const PACKAGE_TASK_STATUS_VALUES: readonly PackageTaskStatus[] = [
  PACKAGE_TASK_STATUS.PENDING,
  PACKAGE_TASK_STATUS.RUNNING,
  PACKAGE_TASK_STATUS.COMPLETED,
  PACKAGE_TASK_STATUS.FAILED,
]

/** 包任务状态 → 中文标签。 */
export const PACKAGE_TASK_STATUS_LABELS: Readonly<Record<PackageTaskStatus, string>> = {
  [PACKAGE_TASK_STATUS.PENDING]: '待执行',
  [PACKAGE_TASK_STATUS.RUNNING]: '执行中',
  [PACKAGE_TASK_STATUS.COMPLETED]: '已完成',
  [PACKAGE_TASK_STATUS.FAILED]: '失败',
}

/** 包任务状态 → UI Tag 类型。 */
export const PACKAGE_TASK_STATUS_TAG_TYPE: Readonly<Record<PackageTaskStatus, string>> = {
  [PACKAGE_TASK_STATUS.PENDING]: 'info',
  [PACKAGE_TASK_STATUS.RUNNING]: 'warning',
  [PACKAGE_TASK_STATUS.COMPLETED]: 'success',
  [PACKAGE_TASK_STATUS.FAILED]: 'danger',
}

/** 终态状态集合（不可再变更）。 */
export const TERMINAL_PACKAGE_TASK_STATUS: readonly PackageTaskStatus[] = [
  PACKAGE_TASK_STATUS.COMPLETED,
  PACKAGE_TASK_STATUS.FAILED,
]

/**
 * 判断包任务状态是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isPackageTaskStatus(value: string): value is PackageTaskStatus {
  return (PACKAGE_TASK_STATUS_VALUES as readonly string[]).includes(value)
}

/**
 * 判断包任务状态是否为终态（不可再变更）。
 * @param value - 待校验的状态
 * @returns True 表示终态
 */
export function isTerminalPackageTaskStatus(value: PackageTaskStatus): boolean {
  return (TERMINAL_PACKAGE_TASK_STATUS as readonly PackageTaskStatus[]).includes(value)
}

// 默认值常量

/** ``small_files_only`` 策略的默认文件大小阈值（10 MB）。 */
export const DEFAULT_MAX_FILE_SIZE_BYTES: number = 10 * 1024 * 1024

/** 流式读写缓冲区大小（64 KB），避免内存爆炸。 */
export const STREAM_BUFFER_SIZE: number = 64 * 1024

/** ``.lomo`` 包文件扩展名。 */
export const PACKAGE_FILE_EXTENSION: string = '.lomo'

/** 导出包文件名模板（``<project_name>_<timestamp>.lomo``）。 */
export const PACKAGE_FILENAME_TEMPLATE: string = '{name}_{timestamp}.lomo'

/** 源机器信息兜底默认值（socket.gethostname() / platform.system() 返回空时使用）。 */
export const SOURCE_MACHINE_INFO_DEFAULTS: Readonly<{ hostname: string; app_version: string; platform: string }> = {
  hostname: 'unknown-host',
  app_version: '4.0.0',
  platform: 'unknown',
}

// 数据结构：包资源条目

/**
 * 资源元数据字典（任意键值对，如 row_count / schema / model_type / framework）。
 */
export type PackageResourceMetadata = Record<string, unknown>

/**
 * 包资源条目契约：``manifest.resources`` 数组中的一项。
 *
 * 一个 PackageResourceEntry 对应包内一个资源文件，记录其 URI / 内容 hash /
 * 包内相对路径 / 大小 / 元数据。导入时按 resource_uri 寻址目标位置，
 * 按 content_hash 校验完整性。
 */
export interface PackageResourceEntry {
  /** 资源类型（dataset/model/workflow/config/snapshot/lineage） */
  resource_type: string
  /** 资源 URI（与 ADR-005 / ADR-011 / ADR-012 对齐） */
  resource_uri: string
  /** 内容 sha256，格式 ``sha256:<hex>``；元数据策略下为空字符串 */
  content_hash: string
  /** 包内相对路径（如 ``datasets/<id>/versions/1.0.0/data.parquet``） */
  path_in_package: string
  /** 资源文件大小（字节）；元数据策略下为 0 */
  size_bytes: number
  /** 资源元数据（如 row_count / schema / model_type / framework） */
  metadata: PackageResourceMetadata
}

/**
 * 判断资源条目是否包含内容（content_hash 非空且 size_bytes > 0）。
 * @param entry - 资源条目
 * @returns True 表示包含内容
 */
export function entryHasContent(entry: PackageResourceEntry): boolean {
  return Boolean(entry.content_hash) && entry.size_bytes > 0
}

// 数据结构：源机器信息

/**
 * 源机器信息契约：导出时记录源机器环境，用于诊断兼容性问题。
 */
export interface SourceMachineInfo {
  /** 主机名（如 ``workshop-pc-01``） */
  hostname: string
  /** 导出时应用版本（如 ``4.0.0``） */
  app_version: string
  /** 平台标识（``win32`` / ``linux`` / ``darwin``） */
  platform: string
}

// 数据结构：包项目元数据

/**
 * 包项目元数据契约：``manifest.project`` 字段。
 *
 * 与 ADR-011 ProjectSyncManifest 的核心字段对齐，导入时用于创建目标项目
 * 或匹配已有项目。
 */
export interface PackageProjectInfo {
  /** 源项目 ID（UUID）；导入时若 reinit_git=true 会生成新 ID */
  project_id: string
  /** 项目显示名 */
  name: string
  /** 项目描述 */
  description: string
  /** 项目作者 */
  author: string
  /** 源项目远端仓库 URL（空字符串表示纯本地项目） */
  remote_url: string
  /** 源项目当前分支名 */
  current_branch: string
  /** 源项目当前 HEAD commit sha */
  current_commit: string
}

// 数据结构：包清单

/**
 * 包清单契约：``.lomo`` 包的 ``manifest.json`` 投影。
 *
 * 一个 PackageManifest 对应一个 ``.lomo`` 包，记录格式版本 + 导出时间 +
 * 导出者 + 源机器 + 项目元数据 + 资源清单 + 内容策略 + 总大小 + 校验和。
 */
export interface PackageManifest {
  /** 包格式版本（PACKAGE_FORMAT_VERSION 常量） */
  format_version: string
  /** 导出时间（ISO8601 字符串） */
  exported_at: string
  /** 导出者 user_id 或 plugin_id */
  exported_by: string
  /** 源机器信息 */
  source_machine: SourceMachineInfo
  /** 项目元数据 */
  project: PackageProjectInfo
  /** 资源清单（按 resource_uri 唯一） */
  resources: PackageResourceEntry[]
  /** 内容策略（CONTENT_POLICY 常量） */
  content_policy: string
  /** 包内所有资源文件总大小（未压缩前） */
  total_size_bytes: number
  /** manifest.json 自身的 sha256（不含此字段），由服务层计算 */
  checksum: string
}

// 数据结构：导出选项

/**
 * 导出选项契约：``POST /export`` 请求体的核心字段。
 */
export interface ExportOptions {
  /** 内容策略（CONTENT_POLICY 常量，默认 INCLUDE_CONTENT） */
  content_policy: string
  /** 是否打包数据集资源（默认 true） */
  include_datasets: boolean
  /** 是否打包模型产物资源（默认 true） */
  include_models: boolean
  /** 是否打包工作流定义（默认 true） */
  include_workflows: boolean
  /** 是否打包配置规格（默认 true） */
  include_configs: boolean
  /** 是否打包实验快照元数据（默认 true） */
  include_snapshots: boolean
  /** 是否打包血缘记录（默认 true） */
  include_lineage: boolean
  /** ``small_files_only`` 策略下的文件大小阈值，默认 10MB */
  max_file_size_bytes: number
  /** 自定义输出文件名（不含路径，服务层追加扩展名）；空字符串使用默认模板 */
  output_filename: string
}

// 数据结构：导入选项

/**
 * 导入选项契约：``POST /import`` 请求体的核心字段。
 */
export interface ImportOptions {
  /** 冲突策略（CONFLICT_STRATEGY 常量，默认 SKIP） */
  conflict_strategy: string
  /** 导入资源的目标所有者 user_id（默认继承源 manifest.exported_by） */
  target_owner_id: string
  /** 导入后是否重新 git init（默认 true）；false 时仅在文件系统恢复资源，不创建 Git 仓库 */
  reinit_git: boolean
  /** 仅校验不实际写入（默认 false）；true 时服务层返回预导入结果，不修改任何文件或数据库 */
  dry_run: boolean
  /** 目标项目名（空字符串表示使用源 manifest.project.name）；用于"导入为副本"场景 */
  target_project_name: string
}

// 数据结构：导出结果

/**
 * 导出结果契约：``ProjectPackageService.export_project()`` 返回值，
 * 也是 ``POST /export`` 响应 data 字段的载荷。
 */
export interface ExportResult {
  /** 导出任务 ID（``pexp_`` 前缀 + uuid） */
  export_id: string
  /** 源项目 ID */
  project_id: string
  /** 生成的 ``.lomo`` 文件绝对路径 */
  package_path: string
  /** 包清单（含 checksum） */
  manifest: PackageManifest
  /** 资源条目总数 */
  resource_count: number
  /** 实际打包内容的资源数（含内容的条目数） */
  packed_count: number
  /** 因策略跳过的资源 URI 列表（如 small_files_only 策略下的大文件） */
  skipped_resources: string[]
  /** 包内所有资源文件总大小（未压缩前） */
  total_size_bytes: number
  /** ``.lomo`` 文件实际大小（压缩后） */
  package_size_bytes: number
  /** 任务状态（PACKAGE_TASK_STATUS 常量） */
  status: string
  /** 失败原因（status=FAILED 时非空） */
  error_message: string
  /** 任务创建时间（ISO8601 字符串，空字符串表示无） */
  created_at: string
  /** 任务完成时间（ISO8601 字符串，空字符串表示无） */
  completed_at: string
}

// 数据结构：导入资源记录 + 导入结果

/** 导入动作枚举字面量类型。 */
export type ImportAction = 'imported' | 'skipped' | 'renamed' | 'failed'

/** 所有导入动作列表。 */
export const IMPORT_ACTION_VALUES: readonly ImportAction[] = [
  'imported',
  'skipped',
  'renamed',
  'failed',
]

/** 导入动作 → 中文标签。 */
export const IMPORT_ACTION_LABELS: Readonly<Record<ImportAction, string>> = {
  imported: '已导入',
  skipped: '已跳过',
  renamed: '已重命名',
  failed: '失败',
}

/** 导入动作 → UI Tag 类型。 */
export const IMPORT_ACTION_TAG_TYPE: Readonly<Record<ImportAction, string>> = {
  imported: 'success',
  skipped: 'info',
  renamed: 'warning',
  failed: 'danger',
}

/**
 * 导入资源记录：单个资源的导入结果。
 */
export interface ImportResourceRecord {
  /** 资源 URI */
  resource_uri: string
  /** 导入动作（imported/skipped/renamed/failed） */
  action: ImportAction
  /** 目标 URI（rename 策略下与 resource_uri 不同；其他策略下相同） */
  target_uri: string
  /** 失败原因（action=failed 时非空） */
  error_message: string
}

/**
 * 导入结果契约：``ProjectPackageService.import_project()`` 返回值，
 * 也是 ``POST /import`` 响应 data 字段的载荷。
 */
export interface ImportResult {
  /** 导入任务 ID（``pimp_`` 前缀 + uuid） */
  import_id: string
  /** 源项目 ID（来自 manifest） */
  source_project_id: string
  /** 目标项目 ID（导入后创建或匹配的项目） */
  target_project_id: string
  /** 源 ``.lomo`` 文件路径 */
  source_package_path: string
  /** 包格式版本 */
  format_version: string
  /** 使用的冲突策略 */
  conflict_strategy: string
  /** 每个资源的导入记录（按 resource_uri 唯一） */
  resource_records: ImportResourceRecord[]
  /** 成功导入资源数 */
  imported_count: number
  /** 跳过资源数 */
  skipped_count: number
  /** 重命名资源数 */
  renamed_count: number
  /** 失败资源数 */
  failed_count: number
  /** 资源总数（= resource_records.length） */
  total_count: number
  /** 警告信息列表（非致命问题） */
  warnings: string[]
  /** 任务状态（PACKAGE_TASK_STATUS 常量） */
  status: string
  /** 失败原因（status=FAILED 时非空） */
  error_message: string
  /** 任务创建时间（ISO8601 字符串，空字符串表示无） */
  created_at: string
  /** 任务完成时间（ISO8601 字符串，空字符串表示无） */
  completed_at: string
}

/**
 * 判断导入结果是否部分失败（有失败但整体未 FAILED）。
 * @param result - 导入结果
 * @returns True 表示部分失败
 */
export function isImportPartialFailure(result: ImportResult): boolean {
  return result.failed_count > 0 && result.status === PACKAGE_TASK_STATUS.COMPLETED
}

// 数据结构：校验结果

/**
 * 包校验结果契约：``ProjectPackageService.validate_package()`` 返回值，
 * 也是 ``POST /validate`` 响应 data 字段的载荷。
 *
 * 校验项：
 *   1. ``manifest.json`` 可解析
 *   2. ``format_version`` 受支持
 *   3. ``checksum`` 与重新计算的 sha256 一致
 *   4. 每个资源条目的 ``content_hash`` 与包内文件实际 sha256 一致
 *   5. ``path_in_package`` 指向的文件存在于包内
 */
export interface ValidationResult {
  /** ``.lomo`` 文件路径 */
  package_path: string
  /** 整体是否通过校验 */
  is_valid: boolean
  /** 包格式版本（解析失败时为空字符串） */
  format_version: string
  /** checksum 是否一致 */
  checksum_verified: boolean
  /** 资源条目总数 */
  resource_count: number
  /** 通过校验的资源数 */
  verified_count: number
  /** 错误信息列表（致命问题） */
  errors: string[]
  /** 警告信息列表（非致命问题） */
  warnings: string[]
  /** 校验时间（ISO8601 字符串，空字符串表示无） */
  validated_at: string
}

// API 请求 / 响应接口（对接 8 个 REST 端点）

/**
 * 导出项目请求体（对应 ``POST /api/v1/project-packages/export``）。
 *
 * 注意：与 ExportOptions 字段重叠，但额外包含 project_id / exported_by /
 * output_dir / output_filename 等路由参数。后端 Pydantic 模型为
 * ``ExportProjectRequest``。
 */
export interface ExportProjectRequest {
  /** 源项目 ID */
  project_id: string
  /** 导出者（user_id 或 plugin_id） */
  exported_by: string
  /** 输出目录（空字符串表示使用服务层默认目录） */
  output_dir: string
  /** 内容策略（CONTENT_POLICY 常量，默认 include_content） */
  content_policy: string
  /** 是否打包数据集资源 */
  include_datasets: boolean
  /** 是否打包模型产物资源 */
  include_models: boolean
  /** 是否打包工作流定义 */
  include_workflows: boolean
  /** 是否打包配置规格 */
  include_configs: boolean
  /** 是否打包实验快照元数据 */
  include_snapshots: boolean
  /** 是否打包血缘记录 */
  include_lineage: boolean
  /** small_files_only 策略下的文件大小阈值（字节，默认 10MB） */
  max_file_size_bytes: number
  /** 自定义输出文件名（不含路径，空字符串使用默认模板） */
  output_filename: string
}

/**
 * 导出项目响应（对应 ``POST /api/v1/project-packages/export`` 成功响应的 data 字段）。
 *
 * 在 ExportResult 基础上追加 download_url 字段。
 */
export interface ExportProjectResponse extends ExportResult {
  /** 下载 URL（``/api/v1/project-packages/exports/{export_id}?download=true``） */
  download_url: string
}

/**
 * 导入项目请求参数（对应 ``POST /api/v1/project-packages/import``）。
 *
 * 注意：导入端点使用 multipart/form-data，file 通过 FormData 字段上传，
 * 其余字段为表单字段。
 */
export interface ImportProjectParams {
  /** 导入者（user_id 或 plugin_id） */
  imported_by: string
  /** 冲突策略（CONFLICT_STRATEGY 常量，默认 skip） */
  conflict_strategy: string
  /** 导入资源的目标所有者（空字符串继承源 manifest.exported_by） */
  target_owner_id: string
  /** 导入后是否重新 git init */
  reinit_git: boolean
  /** 仅校验不实际写入 */
  dry_run: boolean
  /** 目标项目名（空字符串使用源 manifest.project.name） */
  target_project_name: string
}

/** 导入项目响应（对应 ``POST /api/v1/project-packages/import`` 成功响应的 data 字段）。 */
export interface ImportProjectResponse extends ImportResult {}

/** 校验包响应（对应 ``POST /api/v1/project-packages/validate`` 成功响应的 data 字段）。 */
export interface ValidatePackageResponse extends ValidationResult {}

/** 预览包响应（对应 ``POST /api/v1/project-packages/preview`` 成功响应的 data 字段）。 */
export interface PreviewPackageResponse extends PackageManifest {}

// 列表端点查询参数 + 响应

/** 列出导出记录查询参数（对应 ``GET /api/v1/project-packages/exports``）。 */
export interface ListExportsParams {
  /** 按源项目 ID 过滤 */
  project_id?: string
  /** 按状态过滤（PACKAGE_TASK_STATUS 常量） */
  status?: string
  /** 按导出者过滤 */
  exported_by?: string
  /** 分页 limit（默认 100，最大 1000） */
  limit?: number
  /** 分页 offset（默认 0） */
  offset?: number
}

/**
 * 导出记录摘要（列表端点返回的 items）。
 *
 * 与 ExportResult 字段部分重叠，但不含 manifest / skipped_resources 等大字段，
 * 用于列表展示。
 */
export interface ExportRecordSummary {
  /** 导出任务 ID */
  id: string
  /** 源项目 ID */
  project_id: string
  /** ``.lomo`` 文件路径 */
  package_path: string
  /** 包格式版本 */
  format_version: string
  /** 内容策略 */
  content_policy: string
  /** 资源条目数 */
  resource_count: number
  /** 包大小（字节） */
  total_size_bytes: number
  /** manifest.json checksum */
  checksum: string
  /** 任务状态（PACKAGE_TASK_STATUS 常量） */
  status: string
  /** 失败原因（status=FAILED 时非空） */
  error_message: string | null
  /** 导出者 */
  exported_by: string
  /** 创建时间（ISO8601 字符串） */
  created_at: string
  /** 完成时间（ISO8601 字符串，可能为 null） */
  completed_at: string | null
}

/** 列出导出记录响应（对应 ``GET /api/v1/project-packages/exports``）。 */
export interface ListExportsResponse {
  /** 导出记录列表 */
  items: ExportRecordSummary[]
  /** 总条目数 */
  total: number
  /** 当前分页 limit */
  limit: number
  /** 当前分页 offset */
  offset: number
}

/** 查询导出记录详情响应（对应 ``GET /api/v1/project-packages/exports/{export_id}``）。 */
export interface GetExportResponse extends ExportRecordSummary {
  /** 下载 URL（``/api/v1/project-packages/exports/{export_id}?download=true``） */
  download_url: string
}

/** 删除导出包响应（对应 ``DELETE /api/v1/project-packages/exports/{export_id}``）。 */
export interface DeleteExportResponse {
  /** 被删除的导出 ID */
  export_id: string
  /** 是否已删除磁盘包文件（false 表示仅删除数据库记录） */
  file_deleted: boolean
}

/** 列出导入记录查询参数（对应 ``GET /api/v1/project-packages/imports``）。 */
export interface ListImportsParams {
  /** 按目标项目 ID 过滤 */
  target_project_id?: string
  /** 按状态过滤（PACKAGE_TASK_STATUS 常量） */
  status?: string
  /** 按导入者过滤 */
  imported_by?: string
  /** 分页 limit（默认 100，最大 1000） */
  limit?: number
  /** 分页 offset（默认 0） */
  offset?: number
}

/**
 * 导入记录摘要（列表端点返回的 items）。
 */
export interface ImportRecordSummary {
  /** 导入任务 ID */
  id: string
  /** 源 ``.lomo`` 文件路径 */
  source_package_path: string
  /** 源项目 ID（不建外键） */
  source_project_id: string
  /** 目标项目 ID */
  target_project_id: string
  /** 包格式版本 */
  format_version: string
  /** 使用的冲突策略 */
  conflict_strategy: string
  /** 成功导入资源数 */
  imported_count: number
  /** 跳过资源数 */
  skipped_count: number
  /** 重命名资源数 */
  renamed_count: number
  /** 失败资源数 */
  failed_count: number
  /** 任务状态（PACKAGE_TASK_STATUS 常量） */
  status: string
  /** 失败原因（status=FAILED 时非空） */
  error_message: string | null
  /** 导入者 */
  imported_by: string
  /** 创建时间（ISO8601 字符串） */
  created_at: string
  /** 完成时间（ISO8601 字符串，可能为 null） */
  completed_at: string | null
}

/** 列出导入记录响应（对应 ``GET /api/v1/project-packages/imports``）。 */
export interface ListImportsResponse {
  /** 导入记录列表 */
  items: ImportRecordSummary[]
  /** 总条目数 */
  total: number
  /** 当前分页 limit */
  limit: number
  /** 当前分页 offset */
  offset: number
}

// 服务接口契约（前端接口占位，通过 Pinia Store 间接调用）

/**
 * 项目包服务接口契约：定义导入导出服务的方法签名。
 *
 * 前端不直接实现此接口，而是通过 `useProjectPackageStore` Pinia Store
 * 间接调用后端 REST API。此接口仅作为类型契约占位，与后端
 * `IProjectPackageService` 对齐。
 */
export interface IProjectPackageService {
  /** 导出项目为 ``.lomo`` 包 */
  exportProject(
    projectId: string,
    outputDir: string,
    options: ExportOptions,
    exportedBy: string,
  ): Promise<ExportResult>

  /** 从 ``.lomo`` 包导入项目 */
  importProject(
    packagePath: string,
    options: ImportOptions,
    importedBy: string,
  ): Promise<ImportResult>

  /** 校验 ``.lomo`` 包完整性（不实际导入） */
  validatePackage(packagePath: string): Promise<ValidationResult>

  /** 预览 ``.lomo`` 包内容（返回 manifest，不实际导入） */
  previewImport(packagePath: string): Promise<PackageManifest>
}
