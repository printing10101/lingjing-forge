/**
 * RL Agent 契约（ADR-017 阶段 8 p8-6）
 *
 * 对应后端 `python/app/contracts/rl_agent.py`。
 * 详见 `docs/adr/ADR-017-世界模型与RL模块.md`。
 *
 * 设计要点：
 *   1. **离线 RL 优先**：v1 仅支持基于历史数据 + 仿真环境的离线 RL，
 *      在线 RL 列入 v2 且必须有人工监督
 *   2. **SafetyShield 硬约束**：强制过滤违反安全约束的动作，不可被 RL 策略覆盖
 *   3. 任务类型 `rl_act` 已在 `core-contracts-design.md` 预留
 *   4. **动作向量约定**：4 维 delta（主轴转速/进给/切深/切宽），取值 [-1, 1]
 *   5. **PPO 算法**：默认策略算法，clipped objective + GAE 优势估计
 *   6. **训练管线**：`collect_episodes → build_replay_buffer → train_policy →
 *      evaluate → snapshot`，基于阶段 1 `Workflow` + 阶段 2 `Snapshot`
 *   7. **可复现性**：训练随机种子固定，snapshot 持久化策略版本
 *   8. **权限模型**：`rl_agent:read`（查询/列表）、`rl_agent:write`（决策/训练）
 *   9. **异常层级**：`RLAgentError` 基类 → `PolicyError` / `TrainingError` /
 *      `SafetyViolationError` / `PolicyNotFoundError` / `TrainingAlreadyRunningError`
 *
 * 稳定性：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

// 复用世界模型契约中的状态/动作字段标签（避免重复定义）
export {
  STATE_FIELD,
  STATE_FIELD_VALUES,
  STATE_FIELD_LABELS,
  isStateField,
  ACTION_FIELD,
  ACTION_FIELD_VALUES,
  ACTION_FIELD_LABELS,
  isActionField,
  DEFAULT_STATE_DIM,
  DEFAULT_ACTION_DIM,
} from './world_model'

import type {
  StateField,
  ActionField,
} from './world_model'

// 重新导出类型供本模块类型签名使用（不重复定义）
export type { StateField, ActionField }

// 任务类型常量

/**
 * RL agent 决策任务类型常量.
 *
 * 在 `PluginManifest` 中声明，由 `RLAgentPlugin` 实现并注册到
 * `ITaskRegistry`。工作流编排器通过此任务类型调度 RL agent 插件。
 */
export const RL_ACT_TASK_TYPE = 'rl_act' as const

// 优化目标常量

/**
 * 优化目标常量.
 *
 * - `MINIMIZE_CHATTER`：优先最小化颤振概率.
 * - `MAXIMIZE_MATERIAL_REMOVAL`：优先最大化材料去除率.
 * - `BALANCE`：平衡颤振抑制 / 刀具寿命 / 加工效率.
 */
export const OPTIMIZATION_TARGET = {
  MINIMIZE_CHATTER: 'minimize_chatter',
  MAXIMIZE_MATERIAL_REMOVAL: 'maximize_material_removal',
  BALANCE: 'balance',
} as const

/** 优化目标字面量类型. */
export type OptimizationTarget =
  (typeof OPTIMIZATION_TARGET)[keyof typeof OPTIMIZATION_TARGET]

/** 所有优化目标列表. */
export const OPTIMIZATION_TARGET_VALUES: readonly OptimizationTarget[] = [
  OPTIMIZATION_TARGET.MINIMIZE_CHATTER,
  OPTIMIZATION_TARGET.MAXIMIZE_MATERIAL_REMOVAL,
  OPTIMIZATION_TARGET.BALANCE,
]

/** 优化目标 → 中文标签. */
export const OPTIMIZATION_TARGET_LABELS: Readonly<Record<OptimizationTarget, string>> = {
  [OPTIMIZATION_TARGET.MINIMIZE_CHATTER]: '优先抑振',
  [OPTIMIZATION_TARGET.MAXIMIZE_MATERIAL_REMOVAL]: '优先材料去除率',
  [OPTIMIZATION_TARGET.BALANCE]: '平衡（默认）',
}

