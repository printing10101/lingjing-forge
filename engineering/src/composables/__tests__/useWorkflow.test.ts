/**
 * useWorkflow composable 单元测试
 *
 * 覆盖范围：
 *   1. REST API 无状态函数（validateWorkflow / runWorkflow / resumeWorkflow /
 *      getWorkflowStatus / cancelWorkflow / listWorkflows / deleteWorkflow）
 *   2. useWorkflowStream SSE 订阅（事件分发 / 节点状态更新 / 终态关闭 / 重连）
 *   3. useWorkflow 聚合 composable（loadWorkflows / submitWorkflow / selectWorkflow /
 *      cancelCurrent / removeWorkflow / refreshCurrentStatus）
 *
 * 对应 ADR-005 阶段 1 验收标准（前端 DAG 可视化 + SSE 实时状态更新）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { WorkflowSpec, WorkflowEvent, TaskResult } from '@/contracts/task'

// Mock 依赖：http 模块 + EventSource
const mocks = vi.hoisted(() => {
  return {
    http: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      resolveBackendUrl: vi.fn((path: string) => path),
    },
  }
})

vi.mock('@/utils/http', () => ({
  default: mocks.http,
  resolveBackendUrl: mocks.http.resolveBackendUrl,
}))

// EventSource mock：允许测试触发 onopen/onerror/addEventListener 回调
class MockEventSource {
  url: string
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>()
  static instances: MockEventSource[] = []
  static lastInstance: MockEventSource | null = null
  readyState = 0

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
    MockEventSource.lastInstance = this
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, [])
    this.listeners.get(type)!.push(listener)
  }

  removeEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    const arr = this.listeners.get(type)
    if (arr) {
      const idx = arr.indexOf(listener)
      if (idx >= 0) arr.splice(idx, 1)
    }
  }

  close(): void {
    this.readyState = 2
  }

  /** 测试辅助：模拟服务端推送事件 */
  emit(type: string, data: unknown): void {
    const arr = this.listeners.get(type) ?? []
    const event = { data: JSON.stringify(data) } as MessageEvent
    arr.forEach(fn => fn(event))
  }

  /** 测试辅助：触发 onopen */
  simulateOpen(): void {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }

  /** 测试辅助：触发 onerror */
  simulateError(): void {
    this.readyState = 0
    this.onerror?.(new Event('error'))
  }
}

// 替换全局 EventSource
vi.stubGlobal('EventSource', MockEventSource)

// 导入被测模块（在所有 mock 注册之后）
import {
  validateWorkflow,
  runWorkflow,
  resumeWorkflow,
  getWorkflowStatus,
  cancelWorkflow,
  listWorkflows,
  deleteWorkflow,
  useWorkflowStream,
  useWorkflow,
  TERMINAL_EVENTS,
} from '../useWorkflow'
import { API_CONFIG, buildApiPath } from '@/config/api'

// 测试数据构造

const BASE = API_CONFIG.WORKFLOWS

function makeSpec(overrides: Partial<WorkflowSpec> = {}): WorkflowSpec {
  return {
    name: 'test_workflow',
    version: '1.0.0',
    nodes: [
      { node_id: 'A', task_type: 'task_a', params: {}, inputs: {}, retry: 0, timeout_seconds: 60 },
      { node_id: 'B', task_type: 'task_b', params: {}, inputs: { in_a: '${A.out_a}' }, retry: 0, timeout_seconds: 60 },
    ],
    edges: [{ upstream: 'A', downstream: 'B' }],
    inputs: {},
    outputs: { final: '${B.out_b}' },
    metadata: {},
    ...overrides,
  }
}

function envelope<T>(data: T): { data: { data: T } } {
  return { data: { data } }
}

// 测试用例

