/**
 * 可解释性可视化契约（ADR-016 阶段 7 p7-5）
 *
 * 对应后端 `python/app/contracts/explainability.py`。
 * 详见 `docs/adr/ADR-016-可解释性可视化.md`。
 *
 * 设计要点：
 *   1. 4 类解释结果对应 LTC 网络的 4 个可解释维度：
 *      - 隐状态投影（hidden_state）：2D/3D 散点图可视化帧间状态演化
 *      - 门控动力学（gate_dynamics）：LTC dt 门控值与时间常数τ的时序曲线
 *      - 反事实解释（counterfactual）：扰动单输入特征的输出敏感性扫描
 *      - 置信度分布（confidence）：MC dropout 多次采样的认知不确定性分布
 *   2. 降维方法：PCA（默认）/ t-SNE（≤5000 样本）/ UMAP（可选依赖）
 *   3. payload（含大型数组）以 JSON 文件存盘，数据库只存元数据 + payload_path
 *   4. input_signature 去重：sha256 前 16 字符
 *   5. 权限模型：explainability:read / explainability:write
 *
 * 稳定性：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

// 解释类型常量

/**
 * 解释类型常量：对应 LTC 网络的 4 个可解释维度。
 *
 * - HIDDEN_STATE：隐状态投影。从 PagedHiddenStateCache 提取关键帧隐向量，
 *   降维到 2D/3D 可视化帧间状态演化轨迹。
 * - GATE_DYNAMICS：门控动力学。LTC 的 dt 门控值与时间常数 τ 的时序曲线，
 *   揭示模型在不同时间步的"记忆更新速率"。
 * - COUNTERFACTUAL：反事实解释。扰动单个输入特征（如主轴转速 +5%），
 *   扫描输出敏感性曲线。
 * - CONFIDENCE：置信度分布。MC dropout 多次随机前向采样的输出分布，
 *   分离认知不确定性（epistemic）与偶然不确定性（aleatoric）。
 */
export const EXPLANATION_TYPE = {
  HIDDEN_STATE: 'hidden_state',
  GATE_DYNAMICS: 'gate_dynamics',
  COUNTERFACTUAL: 'counterfactual',
  CONFIDENCE: 'confidence',
} as const

/** 解释类型字面量类型。 */
export type ExplanationType = (typeof EXPLANATION_TYPE)[keyof typeof EXPLANATION_TYPE]

/** 所有解释类型列表。 */
export const EXPLANATION_TYPE_VALUES: readonly ExplanationType[] = [
  EXPLANATION_TYPE.HIDDEN_STATE,
  EXPLANATION_TYPE.GATE_DYNAMICS,
  EXPLANATION_TYPE.COUNTERFACTUAL,
  EXPLANATION_TYPE.CONFIDENCE,
]

/** 解释类型 → 中文标签。 */
export const EXPLANATION_TYPE_LABELS: Readonly<Record<ExplanationType, string>> = {
  [EXPLANATION_TYPE.HIDDEN_STATE]: '隐状态投影',
  [EXPLANATION_TYPE.GATE_DYNAMICS]: '门控动力学',
  [EXPLANATION_TYPE.COUNTERFACTUAL]: '反事实解释',
  [EXPLANATION_TYPE.CONFIDENCE]: '置信度分布',
}

/** Element Plus Tag 类型字面量（与 el-tag type 对齐）。 */
export type ExplanationTagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'

/** 解释类型 → UI Tag 类型（与 element-plus Tag type 对齐）。 */
export const EXPLANATION_TYPE_TAG_TYPE: Readonly<Record<ExplanationType, ExplanationTagType>> = {
  [EXPLANATION_TYPE.HIDDEN_STATE]: 'primary',
  [EXPLANATION_TYPE.GATE_DYNAMICS]: 'success',
  [EXPLANATION_TYPE.COUNTERFACTUAL]: 'warning',
  [EXPLANATION_TYPE.CONFIDENCE]: 'info',
}

/**
 * 判断解释类型是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isExplanationType(value: string): value is ExplanationType {
  return (EXPLANATION_TYPE_VALUES as readonly string[]).includes(value)
}

// 降维方法常量

/**
 * 降维方法常量：将高维隐向量投影到 2D/3D 可视化空间。
 *
 * - PCA：主成分分析（默认）。线性方法，速度快，保留全局结构，适合 >5000 样本。
 * - TSNE：t-SNE 非线性降维。保留局部邻域结构，O(n²) 复杂度，样本数 ≤5000。
 * - UMAP：UMAP 非线性降维。兼顾局部与全局结构，需可选依赖 umap-learn。
 */
