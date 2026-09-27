// parameterOptimizer API 客户端测试（Phase D 前端）
import { describe, it, expect, vi, beforeEach } from 'vitest'

import http from '@/utils/http'
import {
  recommendParameters,
  evaluateResult,
  compareResults,
  listBaselines,
} from '@/api/parameterOptimizer'

vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const mockedGet = vi.mocked(http.get)
const mockedPost = vi.mocked(http.post)

describe('parameterOptimizer API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('recommendParameters posts and unwraps recommendation', async () => {
    mockedPost.mockResolvedValue({
      data: {
        recommendation: {
          depth_of_cut_mm: 2.0,
          feed_mm_per_rev: 0.2,
          spindle_rpm: 8000,
          cutting_speed_m_min: 300,
          strategy: 'L0_baseline',
          confidence: 0.5,
          basis: [],
          clamped: false,
        },
      },
    } as never)

    const rec = await recommendParameters({ material: 'AL6061', machining_type: 'milling' })
    expect(mockedPost).toHaveBeenCalledWith(
      expect.stringContaining('/optimizer/recommend'),
      expect.objectContaining({ material: 'AL6061' }),
    )
    expect(rec.strategy).toBe('L0_baseline')
    expect(rec.depth_of_cut_mm).toBe(2.0)
  })

  it('evaluateResult returns score', async () => {
    mockedPost.mockResolvedValue({
      data: { score: 0.9, result_ok: true },
    } as never)

    const result = await evaluateResult({ cycle_time_s: 100, result: 'ok' })
    expect(result.score).toBe(0.9)
    expect(result.result_ok).toBe(true)
  })

  it('compareResults posts both groups', async () => {
    mockedPost.mockResolvedValue({
      data: { better: 'a', improvement_pct: 30.0 },
    } as never)

    const result = await compareResults(
      [{ cycle_time_s: 80 }],
      [{ cycle_time_s: 120 }],
    )
    expect(mockedPost).toHaveBeenCalledWith(
      expect.stringContaining('/optimizer/compare'),
      expect.objectContaining({
        a_results: [{ cycle_time_s: 80 }],
        b_results: [{ cycle_time_s: 120 }],
      }),
    )
    expect(result.better).toBe('a')
  })

  it('listBaselines passes filters', async () => {
    mockedGet.mockResolvedValue({
      data: { entries: [{ material: 'AL6061' }], total: 1 },
    } as never)

    const result = await listBaselines({ material: 'AL6061' })
    expect(mockedGet).toHaveBeenCalledWith(
      expect.stringContaining('/optimizer/baselines'),
      expect.objectContaining({ params: { material: 'AL6061' } }),
    )
    expect(result.total).toBe(1)
  })
})