describe('useWorkflow - REST API 无状态函数', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('validateWorkflow', () => {
    it('POST /validate 返回校验结果', async () => {
      const spec = makeSpec()
      mocks.http.post.mockResolvedValueOnce(
        envelope({ valid: true, node_count: 2, edge_count: 1 }),
      )

      const result = await validateWorkflow(spec)

      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, '/validate'),
        spec,
      )
      expect(result.valid).toBe(true)
      expect(result.node_count).toBe(2)
      expect(result.edge_count).toBe(1)
    })

    it('http 抛错时透传异常', async () => {
      mocks.http.post.mockRejectedValueOnce(new Error('网络错误'))
      await expect(validateWorkflow(makeSpec())).rejects.toThrow('网络错误')
    })
  })

  describe('runWorkflow', () => {
    it('POST /run 返回 workflow_run_id', async () => {
      const body = { spec: makeSpec(), owner_id: 'user_1' }
      mocks.http.post.mockResolvedValueOnce(
        envelope({ workflow_run_id: 'wf_001', status: 'queued' }),
      )

      const result = await runWorkflow(body)

      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, '/run'),
        body,
      )
      expect(result.workflow_run_id).toBe('wf_001')
      expect(result.status).toBe('queued')
    })
  })

  describe('resumeWorkflow', () => {
    it('POST /{id}/resume 返回新的 workflow_run_id', async () => {
      const originalId = 'wf_001'
      const body = { spec: makeSpec() }
      mocks.http.post.mockResolvedValueOnce(
        envelope({ workflow_run_id: 'wf_002', status: 'running' }),
      )

      const result = await resumeWorkflow(originalId, body)

      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, `/${originalId}/resume`),
        body,
      )
      expect(result.workflow_run_id).toBe('wf_002')
    })
  })

  describe('getWorkflowStatus', () => {
    it('GET /{id} 返回工作流状态', async () => {
      const runId = 'wf_001'
      const statusPayload = {
        workflow_run_id: runId,
        spec_name: 'test_workflow',
        status: 'running' as const,
        started_at: 1700000000,
        node_statuses: { A: 'completed', B: 'running' },
      }
      mocks.http.get.mockResolvedValueOnce(envelope(statusPayload))

      const result = await getWorkflowStatus(runId)

      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, `/${runId}`),
      )
      expect(result.workflow_run_id).toBe(runId)
      expect(result.status).toBe('running')
      expect(result.node_statuses.A).toBe('completed')
    })
  })

  describe('cancelWorkflow', () => {
    it('POST /{id}/cancel 返回取消状态', async () => {
      const runId = 'wf_001'
      mocks.http.post.mockResolvedValueOnce(
        envelope({ workflow_run_id: runId, status: 'cancelled' }),
      )

      const result = await cancelWorkflow(runId)

      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, `/${runId}/cancel`),
      )
      expect(result.status).toBe('cancelled')
    })
  })

  describe('listWorkflows', () => {
    it('GET / 带查询参数返回列表', async () => {
      const params = { status: 'completed', limit: 10, offset: 0 }
      const listPayload = {
        workflows: [
          { id: 'wf_001', name: 'w1', version: '1.0.0', spec: makeSpec(), status: 'completed', inputs: null, outputs: null, owner_id: null, error: null, metadata: {}, created_at: null, updated_at: null, started_at: null, completed_at: null },
        ],
        limit: 10,
        offset: 0,
      }
      mocks.http.get.mockResolvedValueOnce(envelope(listPayload))

      const result = await listWorkflows(params)

      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        { params },
      )
      expect(result.workflows).toHaveLength(1)
      expect(result.workflows[0].id).toBe('wf_001')
    })

    it('无参数调用使用默认空对象', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({ workflows: [], limit: 20, offset: 0 }),
      )
      await listWorkflows()
      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        { params: {} },
      )
    })
  })

  describe('deleteWorkflow', () => {
    it('DELETE /{id} 返回删除结果', async () => {
      const runId = 'wf_001'
      mocks.http.delete.mockResolvedValueOnce(
        envelope({ workflow_run_id: runId, deleted: true }),
      )

      const result = await deleteWorkflow(runId)

      expect(mocks.http.delete).toHaveBeenCalledWith(
        buildApiPath(BASE, `/${runId}`),
      )
      expect(result.deleted).toBe(true)
    })
  })
})

// useWorkflowStream SSE 订阅测试

