import { describe, it, expect, vi, beforeEach } from 'vitest'
import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'
import {
  query,
  explainPrediction,
  getStats,
  checkHealth,
} from '@/api/processUnderstanding'
import type {
  QueryRequest,
  QueryResponse,
  ExplainRequest,
  ProcessUnderstandingStats,
  HealthStatus,
} from '@/api/processUnderstanding'

vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

const BASE = API_CONFIG.PROCESS_UNDERSTANDING

function makeQueryResponse(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    task_type: 'chatter_prediction',
    intent: 'predict',
    entities: { tool: 'end_mill' },
    response: '建议降低主轴转速',
    confidence: 0.92,
    sources: ['kb-1', 'kb-2'],
    actions: ['adjust_speed'],
    details: { ref: 'doc-1' },
    ...overrides,
  }
}

describe('processUnderstanding API', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  describe('query', () => {
    it('字符串参数自动包装为 { query }', async () => {
      const response = makeQueryResponse()
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: response } })

      const result = await query('如何减小颤振?')

      expect(http.post).toHaveBeenCalledWith(`${BASE}/query`, { query: '如何减小颤振?' })
      expect(result).toEqual(response)
    })

    it('QueryRequest 对象参数直接传入', async () => {
      const response = makeQueryResponse()
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: response } })

      const payload: QueryRequest = {
        query: '刀具磨损评估',
        context: { machine: 'CNC-1' },
      }
      const result = await query(payload)

      expect(http.post).toHaveBeenCalledWith(`${BASE}/query`, payload)
      expect(result).toEqual(response)
    })

    it('回退到 resp.data', async () => {
      const response = makeQueryResponse()
      vi.mocked(http.post).mockResolvedValueOnce({ data: response })

      const result = await query('test')

      expect(result).toEqual(response)
    })

    it('空字符串 query 也能调用', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: makeQueryResponse({ response: '' }) },
      })

      await query('')

      expect(http.post).toHaveBeenCalledWith(`${BASE}/query`, { query: '' })
    })

    it('context 字段为空对象时也正确传递', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: makeQueryResponse() },
      })

      await query({ query: 'q', context: {} })

      expect(http.post).toHaveBeenCalledWith(`${BASE}/query`, {
        query: 'q',
        context: {},
      })
    })

    it('网络错误时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('500'))

      await expect(query('test')).rejects.toThrow('500')
    })

    it('400 错误时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('Bad Request'))

      await expect(query('test')).rejects.toThrow('Bad Request')
    })
  })

  describe('explainPrediction', () => {
    it('成功获取预测解释（完整字段）', async () => {
      const response = makeQueryResponse({ task_type: 'prediction_explain' })
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: response } })

      const req: ExplainRequest = {
        force_pred: 100,
        force_conf: 0.85,
        wear_pred: 0.3,
        wear_conf: 0.7,
        visual_status: 'normal',
        anomaly_prob: 0.05,
        context: '粗加工工序',
      }
      const result = await explainPrediction(req)

      expect(http.post).toHaveBeenCalledWith(`${BASE}/explain`, req)
      expect(result).toEqual(response)
    })

    it('仅必填字段为空对象时也能调用', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: makeQueryResponse() },
      })

      await explainPrediction({})

      expect(http.post).toHaveBeenCalledWith(`${BASE}/explain`, {})
    })

    it('回退到 resp.data', async () => {
      const response = makeQueryResponse()
      vi.mocked(http.post).mockResolvedValueOnce({ data: response })

      const result = await explainPrediction({ force_pred: 50 })

      expect(result).toEqual(response)
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('server error'))

      await expect(explainPrediction({})).rejects.toThrow('server error')
    })

    it('仅传 context 字段时正常调用', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: makeQueryResponse() },
      })

      await explainPrediction({ context: 'some context' })

      expect(http.post).toHaveBeenCalledWith(`${BASE}/explain`, {
        context: 'some context',
      })
    })
  })

  describe('getStats', () => {
    it('返回模块统计信息', async () => {
      const stats: ProcessUnderstandingStats = {
        total_requests: 100,
        avg_latency_ms: 250,
        error_count: 5,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: stats } })

      const result = await getStats()

      expect(http.get).toHaveBeenCalledWith(`${BASE}/stats`)
      expect(result).toEqual(stats)
      expect(result.total_requests).toBe(100)
      expect(result.avg_latency_ms).toBe(250)
    })

    it('回退到 resp.data', async () => {
      const stats: ProcessUnderstandingStats = {
        total_requests: 0,
        avg_latency_ms: 0,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: stats })

      const result = await getStats()

      expect(result).toEqual(stats)
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.get).mockRejectedValueOnce(new Error('503'))

      await expect(getStats()).rejects.toThrow('503')
    })
  })

  describe('checkHealth', () => {
    it('返回健康检查响应（健康状态）', async () => {
      const health: HealthStatus = {
        status: 'healthy',
        total_requests: 500,
        avg_latency_ms: 100,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: health } })

      const result = await checkHealth()

      expect(http.get).toHaveBeenCalledWith(`${BASE}/health`)
      expect(result.status).toBe('healthy')
      expect(result.total_requests).toBe(500)
      expect(result.avg_latency_ms).toBe(100)
    })

    it('返回降级状态时也能解析', async () => {
      const health: HealthStatus = {
        status: 'degraded',
        total_requests: 0,
        avg_latency_ms: 0,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: health } })

      const result = await checkHealth()

      expect(result.status).toBe('degraded')
    })

    it('回退到 resp.data', async () => {
      const health: HealthStatus = {
        status: 'healthy',
        total_requests: 1,
        avg_latency_ms: 1,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: health })

      const result = await checkHealth()

      expect(result).toEqual(health)
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.get).mockRejectedValueOnce(new Error('connection refused'))

      await expect(checkHealth()).rejects.toThrow('connection refused')
    })
  })
})
