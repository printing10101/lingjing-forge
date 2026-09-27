/**
 * 可解释性可视化 Pinia Store（ADR-016 阶段 7 p7-6：LTC 隐状态/门控/反事实/置信度解释）
 *
 * 对接后端 `python/app/api/v1/explainability.py` 8 个端点：
 *   - POST   /api/v1/explainability/hidden-state           生成隐状态投影解释（PCA/t-SNE/UMAP 降维）
 *   - POST   /api/v1/explainability/gate-dynamics          生成门控动力学解释（dt 门控值时序 + 异常帧检测）
 *   - POST   /api/v1/explainability/counterfactual         生成反事实解释（单特征扰动 + 敏感度扫描）
 *   - POST   /api/v1/explainability/confidence             生成置信度分布解释（MC dropout 多次采样）
 *   - GET    /api/v1/explainability/                       列出历史解释记录（分页 + 类型/模型过滤）
 *   - GET    /api/v1/explainability/{explanation_id}       查询解释详情（可选 ?include_payload=true 加载 payload）
 *   - DELETE /api/v1/explainability/{explanation_id}       删除解释记录（同时删除 payload 文件）
 *   - POST   /api/v1/explainability/compare                对比两个解释（生成差异 payload）
 *
 * 设计要点：
 *   1. 4 类解释结果对应 LTC 网络的 4 个可解释维度：
 *      - HIDDEN_STATE：隐状态投影，前端绘制散点图（颜色编码关键帧/能量）
 *      - GATE_DYNAMICS：门控动力学，前端绘制 dt/τ 时序曲线 + 异常帧高亮
 *      - COUNTERFACTUAL：反事实解释，前端绘制扰动-输出敏感性曲线
 *      - CONFIDENCE：置信度分布，前端绘制直方图 + 分位数标注
 *   2. payload（含大型数组）以 JSON 文件存盘，后端 `GET /{id}` 默认仅返回元数据，
 *      前端按需通过 `?include_payload=true` 加载完整 payload 内容（避免列表页膨胀）。
 *   3. 4 个生成端点为同步执行（解释生成通常 <5s），返回 ExplanationRecord（含 payload_path）。
 *   4. 对比端点要求两条解释 explanation_type 一致，否则后端返回 COMPARISON_MISMATCH。
 *   5. 错误统一通过 extractErrorMessage 提取，存入 error state。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import http from '@/utils/http'
import { extractErrorMessage } from '@/utils/error-handler'
import { unwrap } from '@/utils/response'
import { API_CONFIG, buildApiPath } from '@/config/api'
import {
  type ExplanationRecord,
  type ExplanationRecordDetail,
  type ExplanationComparison,
  type GenerateHiddenStateRequest,
  type GenerateHiddenStateResponse,
  type GenerateGateDynamicsRequest,
  type GenerateGateDynamicsResponse,
  type GenerateCounterfactualRequest,
  type GenerateCounterfactualResponse,
  type GenerateConfidenceRequest,
  type GenerateConfidenceResponse,
  type ListExplanationsParams,
  type ListExplanationsResponse,
  type GetExplanationParams,
  type GetExplanationResponse,
  type DeleteExplanationResponse,
  type CompareExplanationsRequest,
  type CompareExplanationsResponse,
} from '@/contracts/explainability'

// Store 局部类型（派生 / UI 辅助）

/** 解释记录分页信息。 */
interface ExplanationPaginationState {
  total: number
  limit: number
  offset: number
}

/** fetchExplanations 入参（与后端 Query 参数对齐）。 */
interface FetchExplanationsParams extends ListExplanationsParams {}

/** fetchExplanation 入参（与后端 Query 参数对齐）。 */
interface FetchExplanationParams extends GetExplanationParams {}

// Store 定义