export const PROJECTION_METHOD = {
  PCA: 'pca',
  TSNE: 'tsne',
  UMAP: 'umap',
} as const

/** 降维方法字面量类型。 */
export type ProjectionMethod = (typeof PROJECTION_METHOD)[keyof typeof PROJECTION_METHOD]

/** 所有降维方法列表。 */
export const PROJECTION_METHOD_VALUES: readonly ProjectionMethod[] = [
  PROJECTION_METHOD.PCA,
  PROJECTION_METHOD.TSNE,
  PROJECTION_METHOD.UMAP,
]

/** 降维方法 → 中文标签。 */
export const PROJECTION_METHOD_LABELS: Readonly<Record<ProjectionMethod, string>> = {
  [PROJECTION_METHOD.PCA]: 'PCA（默认）',
  [PROJECTION_METHOD.TSNE]: 't-SNE',
  [PROJECTION_METHOD.UMAP]: 'UMAP',
}

/** 默认降维方法。 */
export const DEFAULT_PROJECTION_METHOD: ProjectionMethod = PROJECTION_METHOD.PCA

/** 默认投影维度。 */
export const DEFAULT_PROJECTION_DIM: number = 2

/** 默认最大帧数。 */
export const DEFAULT_MAX_FRAMES: number = 1000

/** 默认异常检测 sigma 阈值。 */
export const DEFAULT_ANOMALY_SIGMA: number = 2.0

/** 默认反事实扰动步长（5%）。 */
export const DEFAULT_PERTURBATION_STEP: number = 0.05

/** 默认 MC dropout 采样次数。 */
export const DEFAULT_SAMPLE_COUNT: number = 30

/**
 * 判断降维方法是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isProjectionMethod(value: string): value is ProjectionMethod {
  return (PROJECTION_METHOD_VALUES as readonly string[]).includes(value)
}

// 对比类型常量

/**
 * 解释对比类型常量。
 *
 * - SAME_MODEL_DIFF_INPUT：同模型不同输入（对比输入敏感性）
 * - DIFF_MODEL_SAME_INPUT：不同模型同输入（对比模型版本差异）
 * - DIFF_MODEL_DIFF_INPUT：不同模型不同输入（综合对比）
 */
export const COMPARISON_TYPE = {
  SAME_MODEL_DIFF_INPUT: 'same_model_diff_input',
  DIFF_MODEL_SAME_INPUT: 'diff_model_same_input',
  DIFF_MODEL_DIFF_INPUT: 'diff_model_diff_input',
} as const

/** 对比类型字面量类型。 */
export type ComparisonType = (typeof COMPARISON_TYPE)[keyof typeof COMPARISON_TYPE]

/** 所有对比类型列表。 */
export const COMPARISON_TYPE_VALUES: readonly ComparisonType[] = [
  COMPARISON_TYPE.SAME_MODEL_DIFF_INPUT,
  COMPARISON_TYPE.DIFF_MODEL_SAME_INPUT,
  COMPARISON_TYPE.DIFF_MODEL_DIFF_INPUT,
]

/** 对比类型 → 中文标签。 */
export const COMPARISON_TYPE_LABELS: Readonly<Record<ComparisonType, string>> = {
  [COMPARISON_TYPE.SAME_MODEL_DIFF_INPUT]: '同模型不同输入',
  [COMPARISON_TYPE.DIFF_MODEL_SAME_INPUT]: '不同模型同输入',
  [COMPARISON_TYPE.DIFF_MODEL_DIFF_INPUT]: '不同模型不同输入',
}

/** 对比类型 → UI Tag 类型。 */
export const COMPARISON_TYPE_TAG_TYPE: Readonly<Record<ComparisonType, 'success' | 'warning' | 'danger' | 'info' | 'primary'>> = {
  [COMPARISON_TYPE.SAME_MODEL_DIFF_INPUT]: 'info',
  [COMPARISON_TYPE.DIFF_MODEL_SAME_INPUT]: 'primary',
  [COMPARISON_TYPE.DIFF_MODEL_DIFF_INPUT]: 'warning',
}

