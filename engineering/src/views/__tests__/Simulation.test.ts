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
    SIMULATION: '/api/simulation',
  },
  buildApiPath: (_base: string, path: string) => `${_base}${path}`,
}))

// Mock SimulationViewer 与 CollisionAlertModal 子组件
vi.mock('@/components/simulation/SimulationViewer.vue', () => ({
  default: {
    name: 'SimulationViewer',
    template: '<div class="mock-simulation-viewer"></div>',
    emits: ['ready'],
  },
}))

vi.mock('@/components/simulation/CollisionAlertModal.vue', () => ({
  default: {
    name: 'CollisionAlertModal',
    template: '<div class="mock-collision-alert-modal"></div>',
  },
}))

// Mock project store
vi.mock('@/stores/project', () => ({
  useProjectStore: () => ({
    projectId: 'test-project',
  }),
}))

import Simulation from '@/views/Simulation.vue'

describe('Simulation.vue', () => {
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

  const mountSimulation = (options = {}) => {
    return mount(Simulation, {
      global: {
        plugins: [pinia, router],
        mocks: {
          $t: (key: string, params?: Record<string, unknown>) => {
            if (params) {
              // 简单插值，便于校验
              let result = key
              Object.entries(params).forEach(([k, v]) => {
                result = result.replace(`{${k}}`, String(v))
              })
              return result
            }
            return key
          },
        },
        ...options,
      },
    })
  }

  it('组件能正确挂载', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.simulation-page').exists()).toBe(true)
  })

  it('渲染页面标题与副标题', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    expect(wrapper.find('.page-title').text()).toBe('仿真模拟')
    expect(wrapper.find('.page-subtitle').text()).toBe('NC 代码仿真验证与碰撞检测')
  })

  it('渲染顶部操作按钮（刷新历史、新建仿真）', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    const headerActions = wrapper.find('.page-header__actions')
    expect(headerActions.exists()).toBe(true)
    const buttons = headerActions.findAll('button')
    expect(buttons.length).toBeGreaterThanOrEqual(2)
  })

  it('渲染 4 个统计概览卡片', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    const cards = wrapper.findAll('.stat-card')
    expect(cards.length).toBe(4)
  })

  it('默认渲染 simulation 标签页', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    const tabs = wrapper.findAll('.sim-tab-item')
    expect(tabs.length).toBe(3)
    expect(tabs[0].classes()).toContain('active')
  })

  it('点击标签可切换 activeTab', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    const tabs = wrapper.findAll('.sim-tab-item')
    await tabs[1].trigger('click')
    expect(tabs[1].classes()).toContain('active')
    expect(tabs[0].classes()).not.toContain('active')
  })

  it('初始状态显示 idle 覆盖层', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    expect(wrapper.find('.idle-overlay').exists()).toBe(true)
  })

  it('运行按钮在无 gcode 时禁用', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    const runBtn = wrapper.find('.btn-run')
    expect(runBtn.exists()).toBe(true)
    expect(runBtn.attributes('disabled')).toBeDefined()
  })

  it('挂载时拉取仿真历史', async () => {
    mountSimulation()
    await flushPromises()
    // fetchHistory 会调用 history 接口
    expect(httpMock.get).toHaveBeenCalled()
  })

  it('点击"新建仿真"按钮触发 handleNewSimulation', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    const newBtn = wrapper.find('.page-header__actions button.el-button--primary')
    expect(newBtn.exists()).toBe(true)
    await newBtn.trigger('click')
    // 状态应回到 idle
    expect(wrapper.find('.idle-overlay').exists()).toBe(true)
  })

  it('渲染仿真历史区域', async () => {
    const wrapper = mountSimulation()
    await flushPromises()
    expect(wrapper.find('.history-list').exists() || wrapper.find('.el-empty').exists()).toBe(true)
  })

  it('历史接口返回数据时渲染历史列表', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('history')) {
        return Promise.resolve({
          data: {
            data: {
              items: [
              {
                task_id: 'task-001',
                project_id: 'p1',
                duration_seconds: 12.34,
                collision_collided: false,
                voxel_size: 1.0,
                segment_count: 100,
              },
              {
                task_id: 'task-002',
                project_id: 'p1',
                duration_seconds: 8.5,
                collision_collided: true,
                voxel_size: 0.5,
                segment_count: 80,
              },
            ],
              },
          },
        })
      }
      return Promise.resolve({ data: { data: [] } })
    })
    const wrapper = mountSimulation()
    await flushPromises()
    const items = wrapper.findAll('.history-item')
    expect(items.length).toBe(2)
    expect(items[0].find('.history-id').text()).toBe('task-001')
  })

  it('统计卡片显示历史计算结果', async () => {
    httpMock.get.mockImplementation((url: string) => {
      if (url.includes('history')) {
        return Promise.resolve({
          data: {
            data: {
              items: [
              { task_id: 't1', project_id: 'p', duration_seconds: 10, collision_collided: false, voxel_size: 1, segment_count: 1 },
              { task_id: 't2', project_id: 'p', duration_seconds: 20, collision_collided: true, voxel_size: 1, segment_count: 1 },
            ],
              },
          },
        })
      }
      return Promise.resolve({ data: { data: [] } })
    })
    const wrapper = mountSimulation()
    await flushPromises()
    const statValues = wrapper.findAll('.stat-card__value')
    // 总数=2, 通过=1, 失败=1, 平均时长=15.0s
    expect(statValues[0].text()).toBe('2')
    expect(statValues[1].text()).toBe('1')
    expect(statValues[2].text()).toBe('1')
    expect(statValues[3].text()).toContain('15.0')
  })
})