/** 优化目标 → UI Tag 类型（与 element-plus Tag type 对齐）. */
export const OPTIMIZATION_TARGET_TAG_TYPE: Readonly<Record<OptimizationTarget, string>> = {
  [OPTIMIZATION_TARGET.MINIMIZE_CHATTER]: 'warning',
  [OPTIMIZATION_TARGET.MAXIMIZE_MATERIAL_REMOVAL]: 'success',
  [OPTIMIZATION_TARGET.BALANCE]: 'primary',
}

/** 默认优化目标. */
export const DEFAULT_OPTIMIZATION_TARGET: OptimizationTarget = OPTIMIZATION_TARGET.BALANCE

/**
 * 判断优化目标是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isOptimizationTarget(value: string): value is OptimizationTarget {
  return (OPTIMIZATION_TARGET_VALUES as readonly string[]).includes(value)
}

// 策略算法常量

/**
 * RL 策略算法常量.
 *
 * - `PPO`：Proximal Policy Optimization（v1 默认）.
 * - `DQN`：Deep Q-Network（离散动作空间，v2 计划）.
 * - `SAC`：Soft Actor-Critic（v2 计划）.
 */
export const POLICY_ALGORITHM = {
  PPO: 'ppo',
  DQN: 'dqn',
  SAC: 'sac',
} as const

/** 策略算法字面量类型. */
export type PolicyAlgorithm =
  (typeof POLICY_ALGORITHM)[keyof typeof POLICY_ALGORITHM]

/** 所有策略算法列表. */
export const POLICY_ALGORITHM_VALUES: readonly PolicyAlgorithm[] = [
  POLICY_ALGORITHM.PPO,
  POLICY_ALGORITHM.DQN,
  POLICY_ALGORITHM.SAC,
]

/** 策略算法 → 中文标签. */
export const POLICY_ALGORITHM_LABELS: Readonly<Record<PolicyAlgorithm, string>> = {
  [POLICY_ALGORITHM.PPO]: 'PPO（默认）',
  [POLICY_ALGORITHM.DQN]: 'DQN（v2）',
  [POLICY_ALGORITHM.SAC]: 'SAC（v2）',
}

/** 策略算法 → UI Tag 类型. */
export const POLICY_ALGORITHM_TAG_TYPE: Readonly<Record<PolicyAlgorithm, 'success' | 'warning' | 'danger' | 'info' | 'primary'>> = {
  [POLICY_ALGORITHM.PPO]: 'primary',
  [POLICY_ALGORITHM.DQN]: 'info',
  [POLICY_ALGORITHM.SAC]: 'info',
}

/** 默认策略算法. */
export const DEFAULT_POLICY_ALGORITHM: PolicyAlgorithm = POLICY_ALGORITHM.PPO

/**
 * 判断策略算法是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isPolicyAlgorithm(value: string): value is PolicyAlgorithm {
  return (POLICY_ALGORITHM_VALUES as readonly string[]).includes(value)
}

// 训练状态常量

/**
 * 训练状态常量.
 *
 * - `IDLE`：空闲（未启动训练）.
 * - `RUNNING`：训练中.
 * - `PAUSED`：已暂停.
 * - `COMPLETED`：训练完成.
 * - `FAILED`：训练失败.
 * - `STOPPING`：收到停止请求，正在保存 checkpoint.
 */
export const TRAINING_STATUS = {
  IDLE: 'idle',
  RUNNING: 'running',
  PAUSED: 'paused',
  COMPLETED: 'completed',
  FAILED: 'failed',
  STOPPING: 'stopping',
} as const

/** 训练状态字面量类型. */
export type TrainingStatus = (typeof TRAINING_STATUS)[keyof typeof TRAINING_STATUS]

/** 所有训练状态列表. */
export const TRAINING_STATUS_VALUES: readonly TrainingStatus[] = [
  TRAINING_STATUS.IDLE,
  TRAINING_STATUS.RUNNING,
  TRAINING_STATUS.PAUSED,
  TRAINING_STATUS.COMPLETED,
  TRAINING_STATUS.FAILED,
  TRAINING_STATUS.STOPPING,
]