/** 默认对比类型。 */
export const DEFAULT_COMPARISON_TYPE: ComparisonType = COMPARISON_TYPE.SAME_MODEL_DIFF_INPUT

/**
 * 判断对比类型是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isComparisonType(value: string): value is ComparisonType {
  return (COMPARISON_TYPE_VALUES as readonly string[]).includes(value)
}

// 数据结构：解释结果 payload

/**
 * 隐状态投影解释结果。
 *
 * 前端用此数据绘制散点图（颜色编码关键帧/能量），展示帧间状态演化轨迹。
 */
export interface HiddenStateExplanation {
  /** 解释类型标识（固定为 hidden_state） */
  explanation_type: typeof EXPLANATION_TYPE.HIDDEN_STATE
  /** 帧 ID 序列（与 projections 一一对应） */
  frame_ids: number[]
  /** 降维后的坐标。每项长度为 projection_dim（2 或 3） */
  projections: number[][]
  /** 每帧信号能量（L2 范数平方均值），用于颜色编码 */
  energies: number[]
  /** 每帧是否为关键帧（来自 KeyframeDecision.is_keyframe） */
  keyframe_flags: boolean[]
  /** 实际使用的降维方法 */
  projection_method: ProjectionMethod
  /** 投影维度（2 或 3） */
  projection_dim: number
  /** 原始隐向量维度（降维前） */
  hidden_dim: number
  /** 样本（帧）数量 */
  sample_count: number
  /** 解释所用模型 URI */
  model_uri: string
}

/**
 * 门控动力学解释结果。
 *
 * 前端用此数据绘制 dt 门控值与 τ 时间常数的时序曲线，标识异常帧。
 */
export interface GateDynamicsExplanation {
  /** 解释类型标识（固定为 gate_dynamics） */
  explanation_type: typeof EXPLANATION_TYPE.GATE_DYNAMICS
  /** 帧 ID 序列 */
  frame_ids: number[]
  /** 每帧每特征的门控值（shape=[N, hidden_dim]） */
  gate_values: number[][]
  /** 每帧每特征的时间常数 τ（shape=[N, hidden_dim]），τ = 1 / dt */
  time_constants: number[][]
  /** 每个特征的全局平均门控值（shape=[hidden_dim]） */
  mean_gate_per_feature: number[]
  /** 异常帧 ID 列表（门控值超过 mean ± 2σ 的帧） */
  anomaly_frames: number[]
  /** 解释所用模型 URI */
  model_uri: string
}

/**
 * 反事实解释结果。
 *
 * 前端用此数据绘制扰动-输出敏感性曲线，标注临界点。
 */
export interface CounterfactualExplanation {
  /** 解释类型标识（固定为 counterfactual） */
  explanation_type: typeof EXPLANATION_TYPE.COUNTERFACTUAL
  /** 基准输入（特征名 → 值） */
  base_input: Record<string, number>
  /** 被扰动的特征名 */
  perturbed_feature: string
  /** 扰动值序列（如 [-10, -5, 0, 5, 10] 表示相对基准 ±10%） */
  perturbation_range: number[]
  /** 每个扰动点对应的模型输出（颤振概率 / 刀具磨损等） */
  outputs: number[]
  /** 敏感度系数（输出对扰动的一阶导数均值），绝对值越大表示该特征影响越大 */
  sensitivity: number
  /** 临界点列表（输出突变点），每项含 perturbation / output / delta */
  critical_points: Array<{
    perturbation: number
    output: number
    delta: number
  }>
  /** 解释所用模型 URI */
  model_uri: string
}

/** 置信度分位数（p5/p25/p50/p75/p95）。 */
export type ConfidencePercentiles = Record<string, number>

/** 直方图数据（bins + counts）。 */
export interface ConfidenceHistogram {
  bins: number[]
  counts: number[]
}

/**
 * 置信度分布解释结果。
 *
 * 前端用此数据绘制箱线图与直方图，分离认知/偶然不确定性。
 */
