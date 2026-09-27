/**
 * 世界模型契约（ADR-017 阶段 8 p8-6）
 *
 * 对应后端 `python/app/contracts/world_model.py`。
 * 详见 `docs/adr/ADR-017-世界模型与RL模块.md`。
 *
 * 设计要点：
 *   1. **离线 RL 优先**：v1 仅离线 RL，世界模型预测的轨迹供 RL agent 离线训练使用
 *   2. **不接 CNC 控制器**：预测结果仅供决策参考，物理执行需"持证操作员 + 导师签字 + 保险"
 *   3. 任务类型 `wm_predict_state` 已在 `core-contracts-design.md` 预留
 *   4. **状态向量约定**：默认 8 维（颤振概率 / 磨损 / 质量 / 主轴转速 / 进给 / 切深 / 切宽 / 温度）
 *   5. **轨迹预测**：自回归多步预测，horizon 默认 10，上限 100（防止漂移累积）
 *   6. **不确定性估计**：`uncertainty_estimate` 字段记录模型预测不确定性
 *   7. **权限模型**：`world_model:read`（查询/列表）、`world_model:write`（预测/注册版本）
 *   8. **异常层级**：`WorldModelError` 基类 → `PredictionError` / `ModelNotFoundError` /
 *      `InvalidStateError` 子类
 *
 * 稳定性：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

// 任务类型常量

/**
 * 世界模型状态预测任务类型常量.
 *
 * 在 `PluginManifest` 中声明，由 `WorldModelPlugin` 实现并注册到
 * `ITaskRegistry`。工作流编排器通过此任务类型调度世界模型插件。
 */
export const WM_PREDICT_STATE_TASK_TYPE = 'wm_predict_state' as const

// 默认维度与步长常量

/** 默认状态向量维度（颤振概率/磨损/质量/主轴转速/进给/切深/切宽/温度）. */
export const DEFAULT_STATE_DIM = 8 as const

/** 默认动作向量维度（主轴转速/进给/切深/切宽的 delta）. */
export const DEFAULT_ACTION_DIM = 4 as const

/** 默认预测步长. */
export const DEFAULT_HORIZON = 10 as const

/** 最大预测步长（防止自回归漂移累积）. */
export const MAX_HORIZON = 100 as const

/** 最小预测步长. */
export const MIN_HORIZON = 1 as const

// 状态字段标签常量

/**
 * 状态向量字段标签常量（与后端 `StateField` 对齐）.
 *
 * 状态字典字段名，用于世界模型输入输出的结构化描述。
 */
export const STATE_FIELD = {
  SPINDLE_SPEED: 'spindle_speed',
  FEED_RATE: 'feed_rate',
  DEPTH_OF_CUT: 'depth_of_cut',
  WIDTH_OF_CUT: 'width_of_cut',
  TOOL_WEAR: 'tool_wear',
  VIBRATION_RMS: 'vibration_rms',
  TEMPERATURE: 'temperature',
  CHATTER_PROBABILITY: 'chatter_probability',
} as const

/** 状态字段名类型. */
export type StateField = (typeof STATE_FIELD)[keyof typeof STATE_FIELD]

/** 所有状态字段列表（默认 8 维）. */
export const STATE_FIELD_VALUES: readonly StateField[] = [
  STATE_FIELD.SPINDLE_SPEED,
  STATE_FIELD.FEED_RATE,
  STATE_FIELD.DEPTH_OF_CUT,
  STATE_FIELD.WIDTH_OF_CUT,
  STATE_FIELD.TOOL_WEAR,
  STATE_FIELD.VIBRATION_RMS,
  STATE_FIELD.TEMPERATURE,
  STATE_FIELD.CHATTER_PROBABILITY,
]

