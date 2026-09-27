/**
 * NL2CAD 模块共享类型定义
 *
 * 对应后端 ``app.api.v1.nl2cad.services`` 中的 ``_validate_params`` 输出结构，
 * 替换原先散落在 NLModeling.vue / WorkflowGuide.vue / NLInputPanel.vue 中的
 * ``any`` 类型注解，提升类型安全性。
 */

/** 零件形状类型（与后端 ``shape_type`` 字段对齐） */
export type ShapeType = 'box' | 'cylinder' | 'sphere' | 'cone'

/** CAD 尺寸字段（不同形状使用不同子集） */
export interface CADDimensions {
  length?: number
  width?: number
  height?: number
  radius?: number
}

/** 零件位置坐标 */
export interface CADPosition {
  x: number
  y: number
  z: number
}

/** 加工特征（孔/槽/倒角等，结构由后端动态返回） */
export interface CADFeature {
  type: string
  [key: string]: unknown
}

/**
 * 从自然语言提取的 CAD 参数。
 *
 * 字段对齐后端 ``NL2CADService._validate_params`` 的输出。
 * ``description`` / ``process_plan`` / ``machine_type`` 等字段在
 * 工作流下游阶段补充，故为可选。
 */
export interface CADParams {
  shape_type: ShapeType
  dimensions: CADDimensions
  position?: CADPosition
  features?: CADFeature[]
  confidence?: number
  material?: string
  /** 降级标志：LLM 不可用时由规则引擎兜底产出 */
  _fallback?: 'rule_based'
  /** 工作流上下文中携带的描述（NLModeling 入口传入） */
  description?: string
  /** 工作流下游：工艺规划结果 */
  process_plan?: ProcessPlan
  /** 工作流下游：机床类型 */
  machine_type?: string
  /** 允许后端扩展字段透传，避免破坏性类型错误 */
  [key: string]: unknown
}

/** 工艺规划配置（WorkflowGuide.processConfig 结构） */
export interface ProcessConfig {
  material: string
  machine_type: string
  precision: string
}

/** 工艺规划结果（后端返回结构较动态，使用宽松索引签名） */
export interface ProcessPlan {
  operations?: Array<Record<string, unknown>>
  operation_plan?: {
    operations?: Array<Record<string, unknown>>
    [key: string]: unknown
  }
  [key: string]: unknown
}

/** 仿真状态枚举 */
export type SimulationStatus = 'idle' | 'running' | 'completed' | 'failed'

/** 仿真结果（对齐后端 ``SimulationResult`` 返回结构） */
export interface SimulationResult {
  status: SimulationStatus
  task_id: string
  collision_detected: boolean
  collision_positions: Array<unknown>
  collision_severity: string
  voxel_count: number
  removed_voxel_count: number
  duration_seconds: number
  stock_stl_url: string
  error: string
}

/** 三维点坐标（部分组件中用到的简短别名） */
export interface Point3D {
  x: number
  y: number
  z: number
}
