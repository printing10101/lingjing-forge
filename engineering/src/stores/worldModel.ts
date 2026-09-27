/**
 * 世界模型 Pinia Store（ADR-017 阶段 8 p8-6：轨迹预测 + 版本管理）
 *
 * 对接后端 `python/app/api/v1/world_model.py` 3 个端点：
 *   - GET    /api/v1/world-model/versions              列出世界模型版本（分页 + active_only 过滤）
 *   - GET    /api/v1/world-model/versions/{version}    查询版本详情
 *   - POST   /api/v1/world-model/predict               直接预测（不走工作流）
 *
 * 设计要点：
 *   1. **离线 RL 优先**：v1 仅离线 RL，世界模型预测的轨迹供 RL agent 离线训练使用
 *   2. **不接 CNC 控制器**：预测结果仅供决策参考，物理执行需"持证操作员 + 导师签字 + 保险"
 *   3. **状态向量约定**：默认 8 维（颤振概率 / 磨损 / 振动 / 温度 / 主轴转速 / 进给 / 切深 / 切宽）
 *   4. **轨迹预测**：自回归多步预测，horizon 默认 10，上限 100（防止漂移累积）
 *   5. POST /predict 不持久化（按需生成），前端如需保存可走工作流 wm_predict_state 任务类型
 *   6. 错误统一通过 extractErrorMessage 提取，存入 error state
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import http from '@/utils/http'
import { extractErrorMessage } from '@/utils/error-handler'
import { unwrap } from '@/utils/response'
import { API_CONFIG, buildApiPath } from '@/config/api'
import {
  type WorldModelVersion,
  type WorldModelPredictRequest,
  type WorldModelPredictResponse,
  type ListWorldModelVersionsParams,
  type ListWorldModelVersionsResponse,
} from '@/contracts/world_model'

// Store 局部类型（派生 / UI 辅助）

/** 版本列表分页信息。 */
interface WorldModelVersionPaginationState {
  total: number
  limit: number
  offset: number
}

/** fetchVersions 入参（与后端 Query 参数对齐）。 */
interface FetchWorldModelVersionsParams extends ListWorldModelVersionsParams {}

// Store 定义

export const useWorldModelStore = defineStore(
  'worldModel',
  () => {
// State：版本列表

    /** 世界模型版本列表（list 端点返回的 items）。 */
    const versions = ref<WorldModelVersion[]>([])
    /** 版本列表分页信息。 */
    const versionPagination = ref<WorldModelVersionPaginationState>({
      total: 0,
      limit: 50,
      offset: 0,
    })

// State：当前版本详情

    /** 当前查看的世界模型版本详情。 */
    const currentVersion = ref<WorldModelVersion | null>(null)

// State：最近一次预测结果

    /** 最近一次世界模型预测响应（含轨迹 / 指标 / 模型信息）。 */
    const lastPrediction = ref<WorldModelPredictResponse | null>(null)

// Loading 标志

    const versionsLoading = ref(false) // GET /versions
    const versionLoading = ref(false) // GET /versions/{version}
    const predicting = ref(false) // POST /predict
    const error = ref<string | null>(null)

// Computed

    /** 是否有世界模型版本。 */
    const hasVersions = computed<boolean>(() => versions.value.length > 0)

    /** 当前激活的版本（is_active=true）。 */
    const activeVersion = computed<WorldModelVersion | null>(() =>
      versions.value.find((v) => v.is_active) ?? null,
    )

    /** 版本列表当前页码（1-based）。 */
    const currentPage = computed<number>(() =>
      versionPagination.value.limit > 0
        ? Math.floor(versionPagination.value.offset / versionPagination.value.limit) + 1
        : 1,
    )

    /** 版本列表总页数。 */
    const totalPages = computed<number>(() =>
      versionPagination.value.limit > 0
        ? Math.ceil(versionPagination.value.total / versionPagination.value.limit)
        : 1,
    )

    /** 是否有任何加载操作进行中。 */
    const anyLoading = computed<boolean>(
      () =>
        versionsLoading.value ||
        versionLoading.value ||
        predicting.value,
    )

    /** 最近一次预测的轨迹步数。 */
    const lastPredictionStepCount = computed<number>(() =>
      lastPrediction.value?.predicted_trajectory?.length ?? 0,
    )

    /** 最近一次预测的最大颤振概率（用于 UI 风险提示）。 */
    const lastPredictionMaxChatter = computed<number>(() =>
      lastPrediction.value?.trajectory_metrics?.max_chatter_probability ?? 0,
    )

    // unwrap() 已提取到 @/utils/response.ts（消除 6 个 Store 的重复定义）

// Actions：版本管理

    /**
     * 列出世界模型版本（分页 + active_only 过滤）.
     * 对应 GET /api/v1/world-model/versions
     *
     * @param params - 查询参数（active_only / limit / offset）
     * @returns 版本列表响应，失败返回 null
     */
    async function fetchVersions(
      params: FetchWorldModelVersionsParams = {},
    ): Promise<ListWorldModelVersionsResponse | null> {
      versionsLoading.value = true
      error.value = null
      try {
        const response = await http.get(
          buildApiPath(API_CONFIG.WORLD_MODEL, '/versions'),
          {
            params: {
              active_only: params.active_only ?? false,
              limit: params.limit ?? 50,
              offset: params.offset ?? 0,
            },
          },
        )
        const payload = unwrap<ListWorldModelVersionsResponse>(response)
        versions.value = payload.items ?? []
        versionPagination.value = {
          total: payload.total ?? 0,
          limit: payload.limit ?? 50,
          offset: payload.offset ?? 0,
        }
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '获取世界模型版本列表失败')
        return null
      } finally {
        versionsLoading.value = false
      }
    }

    /**
     * 查询世界模型版本详情.
     * 对应 GET /api/v1/world-model/versions/{version}
     *
     * @param version - 版本号（semver）
     * @returns 版本详情，失败返回 null
     */
    async function fetchVersion(
      version: string,
    ): Promise<WorldModelVersion | null> {
      versionLoading.value = true
      error.value = null
      try {
        const response = await http.get(
          buildApiPath(API_CONFIG.WORLD_MODEL, `/versions/${encodeURIComponent(version)}`),
        )
        const payload = unwrap<WorldModelVersion>(response)
        currentVersion.value = payload
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '获取世界模型版本详情失败')
        return null
      } finally {
        versionLoading.value = false
      }
    }