describe('useWorkflow - useWorkflowStream SSE 订阅', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    MockEventSource.instances = []
    MockEventSource.lastInstance = null
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('connect() 建立 EventSource 连接并注册事件监听器', async () => {
    const { connect, isConnected } = useWorkflowStream('wf_001')
    connect()

    const es = MockEventSource.lastInstance
    expect(es).not.toBeNull()
    expect(es!.url).toBe(buildApiPath(BASE, '/wf_001/stream'))

    es!.simulateOpen()
    expect(isConnected.value).toBe(true)
  })

  it('node_started 事件更新 nodeStatuses 为 running', async () => {
    const { connect, nodeStatuses } = useWorkflowStream('wf_001')
    connect()

    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      node_id: 'A',
      event_type: 'node_started',
      payload: { job_id: 'job_a', status: 'running', progress: 0, timestamp: Date.now() },
      timestamp: Date.now(),
    }
    es.emit('node_started', event)

    expect(nodeStatuses.value.A).toBe('running')
  })

  it('node_completed 事件更新 nodeStatuses 为 completed', async () => {
    const { connect, nodeStatuses } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      node_id: 'A',
      event_type: 'node_completed',
      payload: {
        job_id: 'job_a',
        status: 'completed',
        progress: 1,
        timestamp: Date.now(),
      },
      timestamp: Date.now(),
    }
    es.emit('node_completed', event)

    expect(nodeStatuses.value.A).toBe('completed')
  })

  it('node_failed 事件更新 nodeStatuses 为 failed', async () => {
    const { connect, nodeStatuses, currentStatus } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      node_id: 'A',
      event_type: 'node_failed',
      payload: {
        job_id: 'job_a',
        status: 'failed',
        progress: 0,
        timestamp: Date.now(),
      },
      timestamp: Date.now(),
    }
    es.emit('node_failed', event)

    expect(nodeStatuses.value.A).toBe('failed')
    // 单节点失败不立即置 workflow 为 failed，由 workflow_failed 统一收尾
    expect(currentStatus.value).toBe('running')
  })

  it('node_skipped 事件更新 nodeStatuses 为 skipped', async () => {
    const { connect, nodeStatuses } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      node_id: 'B',
      event_type: 'node_skipped',
      payload: {
        job_id: 'job_b',
        status: 'skipped',
        progress: 0,
        timestamp: Date.now(),
      },
      timestamp: Date.now(),
    }
    es.emit('node_skipped', event)

    expect(nodeStatuses.value.B).toBe('skipped')
  })

  it('workflow_completed 事件设置 isDone=true 并关闭连接', async () => {
    const { connect, isDone, currentStatus, isConnected } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()
    expect(isConnected.value).toBe(true)

    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      event_type: 'workflow_completed',
      payload: {
        job_id: 'wf_001',
        status: 'completed',
        progress: 1,
        timestamp: Date.now(),
      },
      timestamp: Date.now(),
    }
    es.emit('workflow_completed', event)

    expect(currentStatus.value).toBe('completed')
    expect(isDone.value).toBe(true)
    // 终态后连接应关闭
    expect(es.readyState).toBe(2)
  })

  it('workflow_failed 事件提取 error 并关闭连接', async () => {
    const { connect, isDone, currentStatus, error } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const failedResult: TaskResult = {
      status: 'failed',
      outputs: {},
      metrics: {},
      error: '节点 B 执行失败：模型加载异常',
      error_code: 'MODEL_LOAD_ERROR',
    }
    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      event_type: 'workflow_failed',
      payload: failedResult,
      timestamp: Date.now(),
    }
    es.emit('workflow_failed', event)

    expect(currentStatus.value).toBe('failed')
    expect(isDone.value).toBe(true)
    expect(error.value).toContain('节点 B 执行失败')
    expect(es.readyState).toBe(2)
  })

  it('workflow_failed 事件无 error 字段时使用默认消息', async () => {
    const { connect, error } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const event: WorkflowEvent = {
      workflow_run_id: 'wf_001',
      event_type: 'workflow_failed',
      payload: { status: 'failed', outputs: {}, metrics: {} },
      timestamp: Date.now(),
    }
    es.emit('workflow_failed', event)

    expect(error.value).toBe('工作流执行失败')
  })

  it('事件解析失败时不断开整条流', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { connect, nodeStatuses } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    // 直接通过原生 emit 发送非法 JSON
    const arr = (es as unknown as { listeners: Map<string, unknown[]> }).listeners.get('node_started') as Array<(ev: MessageEvent) => void> | undefined
    expect(arr).toBeDefined()
    arr![0]({ data: '{invalid json' } as MessageEvent)

    expect(warnSpy).toHaveBeenCalled()
    expect(nodeStatuses.value.A).toBeUndefined()
    warnSpy.mockRestore()
  })

  it('stream_error 事件设置 error 并关闭连接', async () => {
    const { connect, error, isDone } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    es.emit('stream_error', { error: '后端流异常退出' })

    expect(error.value).toBe('后端流异常退出')
    expect(isDone.value).toBe(true)
    expect(es.readyState).toBe(2)
  })

  it('stream_error 解析失败时使用兜底消息', async () => {
    const { connect, error } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    const arr = (es as unknown as { listeners: Map<string, unknown[]> }).listeners.get('stream_error') as Array<(ev: MessageEvent) => void> | undefined
    arr![0]({ data: '{bad' } as MessageEvent)

    expect(error.value).toBe('工作流事件流异常（解析失败）')
  })

  it('onerror 触发指数退避重连（autoReconnect=true）', async () => {
    const { connect, isConnected } = useWorkflowStream('wf_001', {
      autoReconnect: true,
      maxRetries: 3,
      baseDelay: 100,
      maxDelay: 1000,
    })
    connect()
    const firstEs = MockEventSource.lastInstance!
    esSimulateError(firstEs)

    expect(isConnected.value).toBe(false)

    // 推进 100ms（第一次重连）
    await vi.advanceTimersByTimeAsync(100)
    expect(MockEventSource.instances.length).toBe(2)
  })

  it('达到 maxRetries 后停止重连并设置 error', async () => {
    const { connect, error } = useWorkflowStream('wf_001', {
      autoReconnect: true,
      maxRetries: 2,
      baseDelay: 50,
      maxDelay: 200,
    })
    connect()

    const es1 = MockEventSource.lastInstance!
    esSimulateError(es1)
    await vi.advanceTimersByTimeAsync(50)

    const es2 = MockEventSource.lastInstance!
    esSimulateError(es2)
    await vi.advanceTimersByTimeAsync(100)

    const es3 = MockEventSource.lastInstance!
    esSimulateError(es3)
    // 第三次错误后不再重连（maxRetries=2）
    await vi.advanceTimersByTimeAsync(500)

    expect(error.value).toContain('已达最大重试次数')
  })

  it('autoReconnect=false 时不重连', async () => {
    const { connect } = useWorkflowStream('wf_001', {
      autoReconnect: false,
      maxRetries: 5,
    })
    connect()
    const initialCount = MockEventSource.instances.length

    const es = MockEventSource.lastInstance!
    esSimulateError(es)

    await vi.advanceTimersByTimeAsync(1000)
    expect(MockEventSource.instances.length).toBe(initialCount)
  })

  it('reset() 清空所有状态', async () => {
    const { connect, reset, events, nodeStatuses, currentStatus, error, isDone } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()

    es.emit('node_started', {
      workflow_run_id: 'wf_001',
      node_id: 'A',
      event_type: 'node_started',
      payload: { job_id: 'j', status: 'running', progress: 0, timestamp: 0 },
      timestamp: 0,
    })

    expect(events.value.length).toBeGreaterThan(0)
    expect(nodeStatuses.value.A).toBe('running')

    reset()

    expect(events.value).toHaveLength(0)
    expect(nodeStatuses.value).toEqual({})
    expect(currentStatus.value).toBeNull()
    expect(error.value).toBeNull()
    expect(isDone.value).toBe(false)
  })

  it('close() 关闭 EventSource 并清理定时器', async () => {
    const { connect, close, isConnected } = useWorkflowStream('wf_001')
    connect()
    const es = MockEventSource.lastInstance!
    es.simulateOpen()
    expect(isConnected.value).toBe(true)

    close()
    expect(isConnected.value).toBe(false)
    expect(es.readyState).toBe(2)
  })

  it('TERMINAL_EVENTS 导出包含 workflow_completed 与 workflow_failed', () => {
    expect(TERMINAL_EVENTS.has('workflow_completed')).toBe(true)
    expect(TERMINAL_EVENTS.has('workflow_failed')).toBe(true)
    expect(TERMINAL_EVENTS.has('node_started')).toBe(false)
  })
})

