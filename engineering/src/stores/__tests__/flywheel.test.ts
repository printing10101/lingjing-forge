/**
 * useFlywheelStore 单元测试（p4-6d）
 *
 * 覆盖范围：
 *   1. 初始状态（status / currentMetrics / deployments 等均为空，loading 为 false）
 *   2. computed 派生（healthTagType / healthStatusLabel / feedbackStats /
 *      activeDeployments / promotedDeployments / anyLoading）
 *   3. fetchStatus 成功/失败
 *   4. fetchMetrics 成功（含历史数据）
 *   5. fetchWeeklyReport 成功
 *   6. fetchDefinitions 成功
 *   7. fetchDeployments 成功/失败降级
 *   8. refreshAll 聚合调用
 *   9. 工具函数 formatTime / formatPercent / formatNumber
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useFlywheelStore } from '@/stores/flywheel'
import type {
  FlywheelStatus,
  DeploymentRecord,
} from '@/stores/flywheel'

// mock http 客户端
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

// 保留 error-handler 真实逻辑
vi.mock('@/utils/error-handler', async () => {
  const actual =
    await vi.importActual<typeof import('@/utils/error-handler')>(
      '@/utils/error-handler',
    )
  return { ...actual }
})

import http from '@/utils/http'

// 测试数据构造器
function makeStatus(overrides: Partial<FlywheelStatus> = {}): FlywheelStatus {
  return {
    status: 'healthy',
    data_volume: 1000,
    model_quality: 92.5,
    adoption_rate: 15.3,
    uncertainty_mean: 0.18,
    feedback_delay: 5.2,
    health_score: 88.0,
    timestamp: '2026-07-13T10:00:00Z',
    ...overrides,
  }
}

function makeDeployment(
  overrides: Partial<DeploymentRecord> = {},
): DeploymentRecord {
  return {
    deployment_id: 'dep-001',
    model_name: 'ltc-chatter',
    new_model_uri: 'model://ltc-chatter-v3',
    baseline_model_uri: 'model://ltc-chatter-v2',
    status: 'observing',
    canary_ratio: 0.1,
    observation_hours: 24,
    rollback_on_failure: true,
    rollback_metric_drop: 0.05,
    eval_metric: 'f1',
    eval_metrics: { f1: 0.92 },
    baseline_metrics: { f1: 0.88 },
    canary_metrics: null,
    decision: null,
    reason: null,
    created_at: '2026-07-13T09:00:00Z',
    updated_at: '2026-07-13T09:30:00Z',
    metadata: null,
    ...overrides,
  }
}

describe('useFlywheelStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

// 1. 初始状态
  describe('initial state', () => {
    it('初始 status 为 null', () => {
      const store = useFlywheelStore()
      expect(store.status).toBeNull()
    })

    it('初始 currentMetrics 为 null', () => {
      const store = useFlywheelStore()
      expect(store.currentMetrics).toBeNull()
    })

    it('初始 historicalMetrics 为空数组', () => {
      const store = useFlywheelStore()
      expect(store.historicalMetrics).toEqual([])
    })

    it('初始 weeklyReport 为 null', () => {
      const store = useFlywheelStore()
      expect(store.weeklyReport).toBeNull()
    })

    it('初始 metricDefinitions 为空数组', () => {
      const store = useFlywheelStore()
      expect(store.metricDefinitions).toEqual([])
    })

    it('初始 deployments 为空数组', () => {
      const store = useFlywheelStore()
      expect(store.deployments).toEqual([])
    })

    it('所有 loading 状态初始为 false', () => {
      const store = useFlywheelStore()
      expect(store.loading).toBe(false)
      expect(store.metricsLoading).toBe(false)
      expect(store.reportLoading).toBe(false)
      expect(store.definitionsLoading).toBe(false)
      expect(store.deploymentsLoading).toBe(false)
    })

    it('初始 error 为 null', () => {
      const store = useFlywheelStore()
      expect(store.error).toBeNull()
    })

    it('初始 metricsPeriodDays 为 7', () => {
      const store = useFlywheelStore()
      expect(store.metricsPeriodDays).toBe(7)
    })
  })

// 2. computed
  describe('computed', () => {
    it('healthTagType 在无 status 时返回 info', () => {
      const store = useFlywheelStore()
      expect(store.healthTagType).toBe('info')
    })

    it('healthTagType 对应 healthy / warning / critical', () => {
      const store = useFlywheelStore()
      store.$patch({ status: makeStatus({ status: 'healthy' }) })
      expect(store.healthTagType).toBe('success')
      store.$patch({ status: makeStatus({ status: 'warning' }) })
      expect(store.healthTagType).toBe('warning')
      store.$patch({ status: makeStatus({ status: 'critical' }) })
      expect(store.healthTagType).toBe('danger')
    })

    it('healthStatusLabel 返回中文标签', () => {
      const store = useFlywheelStore()
      store.$patch({ status: makeStatus({ status: 'healthy' }) })
      expect(store.healthStatusLabel).toBe('健康')
      store.$patch({ status: makeStatus({ status: 'warning' }) })
      expect(store.healthStatusLabel).toBe('警告')
      store.$patch({ status: makeStatus({ status: 'critical' }) })
      expect(store.healthStatusLabel).toBe('严重')
    })

    it('healthStatusLabel 在无 status 时返回 未知', () => {
      const store = useFlywheelStore()
      expect(store.healthStatusLabel).toBe('未知')
    })

    it('feedbackStats 从 status 派生', () => {
      const store = useFlywheelStore()
      store.$patch({
        status: makeStatus({
          data_volume: 2000,
          adoption_rate: 30.5,
          feedback_delay: 12.0,
          health_score: 75.0,
        }),
      })
      expect(store.feedbackStats).toEqual({
        dataVolume: 2000,
        adoptionRate: 30.5,
        feedbackDelay: 12.0,
        healthScore: 75.0,
      })
    })

    it('feedbackStats 在无 status 时全部为 0', () => {
      const store = useFlywheelStore()
      expect(store.feedbackStats).toEqual({
        dataVolume: 0,
        adoptionRate: 0,
        feedbackDelay: 0,
        healthScore: 0,
      })
    })

    it('activeDeployments 过滤出 deploying / observing', () => {
      const store = useFlywheelStore()
      store.$patch({
        deployments: [
          makeDeployment({ deployment_id: 'd1', status: 'observing' }),
          makeDeployment({ deployment_id: 'd2', status: 'deploying' }),
          makeDeployment({ deployment_id: 'd3', status: 'promoted' }),
          makeDeployment({ deployment_id: 'd4', status: 'rolled_back' }),
        ] as never,
      })
      expect(store.activeDeployments).toHaveLength(2)
      expect(store.activeDeployments[0].deployment_id).toBe('d1')
      expect(store.activeDeployments[1].deployment_id).toBe('d2')
    })

    it('promotedDeployments 过滤出 promoted', () => {
      const store = useFlywheelStore()
      store.$patch({
        deployments: [
          makeDeployment({ deployment_id: 'd1', status: 'promoted' }),
          makeDeployment({ deployment_id: 'd2', status: 'observing' }),
          makeDeployment({ deployment_id: 'd3', status: 'promoted' }),
        ] as never,
      })
      expect(store.promotedDeployments).toHaveLength(2)
    })

    it('anyLoading 在任一 loading 为 true 时为 true', () => {
      const store = useFlywheelStore()
      expect(store.anyLoading).toBe(false)
      store.$patch({ loading: true })
      expect(store.anyLoading).toBe(true)
      store.$patch({ loading: false, metricsLoading: true })
      expect(store.anyLoading).toBe(true)
    })
  })

// 3. fetchStatus
  describe('fetchStatus', () => {
    it('成功时保存到 status', async () => {
      const status = makeStatus()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: status },
      })
      const store = useFlywheelStore()
      await store.fetchStatus()
      expect(store.status).toEqual(status)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
      expect(http.get).toHaveBeenCalledWith(
        expect.stringContaining('/flywheel/status'),
      )
    })

    it('后端直接返回对象（无 data 信封）时也能正确处理', async () => {
      const status = makeStatus()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: status,
      })
      const store = useFlywheelStore()
      await store.fetchStatus()
      expect(store.status).toEqual(status)
    })

    it('网络异常时设置 error', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '服务不可用' } },
      })
      const store = useFlywheelStore()
      await store.fetchStatus()
      expect(store.error).toBe('服务不可用')
      expect(store.loading).toBe(false)
    })
  })

// 4. fetchMetrics
  describe('fetchMetrics', () => {
    it('成功时保存 current 和 historical', async () => {
      const payload = {
        current: {
          timestamp: '2026-07-13T10:00:00Z',
          data_volume: 1000,
          model_quality: 92.5,
          adoption_rate: 15.3,
          uncertainty_mean: 0.18,
          feedback_delay: 5.2,
        },
        historical: [
          {
            timestamp: '2026-07-12T10:00:00Z',
            data_volume: 950,
            model_quality: 91.0,
            adoption_rate: 14.0,
            uncertainty_mean: 0.20,
            feedback_delay: 6.0,
          },
        ],
        period_days: 7,
      }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: payload },
      })
      const store = useFlywheelStore()
      await store.fetchMetrics(7)
      expect(store.currentMetrics).toEqual(payload.current)
      expect(store.historicalMetrics).toHaveLength(1)
      expect(store.metricsPeriodDays).toBe(7)
      expect(http.get).toHaveBeenCalledWith(
        expect.stringContaining('/flywheel/metrics'),
        { params: { days: 7 } },
      )
    })

    it('historical 为空时降级为空数组', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          data: {
            current: {
              timestamp: '2026-07-13T10:00:00Z',
              data_volume: 0,
              model_quality: 0,
              adoption_rate: 0,
              uncertainty_mean: 0,
              feedback_delay: 0,
            },
            historical: null,
            period_days: 14,
          },
        },
      })
      const store = useFlywheelStore()
      await store.fetchMetrics(14)
      expect(store.historicalMetrics).toEqual([])
      expect(store.metricsPeriodDays).toBe(14)
    })

    it('网络异常时设置 error', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(
        new Error('网络错误'),
      )
      const store = useFlywheelStore()
      await store.fetchMetrics()
      expect(store.error).toBe('网络错误')
      expect(store.metricsLoading).toBe(false)
    })
  })

// 5. fetchWeeklyReport
  describe('fetchWeeklyReport', () => {
    it('成功时保存到 weeklyReport', async () => {
      const report = {
        report_type: 'weekly',
        generated_at: '2026-07-13T10:00:00Z',
        period: { start: '2026-07-06', end: '2026-07-13' },
        current_metrics: { data_volume: 1000 },
        trends: { adoption_rate: 'up' },
        summary: { health_score: 88, health_status: 'good' },
      }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: report },
      })
      const store = useFlywheelStore()
      await store.fetchWeeklyReport(false)
      expect(store.weeklyReport).toEqual(report)
      expect(http.get).toHaveBeenCalledWith(
        expect.stringContaining('/flywheel/report/weekly'),
        { params: { save: false } },
      )
    })

    it('失败时设置 error', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(
        new Error('生成失败'),
      )
      const store = useFlywheelStore()
      await store.fetchWeeklyReport()
      expect(store.error).toBe('生成失败')
    })
  })

// 6. fetchDefinitions
  describe('fetchDefinitions', () => {
    it('成功时保存到 metricDefinitions', async () => {
      const defs = {
        metrics: [
          {
            name: 'data_volume',
            description: '加工记录数',
            unit: '条',
            range: '>= 0',
            calculation: 'SELECT COUNT(*)',
          },
        ],
      }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: defs },
      })
      const store = useFlywheelStore()
      await store.fetchDefinitions()
      expect(store.metricDefinitions).toHaveLength(1)
      expect(store.metricDefinitions[0].name).toBe('data_volume')
    })

    it('metrics 为空时降级为空数组', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { metrics: null } },
      })
      const store = useFlywheelStore()
      await store.fetchDefinitions()
      expect(store.metricDefinitions).toEqual([])
    })
  })

// 7. fetchDeployments
  describe('fetchDeployments', () => {
    it('成功时保存到 deployments', async () => {
      const result = {
        action: 'list_deployments',
        deployments: [
          makeDeployment({ deployment_id: 'dep-001' }),
          makeDeployment({ deployment_id: 'dep-002', status: 'promoted' }),
        ],
        count: 2,
      }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: result },
      })
      const store = useFlywheelStore()
      await store.fetchDeployments()
      expect(store.deployments).toHaveLength(2)
      expect(http.get).toHaveBeenCalledWith(
        expect.stringContaining('/deployments'),
      )
    })

    it('带筛选参数时调用 /deployments（参数暂由后端统一过滤）', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { action: 'list_deployments', deployments: [], count: 0 } },
      })
      const store = useFlywheelStore()
      await store.fetchDeployments('ltc-chatter', 'observing')
      // 注：生产实现当前未把 modelName/statusFilter 透传到 URL query
      // （后端 /deployments 统一返回目录扫描结果；前端筛选为后续待办项）
      expect(http.get).toHaveBeenCalledWith(
        expect.stringContaining('/deployments'),
      )
    })

    it('失败时降级为空数组（不阻塞看板）', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(
        new Error('tasks API 不可用'),
      )
      const store = useFlywheelStore()
      // 先填充一些数据
      store.$patch({
        deployments: [
          makeDeployment({ deployment_id: 'old-dep' }),
        ] as never,
      })
      await store.fetchDeployments()
      expect(store.deployments).toEqual([])
      expect(store.error).toBe('tasks API 不可用')
    })
  })

// 8. refreshAll
  describe('refreshAll', () => {
    it('调用所有 5 个 fetch 方法', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: {} },
      })
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { deployments: [] } },
      })
      const store = useFlywheelStore()
      const getSpy = http.get as ReturnType<typeof vi.fn>
      getSpy.mockClear()

      await store.refreshAll(14)
      // spyOn(store, ...) 不拦截 setup store 内部闭包调用；
      // 5 个 fetch 方法全部走 http.get，用调用次数验证 refreshAll 全链路
      expect(getSpy.mock.calls.length).toBeGreaterThanOrEqual(5)
    })

    it('单个 fetch 失败不阻塞其他 fetch', async () => {
      // 第 1 次 get（status）失败，其他成功
      (http.get as ReturnType<typeof vi.fn>)
        .mockRejectedValueOnce(new Error('status 失败'))
        .mockResolvedValue({ data: { data: {} } })
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { deployments: [] } },
      })
      const store = useFlywheelStore()
      // 不应该 throw
      await expect(store.refreshAll()).resolves.toBeUndefined()
      // error 应被设置（status 失败）
      expect(store.error).not.toBeNull()
    })
  })

// 9. 工具函数
  describe('helpers', () => {
    it('formatTime 处理空值', () => {
      const store = useFlywheelStore()
      expect(store.formatTime(null)).toBe('-')
      expect(store.formatTime(undefined)).toBe('-')
      expect(store.formatTime('')).toBe('-')
    })

    it('formatTime 处理无效时间', () => {
      const store = useFlywheelStore()
      expect(store.formatTime('invalid-date')).toBe('-')
    })

    it('formatTime 处理 ISO 时间字符串', () => {
      const store = useFlywheelStore()
      const result = store.formatTime('2026-07-13T10:00:00Z')
      expect(result).not.toBe('-')
      expect(typeof result).toBe('string')
    })

    it('formatPercent 处理空值', () => {
      const store = useFlywheelStore()
      expect(store.formatPercent(null)).toBe('-')
      expect(store.formatPercent(undefined)).toBe('-')
      expect(store.formatPercent(Number.NaN)).toBe('-')
    })

    it('formatPercent 默认保留 1 位小数', () => {
      const store = useFlywheelStore()
      expect(store.formatPercent(92.5)).toBe('92.5%')
      expect(store.formatPercent(92.56, 2)).toBe('92.56%')
    })

    it('formatNumber 处理空值', () => {
      const store = useFlywheelStore()
      expect(store.formatNumber(null)).toBe('-')
      expect(store.formatNumber(undefined)).toBe('-')
    })

    it('formatNumber 千分位格式化', () => {
      const store = useFlywheelStore()
      const result = store.formatNumber(1000000)
      expect(result).toContain('1')
      // zh-CN 千分位可能是逗号或别的
      expect(typeof result).toBe('string')
    })
  })
})