export interface ConfidenceExplanation {
  /** 解释类型标识（固定为 confidence） */
  explanation_type: typeof EXPLANATION_TYPE.CONFIDENCE
  /** MC dropout 采样次数 */
  sample_count: number
  /** 输出均值（最终预测值） */
  mean: number
  /** 输出标准差（总不确定性） */
  std: number
  /** 输出分位数（p5/p25/p50/p75/p95） */
  percentiles: ConfidencePercentiles
  /** 直方图数据（bins + counts） */
  histogram: ConfidenceHistogram
  /** 认知不确定性（多次采样的标准差，可由数据降低） */
  epistemic: number
  /** 偶然不确定性（数据噪声估计，无法由数据降低） */
  aleatoric: number
  /** 异常分数（std / (|mean| + ε)），高值表示模型对该输入不确定 */
  anomaly_score: number
  /** 解释所用模型 URI */
  model_uri: string
}

/**
 * 解释结果 payload 联合类型。
 *
 * 通过 explanation_type 字段区分具体类型，前端可用类型守卫收窄。
 */
export type ExplanationPayload =
  | HiddenStateExplanation
  | GateDynamicsExplanation
  | CounterfactualExplanation
  | ConfidenceExplanation

// 数据结构：解释记录与对比

/** 解释记录附加元数据（如降维方法、采样次数、异常帧数等）。 */
export type ExplanationMetadata = Record<string, unknown>

/**
 * 解释记录契约：数据库表 explanation_records 的投影。
 *
 * payload（含大型数组）以 JSON 文件存盘，前端按需通过
 * `?include_payload=true` 加载完整 payload 内容。
 */
export interface ExplanationRecord {
  /** 记录 ID（exp_ 前缀 + uuid） */
  id: string
  /** 解释类型 */
  explanation_type: ExplanationType
  /** 解释所用模型 URI */
  model_uri: string
  /** 关联实验快照 ID（可能为空，不建外键） */
  source_snapshot_id: string | null
  /** 输入签名（sha256 前 16 字符，用于去重） */
  input_signature: string
  /** payload JSON 文件绝对路径 */
  payload_path: string
  /** payload 文件大小（字节） */
  payload_size_bytes: number
  /** 附加元数据 */
  metadata: ExplanationMetadata
  /** 创建者 user_id 或 plugin_id */
  created_by: string | null
  /** 创建时间（ISO 8601 字符串） */
  created_at: string
  /** 过期时间（ISO 8601 字符串，过期后由清理任务删除 payload 文件） */
  expires_at: string | null
}

/**
 * 解释记录详情（含完整 payload）。
 *
 * 通过 GET /{id}?include_payload=true 获取。
 */
export interface ExplanationRecordDetail extends ExplanationRecord {
  /** 完整 payload 内容（仅 include_payload=true 时存在） */
  payload?: ExplanationPayload
}

/**
 * 解释对比记录契约：数据库表 explanation_comparisons 的投影。
 */
export interface ExplanationComparison {
  /** 对比记录 ID（cmp_ 前缀 + uuid） */
  id: string
  /** 基准解释记录 ID */
  base_explanation_id: string
  /** 对比解释记录 ID */
  compared_explanation_id: string
  /** 对比类型 */
  comparison_type: ComparisonType
  /** 差异 payload JSON 文件路径 */
  diff_payload_path: string
  /** 创建者 */
  created_by: string | null
  /** 创建时间（ISO 8601 字符串） */
  created_at: string
}

// API 请求/响应接口（对应后端 8 个端点）

/**
 * 端点 1: POST /hidden-state 请求体。
 *
 * 权限：explainability:write
 */
export interface GenerateHiddenStateRequest {
  /** 模型 URI（如 model://LTC-ChatterPredictor/1.0.0） */
  model_uri: string
  /** 关联实验快照 ID（可选） */
  source_snapshot_id?: string | null
  /** 降维方法（默认 pca） */
  projection_method?: ProjectionMethod
  /** 投影维度（2 或 3，默认 2） */
  projection_dim?: number
  /** 最大帧数（1-10000，超过则均匀采样，默认 1000） */
  max_frames?: number
  /** 创建者（user_id 或 plugin_id） */
  created_by?: string | null
}

/** 端点 1 响应数据：解释记录。 */
export type GenerateHiddenStateResponse = ExplanationRecord

/**
 * 端点 2: POST /gate-dynamics 请求体。
 *
 * 权限：explainability:write
 */
export interface GenerateGateDynamicsRequest {
  /** 模型 URI */
  model_uri: string
  /** 关联实验快照 ID（可选） */
  source_snapshot_id?: string | null
  /** 异常检测阈值（1.0-5.0，默认 2.0） */
  anomaly_sigma?: number
  /** 创建者 */
  created_by?: string | null
}