// Actions：预测

    /**
     * 执行世界模型轨迹预测（不走工作流，直接调用服务层）.
     * 对应 POST /api/v1/world-model/predict
     *
     * 自回归多步预测，返回 predicted_trajectory（horizon 步）+ trajectory_metrics + model_info。
     * 前端用此数据绘制状态演化轨迹、颤振概率时序、磨损累积曲线。
     *
     * 工程约束：
     *   - horizon 范围 [1, 100]，默认 10
     *   - current_state 至少包含全部 8 个状态字段
     *   - candidate_action 包含 4 个 delta 字段
     *   - 预测结果不持久化，如需保存走工作流 wm_predict_state 任务类型
     *
     * @param request - 预测请求（含 current_state / candidate_action / horizon / model_uri）
     * @returns 预测响应（含轨迹 / 指标 / 模型信息），失败返回 null
     */
    async function predict(
      request: WorldModelPredictRequest,
    ): Promise<WorldModelPredictResponse | null> {
      predicting.value = true
      error.value = null
      try {
        const response = await http.post(
          buildApiPath(API_CONFIG.WORLD_MODEL, '/predict'),
          request,
        )
        const payload = unwrap<WorldModelPredictResponse>(response)
        lastPrediction.value = payload
        return payload
      } catch (e) {
        error.value = extractErrorMessage(e, '世界模型预测失败')
        return null
      } finally {
        predicting.value = false
      }
    }

// 清理方法

    /** 清空当前版本详情。 */
    function clearCurrentVersion(): void {
      currentVersion.value = null
    }

    /** 清空最近一次预测结果。 */
    function clearLastPrediction(): void {
      lastPrediction.value = null
    }

    /** 重置 Store 到初始状态。 */
    function $reset(): void {
      versions.value = []
      versionPagination.value = { total: 0, limit: 50, offset: 0 }
      currentVersion.value = null
      lastPrediction.value = null
      versionsLoading.value = false
      versionLoading.value = false
      predicting.value = false
      error.value = null
    }

    return {
      // State
      versions,
      versionPagination,
      currentVersion,
      lastPrediction,
      versionsLoading,
      versionLoading,
      predicting,
      error,
      // Computed
      hasVersions,
      activeVersion,
      currentPage,
      totalPages,
      anyLoading,
      lastPredictionStepCount,
      lastPredictionMaxChatter,
      // Actions
      fetchVersions,
      fetchVersion,
      predict,
      // 清理方法
      clearCurrentVersion,
      clearLastPrediction,
      $reset,
    }
  },
)
