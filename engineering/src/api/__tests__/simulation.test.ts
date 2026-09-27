import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import type { SimulationResult, SimulationStatus } from '@/types'
import {
  getSimulationResult,
  clearSimulationCache,
  getCacheStats,
} from '@/api/simulation'

vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

function makeStatus(
  overrides: Partial<SimulationStatus> = {},
): SimulationStatus {
  return {
    task_id: 'task-1',
    status: 'completed',
    progress: 100,
    result: makeResult(),
    ...overrides,
  }
}

function makeResult(overrides: Partial<SimulationResult> = {}): SimulationResult {
  return {
    task_id: 'task-1',
    collision_detected: false,
    simulation_result: {
      workpiece_stl_path: '/tmp/work.stl',
      voxel_count: 1000,
      removed_voxel_count: 200,
      voxel_size: 0.5,
      original_bbox: null,
    },
    collision_details: {
      timestamp: '2025-01-01T00:00:00Z',
      positions: [],
      segment_indices: [],
      severity: 'none',
      count: 0,
    },
    duration_seconds: 1.5,
    voxel_count: 1000,
    removed_voxel_count: 200,
    voxel_size: 0.5,
    toolpath_segment_count: 10,
    ...overrides,
  }
}

describe('simulation API', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.useRealTimers()
    clearSimulationCache()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('getSimulationResult', () => {
    it('成功获取仿真结果（无碰撞）', async () => {
      const status = makeStatus()
      const result = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      const data = await getSimulationResult('task-1')

      expect(http.get).toHaveBeenCalledTimes(2)
      expect(http.get).toHaveBeenNthCalledWith(
        1,
        buildApiPath(API_CONFIG.SIMULATION, '/status/task-1'),
      )
      expect(http.get).toHaveBeenNthCalledWith(
        2,
        buildApiPath(API_CONFIG.SIMULATION, '/result/task-1'),
      )
      expect(data.task_id).toBe('task-1')
      expect(data.force_data).toEqual([])
      expect(data.temperature_data).toEqual([])
      expect(data.vibration_data).toEqual([])
      expect(typeof data.timestamp).toBe('number')
    })

    it('有碰撞（warning 级别）时映射为力矢量（magnitude=500）', async () => {
      const result = makeResult({
        collision_detected: true,
        collision_details: {
          timestamp: '2025-01-01T00:00:00Z',
          positions: [
            [1, 2, 3],
            [4, 5, 6],
          ],
          segment_indices: [0, 1],
          severity: 'warning',
          count: 2,
        },
      })
      const status = makeStatus({ result })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      const data = await getSimulationResult('task-warn')

      expect(data.force_data!).toHaveLength(2)
      expect(data.force_data![0].position).toEqual([1, 2, 3])
      expect(data.force_data![0].direction).toEqual([0, 0, -1])
      expect(data.force_data![0].magnitude).toBe(500)
      expect(data.force_data![1].position).toEqual([4, 5, 6])
      expect(data.force_data![1].magnitude).toBe(500)
    })

    it('碰撞严重级别 critical 时 magnitude=1000', async () => {
      const result = makeResult({
        collision_details: {
          timestamp: '2025-01-01T00:00:00Z',
          positions: [[0, 0, 0]],
          segment_indices: [0],
          severity: 'critical',
          count: 1,
        },
      })
      const status = makeStatus({ result })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      const data = await getSimulationResult('task-crit')

      expect(data.force_data!).toHaveLength(1)
      expect(data.force_data![0].magnitude).toBe(1000)
    })

    it('task_id 与传入参数一致（来自 result）', async () => {
      const result = makeResult({ task_id: 'custom-id' })
      const status = makeStatus({ task_id: 'custom-id', result })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      const data = await getSimulationResult('custom-id')

      expect(data.task_id).toBe('custom-id')
    })

    it('缓存命中时不发起 HTTP 请求', async () => {
      const status = makeStatus()
      const result = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('task-cache')

      // 第二次调用，应命中缓存，不发起新请求
      vi.mocked(http.get).mockClear()
      const data = await getSimulationResult('task-cache')

      expect(http.get).not.toHaveBeenCalled()
      expect(data.task_id).toBe('task-1')
    })

    it('forceRefresh=true 时跳过缓存强制刷新', async () => {
      const status = makeStatus()
      const result = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('task-refresh')

      // 强制刷新，应重新发起请求
      const status2 = makeStatus()
      const result2 = makeResult({ voxel_count: 2000 })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status2 } })
        .mockResolvedValueOnce({ data: { data: result2 } })

      await getSimulationResult('task-refresh', true)

      expect(http.get).toHaveBeenCalledTimes(4)
    })

    it('缓存过期后自动重新请求', async () => {
      vi.useFakeTimers()
      const status = makeStatus()
      const result = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('task-expire')

      // 推进时间超过 5 分钟（缓存 maxAge）
      vi.advanceTimersByTime(6 * 60 * 1000)

      const status2 = makeStatus()
      const result2 = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status2 } })
        .mockResolvedValueOnce({ data: { data: result2 } })

      await getSimulationResult('task-expire')

      // 应该发起了 4 次请求（两次完整调用）
      expect(http.get).toHaveBeenCalledTimes(4)
    })

    it('status.status 不为 completed 时抛出错误', async () => {
      const status: SimulationStatus = {
        task_id: 'task-fail',
        status: 'failed',
        progress: 50,
        result: null,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: status } })

      await expect(getSimulationResult('task-fail')).rejects.toThrow('获取仿真结果失败')
    })

    it('status.status 为 running 时抛出错误', async () => {
      const status: SimulationStatus = {
        task_id: 'task-running',
        status: 'running',
        progress: 50,
        result: null,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: status } })

      await expect(getSimulationResult('task-running')).rejects.toThrow('获取仿真结果失败')
    })

    it('status.status 为 pending 时抛出错误', async () => {
      const status: SimulationStatus = {
        task_id: 'task-pending',
        status: 'pending',
        progress: 0,
        result: null,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: status } })

      await expect(getSimulationResult('task-pending')).rejects.toThrow('获取仿真结果失败')
    })

    it('status.result 为 null 时抛出错误', async () => {
      const status: SimulationStatus = {
        task_id: 'task-no-result',
        status: 'completed',
        progress: 100,
        result: null,
      }
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: status } })

      await expect(getSimulationResult('task-no-result')).rejects.toThrow('获取仿真结果失败')
    })

    it('status 请求失败时抛出"获取仿真结果失败"', async () => {
      vi.mocked(http.get).mockRejectedValueOnce(new Error('network error'))

      await expect(getSimulationResult('task-err')).rejects.toThrow('获取仿真结果失败')
    })

    it('result 请求失败时抛出"获取仿真结果失败"', async () => {
      const status = makeStatus()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockRejectedValueOnce(new Error('result fetch failed'))

      await expect(getSimulationResult('task-result-err')).rejects.toThrow('获取仿真结果失败')
    })

    it('缓存失败后再次调用应重新发起请求', async () => {
      // 第一次失败
      vi.mocked(http.get).mockRejectedValueOnce(new Error('network error'))
      await expect(getSimulationResult('task-retry')).rejects.toThrow('获取仿真结果失败')

      // 第二次成功
      const status = makeStatus()
      const result = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      const data = await getSimulationResult('task-retry')
      expect(data.task_id).toBe('task-1')
    })

    it('特殊字符 taskId 也能正确处理', async () => {
      const status = makeStatus({ task_id: 'task/with-special' })
      const result = makeResult({ task_id: 'task/with-special' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('task/with-special')

      expect(http.get).toHaveBeenNthCalledWith(
        1,
        buildApiPath(API_CONFIG.SIMULATION, '/status/task/with-special'),
      )
    })
  })

  describe('clearSimulationCache', () => {
    it('清除指定 taskId 的缓存', async () => {
      const status = makeStatus({ task_id: 't1' })
      const result = makeResult({ task_id: 't1' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('t1')
      expect(getCacheStats().size).toBe(1)

      clearSimulationCache('t1')
      expect(getCacheStats().size).toBe(0)
    })

    it('不传参数清除所有缓存', async () => {
      // 添加两个缓存
      for (const id of ['t1', 't2']) {
        const status = makeStatus({ task_id: id })
        const result = makeResult({ task_id: id })
        vi.mocked(http.get)
          .mockResolvedValueOnce({ data: { data: status } })
          .mockResolvedValueOnce({ data: { data: result } })
        await getSimulationResult(id)
      }
      expect(getCacheStats().size).toBe(2)

      clearSimulationCache()
      expect(getCacheStats().size).toBe(0)
    })

    it('清除不存在的 taskId 不报错', () => {
      expect(() => clearSimulationCache('not-exist')).not.toThrow()
      expect(getCacheStats().size).toBe(0)
    })

    it('清除后下次调用会重新发起请求', async () => {
      const status = makeStatus()
      const result = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('task-clear')
      expect(http.get).toHaveBeenCalledTimes(2)

      clearSimulationCache('task-clear')

      const status2 = makeStatus()
      const result2 = makeResult()
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status2 } })
        .mockResolvedValueOnce({ data: { data: result2 } })

      await getSimulationResult('task-clear')
      expect(http.get).toHaveBeenCalledTimes(4)
    })
  })

  describe('getCacheStats', () => {
    it('初始状态 size 为 0', () => {
      const stats = getCacheStats()
      expect(stats.size).toBe(0)
      expect(stats.maxAge).toBe(5 * 60 * 1000)
      expect(stats.maxSize).toBe(50)
    })

    it('添加缓存后 size 增加', async () => {
      const status = makeStatus({ task_id: 't1' })
      const result = makeResult({ task_id: 't1' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('t1')

      expect(getCacheStats().size).toBe(1)
    })

    it('maxAge 为 5 分钟（毫秒）', () => {
      expect(getCacheStats().maxAge).toBe(300000)
    })

    it('maxSize 为 50', () => {
      expect(getCacheStats().maxSize).toBe(50)
    })
  })

  describe('缓存清理逻辑', () => {
    it('当缓存超过 maxSize 时删除最旧条目', async () => {
      // 填充 50 个缓存
      for (let i = 0; i < 50; i++) {
        const id = `task-${i}`
        const status = makeStatus({ task_id: id })
        const result = makeResult({ task_id: id })
        vi.mocked(http.get)
          .mockResolvedValueOnce({ data: { data: status } })
          .mockResolvedValueOnce({ data: { data: result } })
        await getSimulationResult(id)
      }
      expect(getCacheStats().size).toBe(50)

      // 添加第 51 个，应该触发清理，size 仍为 50
      const status = makeStatus({ task_id: 'task-50' })
      const result = makeResult({ task_id: 'task-50' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('task-50')

      expect(getCacheStats().size).toBe(50)

      // task-0 应该已被驱逐
      vi.mocked(http.get).mockClear()
      const status2 = makeStatus({ task_id: 'task-0' })
      const result2 = makeResult({ task_id: 'task-0' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status2 } })
        .mockResolvedValueOnce({ data: { data: result2 } })

      await getSimulationResult('task-0')
      // 因为 task-0 已被驱逐，应该重新发起请求
      expect(http.get).toHaveBeenCalledTimes(2)
    })

    it('过期缓存条目在添加新条目时被清理', async () => {
      vi.useFakeTimers()

      const status = makeStatus({ task_id: 'old' })
      const result = makeResult({ task_id: 'old' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status } })
        .mockResolvedValueOnce({ data: { data: result } })

      await getSimulationResult('old')
      expect(getCacheStats().size).toBe(1)

      // 推进时间超过 5 分钟
      vi.advanceTimersByTime(6 * 60 * 1000)

      // 添加新条目，应触发清理过期条目
      const status2 = makeStatus({ task_id: 'new' })
      const result2 = makeResult({ task_id: 'new' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status2 } })
        .mockResolvedValueOnce({ data: { data: result2 } })

      await getSimulationResult('new')

      // 'old' 应已被过期清理
      expect(getCacheStats().size).toBe(1)

      // 'old' 应重新发起请求
      vi.mocked(http.get).mockClear()
      const status3 = makeStatus({ task_id: 'old' })
      const result3 = makeResult({ task_id: 'old' })
      vi.mocked(http.get)
        .mockResolvedValueOnce({ data: { data: status3 } })
        .mockResolvedValueOnce({ data: { data: result3 } })

      await getSimulationResult('old')
      expect(http.get).toHaveBeenCalledTimes(2)
    })
  })
})