/** 端点 2 响应数据：解释记录。 */
export type GenerateGateDynamicsResponse = ExplanationRecord

/**
 * 端点 3: POST /counterfactual 请求体。
 *
 * 权限：explainability:write
 */
export interface GenerateCounterfactualRequest {
  /** 模型 URI */
  model_uri: string
  /** 基准输入（特征名 → 值），至少 1 个特征 */
  base_input: Record<string, number>
  /** 被扰动的特征名（必须在 base_input 中） */
  perturbed_feature: string
  /** 扰动值序列（如为空则按 perturbation_step 生成） */
  perturbation_range?: number[] | null
  /** 扰动步长（相对基准值的比例，0.01-0.5，默认 0.05 即 5%） */
  perturbation_step?: number
  /** 关联实验快照 ID（可选） */
  source_snapshot_id?: string | null
  /** 创建者 */
  created_by?: string | null
}

/** 端点 3 响应数据：解释记录。 */
export type GenerateCounterfactualResponse = ExplanationRecord

/**
 * 端点 4: POST /confidence 请求体。
 *
 * 权限：explainability:write
 */
export interface GenerateConfidenceRequest {
  /** 模型 URI */
  model_uri: string
  /** 输入数据（特征名 → 值） */
  input_data: Record<string, unknown>
  /** MC dropout 采样次数（5-200，默认 30） */
  sample_count?: number
  /** 关联实验快照 ID（可选） */
  source_snapshot_id?: string | null
  /** 创建者 */
  created_by?: string | null
}

/** 端点 4 响应数据：解释记录。 */
export type GenerateConfidenceResponse = ExplanationRecord

/**
 * 端点 5: GET / 查询参数。
 *
 * 权限：explainability:read
 */
export interface ListExplanationsParams {
  /** 按解释类型过滤（可选） */
  explanation_type?: ExplanationType | null
  /** 按模型 URI 过滤（可选） */
  model_uri?: string | null
  /** 每页数量（1-500，默认 50） */
  limit?: number
  /** 分页偏移（默认 0） */
  offset?: number
}

/** 端点 5 响应数据：解释记录分页列表。 */
export interface ListExplanationsResponse {
  /** 记录列表 */
  items: ExplanationRecord[]
  /** 总数 */
  total: number
  /** 每页数量 */
  limit: number
  /** 分页偏移 */
  offset: number
}

/**
 * 端点 6: GET /{explanation_id} 查询参数。
 *
 * 权限：explainability:read
 */
export interface GetExplanationParams {
  /** 为 true 时加载完整 payload 内容（含大型数组） */
  include_payload?: boolean
}

/** 端点 6 响应数据：解释详情（含 payload 时附加 payload 字段）。 */
export type GetExplanationResponse = ExplanationRecordDetail

/** 端点 7: DELETE /{explanation_id} 响应数据。 */
export interface DeleteExplanationResponse {
  /** 被删除的解释 ID */
  explanation_id: string
  /** 是否删除成功 */
  deleted: boolean
}

/**
 * 端点 8: POST /compare 请求体。
 *
 * 权限：explainability:read
 *
 * 注意：两条解释的 explanation_type 必须一致，否则后端抛 ComparisonMismatchError。
 */
export interface CompareExplanationsRequest {
  /** 基准解释记录 ID */
  base_explanation_id: string
  /** 对比解释记录 ID（不能与 base 相同） */
  compared_explanation_id: string
  /** 对比类型（默认 same_model_diff_input） */
  comparison_type?: ComparisonType
  /** 创建者 */
  created_by?: string | null
}

/** 端点 8 响应数据：解释对比记录。 */
export type CompareExplanationsResponse = ExplanationComparison

// 错误码常量（与后端 ExplainabilityError.code 对齐）

/** 可解释性错误码常量（与后端 ExplainabilityError 子类的 code 字段对齐）。 */
export const EXPLAINABILITY_ERROR_CODE = {
  /** 基类错误 */
  EXPLAINABILITY_ERROR: 'EXPLAINABILITY_ERROR',
  /** 解释记录未找到 */
  EXPLANATION_NOT_FOUND: 'EXPLANATION_NOT_FOUND',
  /** 解释请求参数校验失败 */
  EXPLANATION_VALIDATION_ERROR: 'EXPLANATION_VALIDATION_ERROR',
  /** 降维投影失败 */
  PROJECTION_ERROR: 'PROJECTION_ERROR',
  /** MC dropout 采样失败 */
  SAMPLING_ERROR: 'SAMPLING_ERROR',
  /** 对比的两条解释类型不一致 */
  COMPARISON_MISMATCH: 'COMPARISON_MISMATCH',
} as const