/** 训练状态 → 中文标签. */
export const TRAINING_STATUS_LABELS: Readonly<Record<TrainingStatus, string>> = {
  [TRAINING_STATUS.IDLE]: '空闲',
  [TRAINING_STATUS.RUNNING]: '训练中',
  [TRAINING_STATUS.PAUSED]: '已暂停',
  [TRAINING_STATUS.COMPLETED]: '训练完成',
  [TRAINING_STATUS.FAILED]: '训练失败',
  [TRAINING_STATUS.STOPPING]: '正在停止',
}

/** 训练状态 → UI Tag 类型. */
export const TRAINING_STATUS_TAG_TYPE: Readonly<Record<TrainingStatus, 'success' | 'warning' | 'danger' | 'info' | 'primary'>> = {
  [TRAINING_STATUS.IDLE]: 'info',
  [TRAINING_STATUS.RUNNING]: 'success',
  [TRAINING_STATUS.PAUSED]: 'warning',
  [TRAINING_STATUS.COMPLETED]: 'primary',
  [TRAINING_STATUS.FAILED]: 'danger',
  [TRAINING_STATUS.STOPPING]: 'warning',
}

/** 终态训练状态（不可继续训练）. */
export const TERMINAL_TRAINING_STATUS: readonly TrainingStatus[] = [
  TRAINING_STATUS.COMPLETED,
  TRAINING_STATUS.FAILED,
]

/**
 * 判断训练状态是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isTrainingStatus(value: string): value is TrainingStatus {
  return (TRAINING_STATUS_VALUES as readonly string[]).includes(value)
}

/**
 * 判断训练状态是否为终态.
 * @param value - 待校验的训练状态
 * @returns True 表示终态（COMPLETED / FAILED）
 */
export function isTerminalTrainingStatus(value: TrainingStatus): boolean {
  return (TERMINAL_TRAINING_STATUS as readonly TrainingStatus[]).includes(value)
}

// 默认模型 URI 与训练参数

/** 默认 RL 策略 URI（与后端 `RLActRequest` 默认值对齐）. */
export const DEFAULT_RL_AGENT_URI = 'model://rl_agent/1.0.0' as const

/** 默认最大训练步数（与后端 `TrainingStartRequest` 默认值对齐）. */
export const DEFAULT_MAX_STEPS = 100000 as const

/** 最小训练步数（与后端 Pydantic 校验对齐）. */
export const MIN_MAX_STEPS = 1000 as const

/** 最大训练步数上限（与后端 Pydantic 校验对齐）. */
export const MAX_MAX_STEPS = 1_000_000 as const

// 安全约束规格

/**
 * 安全约束规格（与后端 `SafetyConstraintsSpec` / `SafetyShield` 对齐）.
 *
 * SafetyShield 硬约束强制过滤违反以下约束的动作，不可被 RL 策略覆盖。
 *
 * 前置约束：
 *   - `max_chatter_probability` 必须在 [0, 1]
 *   - `max_tool_wear_increment` 必须为正数
 *   - `min_surface_quality` 必须在 [0, 1]
 */
export interface SafetyConstraintsSpec {
  /** 最大允许颤振概率 [0, 1]，默认 0.3 */
  max_chatter_probability: number
  /** 最大允许刀具磨损增量 (mm/步)，默认 0.01 */
  max_tool_wear_increment: number
  /** 最小表面质量（`1 - surface_roughness / threshold`），默认 0.8 */
  min_surface_quality: number
}

/** 默认安全约束规格（与后端 `SafetyConstraintsSpec()` 默认值对齐）. */
export const DEFAULT_SAFETY_CONSTRAINTS: Readonly<SafetyConstraintsSpec> = {
  max_chatter_probability: 0.3,
  max_tool_wear_increment: 0.01,
  min_surface_quality: 0.8,
} as const

// 决策请求/响应数据结构

/**
 * RL agent 决策请求.
 *
 * 对应后端 `RLActRequest` dataclass。
 *
 * 前置约束：
 *   - `current_state` 不能为空（至少包含全部 8 个状态字段）
 *   - `candidate_actions` 不能为空（至少 1 个，每个动作含 4 个 delta 字段）
 *   - `optimization_target` 必须合法
 *   - `model_uri` 不能为空
 */