export const useExplainabilityStore = defineStore(
  'explainability',
  () => {
// State：解释记录列表

    /** 解释记录列表（list 端点返回的 items）。 */
    const explanations = ref<ExplanationRecord[]>([])
    /** 解释记录分页信息。 */
    const explanationPagination = ref<ExplanationPaginationState>({
      total: 0,
      limit: 100,
      offset: 0,
    })

// State：当前解释记录详情

    /** 当前查看的解释记录详情（include_payload=true 时含 payload）。 */
    const currentExplanation = ref<ExplanationRecordDetail | null>(null)

// State：最近一次对比结果

    /** 最近一次对比响应（含 diff_payload_path）。 */
    const lastComparisonResult = ref<ExplanationComparison | null>(null)

// State：最近一次操作响应（用于 UI 提示）

    /** 最近一次隐状态投影解释响应。 */
    const lastHiddenStateResult = ref<GenerateHiddenStateResponse | null>(null)
    /** 最近一次门控动力学解释响应。 */
    const lastGateDynamicsResult = ref<GenerateGateDynamicsResponse | null>(null)
    /** 最近一次反事实解释响应。 */
    const lastCounterfactualResult = ref<GenerateCounterfactualResponse | null>(null)
    /** 最近一次置信度分布解释响应。 */
    const lastConfidenceResult = ref<GenerateConfidenceResponse | null>(null)
    /** 最近一次删除响应。 */
    const lastDeleteResult = ref<DeleteExplanationResponse | null>(null)

// Loading 标志

    const generatingHiddenState = ref(false) // POST /hidden-state
    const generatingGateDynamics = ref(false) // POST /gate-dynamics
    const generatingCounterfactual = ref(false) // POST /counterfactual
    const generatingConfidence = ref(false) // POST /confidence
    const explanationsLoading = ref(false) // GET /
    const explanationLoading = ref(false) // GET /{id}
    const deleting = ref(false) // DELETE /{id}
    const comparing = ref(false) // POST /compare
    const error = ref<string | null>(null)

// Computed

    /** 是否有解释记录。 */
    const hasExplanations = computed<boolean>(() => explanations.value.length > 0)

    /** 解释记录列表当前页码（1-based）。 */
    const currentPage = computed<number>(() =>
      explanationPagination.value.limit > 0
        ? Math.floor(explanationPagination.value.offset / explanationPagination.value.limit) + 1
        : 1,
    )

    /** 解释记录列表总页数。 */
    const totalPages = computed<number>(() =>
      explanationPagination.value.limit > 0
        ? Math.ceil(explanationPagination.value.total / explanationPagination.value.limit)
        : 1,
    )

    /** 是否有任何加载操作进行中。 */
    const anyLoading = computed<boolean>(
      () =>
        generatingHiddenState.value ||
        generatingGateDynamics.value ||
        generatingCounterfactual.value ||
        generatingConfidence.value ||
        explanationsLoading.value ||
        explanationLoading.value ||
        deleting.value ||
        comparing.value,
    )

    // unwrap() 已提取到 @/utils/response.ts（消除 6 个 Store 的重复定义）

// Actions：生成解释

    /**
     * 生成隐状态投影解释.
     * 对应 POST /api/v1/explainability/hidden-state
     *
     * 从 PagedHiddenStateCache 提取关键帧隐向量，降维到 2D/3D 可视化空间。
     * 前端用此数据绘制散点图（颜色编码关键帧/能量），展示帧间状态演化轨迹。
     *
     * @param request - 生成请求（含 model_uri / source_snapshot_id / projection_method / projection_dim / max_frames / created_by）
     * @returns 解释记录（含 payload_path），失败返回 null
     */
    async function generateHiddenStateExplanation(
      request: GenerateHiddenStateRequest,
    ): Promise<GenerateHiddenStateResponse | null> {
      generatingHiddenState.value = true
      error.value = null
      try {
        const response = await http.post(
          buildApiPath(API_CONFIG.EXPLAINABILITY, '/hidden-state'),
          request,
        )
        const payload = unwrap<GenerateHiddenStateResponse>(response)
        lastHiddenStateResult.value = payload
        // 生成成功后刷新列表（确保新记录立即可见）
        await fetchExplanations({
          limit: explanationPagination.value.limit,
          offset: 0,
        })
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '生成隐状态投影解释失败')
        return null
      } finally {
        generatingHiddenState.value = false
      }
    }

    /**
     * 生成门控动力学解释.
     * 对应 POST /api/v1/explainability/gate-dynamics
     *
     * 提取 LTC 的 dt 门控值与时间常数 τ 的时序曲线，检测异常帧
     * （门控值超过 mean ± anomaly_sigma*std 的帧）。
     * 前端用此数据绘制 dt/τ 时序曲线 + 异常帧高亮。
     *
     * @param request - 生成请求（含 model_uri / source_snapshot_id / anomaly_sigma / created_by）
     * @returns 解释记录（含 payload_path），失败返回 null
     */
    async function generateGateDynamicsExplanation(
      request: GenerateGateDynamicsRequest,
    ): Promise<GenerateGateDynamicsResponse | null> {
      generatingGateDynamics.value = true
      error.value = null
      try {
        const response = await http.post(
          buildApiPath(API_CONFIG.EXPLAINABILITY, '/gate-dynamics'),
          request,
        )
        const payload = unwrap<GenerateGateDynamicsResponse>(response)
        lastGateDynamicsResult.value = payload
        await fetchExplanations({
          limit: explanationPagination.value.limit,
          offset: 0,
        })
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '生成门控动力学解释失败')
        return null
      } finally {
        generatingGateDynamics.value = false
      }
    }

    /**
     * 生成反事实解释.
     * 对应 POST /api/v1/explainability/counterfactual
     *
     * 扰动单个输入特征（如主轴转速 +5%），扫描输出敏感性曲线，
     * 返回 critical_points（关键拐点）。前端用此数据绘制扰动-输出曲线。
     *
     * 前置约束：
     *   - base_input 不能为空
     *   - perturbed_feature 必须在 base_input 中
     *
     * @param request - 生成请求（含 model_uri / base_input / perturbed_feature / perturbation_range / perturbation_step / source_snapshot_id / created_by）
     * @returns 解释记录（含 payload_path），失败返回 null
     */
    async function generateCounterfactualExplanation(
      request: GenerateCounterfactualRequest,
    ): Promise<GenerateCounterfactualResponse | null> {
      generatingCounterfactual.value = true
      error.value = null
      try {
        const response = await http.post(
          buildApiPath(API_CONFIG.EXPLAINABILITY, '/counterfactual'),
          request,
        )
        const payload = unwrap<GenerateCounterfactualResponse>(response)
        lastCounterfactualResult.value = payload
        await fetchExplanations({
          limit: explanationPagination.value.limit,
          offset: 0,
        })
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '生成反事实解释失败')
        return null
      } finally {
        generatingCounterfactual.value = false
      }
    }

    /**
     * 生成置信度分布解释.
     * 对应 POST /api/v1/explainability/confidence
     *
     * 通过 MC dropout 多次随机前向采样，分离认知不确定性（epistemic）
     * 与偶然不确定性（aleatoric）。前端用此数据绘制直方图 + 分位数标注。
     *
     * 前置约束：
     *   - input_data 不能为空
     *
     * @param request - 生成请求（含 model_uri / input_data / sample_count / source_snapshot_id / created_by）
     * @returns 解释记录（含 payload_path），失败返回 null
     */
    async function generateConfidenceExplanation(
      request: GenerateConfidenceRequest,
    ): Promise<GenerateConfidenceResponse | null> {
      generatingConfidence.value = true
      error.value = null
      try {
        const response = await http.post(
          buildApiPath(API_CONFIG.EXPLAINABILITY, '/confidence'),
          request,
        )
        const payload = unwrap<GenerateConfidenceResponse>(response)
        lastConfidenceResult.value = payload
        await fetchExplanations({
          limit: explanationPagination.value.limit,
          offset: 0,
        })
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '生成置信度分布解释失败')
        return null
      } finally {
        generatingConfidence.value = false
      }
    }