// useWorkflow 聚合 composable 测试

describe('useWorkflow - 聚合 composable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    MockEventSource.instances = []
    MockEventSource.lastInstance = null
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loadWorkflows 调用 listWorkflows 并更新 workflows 列表', async () => {
    mocks.http.get.mockResolvedValueOnce(
      envelope({
        workflows: [
          { id: 'wf_001', name: 'w1', version: '1.0.0', spec: makeSpec(), status: 'completed', inputs: null, outputs: null, owner_id: null, error: null, metadata: {}, created_at: null, updated_at: null, started_at: null, completed_at: null },
          { id: 'wf_002', name: 'w2', version: '1.0.0', spec: makeSpec(), status: 'failed', inputs: null, outputs: null, owner_id: null, error: null, metadata: {}, created_at: null, updated_at: null, started_at: null, completed_at: null },
        ],
        limit: 20,
        offset: 0,
      }),
    )

    const { loadWorkflows, workflows, loading, totalCount } = useWorkflow()
    expect(loading.value).toBe(false)

    const promise = loadWorkflows()
    expect(loading.value).toBe(true)

    await promise

    expect(loading.value).toBe(false)
    expect(workflows.value).toHaveLength(2)
    expect(workflows.value[0].id).toBe('wf_001')
    expect(totalCount.value).toBe(2) // length + offset = 2 + 0
  })

  it('loadWorkflows 使用分页与筛选参数', async () => {
    mocks.http.get.mockResolvedValueOnce(
      envelope({ workflows: [], limit: 10, offset: 20 }),
    )

    const wf = useWorkflow()
    wf.currentPage.value = 3
    wf.pageSize.value = 10
    wf.statusFilter.value = 'failed'
    wf.ownerFilter.value = 'user_1'

    await wf.loadWorkflows()

    expect(mocks.http.get).toHaveBeenCalledWith(
      buildApiPath(BASE, ''),
      {
        params: {
          limit: 10,
          offset: 20, // (3-1)*10
          status: 'failed',
          owner_id: 'user_1',
        },
      },
    )
  })

  it('loadWorkflows 失败时不抛错，仅 console.warn', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mocks.http.get.mockRejectedValueOnce(new Error('网络错误'))

    const { loadWorkflows, loading, workflows } = useWorkflow()
    await expect(loadWorkflows()).resolves.toBeUndefined()

    expect(loading.value).toBe(false)
    expect(workflows.value).toHaveLength(0)
    expect(warnSpy).toHaveBeenCalled()
    warnSpy.mockRestore()
  })

  it('submitWorkflow 调用 runWorkflow 并自动建立 SSE 订阅', async () => {
    mocks.http.post.mockResolvedValueOnce(
      envelope({ workflow_run_id: 'wf_new', status: 'queued' }),
    )
    mocks.http.get.mockResolvedValueOnce(
      envelope({
        workflow_run_id: 'wf_new',
        spec_name: 'test',
        status: 'running',
        started_at: 0,
        node_statuses: {},
      }),
    )

    const { submitWorkflow, currentRunId } = useWorkflow()
    const body = { spec: makeSpec(), owner_id: 'user_1' }

    const runId = await submitWorkflow(body)

    expect(mocks.http.post).toHaveBeenCalledWith(
      buildApiPath(BASE, '/run'),
      body,
    )
    expect(runId).toBe('wf_new')
    expect(currentRunId.value).toBe('wf_new')
    // SSE 应已建立连接
    expect(MockEventSource.lastInstance).not.toBeNull()
    expect(MockEventSource.lastInstance!.url).toBe(
      buildApiPath(BASE, '/wf_new/stream'),
    )
  })

  it('resumeCurrentWorkflow 调用 resumeWorkflow 并切换 SSE 订阅', async () => {
    mocks.http.post.mockResolvedValueOnce(
      envelope({ workflow_run_id: 'wf_resume', status: 'running' }),
    )
    mocks.http.get.mockResolvedValueOnce(
      envelope({
        workflow_run_id: 'wf_resume',
        spec_name: 'test',
        status: 'running',
        started_at: 0,
        node_statuses: {},
      }),
    )

    const { resumeCurrentWorkflow, currentRunId } = useWorkflow()
    const body = { spec: makeSpec() }

    const newRunId = await resumeCurrentWorkflow('wf_original', body)

    expect(mocks.http.post).toHaveBeenCalledWith(
      buildApiPath(BASE, '/wf_original/resume'),
      body,
    )
    expect(newRunId).toBe('wf_resume')
    expect(currentRunId.value).toBe('wf_resume')
  })

  it('cancelCurrent 调用 cancelWorkflow 并刷新状态', async () => {
    mocks.http.post.mockResolvedValueOnce(
      envelope({ workflow_run_id: 'wf_001', status: 'cancelled' }),
    )
    mocks.http.get.mockResolvedValueOnce(
      envelope({
        workflow_run_id: 'wf_001',
        spec_name: 'test',
        status: 'cancelled',
        started_at: 0,
        node_statuses: {},
      }),
    )

    const { cancelCurrent, currentRunId } = useWorkflow()
    currentRunId.value = 'wf_001'

    await cancelCurrent()

    expect(mocks.http.post).toHaveBeenCalledWith(
      buildApiPath(BASE, '/wf_001/cancel'),
    )
  })

  it('cancelCurrent 无 currentRunId 时不发请求', async () => {
    const { cancelCurrent, currentRunId } = useWorkflow()
    currentRunId.value = ''

    await cancelCurrent()

    expect(mocks.http.post).not.toHaveBeenCalled()
  })

  it('removeWorkflow 调用 deleteWorkflow 并从列表移除', async () => {
    mocks.http.delete.mockResolvedValueOnce(
      envelope({ workflow_run_id: 'wf_001', deleted: true }),
    )

    const { removeWorkflow, workflows } = useWorkflow()
    workflows.value = [
      { id: 'wf_001', name: 'w1', version: '1.0.0', spec: makeSpec(), status: 'completed', inputs: null, outputs: null, owner_id: null, error: null, metadata: {}, created_at: null, updated_at: null, started_at: null, completed_at: null },
      { id: 'wf_002', name: 'w2', version: '1.0.0', spec: makeSpec(), status: 'failed', inputs: null, outputs: null, owner_id: null, error: null, metadata: {}, created_at: null, updated_at: null, started_at: null, completed_at: null },
    ]

    await removeWorkflow('wf_001')

    expect(mocks.http.delete).toHaveBeenCalledWith(
      buildApiPath(BASE, '/wf_001'),
    )
    expect(workflows.value).toHaveLength(1)
    expect(workflows.value.find(w => w.id === 'wf_001')).toBeUndefined()
  })

  it('removeWorkflow 删除当前订阅的 run 时关闭 SSE', async () => {
    mocks.http.delete.mockResolvedValueOnce(
      envelope({ workflow_run_id: 'wf_current', deleted: true }),
    )

    const { removeWorkflow, currentRunId, currentStatus } = useWorkflow()
    currentRunId.value = 'wf_current'

    await removeWorkflow('wf_current')

    expect(currentRunId.value).toBe('')
    expect(currentStatus.value).toBeNull()
  })

  it('refreshCurrentStatus 无 currentRunId 时不发请求', async () => {
    const { refreshCurrentStatus, currentRunId } = useWorkflow()
    currentRunId.value = ''

    await refreshCurrentStatus()

    expect(mocks.http.get).not.toHaveBeenCalled()
  })

  it('refreshCurrentStatus 失败时 console.warn 不抛错', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mocks.http.get.mockRejectedValueOnce(new Error('网络错误'))

    const { refreshCurrentStatus, currentRunId, currentLoading } = useWorkflow()
    currentRunId.value = 'wf_001'

    await expect(refreshCurrentStatus()).resolves.toBeUndefined()
    expect(currentLoading.value).toBe(false)
    expect(warnSpy).toHaveBeenCalled()
    warnSpy.mockRestore()
  })

  it('selectWorkflow 对终态工作流不建立 SSE 订阅', async () => {
    mocks.http.get.mockResolvedValueOnce(
      envelope({
        workflow_run_id: 'wf_done',
        spec_name: 'test',
        status: 'completed',
        started_at: 0,
        node_statuses: {},
      }),
    )

    const { selectWorkflow, currentRunId, currentStatus } = useWorkflow()
    await selectWorkflow('wf_done')

    expect(currentRunId.value).toBe('wf_done')
    expect(currentStatus.value?.status).toBe('completed')
    // 已完成的工作流不应建立 SSE
    expect(MockEventSource.lastInstance).toBeNull()
  })

  it('selectWorkflow 对运行中工作流建立 SSE 订阅', async () => {
    mocks.http.get.mockResolvedValueOnce(
      envelope({
        workflow_run_id: 'wf_running',
        spec_name: 'test',
        status: 'running',
        started_at: 0,
        node_statuses: {},
      }),
    )

    const { selectWorkflow } = useWorkflow()
    await selectWorkflow('wf_running')

    expect(MockEventSource.lastInstance).not.toBeNull()
    expect(MockEventSource.lastInstance!.url).toBe(
      buildApiPath(BASE, '/wf_running/stream'),
    )
  })
})

// 辅助函数

/** 触发 MockEventSource 的 onerror 回调 */
function esSimulateError(es: MockEventSource): void {
  es.simulateError()
}
