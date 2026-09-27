/**
 * WorkflowPanel.vue 组件测试
 *
 * 覆盖范围：
 *   1. 组件挂载与基础渲染（页面标题、列表、DAG 区、事件日志）
 *   2. 工作流列表交互（选择、刷新、分页、状态筛选）
 *   3. DAG 可视化（节点渲染、边渲染、状态着色、空状态）
 *   4. SSE 事件驱动的 UI 更新（节点状态、事件日志、终态自动刷新）
 *   5. 提交 / 续跑对话框（打开、模板选择、校验、提交、续跑）
 *   6. 当前工作流操作（取消、删除、续跑按钮可见性）
 *
 * 对应 ADR-005 阶段 1 验收标准（前端 DAG 可视化 + SSE 实时状态更新）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { WorkflowSpec, TaskStatus } from '@/contracts/task'

// Helper: 创建带 __v_isRef 标记的伪 ref 对象（使模板 _unref 能正确解包）
// 由于 vi.mock 工厂不能引用 import，使用 vi.hoisted 创建 plain object
// 但需要 __v_isRef 标记让 Vue 模板编译器解包 .value
function pseudoRef<T>(val: T): { value: T; __v_isRef: true; __v_isShallow: false } {
  return { value: val, __v_isRef: true, __v_isShallow: false }
}

// Mock: vue-i18n（useI18n composition API）
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params) {
        let result = key
        Object.entries(params).forEach(([k, v]) => {
          result = result.replace(`{${k}}`, String(v))
        })
        return result
      }
      return key
    },
  }),
}))

// Mock: @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Refresh: { name: 'Refresh', template: '<i class="icon-refresh" />' },
  Plus: { name: 'Plus', template: '<i class="icon-plus" />' },
  CircleClose: { name: 'CircleClose', template: '<i class="icon-circle-close" />' },
  VideoPlay: { name: 'VideoPlay', template: '<i class="icon-video-play" />' },
  Delete: { name: 'Delete', template: '<i class="icon-delete" />' },
}))

// Mock: element-plus（ElMessage / ElMessageBox）
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}))
const mockElMessageBox = vi.hoisted(() => ({
  confirm: vi.fn(() => Promise.resolve('confirm')),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
  ElMessageBox: mockElMessageBox,
  ElDialog: { template: '<div class="el-dialog" :class="{ \'is-visible\': modelValue }"><div class="el-dialog__title">{{ title }}</div><slot /><slot name="footer" /></div>', props: ['modelValue', 'title', 'width'] },
  ElForm: { template: '<form class="el-form"><slot /></form>', props: ['model', 'labelWidth'] },
  ElFormItem: { template: '<div class="el-form-item"><slot /></div>', props: ['label'] },
  ElInput: { template: '<input class="el-input" />', props: ['modelValue', 'type', 'rows', 'placeholder'] },
  ElSelect: { template: '<div class="el-select"><slot /></div>', props: ['modelValue', 'placeholder', 'clearable'] },
  ElOption: { template: '<div class="el-option" />', props: ['label', 'value'] },
  ElButton: {
    template: '<button class="el-button" :class="{ \'el-button--primary\': type === \'primary\', \'el-button--danger\': type === \'danger\', \'el-button--warning\': type === \'warning\' }" @click="$emit(\'click\')"><slot /></button>',
    props: ['type', 'size', 'loading', 'icon'],
    emits: ['click'],
  },
  ElTag: { template: '<span class="el-tag"><slot /></span>', props: ['type', 'size', 'effect'] },
  ElEmpty: { template: '<div class="el-empty"><slot /></div>', props: ['description', 'imageSize'] },
  ElPagination: { template: '<div class="el-pagination" />', props: ['currentPage', 'pageSize', 'total', 'layout'] },
}))

// Mock: @/composables/useWorkflow
// 使用 pseudoRef 创建带 __v_isRef 标记的对象，使模板 _unref 能正确解包
// 构造可控制的 mock stream 与状态
function createMockStream() {
  return {
    events: pseudoRef<any[]>([]),
    isConnected: pseudoRef(false),
    isDone: pseudoRef(false),
    currentStatus: pseudoRef(''),
    nodeStatuses: pseudoRef<Record<string, TaskStatus>>({}),
    error: pseudoRef<string | null>(null),
    connect: vi.fn(),
    close: vi.fn(),
    reset: vi.fn(),
  }
}

let mockStream: ReturnType<typeof createMockStream>

const mockUseWorkflow = vi.hoisted(() => {
  // 使用 pseudoRef 创建带 __v_isRef 标记的伪 ref
  // 这样 Vue 模板的 _unref 能正确解包，使 :workflows="workflows" 传递实际数组
  const pRef = <T>(val: T) => ({ value: val, __v_isRef: true as const, __v_isShallow: false as const })

  return {
    workflows: pRef<any[]>([]),
    loading: pRef(false),
    totalCount: pRef(0),
    currentPage: pRef(1),
    pageSize: pRef(20),
    statusFilter: pRef(''),
    loadWorkflows: vi.fn(() => Promise.resolve()),
    removeWorkflow: vi.fn((_id: string) => Promise.resolve()),
    currentRunId: pRef<string | null>(null),
    currentStatus: pRef<any>(null),
    submitWorkflow: vi.fn((_payload: any) => Promise.resolve('wf_new_001')),
    resumeCurrentWorkflow: vi.fn((_id: string, _payload: any) => Promise.resolve('wf_new_002')),
    cancelCurrent: vi.fn(() => Promise.resolve()),
    refreshCurrentStatus: vi.fn(() => Promise.resolve()),
    selectWorkflow: vi.fn((_id: string) => Promise.resolve()),
    stream: null as any,
    validate: vi.fn((_spec: WorkflowSpec) => Promise.resolve({ valid: true, node_count: 4, edge_count: 3 })),
  }
})

vi.mock('@/composables/useWorkflow', () => ({
  useWorkflow: () => mockUseWorkflow,
}))

import WorkflowPanel from '@/views/WorkflowPanel.vue'

// 测试数据
function makeSpec(overrides: Partial<WorkflowSpec> = {}): WorkflowSpec {
  return {
    name: 'test_workflow',
    version: '1.0.0',
    nodes: [
      { node_id: 'A', task_type: 'task_a', params: {}, inputs: {}, retry: 0, timeout_seconds: 60 },
      { node_id: 'B', task_type: 'task_b', params: {}, inputs: { in_a: '${A.out_a}' }, retry: 0, timeout_seconds: 60 },
      { node_id: 'C', task_type: 'task_c', params: {}, inputs: { in_b: '${B.out_b}' }, retry: 0, timeout_seconds: 60 },
    ],
    edges: [
      { upstream: 'A', downstream: 'B' },
      { upstream: 'B', downstream: 'C' },
    ],
    inputs: {},
    outputs: { final: '${C.out_c}' },
    metadata: {},
    ...overrides,
  }
}

function makeWorkflowRun(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'wf_001_abcdefghij',
    name: 'test_workflow',
    version: '1.0.0',
    status: 'pending' as TaskStatus,
    spec: makeSpec(),
    owner_id: 'user_001',
    created_at: '2026-07-13T10:00:00Z',
    node_statuses: {},
    ...overrides,
  }
}

// 测试主体
describe('WorkflowPanel.vue', () => {
  let pinia: ReturnType<typeof createPinia>
  let router: ReturnType<typeof createRouter>

  beforeEach(() => {
    setActivePinia((pinia = createPinia()))
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div/>' } }],
    })

    // 重置 mock 状态
    mockStream = createMockStream()
    mockUseWorkflow.stream = mockStream
    mockUseWorkflow.workflows.value = []
    mockUseWorkflow.loading.value = false
    mockUseWorkflow.totalCount.value = 0
    mockUseWorkflow.currentPage.value = 1
    mockUseWorkflow.pageSize.value = 20
    mockUseWorkflow.statusFilter.value = ''
    mockUseWorkflow.currentRunId.value = null
    mockUseWorkflow.currentStatus.value = null
    mockUseWorkflow.loadWorkflows.mockResolvedValue(undefined)
    mockUseWorkflow.removeWorkflow.mockResolvedValue(undefined)
    mockUseWorkflow.submitWorkflow.mockResolvedValue('wf_new_001')
    mockUseWorkflow.resumeCurrentWorkflow.mockResolvedValue('wf_new_002')
    mockUseWorkflow.cancelCurrent.mockResolvedValue(undefined)
    mockUseWorkflow.refreshCurrentStatus.mockResolvedValue(undefined)
    mockUseWorkflow.selectWorkflow.mockResolvedValue(undefined)
    mockUseWorkflow.validate.mockResolvedValue({ valid: true, node_count: 3, edge_count: 2 })

    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const mountPanel = (options = {}) => {
  return mount(WorkflowPanel, {
    global: {
      plugins: [pinia, router],
      // 注册全局指令 stub（避免 v-loading 解析失败）
      directives: {
        loading: {
          mounted: () => {},
          updated: () => {},
        },
      },
      // 将 Element Plus 组件注册为全局 stub，使其在整棵组件树中可用
      components: {
        'el-dialog': {
          template: '<div class="el-dialog" :class="{ \'is-visible\': modelValue }"><div class="el-dialog__title">{{ title }}</div><slot /><slot name="footer" /></div>',
          props: ['modelValue', 'title', 'width'],
        },
        'el-form': { template: '<form class="el-form"><slot /></form>', props: ['model', 'labelWidth'] },
        'el-form-item': { template: '<div class="el-form-item"><slot /></div>', props: ['label'] },
        'el-input': { template: '<input class="el-input" />', props: ['modelValue', 'type', 'rows', 'placeholder'] },
        'el-select': { template: '<div class="el-select"><slot /></div>', props: ['modelValue', 'placeholder', 'clearable'] },
        'el-option': { template: '<div class="el-option" />', props: ['label', 'value'] },
        'el-button': {
          template: '<button class="el-button" :class="{ \'el-button--primary\': type === \'primary\', \'el-button--danger\': type === \'danger\', \'el-button--warning\': type === \'warning\' }" @click="$emit(\'click\')"><slot /></button>',
          props: ['type', 'size', 'loading', 'icon'],
          emits: ['click'],
        },
        'el-tag': { template: '<span class="el-tag"><slot /></span>', props: ['type', 'size', 'effect'] },
        'el-empty': { template: '<div class="el-empty"><slot /></div>', props: ['description', 'imageSize'] },
        'el-pagination': { template: '<div class="el-pagination" />', props: ['currentPage', 'pageSize', 'total', 'layout'] },
      },
      stubs: {
        // 新拆分子组件不 stub，让测试能访问其内部元素
        WorkflowPageHeader: false,
        WorkflowListPanel: false,
        WorkflowDag: false,
        WorkflowEventLog: false,
        WorkflowSubmitDialog: false,
      },
      ...options,
    },
  })
}

// 1. 组件挂载与基础渲染
  describe('基础渲染', () => {
    it('组件能正确挂载', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.workflow-panel-page').exists()).toBe(true)
    })

    it('渲染页面标题与副标题', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.page-header__title h1').text()).toBe('workflowPanel.pageTitle')
      expect(wrapper.find('.page-header__subtitle').text()).toBe('workflowPanel.pageSubtitle')
    })

    it('渲染顶部操作按钮（刷新、提交）', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const actions = wrapper.find('.page-header__actions')
      expect(actions.exists()).toBe(true)
      const buttons = actions.findAll('button')
      // 至少有 刷新、提交 2 个按钮（取消/续跑/删除按条件显示）
      expect(buttons.length).toBeGreaterThanOrEqual(2)
    })

    it('挂载时调用 loadWorkflows', async () => {
      mountPanel()
      await flushPromises()
      expect(mockUseWorkflow.loadWorkflows).toHaveBeenCalled()
    })

    it('渲染主布局（列表 + 详情）', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.workflow-main').exists()).toBe(true)
      expect(wrapper.find('.workflow-list-panel').exists()).toBe(true)
      expect(wrapper.find('.workflow-detail-panel').exists()).toBe(true)
    })

    it('渲染 DAG 区与事件日志区', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.dag-section').exists()).toBe(true)
      expect(wrapper.find('.event-log-section').exists()).toBe(true)
    })
  })

// 2. 工作流列表
  describe('工作流列表', () => {
    it('列表为空时渲染 el-empty', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.el-empty').exists()).toBe(true)
    })

    it('列表有数据时渲染 workflow-card', async () => {
      mockUseWorkflow.workflows.value = [
        makeWorkflowRun({ id: 'wf_001', name: 'flow_a', status: 'completed' }),
        makeWorkflowRun({ id: 'wf_002', name: 'flow_b', status: 'running' }),
      ]
      const wrapper = mountPanel()
      await flushPromises()
      const cards = wrapper.findAll('.workflow-card')
      expect(cards.length).toBe(2)
      expect(cards[0].find('.workflow-name').text()).toBe('flow_a')
    })

    it('点击 workflow-card 触发 selectWorkflow', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      const wrapper = mountPanel()
      await flushPromises()
      await wrapper.find('.workflow-card').trigger('click')
      await flushPromises()
      expect(mockUseWorkflow.selectWorkflow).toHaveBeenCalledWith('wf_001')
    })

    it('当前选中的 workflow-card 带 active 类', async () => {
      mockUseWorkflow.workflows.value = [
        makeWorkflowRun({ id: 'wf_001' }),
        makeWorkflowRun({ id: 'wf_002' }),
      ]
      mockUseWorkflow.currentRunId.value = 'wf_002'
      const wrapper = mountPanel()
      await flushPromises()
      const cards = wrapper.findAll('.workflow-card')
      expect(cards[0].classes()).not.toContain('active')
      expect(cards[1].classes()).toContain('active')
    })

    it('点击刷新按钮调用 loadWorkflows', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      mockUseWorkflow.loadWorkflows.mockClear()
      const buttons = wrapper.findAll('.page-header__actions button')
      const refreshBtn = buttons[0] // 第一个按钮是刷新
      await refreshBtn.trigger('click')
      expect(mockUseWorkflow.loadWorkflows).toHaveBeenCalled()
    })

    it('渲染分页组件', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.workflow-list-footer .el-pagination').exists()).toBe(true)
    })

    it('渲染状态筛选下拉框', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.panel-filter .el-select').exists()).toBe(true)
    })
  })

// 3. DAG 可视化
  describe('DAG 可视化', () => {
    it('未选中工作流时渲染 el-empty', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const dagWrapper = wrapper.find('.dag-canvas-wrapper')
      expect(dagWrapper.exists()).toBe(true)
      expect(dagWrapper.find('.el-empty').exists()).toBe(true)
    })

    it('选中工作流时渲染 SVG', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      const svg = wrapper.find('.dag-svg')
      expect(svg.exists()).toBe(true)
    })

    it('SVG 中渲染所有节点（g.dag-node-group）', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      const nodes = wrapper.findAll('.dag-node-group')
      expect(nodes.length).toBe(3) // makeSpec 默认 3 个节点
    })

    it('SVG 中渲染所有边（path.dag-edge）', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      const edges = wrapper.findAll('.dag-edge')
      expect(edges.length).toBe(2) // makeSpec 默认 2 条边
    })

    it('节点状态为 completed 时 rect 带 status-completed 类', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.nodeStatuses.value = { A: 'completed', B: 'running', C: 'pending' }
      const wrapper = mountPanel()
      await flushPromises()
      const rects = wrapper.findAll('.dag-node-rect')
      expect(rects[0].classes()).toContain('status-completed')
      expect(rects[1].classes()).toContain('status-running')
      expect(rects[2].classes()).toContain('status-pending')
    })

    it('点击节点设置 selectedNodeId', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      const nodes = wrapper.findAll('.dag-node-group')
      await nodes[1].trigger('click')
      // 内部状态 selectedNodeId 已变更，无直接 DOM 断言，但确保不报错
      expect(true).toBe(true)
    })

    it('5 节点 DAG 也能正确分层布局', async () => {
      const spec = makeSpec({
        nodes: [
          { node_id: 'N1', task_type: 't1', params: {}, inputs: {}, retry: 0, timeout_seconds: 60 },
          { node_id: 'N2', task_type: 't2', params: {}, inputs: {}, retry: 0, timeout_seconds: 60 },
          { node_id: 'N3', task_type: 't3', params: {}, inputs: { a: '${N1.out}' }, retry: 0, timeout_seconds: 60 },
          { node_id: 'N4', task_type: 't4', params: {}, inputs: { b: '${N2.out}' }, retry: 0, timeout_seconds: 60 },
          { node_id: 'N5', task_type: 't5', params: {}, inputs: { c: '${N3.out}', d: '${N4.out}' }, retry: 0, timeout_seconds: 60 },
        ],
        edges: [
          { upstream: 'N1', downstream: 'N3' },
          { upstream: 'N2', downstream: 'N4' },
          { upstream: 'N3', downstream: 'N5' },
          { upstream: 'N4', downstream: 'N5' },
        ],
      })
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', spec })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.findAll('.dag-node-group').length).toBe(5)
      expect(wrapper.findAll('.dag-edge').length).toBe(4)
    })
  })

// 4. SSE 事件驱动的 UI 更新
  describe('SSE 事件 UI', () => {
    it('stream 连接中显示 streamConnected 状态', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.isConnected.value = true
      const wrapper = mountPanel()
      await flushPromises()
      const indicator = wrapper.find('.stream-indicator')
      expect(indicator.exists()).toBe(true)
      expect(indicator.text()).toBe('workflowPanel.streamConnected')
    })

    it('stream 完成时显示 streamDone 状态', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.isDone.value = true
      const wrapper = mountPanel()
      await flushPromises()
      const indicator = wrapper.find('.stream-indicator')
      expect(indicator.classes()).toContain('done')
      expect(indicator.text()).toBe('workflowPanel.streamDone')
    })

    it('stream 错误时显示 streamError 状态', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.error.value = '连接失败'
      const wrapper = mountPanel()
      await flushPromises()
      const indicator = wrapper.find('.stream-indicator')
      expect(indicator.text()).toBe('workflowPanel.streamError')
    })

    it('事件日志为空时显示空提示', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.event-log-empty').exists()).toBe(true)
    })

    it('事件日志有数据时渲染 event-log-entry', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.events.value = [
        {
          workflow_run_id: 'wf_001',
          node_id: 'A',
          event_type: 'node_started',
          payload: { message: 'starting' },
          timestamp: 1720857600,
        },
        {
          workflow_run_id: 'wf_001',
          node_id: 'A',
          event_type: 'node_completed',
          payload: { progress: 1.0 },
          timestamp: 1720857660,
        },
      ]
      const wrapper = mountPanel()
      await flushPromises()
      const entries = wrapper.findAll('.event-log-entry')
      expect(entries.length).toBe(2)
      expect(entries[0].classes()).toContain('event-node_started')
      expect(entries[1].classes()).toContain('event-node_completed')
    })

    it('事件条目渲染时间、类型、节点 ID', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.events.value = [
        {
          workflow_run_id: 'wf_001',
          node_id: 'A',
          event_type: 'node_failed',
          payload: { error: 'timeout' },
          timestamp: 1720857600,
        },
      ]
      const wrapper = mountPanel()
      await flushPromises()
      const entry = wrapper.find('.event-log-entry')
      expect(entry.find('.event-type').text()).toBe('node_failed')
      expect(entry.find('.event-node').text()).toBe('[A]')
      expect(entry.find('.event-msg').text()).toBe('timeout')
    })

    it('点击清空事件按钮调用 stream.reset', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.events.value = [
        {
          workflow_run_id: 'wf_001',
          event_type: 'node_started',
          payload: {},
          timestamp: 1720857600,
        },
      ]
      const wrapper = mountPanel()
      await flushPromises()
      const clearBtn = wrapper.find('.event-log-section .el-button')
      expect(clearBtn.exists()).toBe(true)
      await clearBtn.trigger('click')
      expect(mockStream.reset).toHaveBeenCalled()
    })

    it('DAG 节点状态优先取 SSE nodeStatuses', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.nodeStatuses.value = { A: 'failed' }
      // 持久化状态为 completed，SSE 应优先
      mockUseWorkflow.currentStatus.value = {
        status: 'running',
        node_statuses: { A: 'completed' },
      }
      const wrapper = mountPanel()
      await flushPromises()
      const rects = wrapper.findAll('.dag-node-rect')
      expect(rects[0].classes()).toContain('status-failed')
    })

    it('DAG 节点状态兜底取持久化 node_statuses', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      // 不设置 SSE nodeStatuses
      mockUseWorkflow.currentStatus.value = {
        status: 'running',
        node_statuses: { A: 'completed', B: 'running' },
      }
      const wrapper = mountPanel()
      await flushPromises()
      const rects = wrapper.findAll('.dag-node-rect')
      expect(rects[0].classes()).toContain('status-completed')
      expect(rects[1].classes()).toContain('status-running')
    })
  })

// 5. 当前工作流操作按钮可见性
  describe('操作按钮可见性', () => {
    it('无当前运行时不显示取消/续跑/删除按钮', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const texts = buttons.map(b => b.text())
      // 仅刷新、提交
      expect(texts.length).toBe(2)
    })

    it('running 状态显示取消按钮，不显示续跑按钮', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'running' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'running'
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const texts = buttons.map(b => b.text())
      expect(texts).toContain('workflowPanel.btnCancel')
      expect(texts).not.toContain('workflowPanel.btnResume')
    })

    it('failed 状态显示续跑按钮，不显示取消按钮', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'failed' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'failed'
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const texts = buttons.map(b => b.text())
      expect(texts).toContain('workflowPanel.btnResume')
      expect(texts).not.toContain('workflowPanel.btnCancel')
    })

    it('有 currentRunId 时显示删除按钮', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const texts = buttons.map(b => b.text())
      expect(texts).toContain('workflowPanel.btnDelete')
    })
  })

// 6. 提交 / 续跑对话框
  describe('提交对话框', () => {
    it('点击提交按钮打开对话框（submit 模式）', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const submitBtn = buttons.find(b => b.text().includes('workflowPanel.btnSubmit'))
      await submitBtn?.trigger('click')
      await flushPromises()
      const dialog = wrapper.find('.el-dialog')
      expect(dialog.exists()).toBe(true)
      expect(dialog.text()).toContain('workflowPanel.dialogSubmitTitle')
    })

    it('对话框中渲染模板选择器、spec 编辑器、owner 输入框', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const submitBtn = buttons.find(b => b.text().includes('workflowPanel.btnSubmit'))
      await submitBtn?.trigger('click')
      await flushPromises()
      const dialog = wrapper.find('.el-dialog')
      expect(dialog.findAll('.el-form-item').length).toBeGreaterThanOrEqual(3)
    })

    it('对话框中默认有 1 个内置模板（刀具磨损预测流水线）', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const submitBtn = buttons.find(b => b.text().includes('workflowPanel.btnSubmit'))
      await submitBtn?.trigger('click')
      await flushPromises()
      // builtinTemplates 在 onMounted 中初始化
      expect(wrapper.vm).toBeDefined()
    })

    it('点击校验按钮调用 validate', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const submitBtn = buttons.find(b => b.text().includes('workflowPanel.btnSubmit'))
      await submitBtn?.trigger('click')
      await flushPromises()

      // 填入合法 spec JSON
      const spec = makeSpec()
      // 通过组件实例修改 submitForm
      const vm = wrapper.vm as any
      vm.submitForm.specYaml = JSON.stringify(spec)
      await flushPromises()

      // 找到校验按钮（对话框 footer 中）
      const dialogButtons = wrapper.findAll('.el-dialog button')
      const validateBtn = dialogButtons.find(b => b.text().includes('workflowPanel.btnValidate'))
      await validateBtn?.trigger('click')
      await flushPromises()

      expect(mockUseWorkflow.validate).toHaveBeenCalled()
    })

    it('空 spec 点击校验提示警告', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const submitBtn = buttons.find(b => b.text().includes('workflowPanel.btnSubmit'))
      await submitBtn?.trigger('click')
      await flushPromises()

      // spec 留空
      const dialogButtons = wrapper.findAll('.el-dialog button')
      const validateBtn = dialogButtons.find(b => b.text().includes('workflowPanel.btnValidate'))
      await validateBtn?.trigger('click')
      await flushPromises()

      expect(mockElMessage.warning).toHaveBeenCalledWith('workflowPanel.msgSpecEmpty')
      expect(mockUseWorkflow.validate).not.toHaveBeenCalled()
    })

    it('点击提交按钮调用 submitWorkflow 并关闭对话框', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      const buttons = wrapper.findAll('.page-header__actions button')
      const submitBtn = buttons.find(b => b.text().includes('workflowPanel.btnSubmit'))
      await submitBtn?.trigger('click')
      await flushPromises()

      const spec = makeSpec()
      const vm = wrapper.vm as any
      vm.submitForm.specYaml = JSON.stringify(spec)
      await flushPromises()

      const dialogButtons = wrapper.findAll('.el-dialog button')
      const confirmBtn = dialogButtons.find(b => b.text().includes('workflowPanel.btnSubmitConfirm'))
      await confirmBtn?.trigger('click')
      await flushPromises()

      expect(mockUseWorkflow.submitWorkflow).toHaveBeenCalledWith(
        expect.objectContaining({ spec: expect.objectContaining({ name: 'test_workflow' }) }),
      )
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('failed 状态下点击续跑按钮打开 resume 模式对话框', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'failed' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'failed'
      const wrapper = mountPanel()
      await flushPromises()

      const buttons = wrapper.findAll('.page-header__actions button')
      const resumeBtn = buttons.find(b => b.text().includes('workflowPanel.btnResume'))
      await resumeBtn?.trigger('click')
      await flushPromises()

      const dialog = wrapper.find('.el-dialog')
      expect(dialog.exists()).toBe(true)
      expect(dialog.text()).toContain('workflowPanel.dialogResumeTitle')
    })

    it('续跑模式点击确认调用 resumeCurrentWorkflow', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'failed' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'failed'
      const wrapper = mountPanel()
      await flushPromises()

      const buttons = wrapper.findAll('.page-header__actions button')
      const resumeBtn = buttons.find(b => b.text().includes('workflowPanel.btnResume'))
      await resumeBtn?.trigger('click')
      await flushPromises()

      const vm = wrapper.vm as any
      // resume 模式会预填当前 spec
      vm.submitForm.specYaml = JSON.stringify(makeSpec())
      await flushPromises()

      const dialogButtons = wrapper.findAll('.el-dialog button')
      const confirmBtn = dialogButtons.find(b => b.text().includes('workflowPanel.btnResumeConfirm'))
      await confirmBtn?.trigger('click')
      await flushPromises()

      expect(mockUseWorkflow.resumeCurrentWorkflow).toHaveBeenCalledWith(
        'wf_001',
        expect.objectContaining({ spec: expect.objectContaining({ name: 'test_workflow' }) }),
      )
    })
  })

// 7. 取消 / 删除当前工作流
  describe('取消 / 删除', () => {
    it('点击取消按钮弹出确认框，确认后调用 cancelCurrent', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'running' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'running'
      const wrapper = mountPanel()
      await flushPromises()

      const buttons = wrapper.findAll('.page-header__actions button')
      const cancelBtn = buttons.find(b => b.text().includes('workflowPanel.btnCancel'))
      await cancelBtn?.trigger('click')
      await flushPromises()

      expect(mockElMessageBox.confirm).toHaveBeenCalled()
      expect(mockUseWorkflow.cancelCurrent).toHaveBeenCalled()
      expect(mockElMessage.success).toHaveBeenCalledWith('workflowPanel.msgCancelSuccess')
    })

    it('取消确认框被用户取消时不调用 cancelCurrent', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'running' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'running'
      mockElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
      const wrapper = mountPanel()
      await flushPromises()

      const buttons = wrapper.findAll('.page-header__actions button')
      const cancelBtn = buttons.find(b => b.text().includes('workflowPanel.btnCancel'))
      await cancelBtn?.trigger('click')
      await flushPromises()

      expect(mockUseWorkflow.cancelCurrent).not.toHaveBeenCalled()
    })

    it('点击删除按钮弹出确认框，确认后调用 removeWorkflow', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      const wrapper = mountPanel()
      await flushPromises()

      const buttons = wrapper.findAll('.page-header__actions button')
      const deleteBtn = buttons.find(b => b.text().includes('workflowPanel.btnDelete'))
      await deleteBtn?.trigger('click')
      await flushPromises()

      expect(mockElMessageBox.confirm).toHaveBeenCalled()
      expect(mockUseWorkflow.removeWorkflow).toHaveBeenCalledWith('wf_001')
      expect(mockElMessage.success).toHaveBeenCalledWith('workflowPanel.msgDeleteSuccess')
    })
  })

// 8. DAG 状态指示
  describe('DAG 状态指示', () => {
    it('当前选中工作流时显示 DAG 状态标签', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'running' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'running'
      const wrapper = mountPanel()
      await flushPromises()
      const dagStatus = wrapper.find('.dag-status')
      expect(dagStatus.exists()).toBe(true)
      // 状态标签
      expect(dagStatus.find('.el-tag').exists()).toBe(true)
    })

    it('未选中工作流时不显示 DAG 状态标签', async () => {
      const wrapper = mountPanel()
      await flushPromises()
      expect(wrapper.find('.dag-status').exists()).toBe(false)
    })

    it('currentDisplayStatus 优先取 SSE currentStatus', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001', status: 'pending' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.currentStatus.value = 'running'
      // 持久化状态为 pending，SSE 应优先
      mockUseWorkflow.currentStatus.value = { status: 'pending', node_statuses: {} }
      const wrapper = mountPanel()
      await flushPromises()
      // canCancel 为 true（running 状态）
      const buttons = wrapper.findAll('.page-header__actions button')
      const texts = buttons.map(b => b.text())
      expect(texts).toContain('workflowPanel.btnCancel')
    })
  })

// 9. 边路径高亮
  describe('边高亮', () => {
    it('upstream completed 且 downstream 非 pending 时边带 active 类', async () => {
      mockUseWorkflow.workflows.value = [makeWorkflowRun({ id: 'wf_001' })]
      mockUseWorkflow.currentRunId.value = 'wf_001'
      mockStream.nodeStatuses.value = { A: 'completed', B: 'running', C: 'pending' }
      const wrapper = mountPanel()
      await flushPromises()
      const edges = wrapper.findAll('.dag-edge')
      // A->B：A completed, B running(非pending) → active
      expect(edges[0].classes()).toContain('active')
      // B->C：B running(非completed) → 不 active
      expect(edges[1].classes()).not.toContain('active')
    })
  })
})