export interface RLActRequest {
  /** 当前加工状态（字段名见 `StateField`，至少包含全部 8 个状态字段） */
  current_state: Record<string, number>
  /** 候选动作集（至少 1 个，每个动作含 4 个 delta 字段） */
  candidate_actions: Array<Record<string, number>>
  /** 优化目标（`OptimizationTarget` 常量，默认 balance） */
  optimization_target: OptimizationTarget
  /** 安全约束规格（为空则后端使用默认值） */
  safety_constraints: SafetyConstraintsSpec | null
  /** RL 策略模型 URI（如 `model://rl_agent/1.0.0`） */
  model_uri: string
}

/**
 * 单候选动作评估结果.
 *
 * 对应后端 `ActionEvaluation` dataclass。
 */
export interface ActionEvaluation {
  /** 候选动作 */
  action: Record<string, number>
  /** RL 价值函数期望回报 */
  expected_return: number
  /** 预测颤振概率 [0, 1] */
  predicted_chatter_prob: number
  /** 预测刀具磨损增量 (mm) */
  predicted_tool_wear: number
  /** 是否违反安全约束 */
  safety_violation: boolean
  /** Q 值（与 `expected_return` 一致，PPO 离线 RL 中 Q ≈ V(s)） */
  q_value: number
}

/**
 * 策略元信息.
 *
 * 对应后端 `PolicyInfo` dataclass。
 */
export interface PolicyInfo {
  /** 策略算法（`PolicyAlgorithm` 常量） */
  algorithm: PolicyAlgorithm
  /** 策略版本（semver） */
  policy_version: string
  /** 训练 episode 数 */
  training_episodes: number
  /** 探索率 ε [0, 1] */
  exploration_rate: number
}

/**
 * 推荐动作.
 *
 * 对应后端 `RecommendedAction` dataclass。
 *
 * 工程约束：`reasoning` 字段会显式提示
 * "本动作仅供 CAM 验证层参考，实际加工需经持证操作员审核"。
 */
export interface RecommendedAction {
  /** 推荐的切削参数调整量 */
  action: Record<string, number>
  /** 推荐理由（自然语言，供工程师审查） */
  reasoning: string
}

/**
 * RL agent 决策响应.
 *
 * 对应后端 `RLActResponse` dataclass。
 */
export interface RLActResponse {
  /** 推荐动作（含自然语言理由） */
  recommended_action: RecommendedAction
  /** 所有候选动作的评估结果 */
  action_evaluation: ActionEvaluation[]
  /** 策略元信息 */
  policy_info: PolicyInfo
}

// 策略版本数据结构

/**
 * RL 策略版本记录.
 *
 * 对应后端 `PolicyVersion` dataclass + ORM `RLAgentPolicyVersionORM`。
 *
 * 注：`created_at` 为 ISO 8601 字符串（后端 `to_dict()` 序列化后）。
 */
export interface PolicyVersion {
  /** 版本号（semver，如 `1.0.0`） */
  version: string
  /** 策略模型 URI（`model://rl_agent/<version>`） */
  model_uri: string
  /** 策略算法（`PolicyAlgorithm` 常量） */
  algorithm: PolicyAlgorithm
  /** 版本描述 */
  description: string
  /** 创建时间（ISO 8601 字符串） */
  created_at: string
  /** 训练 episode 数 */
  training_episodes: number
  /** 训练步数 */
  training_steps: number
  /** 训练时平均 episode 奖励 */
  mean_reward: number
  /** 是否为当前激活版本 */
  is_active: boolean
}

// 训练状态与控制

/**
 * 训练指标快照（对应后端 `TrainingMetrics` 的可序列化版本）.
 *
 * 对应后端 `TrainingMetricsSnapshot` dataclass。
 */
export interface TrainingMetricsSnapshot {
  /** 当前训练步数 */
  step: number
  /** 当前 episode 数 */
  episode: number
  /** 策略网络损失 */
  policy_loss: number
  /** 价值网络损失 */
  value_loss: number
  /** 策略熵 */
  entropy: number
  /** 近似 KL 散度 */
  approx_kl: number
  /** PPO clip 触发比例 */
  clip_fraction: number
  /** 平均 episode 奖励 */
  mean_reward: number
  /** 平均状态价值估计 */
  mean_value: number
  /** 当前 ε-greedy 探索率 */
  epsilon: number
  /** 训练耗时（秒） */
  elapsed_seconds: number
}

/**
 * 训练状态信息.
 *
 * 对应后端 `TrainingStatusInfo` dataclass。
 */
