/**
 * 灵境制造 — 集中类型定义
 *
 * 统一导出所有 Store / Composable / 通用类型，
 * 消除跨模块类型导入中的分散引用。
 */

export type {
  AgentSummary,
  CheckpointInfo,
  MemoryEntryInfo,
  AgentDetail,
} from '@/stores/agents'

export type {
  Plugin,
  PluginDetail,
} from '@/stores/plugin'

export type {
  AppSettings,
} from '@/stores/settings'

export type {
  VersionStatus,
} from '@/stores/version'

export type {
  SSEEvent,
  UseEventSourceOptions,
} from '@/composables/useEventSource'

/** 训练/推理任务状态 */
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

/** 插件类型 */
export type PluginType = 'adapter' | 'data_source' | 'tool' | 'enhancement'

/** 插件状态 */
export type PluginStatus = 'enabled' | 'disabled' | 'error' | 'installing' | 'uninstalling'

/** API 通用响应包装 */
export interface ApiResponse<T = unknown> {
  data: T
  message?: string
  status?: number
}

/** 分页参数 */
export interface Pagination {
  page: number
  pageSize: number
  total: number
}

/** 版本号三组 */
export interface SemVer {
  major: number
  minor: number
  patch: number
}

/** 仿真请求参数 */
export interface SimulationRequest {
  project_id: string
  voxel_size: number
  tool_diameter: number
  tool_length: number
  tool_type: 'flat' | 'ball' | 'drill'
  tool_corner_radius: number
  gcode: string
  safe_z_height: number
  stock_stl_path: string
}

/** 碰撞检测详情(后端返回) */
export interface CollisionInfo {
  collided: boolean
  collision_positions: [number, number, number][]
  collision_segment_indices: number[]
  collision_severity: 'none' | 'warning' | 'critical'
}

/** 仿真结果(后端响应格式) */
export interface SimulationResult {
  task_id: string
  collision_detected: boolean
  simulation_result: {
    workpiece_stl_path: string
    voxel_count: number
    removed_voxel_count: number
    voxel_size: number
    original_bbox: Record<string, number> | null
  }
  collision_details: {
    timestamp: string
    positions: [number, number, number][]
    segment_indices: number[]
    severity: string
    count: number
  }
  duration_seconds: number
  voxel_count: number
  removed_voxel_count: number
  voxel_size: number
  toolpath_segment_count: number
}

/** 仿真任务状态 */
export interface SimulationStatus {
  task_id: string
  status: 'pending' | 'running' | 'completed' | 'not_found' | 'failed'
  progress: number
  result: SimulationResult | null
}

/** 刀具路径段(前端用) */
export interface ToolpathSegmentData {
  type: 'rapid' | 'linear' | 'arc' | 'dwell'
  start_point: [number, number, number]
  end_point: [number, number, number]
  block_number: number
  g_code: string
}

/** 播放状态 */
export type PlaybackState = 'idle' | 'playing' | 'paused'

/** 工程元数据 */
export interface ProjectMetadata {
  name: string
  created_at: string
  modified_at: string
  author: string
  description: string
}

/** 资源文件条目 */
export interface ResourceEntry {
  id: string
  type: 'drawing' | 'model' | 'toolpath' | 'simulation' | 'postprocessor' | 'extension'
  path: string
  original_name: string
  mime_type: string
  added_at: string
  metadata: Record<string, unknown>
}

/** 工程加工数据 */
export interface ProjectData {
  stock_definition: Record<string, unknown>
  tool_selection: Array<Record<string, unknown>>
  process_steps: Array<Record<string, unknown>>
  toolpath_config: Record<string, unknown>
  postprocessor_config: Record<string, unknown>
  simulation_config: Record<string, unknown>
}

/** 工程清单(project.json) */
export interface ProjectManifest {
  version: string
  metadata: ProjectMetadata
  resources: ResourceEntry[]
  data: ProjectData
  extensions: Record<string, unknown>
}

/** 工程摘要(列表用) */
export interface ProjectSummary {
  path: string
  name: string
  created_at: string
  modified_at: string
  resource_count: number
  file_size: number
  error?: string
}

/** 新建工程请求 */
export interface NewProjectRequest {
  name: string
  author: string
  description: string
}

/** 保存工程请求 */
export interface SaveProjectRequest {
  manifest: ProjectManifest
  project_id: string
  output_name: string
}

/** 打开工程请求 */
export interface OpenProjectRequest {
  file_path?: string
  upload_data?: string
}

/** 包围盒 */
export interface BBox {
  length: number
  width: number
  height: number
  min_point: [number, number, number]
  max_point: [number, number, number]
}

/** 模型基础信息 */
export interface ModelInfo {
  volume: number
  surface_area: number
  bounding_box: BBox
  center_of_mass: { x: number; y: number; z: number }
  entity_count: number
  face_count: number
  vertex_count: number
  edge_count: number
  shell_count: number
  solid_count: number
}

/** 单个实体信息 */
export interface EntityInfo {
  name: string
  entity_index: number
  volume: number
  surface_area: number
  bounding_box: BBox
  center_of_mass: [number, number, number]
  face_count: number
  vertex_count: number
}

/** STL输出文件 */
export interface StlFileInfo {
  file_name: string
  stl_url: string
  stl_path: string
  format: 'stl' | 'brep'
  face_count: number
  vertex_count: number
  file_size: number
  entity_index: number
  entity_name: string
  precision_used: string
}