// Actions：列表查询

    /**
     * 列出历史解释记录（分页 + 类型/模型过滤）.
     * 对应 GET /api/v1/explainability/
     *
     * @param params - 查询参数（explanation_type / model_uri / limit / offset）
     * @returns 解释记录列表响应，失败返回 null
     */
    async function fetchExplanations(
      params: FetchExplanationsParams = {},
    ): Promise<ListExplanationsResponse | null> {
      explanationsLoading.value = true
      error.value = null
      try {
        const response = await http.get(
          buildApiPath(API_CONFIG.EXPLAINABILITY, '/'),
          {
            params: {
              explanation_type: params.explanation_type ?? undefined,
              model_uri: params.model_uri ?? undefined,
              limit: params.limit ?? 100,
              offset: params.offset ?? 0,
            },
          },
        )
        const payload = unwrap<ListExplanationsResponse>(response)
        explanations.value = payload.items ?? []
        explanationPagination.value = {
          total: payload.total ?? 0,
          limit: payload.limit ?? 100,
          offset: payload.offset ?? 0,
        }
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '获取解释记录列表失败')
        return null
      } finally {
        explanationsLoading.value = false
      }
    }

    /**
     * 查询解释详情（可选加载 payload）.
     * 对应 GET /api/v1/explainability/{explanation_id}
     *
     * 默认仅返回元数据；设置 include_payload=true 加载完整 payload（含大型数组）。
     * payload 加载后存入 currentExplanation.payload，前端可直接消费。
     *
     * @param explanationId - 解释记录 ID
     * @param params - 查询参数（include_payload: boolean）
     * @returns 解释记录详情（含可选 payload），失败返回 null
     */
    async function fetchExplanation(
      explanationId: string,
      params: FetchExplanationParams = {},
    ): Promise<GetExplanationResponse | null> {
      explanationLoading.value = true
      error.value = null
      try {
        const response = await http.get(
          buildApiPath(API_CONFIG.EXPLAINABILITY, `/${explanationId}`),
          {
            params: {
              include_payload: params.include_payload ?? false,
            },
          },
        )
        const payload = unwrap<GetExplanationResponse>(response)
        currentExplanation.value = payload
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '获取解释详情失败')
        return null
      } finally {
        explanationLoading.value = false
      }
    }

