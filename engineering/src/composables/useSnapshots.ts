/**
 * 实验快照 composable
 *
 * 对应后端 python/app/api/v1/snapshots.py（ADR-005 阶段 2）。
 * 封装 /api/v1/snapshots REST API：列表 / 创建 / 详情 / 一键复现。
 *
 * 设计参考：src/composables/useWorkflow.ts（无状态函数 + 状态化 composable 聚合）
 */

import { ref, onUnmounted } from 'vue'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import type {
  ExperimentSnapshot,
  SnapshotFilters,
} from '@/contracts/observability'

// 类型定义

/** 列表项摘要（detail=false 时后端返回的字段集，可能省略 config/environment）。 */
export type SnapshotSummary = Partial<ExperimentSnapshot> &
  Pick<
    ExperimentSnapshot,
    'snapshot_id' | 'created_at' | 'created_by' | 'git_sha' | 'model_uri'
  >

/** 列表响应。 */
export interface ListSnapshotsResponse {
  items: SnapshotSummary[]
  limit: number
  offset: number
}

/** 列表查询参数。 */
export interface ListSnapshotsParams extends SnapshotFilters {
  limit?: number
  offset?: number
  /** true 时返回完整 config/environment；false 仅返回摘要（默认 false）。 */
  detail?: boolean
}

/** 创建快照请求体（与后端 CreateSnapshotRequest 对齐）。 */
export interface CreateSnapshotRequest {
  config: Record<string, unknown>
  dataset_versions: string[]
  model_uri: string
  metrics: Record<string, number>
  created_by: string
  notes?: string
}

/** 创建快照响应。 */
export interface CreateSnapshotResponse {
  snapshot_id: string
  created_at: string
}

/** 复现快照响应。 */
export interface ReproduceSnapshotResponse {
  workflow_run_id: string
  snapshot_id: string
}

// REST API 调用（无状态函数）

/** 后端统一响应壳：{ code, message, data, request_id }，code !== 0 由 http 拦截器抛错。 */
interface ApiEnvelope<T> {
  data: T
  message?: string
}

const BASE = API_CONFIG.SNAPSHOTS

/**
 * 列出实验快照（按 created_at 降序）。
 */
export async function listSnapshots(
  params: ListSnapshotsParams = {},
): Promise<ListSnapshotsResponse> {
  const res = await http.get<ApiEnvelope<ListSnapshotsResponse>>(
    buildApiPath(BASE, ''),
    { params },
  )
  return res.data.data
}

/**
 * 获取快照详情。
 */
export async function getSnapshot(
  snapshotId: string,
): Promise<ExperimentSnapshot> {
  const res = await http.get<ApiEnvelope<ExperimentSnapshot>>(
    buildApiPath(BASE, `/${snapshotId}`),
  )
  return res.data.data
}

/**
 * 创建实验快照（后端自动采集 git_sha + environment）。
 */
export async function createSnapshot(
  body: CreateSnapshotRequest,
): Promise<CreateSnapshotResponse> {
  const res = await http.post<ApiEnvelope<CreateSnapshotResponse>>(
    buildApiPath(BASE, ''),
    body,
  )
  return res.data.data
}

/**
 * 一键复现快照（启动新的工作流运行）。
 * @returns workflow_run_id（复现工作流的运行 ID）
 */
export async function reproduceSnapshot(
  snapshotId: string,
): Promise<ReproduceSnapshotResponse> {
  const res = await http.post<ApiEnvelope<ReproduceSnapshotResponse>>(
    buildApiPath(BASE, `/${snapshotId}/reproduce`),
  )
  return res.data.data
}

// 状态化 composable

/**
 * 实验快照管理 composable。
 *
 * 维护：
 *   - 快照列表（分页 + 筛选）
 *   - 当前选中快照（用于详情面板）
 *   - 创建/复现的 loading 状态
 */
export function useSnapshots() {
  const snapshots = ref<SnapshotSummary[]>([])
  const loading = ref(false)
  const totalCount = ref(0)
  const currentPage = ref(1)
  const pageSize = ref(20)
  const filterCreatedBy = ref<string>('')
  const filterGitSha = ref<string>('')
  const filterModelUri = ref<string>('')

  const currentSnapshot = ref<ExperimentSnapshot | null>(null)
  const currentLoading = ref(false)

  const creating = ref(false)
  const reproducing = ref(false)

  /**
   * 加载快照列表。
   */
  async function loadSnapshots(): Promise<void> {
    loading.value = true
    try {
      const params: ListSnapshotsParams = {
        limit: pageSize.value,
        offset: (currentPage.value - 1) * pageSize.value,
        detail: false,
      }
      if (filterCreatedBy.value) params.created_by = filterCreatedBy.value
      if (filterGitSha.value) params.git_sha = filterGitSha.value
      if (filterModelUri.value) params.model_uri = filterModelUri.value

      const res = await listSnapshots(params)
      snapshots.value = res.items
      // 后端目前不返回 total 字段；以返回数量 + offset 估算
      totalCount.value = res.items.length + res.offset
    } catch (e: unknown) {
      console.warn('[useSnapshots] loadSnapshots failed:', e)
    } finally {
      loading.value = false
    }
  }

  /**
   * 重置筛选条件并重新加载。
   */
  async function resetFilters(): Promise<void> {
    filterCreatedBy.value = ''
    filterGitSha.value = ''
    filterModelUri.value = ''
    currentPage.value = 1
    await loadSnapshots()
  }

  /**
   * 选择某个快照查看详情。
   */
  async function selectSnapshot(snapshotId: string): Promise<void> {
    currentLoading.value = true
    try {
      currentSnapshot.value = await getSnapshot(snapshotId)
    } catch (e: unknown) {
      console.warn('[useSnapshots] selectSnapshot failed:', e)
      currentSnapshot.value = null
    } finally {
      currentLoading.value = false
    }
  }

  /**
   * 清空当前详情。
   */
  function clearCurrent(): void {
    currentSnapshot.value = null
  }

  /**
   * 创建快照。
   * @returns snapshot_id
   */
  async function submitSnapshot(
    body: CreateSnapshotRequest,
  ): Promise<string> {
    creating.value = true
    try {
      const { snapshot_id } = await createSnapshot(body)
      // 创建成功后刷新列表
      await loadSnapshots()
      return snapshot_id
    } finally {
      creating.value = false
    }
  }

  /**
   * 一键复现快照。
   * @returns workflow_run_id
   */
  async function reproduce(snapshotId: string): Promise<string> {
    reproducing.value = true
    try {
      const { workflow_run_id } = await reproduceSnapshot(snapshotId)
      return workflow_run_id
    } finally {
      reproducing.value = false
    }
  }

  onUnmounted(() => {
    // 清空状态，避免组件销毁后残留
    currentSnapshot.value = null
  })

  return {
    // 列表
    snapshots,
    loading,
    totalCount,
    currentPage,
    pageSize,
    filterCreatedBy,
    filterGitSha,
    filterModelUri,
    loadSnapshots,
    resetFilters,
    // 当前详情
    currentSnapshot,
    currentLoading,
    selectSnapshot,
    clearCurrent,
    // 创建/复现
    creating,
    reproducing,
    submitSnapshot,
    reproduce,
  }
}