/** 可解释性错误码字面量类型。 */
export type ExplainabilityErrorCode =
  (typeof EXPLAINABILITY_ERROR_CODE)[keyof typeof EXPLAINABILITY_ERROR_CODE]

/** 所有可解释性错误码列表。 */
export const EXPLAINABILITY_ERROR_CODE_VALUES: readonly ExplainabilityErrorCode[] = [
  EXPLAINABILITY_ERROR_CODE.EXPLAINABILITY_ERROR,
  EXPLAINABILITY_ERROR_CODE.EXPLANATION_NOT_FOUND,
  EXPLAINABILITY_ERROR_CODE.EXPLANATION_VALIDATION_ERROR,
  EXPLAINABILITY_ERROR_CODE.PROJECTION_ERROR,
  EXPLAINABILITY_ERROR_CODE.SAMPLING_ERROR,
  EXPLAINABILITY_ERROR_CODE.COMPARISON_MISMATCH,
]

/** 错误码 → 中文说明。 */
export const EXPLAINABILITY_ERROR_CODE_LABELS: Readonly<Record<ExplainabilityErrorCode, string>> = {
  [EXPLAINABILITY_ERROR_CODE.EXPLAINABILITY_ERROR]: '可解释性服务错误',
  [EXPLAINABILITY_ERROR_CODE.EXPLANATION_NOT_FOUND]: '解释记录不存在',
  [EXPLAINABILITY_ERROR_CODE.EXPLANATION_VALIDATION_ERROR]: '请求参数校验失败',
  [EXPLAINABILITY_ERROR_CODE.PROJECTION_ERROR]: '降维投影失败',
  [EXPLAINABILITY_ERROR_CODE.SAMPLING_ERROR]: 'MC dropout 采样失败',
  [EXPLAINABILITY_ERROR_CODE.COMPARISON_MISMATCH]: '对比类型不匹配',
}

/**
 * 判断错误码是否合法。
 * @param value - 待校验的字符串
 * @returns True 表示合法
 */
export function isExplainabilityErrorCode(value: string): value is ExplainabilityErrorCode {
  return (EXPLAINABILITY_ERROR_CODE_VALUES as readonly string[]).includes(value)
}

// 服务接口契约

/**
 * 可解释性服务接口契约。
 *
 * 对应后端 `IExplainabilityService`。前端 Store 通过 API 客户端实现此接口。
 * 单例模式，后端通过 `get_explainability_service()` 获取。
 */
export interface IExplainabilityService {
  /** 生成隐状态投影解释（端点 1） */
  generateHiddenStateExplanation(
    request: GenerateHiddenStateRequest,
  ): Promise<GenerateHiddenStateResponse>

  /** 生成门控动力学解释（端点 2） */
  generateGateDynamicsExplanation(
    request: GenerateGateDynamicsRequest,
  ): Promise<GenerateGateDynamicsResponse>

  /** 生成反事实解释（端点 3） */
  generateCounterfactualExplanation(
    request: GenerateCounterfactualRequest,
  ): Promise<GenerateCounterfactualResponse>

  /** 生成置信度分布解释（端点 4，MC dropout 采样） */
  generateConfidenceExplanation(
    request: GenerateConfidenceRequest,
  ): Promise<GenerateConfidenceResponse>

  /** 列出历史解释记录（端点 5） */
  listExplanations(
    params: ListExplanationsParams,
  ): Promise<ListExplanationsResponse>

  /** 查询解释详情（端点 6） */
  getExplanation(
    explanationId: string,
    params?: GetExplanationParams,
  ): Promise<GetExplanationResponse>

  /** 删除解释记录（端点 7） */
  deleteExplanation(explanationId: string): Promise<DeleteExplanationResponse>

  /** 对比两个解释（端点 8） */
  compareExplanations(
    request: CompareExplanationsRequest,
  ): Promise<CompareExplanationsResponse>
}

// 契约版本

/** 可解释性契约版本（与后端对齐）。 */
export const CONTRACTS_EXPLAINABILITY_VERSION = '1.0.0'