// Actions：删除

    /**
     * 删除解释记录（同时删除 payload 文件）.
     * 对应 DELETE /api/v1/explainability/{explanation_id}
     *
     * 删除后自动刷新当前列表（保持列表与后端一致）。
     * 若删除的是 currentExplanation 对应的记录，同时清空 currentExplanation。
     *
     * @param explanationId - 解释记录 ID
     * @returns 删除响应（含 explanation_id / deleted: true），失败返回 null
     */
    async function deleteExplanation(
      explanationId: string,
    ): Promise<DeleteExplanationResponse | null> {
      deleting.value = true
      error.value = null
      try {
        const response = await http.delete(
          buildApiPath(API_CONFIG.EXPLAINABILITY, `/${explanationId}`),
        )
        const payload = unwrap<DeleteExplanationResponse>(response)
        lastDeleteResult.value = payload
        // 若删除的是当前详情，清空 currentExplanation
        if (currentExplanation.value?.id === explanationId) {
          currentExplanation.value = null
        }
        // 从列表中移除已删除记录（避免额外请求）
        explanations.value = explanations.value.filter(
          (item) => item.id !== explanationId,
        )
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '删除解释记录失败')
        return null
      } finally {
        deleting.value = false
      }
    }

// Actions：对比

    /**
     * 对比两个解释（生成差异 payload）.
     * 对应 POST /api/v1/explainability/compare
     *
     * 要求两条解释 explanation_type 一致，否则后端返回 COMPARISON_MISMATCH。
     * 对比类型：
     *   - SAME_MODEL_DIFF_INPUT：同模型不同输入（输入敏感性分析）
     *   - DIFF_MODEL_SAME_INPUT：不同模型同输入（模型版本对比）
     *   - DIFF_MODEL_DIFF_INPUT：不同模型不同输入（综合对比）
     *
     * @param request - 对比请求（含 base_explanation_id / compared_explanation_id / comparison_type / created_by）
     * @returns 对比记录（含 diff_payload_path），失败返回 null
     */
    async function compareExplanations(
      request: CompareExplanationsRequest,
    ): Promise<CompareExplanationsResponse | null> {
      comparing.value = true
      error.value = null
      try {
        const response = await http.post(
          buildApiPath(API_CONFIG.EXPLAINABILITY, '/compare'),
          request,
        )
        const payload = unwrap<CompareExplanationsResponse>(response)
        lastComparisonResult.value = payload
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '对比解释失败')
        return null
      } finally {
        comparing.value = false
      }
    }

// 清理方法

    /** 清空当前解释记录详情。 */
    function clearCurrentExplanation(): void {
      currentExplanation.value = null
    }

    /** 清空所有最近一次操作结果。 */
    function clearLastResults(): void {
      lastHiddenStateResult.value = null
      lastGateDynamicsResult.value = null
      lastCounterfactualResult.value = null
      lastConfidenceResult.value = null
      lastDeleteResult.value = null
      lastComparisonResult.value = null
    }

    /** 重置 Store 到初始状态。 */
    function $reset(): void {
      explanations.value = []
      explanationPagination.value = { total: 0, limit: 100, offset: 0 }
      currentExplanation.value = null
      lastHiddenStateResult.value = null
      lastGateDynamicsResult.value = null
      lastCounterfactualResult.value = null
      lastConfidenceResult.value = null
      lastDeleteResult.value = null
      lastComparisonResult.value = null
      generatingHiddenState.value = false
      generatingGateDynamics.value = false
      generatingCounterfactual.value = false
      generatingConfidence.value = false
      explanationsLoading.value = false
      explanationLoading.value = false
      deleting.value = false
      comparing.value = false
      error.value = null
    }

    return {
      // State
      explanations,
      explanationPagination,
      currentExplanation,
      lastComparisonResult,
      lastHiddenStateResult,
      lastGateDynamicsResult,
      lastCounterfactualResult,
      lastConfidenceResult,
      lastDeleteResult,
      generatingHiddenState,
      generatingGateDynamics,
      generatingCounterfactual,
      generatingConfidence,
      explanationsLoading,
      explanationLoading,
      deleting,
      comparing,
      error,
      // Computed
      hasExplanations,
      currentPage,
      totalPages,
      anyLoading,
      // Actions
      generateHiddenStateExplanation,
      generateGateDynamicsExplanation,
      generateCounterfactualExplanation,
      generateConfidenceExplanation,
      fetchExplanations,
      fetchExplanation,
      deleteExplanation,
      compareExplanations,
      // 清理方法
      clearCurrentExplanation,
      clearLastResults,
      $reset,
    }
  },
)
