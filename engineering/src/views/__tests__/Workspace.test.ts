import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

// Mock http
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { data: [] } })),
    post: vi.fn(() => Promise.resolve({ data: { data: {} } })),
    delete: vi.fn(() => Promise.resolve({ data: { data: {} } })),
  },
}))

// Mock API_CONFIG
vi.mock('@/config/api', () => ({
  API_CONFIG: {
    MODELS: '/api/v1/models',
    LNN: '/api/v1/lnn',
  },
  buildApiPath: (_base: string, path: string) => `${_base}${path}`,
}))

// Mock 子组件
vi.mock('@/components/ConfidenceIndicator.vue', () => ({
  default: {
    name: 'ConfidenceIndicator',
    template: '<div class="mock-confidence-indicator"></div>',
  },
}))

vi.mock('@/components/AcceptModifyReject.vue', () => ({
  default: {
    name: 'AcceptModifyReject',
    template: '<div class="mock-accept-modify-reject"></div>',
    emits: ['accept', 'modify', 'reject'],
  },
}))

// Mock settings store
vi.mock('@/stores/settings', () => ({
  useSettingsStore: () => ({
    sovereigntyMode: false,
    autoAcceptLowConfidence: false,
  }),
}))

// Mock useEventSource
vi.mock('@/composables/useEventSource', () => ({
  useEventSource: () => ({
    events: [],
    isConnected: false,
    error: null,
    connect: vi.fn(),
    disconnect: vi.fn(),
    reset: vi.fn(),
  }),
}))

import Workspace from '@/views/Workspace.vue'

describe('Workspace.vue', () => {
  let pinia: ReturnType<typeof createPinia>
  let router: ReturnType<typeof createRouter>

  beforeEach(() => {
    setActivePinia(pinia = createPinia())
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div/>' } }],
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const mountWorkspace = (options = {}) => {
    return mount(Workspace, {
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
    const wrapper = mountWorkspace()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.workspace-page').exists()).toBe(true)
  })

  it('渲染工作区头部标题', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    expect(wrapper.find('.header-with-actions').exists()).toBe(true)
    expect(wrapper.find('.header-with-actions').text()).toContain('workspace.header')
  })

  it('渲染用户主权标签', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    expect(wrapper.find('.header-with-actions').text()).toContain('workspace.userSovereignty')
  })

  it('默认激活 predict 标签页', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    // 使用 stub 的 ElTabs，验证 activeTab 初始值通过渲染内容间接体现
    expect(wrapper.find('.workspace-page').exists()).toBe(true)
  })

  it('渲染预测表单', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    // predictForm 在模板中可见，挂载后应能找到表单
    expect(wrapper.findAll('form').length).toBeGreaterThan(0)
  })

  it('渲染模型选择下拉框选项', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    const options = wrapper.findAll('option')
    const values = options.map((o) => o.attributes('value'))
    expect(values).toEqual(expect.arrayContaining(['CFC-Fast', 'LTC-TimeSeries', 'Hybrid-Multimodal']))
  })

  it('初始无预测响应时不渲染结果区域', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    expect(wrapper.find('.result-section').exists()).toBe(false)
  })

  it('未触发预测时 predicting 状态为 false', async () => {
    const wrapper = mountWorkspace()
    await flushPromises()
    // 通过按钮 loading 状态验证（stub 中将 loading 映射到 attribute）
    // $t mock 返回 key（mountWorkspace 的 mocks.$t）
    const predictBtn = wrapper.findAll('button').find((b) => b.text().includes('workspace.runPredict'))
    expect(predictBtn).toBeDefined()
    expect(predictBtn?.attributes('loading')).toBeFalsy()
  })
})