/** 状态字段 → 中文标签. */
export const STATE_FIELD_LABELS: Readonly<Record<StateField, string>> = {
  [STATE_FIELD.SPINDLE_SPEED]: '主轴转速 (rpm)',
  [STATE_FIELD.FEED_RATE]: '进给速度 (mm/min)',
  [STATE_FIELD.DEPTH_OF_CUT]: '切削深度 (mm)',
  [STATE_FIELD.WIDTH_OF_CUT]: '切削宽度 (mm)',
  [STATE_FIELD.TOOL_WEAR]: '刀具磨损 (mm)',
  [STATE_FIELD.VIBRATION_RMS]: '振动 RMS (g)',
  [STATE_FIELD.TEMPERATURE]: '温度 (°C)',
  [STATE_FIELD.CHATTER_PROBABILITY]: '颤振概率 [0, 1]',
}

/**
 * 判断状态字段名是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isStateField(value: string): value is StateField {
  return (STATE_FIELD_VALUES as readonly string[]).includes(value)
}

// 动作字段标签常量

/**
 * 动作向量字段标签常量（与后端 `ActionField` / RL agent 动作向量标签对齐）.
 *
 * 动作为相对调整量（delta），取值范围 [-1, 1]，绝对值由 `SafetyConstraints`
 * 的 range 决定。
 */
export const ACTION_FIELD = {
  SPINDLE_SPEED_DELTA: 'spindle_speed_delta',
  FEED_RATE_DELTA: 'feed_rate_delta',
  DEPTH_OF_CUT_DELTA: 'depth_of_cut_delta',
  WIDTH_OF_CUT_DELTA: 'width_of_cut_delta',
} as const

/** 动作字段名类型. */
export type ActionField = (typeof ACTION_FIELD)[keyof typeof ACTION_FIELD]

/** 所有动作字段列表（默认 4 维 delta）. */
export const ACTION_FIELD_VALUES: readonly ActionField[] = [
  ACTION_FIELD.SPINDLE_SPEED_DELTA,
  ACTION_FIELD.FEED_RATE_DELTA,
  ACTION_FIELD.DEPTH_OF_CUT_DELTA,
  ACTION_FIELD.WIDTH_OF_CUT_DELTA,
]

/** 动作字段 → 中文标签. */
export const ACTION_FIELD_LABELS: Readonly<Record<ActionField, string>> = {
  [ACTION_FIELD.SPINDLE_SPEED_DELTA]: '主轴转速 Δ',
  [ACTION_FIELD.FEED_RATE_DELTA]: '进给速度 Δ',
  [ACTION_FIELD.DEPTH_OF_CUT_DELTA]: '切削深度 Δ',
  [ACTION_FIELD.WIDTH_OF_CUT_DELTA]: '切削宽度 Δ',
}

/**
 * 判断动作字段名是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isActionField(value: string): value is ActionField {
  return (ACTION_FIELD_VALUES as readonly string[]).includes(value)
}

// 默认模型 URI

/** 默认世界模型 URI（与后端 `WorldModelPredictRequest` 默认值对齐）. */
export const DEFAULT_WORLD_MODEL_URI = 'model://world_model/1.0.0' as const

// 预测请求/响应数据结构

/**
 * 世界模型预测请求.
 *
 * 对应后端 `WorldModelPredictRequest` dataclass。
 *
 * 前置约束：
 *   - `current_state` 不能为空（至少包含全部 8 个状态字段）
 *   - `candidate_action` 不能为空（4 个 delta 字段）
 *   - `horizon` 必须在 [1, 100]
 *   - `model_uri` 不能为空
 */
export interface WorldModelPredictRequest {
  /** 当前加工状态（字段名见 `StateField`，至少包含全部 8 个状态字段） */
  current_state: Record<string, number>
  /** 候选切削参数调整量（字段名见 `ActionField`，4 个 delta 字段） */
  candidate_action: Record<string, number>
  /** 预测步长（1 ~ 100，默认 10） */
  horizon: number
  /** 世界模型 URI（如 `model://world_model/1.0.0`） */
  model_uri: string
}

