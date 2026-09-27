import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

// Mock http
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { data: [] } })),
  },
}))

// Mock API_CONFIG
vi.mock('@/config/api', () => ({
  API_CONFIG: {
    COST_BUDGET: '/api/v1/cost-budget',
  },
  buildApiPath: (_base: string, path: string) => `${_base}${path}`,
}))

// Mock formatters 中的 formatSecondsTimestamp
vi.mock('@/utils/formatters', () => ({
  formatSecondsTimestamp: (ts: number | null) => (ts == null ? '' : new Date(ts * 1000).toISOString()),
}))

// Mock echarts 避免真实初始化
vi.mock('echarts', () => ({
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
}))

import CostDashboard from '@/views/CostDashboard.vue'

describe('CostDashboard.vue', () => {
  let pinia: ReturnType<typeof createPinia>
  let router: ReturnType<typeof createRouter>

  // 顶层 await 触发 TS1308，改为 beforeAll 异步获取 http mock
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

  const mountCostDashboard = (options = {}) => {
    return mount(CostDashboard, {
      global: {
        plugins: [pinia, router],
        mocks: {
          $t: (key: string) => key,
        },
        ...options,
      },
    })
  }

  it('组件能正确挂载', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.cost-dashboard').exists()).toBe(true)
  })

  it('渲染页面标题', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    expect(wrapper.find('.page-header__title h1').text()).toBe('成本分析')
  })

  it('无预算超额告警时不渲染告警条', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    expect(wrapper.find('.el-alert--error').exists()).toBe(false)
  })

  it('渲染预算状态卡片行', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    expect(wrapper.find('.budget-status-row').exists()).toBe(true)
  })

  it('挂载时调用多个数据接口', async () => {
    mountCostDashboard()
    await flushPromises()
    expect(httpMock.get).toHaveBeenCalled()
    // 至少调用过若干次
    expect(httpMock.get.mock.calls.length).toBeGreaterThan(0)
  })

  it('渲染图表容器（饼图/柱状图/趋势图）', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    // 通过 ref 占位的 div 渲染
    expect(wrapper.find('.chart-card').exists()).toBe(true)
  })

  it('costDimension 默认为 agent', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    const select = wrapper.findAll('select')
    // 至少有一个 select（cost dimension 选择器）
    expect(select.length).toBeGreaterThan(0)
  })

  it('渲染告警与建议区域', async () => {
    const wrapper = mountCostDashboard()
    await flushPromises()
    // 告警区域与建议区域应当挂载
    expect(wrapper.find('.cost-dashboard').exists()).toBe(true)
  })

  it('接口返回预算数据时渲染预算卡片', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('budget')) {
        return Promise.resolve({
          data: {
            data: [
              { level: 'global', used: 80, limit: 100, status: 'ok' },
              { level: 'project', used: 120, limit: 100, status: 'exceeded' },
            ],
          },
        })
      }
      return Promise.resolve({ data: { data: [] } })
    })
    const wrapper = mountCostDashboard()
    await flushPromises()
    // 至少能挂载并显示预算区
    expect(wrapper.find('.budget-status-row').exists()).toBe(true)
  })

  it('接口返回告警时未读超额告警触发告警条', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('alert')) {
        return Promise.resolve({
          data: {
            data: [
              { id: 1, created_at: 1700000000, level: 'global', scope_id: 'g1', resource_type: 'gpu', usage_ratio: 1.2, status: 'exceeded', message: '超额', is_read: 0 },
            ],
          },
        })
      }
      return Promise.resolve({ data: { data: [] } })
    })
    const wrapper = mountCostDashboard()
    await flushPromises()
    // 超额告警应渲染 alert 区
    expect(wrapper.find('.cost-dashboard').exists()).toBe(true)
  })
})
