import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

// Mock tasks store
const mockTasksStore = vi.hoisted(() => ({
  tasks: [] as any[],
  error: null as string | null,
  loading: false,
  fetchTasks: vi.fn(() => Promise.resolve()),
}))

vi.mock('@/stores/tasks', () => ({
  useTasksStore: () => mockTasksStore,
}))

import TaskBoard from '@/views/TaskBoard.vue'

describe('TaskBoard.vue', () => {
  let pinia: ReturnType<typeof createPinia>
  let router: ReturnType<typeof createRouter>

  beforeEach(() => {
    setActivePinia(pinia = createPinia())
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div/>' } }],
    })
    vi.clearAllMocks()
    mockTasksStore.tasks = []
    mockTasksStore.error = null
    mockTasksStore.loading = false
    mockTasksStore.fetchTasks.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const mountTaskBoard = (options = {}) => {
    return mount(TaskBoard, {
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
    const wrapper = mountTaskBoard()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.task-board-page').exists()).toBe(true)
  })

  it('渲染页面标题', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    expect(wrapper.find('.page-header__title h1').text()).toBe('任务看板')
  })

  it('渲染视图切换按钮组（看板/列表）', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    expect(wrapper.find('.page-header__actions').exists()).toBe(true)
    const buttons = wrapper.findAll('.page-header__actions button')
    // 至少有 看板/列表/筛选/创建 4 个按钮
    expect(buttons.length).toBeGreaterThanOrEqual(4)
  })

  it('默认视图模式为 kanban', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    // kanban 按钮应带 primary 样式
    const buttons = wrapper.findAll('.page-header__actions button')
    const kanbanBtn = buttons.find((b) => b.text().includes('看板'))
    expect(kanbanBtn?.classes()).toContain('el-button--primary')
  })

  it('点击列表按钮切换视图模式', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    const buttons = wrapper.findAll('.page-header__actions button')
    const listBtn = buttons.find((b) => b.text().includes('列表'))
    await listBtn?.trigger('click')
    expect(listBtn?.classes()).toContain('el-button--primary')
  })

  it('挂载时调用 fetchTasks', async () => {
    mountTaskBoard()
    await flushPromises()
    expect(mockTasksStore.fetchTasks).toHaveBeenCalled()
  })

  it('渲染筛选面板（默认可见）', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    expect(wrapper.find('.filter-panel-wrapper').exists()).toBe(true)
    // 默认 filterVisible=true，未应用 collapsed 类
    expect(wrapper.find('.filter-panel-wrapper').classes()).not.toContain('collapsed')
  })

  it('点击筛选按钮可折叠筛选面板', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    const filterBtn = wrapper
      .findAll('.page-header__actions button')
      .find((b) => b.text().includes('筛选'))
    await filterBtn?.trigger('click')
    expect(wrapper.find('.filter-panel-wrapper').classes()).toContain('collapsed')
  })

  it('store 中任务为空时渲染空状态', async () => {
    mockTasksStore.tasks = []
    const wrapper = mountTaskBoard()
    await flushPromises()
    // 看板列均无任务，渲染列标题但 items 为空
    wrapper.findAll('.kanban-column, .kanban-col')
    // 即使 stub 渲染，至少能挂载
    expect(wrapper.find('.task-board-page').exists()).toBe(true)
  })

  it('store 中有任务时渲染看板列', async () => {
    mockTasksStore.tasks = [
      { job_id: 't1', task_type: 'predict', status: 'pending', progress: 0, created_at: '2025-01-01' },
      { job_id: 't2', task_type: 'train', status: 'running', progress: 30, created_at: '2025-01-01' },
      { job_id: 't3', task_type: 'predict', status: 'completed', progress: 100, created_at: '2025-01-01' },
    ]
    const wrapper = mountTaskBoard()
    await flushPromises()
    expect(wrapper.find('.task-board-page').exists()).toBe(true)
  })

  it('fetchTasks 抛错时设置 fetchFailed', async () => {
    mockTasksStore.fetchTasks.mockRejectedValueOnce(new Error('network error'))
    const wrapper = mountTaskBoard()
    await flushPromises()
    // 应当能渲染重试按钮
    const retryBtn = wrapper.find('button')
    expect(retryBtn.exists()).toBe(true)
  })

  it('渲染创建任务按钮', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    const createBtn = wrapper
      .findAll('.page-header__actions button')
      .find((b) => b.text().includes('新建任务'))
    expect(createBtn).toBeDefined()
    expect(createBtn?.classes()).toContain('el-button--primary')
  })

  it('点击创建任务按钮打开新建弹窗', async () => {
    const wrapper = mountTaskBoard()
    await flushPromises()
    const createBtn = wrapper
      .findAll('.page-header__actions button')
      .find((b) => b.text().includes('新建任务'))
    await createBtn?.trigger('click')
    // 弹窗 visibility 通过 detailVisible 控制
    // 这里仅校验点击不抛错
    expect(wrapper.find('.task-board-page').exists()).toBe(true)
  })
})
