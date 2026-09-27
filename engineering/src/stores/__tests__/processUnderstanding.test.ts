import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// mock 工艺理解 API 模块
const queryMock = vi.hoisted(() => vi.fn())
const checkHealthMock = vi.hoisted(() => vi.fn())
const getStatsMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/processUnderstanding', () => ({
  query: (...args: unknown[]) => queryMock(...args),
  checkHealth: (...args: unknown[]) => checkHealthMock(...args),
  getStats: (...args: unknown[]) => getStatsMock(...args),
  explainPrediction: vi.fn(),
}))

// mock 错误处理
vi.mock('@/utils/error-handler', () => ({
  extractErrorMessage: vi.fn((err: unknown, fallback = '操作失败') => {
    if (!err) return fallback
    // 与生产实现对齐：字符串直接返回
    if (typeof err === 'string') return err
    const e = err as Record<string, unknown>
    const resp = e.response as Record<string, unknown> | undefined
    const data = resp?.data as Record<string, unknown> | undefined
    if (data && typeof data.message === 'string') return data.message
    if (typeof e.message === 'string') return e.message
    return fallback
  }),
}))

import { useProcessUnderstandingStore } from '@/stores/processUnderstanding'

describe('useProcessUnderstandingStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    queryMock.mockReset()
    checkHealthMock.mockReset()
    getStatsMock.mockReset()
  })

  describe('initial state', () => {
    it('消息列表初始为空', () => {
      const store = useProcessUnderstandingStore()
      expect(store.messages).toEqual([])
    })

    it('loading 初始为 false', () => {
      const store = useProcessUnderstandingStore()
      expect(store.loading).toBe(false)
    })

    it('health 初始为 null', () => {
      const store = useProcessUnderstandingStore()
      expect(store.health).toBeNull()
    })

    it('stats 初始为 null', () => {
      const store = useProcessUnderstandingStore()
      expect(store.stats).toBeNull()
    })

    it('lastError 初始为 null', () => {
      const store = useProcessUnderstandingStore()
      expect(store.lastError).toBeNull()
    })
  })

  describe('computed', () => {
    it('messageCount 返回消息数量', () => {
      const store = useProcessUnderstandingStore()
      store.$patch({ messages: [{ id: '1', role: 'user', content: 'a', timestamp: 1 }] })
      expect(store.messageCount).toBe(1)
    })

    it('health 非 healthy 时 isHealthy 为 false', () => {
      const store = useProcessUnderstandingStore()
      store.$patch({ health: { status: 'unhealthy', total_requests: 0, avg_latency_ms: 0 } })
      expect(store.isHealthy).toBe(false)
    })

    it('health 为 healthy 时 isHealthy 为 true', () => {
      const store = useProcessUnderstandingStore()
      store.$patch({ health: { status: 'healthy', total_requests: 10, avg_latency_ms: 100 } })
      expect(store.isHealthy).toBe(true)
    })

    it('无消息时 hasHistory 为 false', () => {
      const store = useProcessUnderstandingStore()
      expect(store.hasHistory).toBe(false)
    })

    it('有消息时 hasHistory 为 true', () => {
      const store = useProcessUnderstandingStore()
      store.$patch({ messages: [{ id: '1', role: 'user', content: 'a', timestamp: 1 }] })
      expect(store.hasHistory).toBe(true)
    })
  })

  describe('sendQuery', () => {
    it('空输入时返回 null 且不发送请求', async () => {
      const store = useProcessUnderstandingStore()
      const result = await store.sendQuery('   ')
      expect(result).toBeNull()
      expect(queryMock).not.toHaveBeenCalled()
    })

    it('loading 中时返回 null 且不发送请求', async () => {
      const store = useProcessUnderstandingStore()
      store.$patch({ loading: true })
      const result = await store.sendQuery('hello')
      expect(result).toBeNull()
      expect(queryMock).not.toHaveBeenCalled()
    })

    it('查询成功时添加用户和助手消息', async () => {
      queryMock.mockResolvedValue({
        task_type: 'chatter',
        intent: 'predict',
        entities: {},
        response: '切削速度建议 200m/min',
        confidence: 0.9,
        sources: [],
        actions: [],
        details: {},
      })
      const store = useProcessUnderstandingStore()
      const result = await store.sendQuery('如何选择切削参数？')
      expect(result).not.toBeNull()
      expect(result!.response).toBe('切削速度建议 200m/min')
      expect(store.messages.length).toBe(2)
      expect(store.messages[0].role).toBe('user')
      expect(store.messages[0].content).toBe('如何选择切削参数？')
      expect(store.messages[1].role).toBe('assistant')
      expect(store.messages[1].content).toBe('切削速度建议 200m/min')
      expect(store.loading).toBe(false)
      expect(store.lastError).toBeNull()
    })

    it('查询成功但 response 为空时使用占位文本', async () => {
      queryMock.mockResolvedValue({
        task_type: '',
        intent: '',
        entities: {},
        response: '',
        confidence: 0,
        sources: [],
        actions: [],
        details: {},
      })
      const store = useProcessUnderstandingStore()
      await store.sendQuery('hi')
      expect(store.messages[1].content).toBe('(无回复内容)')
    })

    it('查询失败时添加错误消息并记录 lastError', async () => {
      queryMock.mockRejectedValue({
        response: { data: { message: '服务超时' } },
      })
      const store = useProcessUnderstandingStore()
      const result = await store.sendQuery('test')
      expect(result).toBeNull()
      expect(store.lastError).toBe('服务超时')
      expect(store.messages.length).toBe(2)
      expect(store.messages[1].role).toBe('assistant')
      expect(store.messages[1].content).toContain('服务超时')
      expect(store.loading).toBe(false)
    })

    it('查询失败且无明确 message 时降级处理', async () => {
      queryMock.mockRejectedValue(new Error('network down'))
      const store = useProcessUnderstandingStore()
      await store.sendQuery('test')
      expect(store.lastError).toBe('network down')
    })

    it('查询失败为非 Error 对象时降级', async () => {
      queryMock.mockRejectedValue('string error')
      const store = useProcessUnderstandingStore()
      await store.sendQuery('test')
      // extractErrorMessage 对字符串直接返回
      expect(store.lastError).toBe('string error')
    })
  })

  describe('refreshHealth', () => {
    it('成功时更新 health 状态', async () => {
      checkHealthMock.mockResolvedValue({
        status: 'healthy',
        total_requests: 100,
        avg_latency_ms: 50,
      })
      const store = useProcessUnderstandingStore()
      await store.refreshHealth()
      expect(store.health).toMatchObject({ status: 'healthy', total_requests: 100 })
      expect(store.lastError).toBeNull()
    })

    it('失败时 health 降级为 unhealthy 并记录 lastError', async () => {
      checkHealthMock.mockRejectedValue(new Error('connection refused'))
      const store = useProcessUnderstandingStore()
      await store.refreshHealth()
      expect(store.health).toMatchObject({ status: 'unhealthy', total_requests: 0 })
      expect(store.lastError).toBe('connection refused')
    })
  })

  describe('refreshStats', () => {
    it('成功时更新 stats', async () => {
      getStatsMock.mockResolvedValue({ total_requests: 50, avg_latency_ms: 200 })
      const store = useProcessUnderstandingStore()
      await store.refreshStats()
      expect(store.stats).toMatchObject({ total_requests: 50, avg_latency_ms: 200 })
    })

    it('失败时记录 lastError', async () => {
      getStatsMock.mockRejectedValue(new Error('stats error'))
      const store = useProcessUnderstandingStore()
      await store.refreshStats()
      expect(store.lastError).toBe('stats error')
    })
  })

  describe('clearHistory', () => {
    it('清空消息列表和 lastError', () => {
      const store = useProcessUnderstandingStore()
      store.$patch({
        messages: [{ id: '1', role: 'user', content: 'a', timestamp: 1 }],
        lastError: 'some error',
      })
      store.clearHistory()
      expect(store.messages).toEqual([])
      expect(store.lastError).toBeNull()
    })
  })

  describe('genId', () => {
    it('生成以 msg_ 开头的唯一 ID', () => {
      const store = useProcessUnderstandingStore()
      // genId 未对外暴露，通过 sendQuery 间接验证
      queryMock.mockResolvedValue({
        task_type: '', intent: '', entities: {}, response: 'ok',
        confidence: 0, sources: [], actions: [], details: {},
      })
      store.sendQuery('test')
      expect(store.messages[0].id).toMatch(/^msg_/)
    })
  })
})