/** 导入状态 */
export interface ImportStatus {
  success: boolean
  message: string
  entity_count: number
  face_count: number
  vertex_count: number
  errors: string[]
}

/** STEP导入响应 */
export interface StepImportResult {
  file_name: string
  file_size: number
  parse_time_ms: number
  conversion_time_ms: number
  model_info: ModelInfo
  entities: EntityInfo[]
  is_assembly: boolean
  stl_files: StlFileInfo[]
  brep_files: StlFileInfo[]
  status: ImportStatus
  warnings: string[]
  cached: boolean
  import_id: string
  format: string
}

/** 导入历史条目 */
export interface ImportHistoryEntry {
  file_name: string
  original_name: string
  file_size: number
  created_at: number
  stl_url: string
  has_brep: boolean
}

/** 导入状态枚举 */
export type ImportState = 'idle' | 'uploading' | 'processing' | 'success' | 'error'

/** DXF 文件单条线段实体 */
export interface DxfLine {
  start: [number, number, number]
  end: [number, number, number]
  layer?: string
  color?: number
  handle?: string
}

/** DXF 文件圆实体 */
export interface DxfCircle {
  center: [number, number, number]
  radius: number
  layer?: string
  color?: number
  handle?: string
}

/** DXF 文件尺寸标注 */
export interface DxfDimension {
  dim_type?: string
  measurement: number
  text?: string
  position?: [number, number, number]
  layer?: string
}

/** DXF 几何包围盒 */
export interface DxfExtents {
  min_x?: number
  min_y?: number
  max_x?: number
  max_y?: number
  min_z?: number
  max_z?: number
}

/** DXF 解析响应 */
export interface DxfParseResponse {
  file_id: string
  file_name: string
  file_size: number
  dxf_version: string
  parse_time_ms: number
  entity_counts: Record<string, number>
  total_entities: number
  lines_count: number
  circles_count: number
  arcs_count: number
  texts_count: number
  dimensions_count: number
  extents: DxfExtents
  lines: DxfLine[]
  circles: DxfCircle[]
  dimensions: DxfDimension[]
  warnings: string[]
}

/** DXF 特征提取响应 */
export interface DxfFeatureResponse {
  hole_count: number
  plane_count: number
  overall_length: number
  overall_width: number
  overall_height: number
  height_inferred: boolean
  holes: Array<Record<string, unknown>>
  planes: Array<Record<string, unknown>>
  warnings: string[]
}

/** DXF 上传响应 */
export interface DxfUploadResponse {
  file_id: string
  file_name: string
  file_size: number
  upload_time_ms: number
}

/** 规则条件项 */
export interface RuleCondition {
  parameter: string
  operator: '=' | '<' | '>' | '<=' | '>=' | '!='
  value: string
  unit?: string
}

/** 规则结果项 */
export interface RuleResult {
  parameter: string
  operator: '=' | '<' | '>' | '<=' | '>=' | '!='
  value: string
  unit?: string
}

/** 工艺规则 */
export interface ProcessRule {
  id: number
  name: string
  description: string
  group_id?: number
  conditions: RuleCondition[]
  logic_operator: 'AND' | 'OR'
  result: RuleResult
  status: 'active' | 'inactive' | 'draft'
  priority: number
  created_at?: string
  updated_at?: string
  preview_text?: string
}

/** 规则分组 */
export interface RuleGroup {
  id: number
  name: string
  description: string
  created_at?: string
  updated_at?: string
  rule_count?: number
}

/** 规则列表响应 */
export interface RuleListResponse {
  rules: ProcessRule[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/** 规则分组列表响应 */
export interface RuleGroupListResponse {
  groups: RuleGroup[]
  total: number
}

/** 规则统计 */
export interface RuleStats {
  total_rules: number
  active_rules: number
  inactive_rules: number
  draft_rules: number
  total_groups: number
}

/** 规则导入响应 */
export interface RuleImportResponse {
  imported_groups: number
  imported_rules: number
  total_rules: number
  total_groups: number
}

/** 规则创建请求 */
export interface RuleCreateRequest {
  name: string
  description?: string
  group_id?: number
  conditions: RuleCondition[]
  logic_operator?: 'AND' | 'OR'
  result: RuleResult
  status?: 'active' | 'inactive' | 'draft'
  priority?: number
}

/** 规则更新请求 */
export interface RuleUpdateRequest {
  name?: string
  description?: string
  group_id?: number
  conditions?: RuleCondition[]
  logic_operator?: 'AND' | 'OR'
  result?: RuleResult
  status?: 'active' | 'inactive' | 'draft'
  priority?: number
}

/** 规则分组创建请求 */
export interface RuleGroupCreateRequest {
  name: string
  description?: string
}

/** 规则分组更新请求 */
export interface RuleGroupUpdateRequest {
  name?: string
  description?: string
}

/** 工艺参数选项 */
export interface ProcessParameterOption {
  label: string
  value: string
  category: 'material' | 'process' | 'tool' | 'cutting_parameter'
}

/** 比较运算符选项 */
export interface OperatorOption {
  label: string
  value: '=' | '<' | '>' | '<=' | '>=' | '!='
}

/** 规则验证结果 */
export interface RuleValidation {
  is_valid: boolean
  errors: string[]
  preview_text?: string
}

// 共享类型（消除 Store 间重复定义）
export type { PaginationState } from './pagination'
export { createPagination, nextOffset, hasMore } from './pagination'