/**
 * 单步预测结果.
 *
 * 对应后端 `TrajectoryStep` dataclass。
 */
export interface TrajectoryStep {
  /** 步骤索引（0-based） */
  step: number
  /** 预测的状态（字段名见 `StateField`） */
  predicted_state: Record<string, number>
  /** 颤振概率 [0, 1] */
  chatter_probability: number
  /** 刀具磨损增量 (mm) */
  tool_wear_increment: number
  /** 表面粗糙度 Ra (μm) */
  surface_roughness: number
  /** 模型置信度 [0, 1] */
  confidence: number
}

/**
 * 轨迹汇总指标.
 *
 * 对应后端 `TrajectoryMetrics` dataclass。
 */
export interface TrajectoryMetrics {
  /** 平均颤振概率 */
  mean_chatter_probability: number
  /** 最大颤振概率 */
  max_chatter_probability: number
  /** 累计刀具磨损 (mm) */
  cumulative_tool_wear: number
  /** 最终表面粗糙度 Ra (μm) */
  final_surface_roughness: number
}

/**
 * 世界模型元信息.
 *
 * 对应后端 `WorldModelInfo` dataclass。
 */
export interface WorldModelInfo {
  /** 世界模型版本（semver） */
  world_model_version: string
  /** 训练数据样本数 */
  training_data_size: number
  /** 训练时的预测步长 */
  prediction_horizon: number
  /** 模型预测不确定性估计 [0, 1] */
  uncertainty_estimate: number
}

/**
 * 世界模型预测响应.
 *
 * 对应后端 `WorldModelPredictResponse` dataclass。
 */
export interface WorldModelPredictResponse {
  /** 预测的状态轨迹（长度 = horizon） */
  predicted_trajectory: TrajectoryStep[]
  /** 轨迹汇总指标 */
  trajectory_metrics: TrajectoryMetrics
  /** 世界模型元信息 */
  model_info: WorldModelInfo
}

// 模型版本数据结构

/**
 * 世界模型版本记录.
 *
 * 对应后端 `WorldModelVersion` dataclass + ORM `WorldModelVersionORM`。
 *
 * 注：`created_at` 为 ISO 8601 字符串（后端 `to_dict()` 序列化后）。
 */
export interface WorldModelVersion {
  /** 版本号（semver，如 `1.0.0`） */
  version: string
  /** 模型 URI（`model://world_model/<version>`） */
  model_uri: string
  /** 版本描述 */
  description: string
  /** 创建时间（ISO 8601 字符串） */
  created_at: string
  /** 训练数据样本数 */
  training_data_size: number
  /** 训练时的预测步长 */
  prediction_horizon: number
  /** 是否为当前激活版本 */
  is_active: boolean
}

// 列表查询数据结构

/** 列出世界模型版本的查询参数（GET /versions Query 参数）. */
export interface ListWorldModelVersionsParams {
  /** 为 true 时仅返回当前激活版本 */
  active_only?: boolean
  /** 每页数量（1-500，默认 50） */
  limit?: number
  /** 偏移量（默认 0） */
  offset?: number
}

/** 列出世界模型版本的响应（GET /versions 返回 data）. */
export interface ListWorldModelVersionsResponse {
  /** 版本记录列表 */
  items: WorldModelVersion[]
  /** 总数 */
  total: number
  /** 每页数量 */
  limit: number
  /** 偏移量 */
  offset: number
}

// 错误码常量

/**
 * 世界模型错误码常量.
 *
 * 与后端异常层级对齐：
 *   - `PREDICTION_ERROR`：预测失败（网络前向传播异常 / 数据加载失败）
 *   - `MODEL_NOT_FOUND`：模型未找到（`model_uri` 未注册）
 *   - `INVALID_STATE`：无效状态（状态字典字段缺失 / 值超出范围）
 *   - `INTERNAL_ERROR`：未识别的服务层异常兜底
 */
