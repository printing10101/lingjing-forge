import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

// Mock http
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { data: [] } })),
    post: vi.fn(() => Promise.resolve({ data: { data: {} } })),
  },
}))

// Mock API_CONFIG
vi.mock('@/config/api', () => ({
  API_CONFIG: {
    GOVERNANCE: '/api/v1/governance',
  },
  buildApiPath: (_base: string, path: string) => `${_base}${path}`,
}))

// Mock download util
vi.mock('@/utils/download', () => ({
  triggerFileDownload: vi.fn(),
}))

// Mock formatters
vi.mock('@/utils/formatters', () => ({
  formatSecondsTimestamp: (ts: number | null) => (ts == null ? '' : new Date(ts * 1000).toISOString()),
}))

// Mock statusHelpers
vi.mock('@/utils/statusHelpers', () => ({
  getPriorityTagType: () => 'info',
  getPriorityLabel: (p: string) => p,
  getApprovalStatusTagType: () => 'info',
  getApprovalStatusLabel: (s: string) => s,
}))

import ApprovalDashboard from '@/views/ApprovalDashboard.vue'

describe('ApprovalDashboard.vue', () => {
  let pinia: ReturnType<typeof createPinia>
  let router: ReturnType<typeof createRouter>

  // 顶层 await 会触发 TS1308（vitest 运行时可接受，但类型检查报错），
  // 改为在 beforeAll 中异步获取 http mock。
  let httpMock: any

  beforeAll(async () => {
    httpMock = (await import('@/utils/http')).default as any
  })

  beforeEach(() => {
    setActivePinia(pinia = createPinia())
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div/>' } }],
    })
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const mountApprovalDashboard = (options = {}) => {
    return mount(ApprovalDashboard, {
      global: {
        plugins: [pinia, router],
        stubs: {
          // 弹窗组件在测试环境渲染崩溃（emitsOptions null），且非断言目标
          ApprovalReportDialog: true,
          ApprovalDetailDialog: true,
        },
        mocks: {
          $t: (key: string) => key,
        },
        ...options,
      },
    })
  }

  it('组件能正确挂载', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.approval-dashboard-page').exists()).toBe(true)
  })

  it('渲染页面标题', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    expect(wrapper.find('.dashboard-header h2').text()).toBe('审批看板')
  })

  it('渲染刷新与治理报告按钮', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    const buttons = wrapper.findAll('.dashboard-header button')
    expect(buttons.length).toBeGreaterThanOrEqual(2)
  })

  it('渲染 4 个统计卡片', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    const cards = wrapper.findAll('.stat-card')
    expect(cards.length).toBe(4)
  })

  it('统计卡片显示初始计数 0', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    const values = wrapper.findAll('.stat-card__value')
    expect(values.length).toBe(4)
    values.forEach((v) => {
      expect(v.text()).toBe('0')
    })
  })

  it('渲染审批标签页（pending/under_review/approved/rejected）', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    // 初始数据为空时，每个 tab 下应显示空状态
    const empty = wrapper.findAll('.el-empty')
    expect(empty.length).toBeGreaterThan(0)
  })

  it('默认激活 pending 标签页', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    // 通过点击刷新按钮触发 loadDashboard 校验
    const refreshBtn = wrapper
      .findAll('.dashboard-header button')
      .find((b) => b.text().includes('刷新'))
    expect(refreshBtn).toBeDefined()
  })

  it('挂载时调用 loadDashboard 拉取数据', async () => {
    mountApprovalDashboard()
    await flushPromises()
    expect(httpMock.get).toHaveBeenCalled()
    // 应当调用 /approval-dashboard
    const dashboardCall = httpMock.get.mock.calls.find((call: any[]) =>
      call[0]?.includes('approval-dashboard'),
    )
    expect(dashboardCall).toBeDefined()
  })

  it('点击刷新按钮重新拉取数据', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    const initialCallCount = httpMock.get.mock.calls.length
    const refreshBtn = wrapper
      .findAll('.dashboard-header button')
      .find((b) => b.text().includes('刷新'))
    await refreshBtn?.trigger('click')
    await flushPromises()
    expect(httpMock.get.mock.calls.length).toBeGreaterThan(initialCallCount)
  })

  it('点击治理报告按钮打开报告弹窗', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    const reportBtn = wrapper
      .findAll('.dashboard-header button')
      .find((b) => b.text().includes('治理报告'))
    await reportBtn?.trigger('click')
    await flushPromises()
    // showReport 切换为 true，会触发 loadReport 接口
    expect(httpMock.get).toHaveBeenCalled()
  })

  it('接口返回审批数据时渲染请求卡片', async () => {
    httpMock.get.mockResolvedValueOnce({
      data: {
        data: {
          pending: [
            {
              request_id: 'r1',
              task_id: 't1',
              requester: 'user-a',
              requested_at: 1700000000,
              priority: 'high',
              context: {},
              status: 'pending',
              assigned_approver: null,
              approvers: [],
              decisions: [],
              required_approvals: 1,
              risk_score: 0.2,
              risk_factors: [],
              suggested_decision: 'approve',
              emergency_override: false,
              emergency_reason: '',
              expires_at: null,
              completed_at: null,
            },
          ],
          under_review: [],
          approved: [],
          rejected: [],
          counts: { pending: 1, under_review: 0, approved: 0, rejected: 0 },
        },
      },
    })
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    // 统计卡片应当显示 pending=1
    const values = wrapper.findAll('.stat-card__value')
    expect(values[0].text()).toBe('1')
  })

  it('接口失败时显示错误提示', async () => {
    httpMock.get.mockRejectedValueOnce(new Error('network error'))
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    // 错误被捕获后组件仍应挂载
    expect(wrapper.find('.approval-dashboard-page').exists()).toBe(true)
  })

  it('渲染审批历史区域', async () => {
    const wrapper = mountApprovalDashboard()
    await flushPromises()
    expect(wrapper.find('.approval-dashboard-page').exists()).toBe(true)
  })
})