export interface TrainingStatusInfo {
  /** 训练状态（`TrainingStatus` 常量） */
  status: TrainingStatus
  /** 当前训练步数 */
  current_step: number
  /** 最大训练步数 */
  max_steps: number
  /** 当前 episode 数 */
  current_episode: number
  /** 最新训练指标（仅 RUNNING 时非空） */
  metrics: TrainingMetricsSnapshot | null
  /** 训练开始时间（ISO 8601 字符串，未开始时为 null） */
  started_at: string | null
  /** 训练结束时间（ISO 8601 字符串，终态时非空） */
  finished_at: string | null
  /** 失败原因（FAILED 时非空） */
  error_message: string | null
}

/**
 * 训练启动请求.
 *
 * 对应后端 `TrainingStartRequest` dataclass。
 *
 * 前置约束：
 *   - `max_steps` 必须为正数（1000 ~ 1000000）
 *   - `algorithm` 必须合法
 *   - `optimization_target` 必须合法
 *   - `seed` 不能为负数（为空则后端使用默认 42）
 */
export interface TrainingStartRequest {
  /** 最大训练步数（1000 ~ 1000000，默认 100000） */
  max_steps: number
  /** 随机种子（为空则使用训练器默认 42） */
  seed: number | null
  /** 策略算法（`PolicyAlgorithm` 常量，默认 ppo） */
  algorithm: PolicyAlgorithm
  /** 优化目标（`OptimizationTarget` 常量，默认 balance） */
  optimization_target: OptimizationTarget
}

// 列表查询数据结构

/** 列出 RL 策略版本的查询参数（GET /versions Query 参数）. */
export interface ListPolicyVersionsParams {
  /** 为 true 时仅返回当前激活版本 */
  active_only?: boolean
  /** 按策略算法过滤（ppo / dqn / sac） */
  algorithm?: PolicyAlgorithm | null
  /** 每页数量（1-500，默认 50） */
  limit?: number
  /** 偏移量（默认 0） */
  offset?: number
}

/** 列出 RL 策略版本的响应（GET /versions 返回 data）. */
export interface ListPolicyVersionsResponse {
  /** 版本记录列表 */
  items: PolicyVersion[]
  /** 总数 */
  total: number
  /** 每页数量 */
  limit: number
  /** 偏移量 */
  offset: number
}

// 错误码常量

/**
 * RL Agent 错误码常量.
 *
 * 与后端异常层级对齐：
 *   - `POLICY_ERROR`：策略推理失败（网络前向传播异常 / 权重加载失败）
 *   - `TRAINING_ERROR`：训练失败（环境交互异常 / 梯度爆炸 / 收敛失败）
 *   - `SAFETY_VIOLATION`：安全约束违反（所有候选动作均被 SafetyShield 过滤）
 *   - `POLICY_NOT_FOUND`：策略未找到（`model_uri` 未注册）
 *   - `TRAINING_ALREADY_RUNNING`：训练已在运行（重复启动训练）
 *   - `INTERNAL_ERROR`：未识别的服务层异常兜底
 */
export const RL_AGENT_ERROR_CODE = {
  POLICY_ERROR: 'policy_error',
  TRAINING_ERROR: 'training_error',
  SAFETY_VIOLATION: 'safety_violation',
  POLICY_NOT_FOUND: 'policy_not_found',
  TRAINING_ALREADY_RUNNING: 'training_already_running',
  INTERNAL_ERROR: 'internal_error',
} as const

/** RL Agent 错误码字面量类型. */
export type RLAgentErrorCode =
  (typeof RL_AGENT_ERROR_CODE)[keyof typeof RL_AGENT_ERROR_CODE]

/** 所有 RL Agent 错误码列表. */
export const RL_AGENT_ERROR_CODE_VALUES: readonly RLAgentErrorCode[] = [
  RL_AGENT_ERROR_CODE.POLICY_ERROR,
  RL_AGENT_ERROR_CODE.TRAINING_ERROR,
  RL_AGENT_ERROR_CODE.SAFETY_VIOLATION,
  RL_AGENT_ERROR_CODE.POLICY_NOT_FOUND,
  RL_AGENT_ERROR_CODE.TRAINING_ALREADY_RUNNING,
  RL_AGENT_ERROR_CODE.INTERNAL_ERROR,
]