export const WORLD_MODEL_ERROR_CODE = {
  PREDICTION_ERROR: 'prediction_error',
  MODEL_NOT_FOUND: 'model_not_found',
  INVALID_STATE: 'invalid_state',
  INTERNAL_ERROR: 'internal_error',
} as const

/** 世界模型错误码字面量类型. */
export type WorldModelErrorCode =
  (typeof WORLD_MODEL_ERROR_CODE)[keyof typeof WORLD_MODEL_ERROR_CODE]

/** 所有世界模型错误码列表. */
export const WORLD_MODEL_ERROR_CODE_VALUES: readonly WorldModelErrorCode[] = [
  WORLD_MODEL_ERROR_CODE.PREDICTION_ERROR,
  WORLD_MODEL_ERROR_CODE.MODEL_NOT_FOUND,
  WORLD_MODEL_ERROR_CODE.INVALID_STATE,
  WORLD_MODEL_ERROR_CODE.INTERNAL_ERROR,
]

/** 错误码 → 中文标签. */
export const WORLD_MODEL_ERROR_CODE_LABELS: Readonly<Record<WorldModelErrorCode, string>> = {
  [WORLD_MODEL_ERROR_CODE.PREDICTION_ERROR]: '预测失败',
  [WORLD_MODEL_ERROR_CODE.MODEL_NOT_FOUND]: '模型未找到',
  [WORLD_MODEL_ERROR_CODE.INVALID_STATE]: '无效状态',
  [WORLD_MODEL_ERROR_CODE.INTERNAL_ERROR]: '内部错误',
}

/** 错误码 → UI Tag 类型（与 element-plus Tag type 对齐）. */
export const WORLD_MODEL_ERROR_CODE_TAG_TYPE: Readonly<Record<WorldModelErrorCode, string>> = {
  [WORLD_MODEL_ERROR_CODE.PREDICTION_ERROR]: 'warning',
  [WORLD_MODEL_ERROR_CODE.MODEL_NOT_FOUND]: 'info',
  [WORLD_MODEL_ERROR_CODE.INVALID_STATE]: 'warning',
  [WORLD_MODEL_ERROR_CODE.INTERNAL_ERROR]: 'danger',
}

/**
 * 判断错误码是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isWorldModelErrorCode(value: string): value is WorldModelErrorCode {
  return (WORLD_MODEL_ERROR_CODE_VALUES as readonly string[]).includes(value)
}

// 服务接口

/**
 * 世界模型服务接口.
 *
 * 定义前端 Store 与后端 REST API 的契约边界。具体实现见
 * `src/stores/worldModel.ts`（Pinia Store 对接 `/api/v1/world-model/*`）。
 *
 * 设计约束：
 *   1. 版本查询为只读操作（`world_model:read` 权限）
 *   2. 预测操作触发模型推理（`world_model:write` 权限），同步执行
 *   3. 预测结果不持久化（如需保存轨迹走工作流 `wm_predict_state` 任务类型）
 *   4. 物理执行需"持证操作员 + 导师签字 + 保险"，本契约不涉及
 */
export interface IWorldModelService {
  /** 列出世界模型版本（分页 + active_only 过滤）. */
  listVersions(
    params?: ListWorldModelVersionsParams,
  ): Promise<ListWorldModelVersionsResponse>

  /** 查询版本详情. */
  getVersion(version: string): Promise<WorldModelVersion>

  /**
   * 执行世界模型轨迹预测（不走工作流，直接调用服务层）.
   *
   * 同步执行（horizon ≤ 100，单次 < 2s），返回结构化响应。
   */
  predict(request: WorldModelPredictRequest): Promise<WorldModelPredictResponse>
}

// 契约版本

/** 世界模型契约版本（与后端 Stable v1.0.0 对齐）. */
export const CONTRACTS_WORLD_MODEL_VERSION = '1.0.0' as const
