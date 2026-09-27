import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

// Mock http 模块，避免真实网络请求
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(() =>
      Promise.resolve({ data: { data: [] } }),
    ),
  },
}))

// Mock API_CONFIG 简化使用
vi.mock('@/config/api', () => ({
  API_CONFIG: {
    EQUIPMENT: '/api/v1/equipment',
    PRODUCTION: '/api/v1/production',
  },
}))

import Home from '@/views/Home.vue'

describe('Home.vue', () => {
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
      routes: [
        { path: '/', component: { template: '<div/>' } },
        { path: '/process-planning', component: { template: '<div/>' } },
        { path: '/quality-inspection', component: { template: '<div/>' } },
        { path: '/production-report', component: { template: '<div/>' } },
        { path: '/equipment-monitor', component: { template: '<div/>' } },
      ],
    })
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const mountHome = (options = {}) => {
    return mount(Home, {
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
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.home-page').exists()).toBe(true)
  })

  it('渲染页面标题', async () => {
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.home-page h1').text()).toBe('生产总览')
  })

  it('渲染欢迎横幅', async () => {
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.welcome-banner').exists()).toBe(true)
    expect(wrapper.find('.banner-text h2').exists()).toBe(true)
  })

  it('渲染时间范围筛选按钮', async () => {
    const wrapper = mountHome()
    await flushPromises()
    const buttons = wrapper.findAll('.filter-btn')
    expect(buttons.length).toBe(3)
    // 默认 today 高亮
    expect(buttons[0].classes()).toContain('active')
  })

  it('点击筛选按钮切换 active 状态', async () => {
    const wrapper = mountHome()
    await flushPromises()
    const buttons = wrapper.findAll('.filter-btn')
    await buttons[1].trigger('click')
    expect(buttons[1].classes()).toContain('active')
    expect(buttons[0].classes()).not.toContain('active')
  })

  it('渲染 KPI 卡片', async () => {
    const wrapper = mountHome()
    await flushPromises()
    const cards = wrapper.findAll('.stat-card')
    expect(cards.length).toBe(4)
    // 每张卡片都包含标签与值
    cards.forEach((card) => {
      expect(card.find('.stat-card__label').exists()).toBe(true)
      expect(card.find('.stat-card__value').exists()).toBe(true)
    })
  })

  it('渲染生产进度表格区域', async () => {
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.content-card').exists()).toBe(true)
    expect(wrapper.find('.content-card__title').text()).toBe('生产进度')
  })

  it('渲染实时告警面板', async () => {
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.panel-alerts').exists()).toBe(true)
    expect(wrapper.find('.panel-title').text()).toBe('实时告警')
  })

  it('渲染快捷操作区域', async () => {
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.quick-actions').exists()).toBe(true)
    expect(wrapper.find('.section-title').text()).toBe('快捷操作')
    // 4 个快捷操作按钮
    const actionButtons = wrapper.findAll('.action-btn')
    expect(actionButtons.length).toBe(4)
  })

  it('挂载时并行请求多个数据源', async () => {
    mountHome()
    await flushPromises()
    // 应当调用告警接口与生产仪表板接口
    expect(httpMock.get).toHaveBeenCalledWith('/api/v1/equipment/alarms/')
    expect(httpMock.get).toHaveBeenCalledWith('/api/v1/production/dashboard/')
  })

  it('快捷操作按钮点击触发路由跳转', async () => {
    const wrapper = mountHome()
    await flushPromises()
    const pushSpy = vi.spyOn(router, 'push')
    const actionButtons = wrapper.findAll('.action-btn')
    await actionButtons[0].trigger('click')
    expect(pushSpy).toHaveBeenCalledWith('/process-planning')
  })

  it('告警加载中时显示加载提示', async () => {
    // 让 http.get 永不返回，保持 loading 状态
    httpMock.get.mockImplementationOnce(() => new Promise(() => {}))
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.alert-empty').text()).toBe('告警数据加载中…')
  })

  it('告警接口返回空列表时显示无告警提示', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('alarms')) {
        return Promise.resolve({ data: { data: [] } })
      }
      return Promise.resolve({ data: { data: null } })
    })
    const wrapper = mountHome()
    await flushPromises()
    expect(wrapper.find('.alert-empty').text()).toBe('暂无告警')
  })

  it('告警接口返回数据时渲染告警列表', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('alarms')) {
        return Promise.resolve({
          data: {
            data: [
              { message: '设备A故障', severity: 'high', created_at: '2025-01-01T00:00:00Z' },
              { message: '低库存告警', severity: 'low', created_at: '2025-01-01T01:00:00Z' },
            ],
          },
        })
      }
      return Promise.resolve({ data: { data: null } })
    })
    const wrapper = mountHome()
    await flushPromises()
    const items = wrapper.findAll('.alert-item')
    expect(items.length).toBe(2)
    expect(items[0].find('.alert-message').text()).toBe('设备A故障')
  })

  it('生产仪表板接口返回数据时填充 KPI', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('dashboard')) {
        return Promise.resolve({
          data: {
            data: {
              total_output: 1000,
              qualified_output: 980,
              total_orders: 50,
              active_orders: 10,
              pass_rate: 0.98,
              avg_cycle_time: 60,
            },
          },
        })
      }
      return Promise.resolve({ data: { data: [] } })
    })
    const wrapper = mountHome()
    await flushPromises()
    const cards = wrapper.findAll('.stat-card__value')
    // 第一个 KPI 是今日产量
    expect(cards[0].text()).toContain('1,000')
    // 第二个 KPI 是良品率
    expect(cards[1].text()).toContain('98.0%')
  })
})