/** 错误码 → 中文标签. */
export const RL_AGENT_ERROR_CODE_LABELS: Readonly<Record<RLAgentErrorCode, string>> = {
  [RL_AGENT_ERROR_CODE.POLICY_ERROR]: '策略推理失败',
  [RL_AGENT_ERROR_CODE.TRAINING_ERROR]: '训练失败',
  [RL_AGENT_ERROR_CODE.SAFETY_VIOLATION]: '安全约束违反',
  [RL_AGENT_ERROR_CODE.POLICY_NOT_FOUND]: '策略未找到',
  [RL_AGENT_ERROR_CODE.TRAINING_ALREADY_RUNNING]: '训练已在运行',
  [RL_AGENT_ERROR_CODE.INTERNAL_ERROR]: '内部错误',
}

/** 错误码 → UI Tag 类型. */
export const RL_AGENT_ERROR_CODE_TAG_TYPE: Readonly<Record<RLAgentErrorCode, string>> = {
  [RL_AGENT_ERROR_CODE.POLICY_ERROR]: 'warning',
  [RL_AGENT_ERROR_CODE.TRAINING_ERROR]: 'danger',
  [RL_AGENT_ERROR_CODE.SAFETY_VIOLATION]: 'warning',
  [RL_AGENT_ERROR_CODE.POLICY_NOT_FOUND]: 'info',
  [RL_AGENT_ERROR_CODE.TRAINING_ALREADY_RUNNING]: 'warning',
  [RL_AGENT_ERROR_CODE.INTERNAL_ERROR]: 'danger',
}

/**
 * 判断错误码是否合法.
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isRLAgentErrorCode(value: string): value is RLAgentErrorCode {
  return (RL_AGENT_ERROR_CODE_VALUES as readonly string[]).includes(value)
}

// 服务接口

/**
 * RL Agent 服务接口.
 *
 * 定义前端 Store 与后端 REST API 的契约边界。具体实现见
 * `src/stores/rlAgent.ts`（Pinia Store 对接 `/api/v1/rl-agent/*`）。
 *
 * 设计约束：
 *   1. 版本查询与训练状态查询为只读操作（`rl_agent:read` 权限）
 *   2. 决策与训练控制触发模型推理（`rl_agent:write` 权限）
 *   3. 决策结果不持久化（如需保存走工作流 `rl_act` 任务类型）
 *   4. v1 仅离线 RL：训练数据来自历史数据 + 仿真环境
 *   5. 物理执行需"持证操作员 + 导师签字 + 保险"，本契约不涉及
 */
export interface IRLAgentService {
  /** 列出 RL 策略版本（分页 + algorithm/active 过滤）. */
  listVersions(
    params?: ListPolicyVersionsParams,
  ): Promise<ListPolicyVersionsResponse>

  /** 查询策略版本详情. */
  getVersion(version: string): Promise<PolicyVersion>

  /**
   * 执行 RL 决策（不走工作流，直接调用服务层）.
   *
   * 返回推荐动作 + 候选评估 + 策略元信息。`reasoning` 字段会显式提示
   * "本动作仅供 CAM 验证层参考，实际加工需经持证操作员审核"。
   */
  act(request: RLActRequest): Promise<RLActResponse>

  /** 查询当前 RL 训练状态（无训练记录返回 status=idle）. */
  getTrainingStatus(): Promise<TrainingStatusInfo>

  /**
   * 启动 RL 训练 Workflow.
   *
   * 若已有 RUNNING 训练，后端抛 `TrainingAlreadyRunningError`。
   * 实际训练循环由后台 worker 异步执行。
   */
  startTraining(request: TrainingStartRequest): Promise<TrainingStatusInfo>

  /**
   * 停止当前 RL 训练.
   *
   * 将 RUNNING 记录置为 STOPPING，后台 worker 检测到后保存 checkpoint 并退出。
   * 若无 RUNNING 训练，返回当前状态（不报错）。
   */
  stopTraining(): Promise<TrainingStatusInfo>
}

// 契约版本

/** RL Agent 契约版本（与后端 Stable v1.0.0 对齐）. */
export const CONTRACTS_RL_AGENT_VERSION = '1.0.0' as const
