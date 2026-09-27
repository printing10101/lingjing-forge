import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTasksStore } from '@/stores/tasks'

// mock http 客户端
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

// 保留 error-handler 真实逻辑以便测试 extractErrorMessage 行为
vi.mock('@/utils/error-handler', async () => {
  const actual = await vi.importActual<typeof import('@/utils/error-handler')>('@/utils/error-handler')
  return { ...actual }
})

import http from '@/utils/http'

describe('useTasksStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('初始 tasks 为空数组', () => {
      const store = useTasksStore()
      expect(store.tasks).toEqual([])
    })

    it('初始 currentTask 为 null', () => {
      const store = useTasksStore()
      expect(store.currentTask).toBeNull()
    })

    it('初始 loading 为 false', () => {
      const store = useTasksStore()
      expect(store.loading).toBe(false)
    })

    it('初始 error 为 null', () => {
      const store = useTasksStore()
      expect(store.error).toBeNull()
    })

    it('初始 stats 包含默认值', () => {
      const store = useTasksStore()
      expect(store.stats).toEqual({
        total_tasks: 0,
        active_tasks: 0,
        queued_tasks: 0,
        completed_tasks: 0,
        failed_tasks: 0,
        max_concurrent: 3,
        available_slots: 3,
      })
    })
  })

  describe('computed', () => {
    it('activeTasks 过滤出 running 和 queued 状态', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [
          { job_id: 'j1', status: 'running' },
          { job_id: 'j2', status: 'queued' },
          { job_id: 'j3', status: 'completed' },
          { job_id: 'j4', status: 'failed' },
        ] as never,
      })
      expect(store.activeTasks).toHaveLength(2)
      expect(store.activeTasks[0].job_id).toBe('j1')
      expect(store.activeTasks[1].job_id).toBe('j2')
    })

    it('completedTasks 过滤出 completed 状态', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [
          { job_id: 'j1', status: 'completed' },
          { job_id: 'j2', status: 'running' },
          { job_id: 'j3', status: 'completed' },
        ] as never,
      })
      expect(store.completedTasks).toHaveLength(2)
      expect(store.completedTasks[0].job_id).toBe('j1')
    })

    it('failedTasks 过滤出 failed 状态', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [
          { job_id: 'j1', status: 'failed' },
          { job_id: 'j2', status: 'running' },
        ] as never,
      })
      expect(store.failedTasks).toHaveLength(1)
      expect(store.failedTasks[0].job_id).toBe('j1')
    })

    it('cancelledTasks 过滤出 cancelled 状态', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [
          { job_id: 'j1', status: 'cancelled' },
          { job_id: 'j2', status: 'completed' },
        ] as never,
      })
      expect(store.cancelledTasks).toHaveLength(1)
      expect(store.cancelledTasks[0].job_id).toBe('j1')
    })
  })

  describe('fetchTasks', () => {
    it('成功获取任务列表', async () => {
      const store = useTasksStore()
      const jobs = [
        { job_id: 'j1', status: 'running' },
        { job_id: 'j2', status: 'completed' },
      ]
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { jobs } },
      })

      await store.fetchTasks()

      expect(http.get).toHaveBeenCalled()
      expect(store.tasks).toEqual(jobs)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('使用默认 limit 和 offset 参数', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { jobs: [] } },
      })

      await store.fetchTasks()

      expect(http.get).toHaveBeenCalledTimes(1)
      const callArgs = (http.get as ReturnType<typeof vi.fn>).mock.calls[0]
      const params = callArgs[1].params
      expect(params.limit).toBe('50')
      expect(params.offset).toBe('0')
    })

    it('使用传入的查询参数', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { jobs: [] } },
      })

      await store.fetchTasks({
        task_type: 'chatter',
        status: 'running',
        owner_id: 'u1',
        limit: 10,
        offset: 5,
      })

      const callArgs = (http.get as ReturnType<typeof vi.fn>).mock.calls[0]
      const params = callArgs[1].params
      expect(params.task_type).toBe('chatter')
      expect(params.status).toBe('running')
      expect(params.owner_id).toBe('u1')
      expect(params.limit).toBe('10')
      expect(params.offset).toBe('5')
    })

    it('jobs 为空时设置空数组', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: {} },
      })

      await store.fetchTasks()

      expect(store.tasks).toEqual([])
    })

    it('非零 code 时设置错误信息', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '权限不足' },
      })

      await store.fetchTasks()

      expect(store.error).toBe('权限不足')
      expect(store.tasks).toEqual([])
    })

    it('非零 code 且无 message 时使用默认错误信息', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })

      await store.fetchTasks()

      expect(store.error).toBe('Failed to fetch tasks')
    })

    it('网络错误时设置错误信息', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      await store.fetchTasks()

      expect(store.error).toBeTruthy()
      expect(store.loading).toBe(false)
    })

    it('请求过程中 loading 为 true', async () => {
      const store = useTasksStore()
      let resolveFn: (val: unknown) => void
      ;(http.get as ReturnType<typeof vi.fn>).mockReturnValue(
        new Promise((resolve) => {
          resolveFn = resolve
        }),
      )

      const promise = store.fetchTasks()
      expect(store.loading).toBe(true)

      resolveFn!({ data: { code: 0, data: { jobs: [] } } })
      await promise

      expect(store.loading).toBe(false)
    })
  })

  describe('fetchTask', () => {
    it('成功获取任务详情', async () => {
      const store = useTasksStore()
      const taskData = { job_id: 'j1', status: 'running', progress: 50 }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: taskData },
      })

      const result = await store.fetchTask('j1')

      expect(result).toEqual(taskData)
      expect(store.currentTask).toEqual(taskData)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('非零 code 时返回 null 并设置错误', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: 'Task not found' },
      })

      const result = await store.fetchTask('j1')

      expect(result).toBeNull()
      expect(store.error).toBe('Task not found')
    })

    it('非零 code 无 message 时使用默认错误', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })

      const result = await store.fetchTask('j1')

      expect(result).toBeNull()
      expect(store.error).toBe('Task not found')
    })

    it('网络错误时返回 null 并设置错误', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      const result = await store.fetchTask('j1')

      expect(result).toBeNull()
      expect(store.error).toBeTruthy()
      expect(store.loading).toBe(false)
    })
  })

  describe('fetchTaskProgress', () => {
    it('成功更新当前任务的进度', async () => {
      const store = useTasksStore()
      store.$patch({
        currentTask: { job_id: 'j1', status: 'running', progress: 30 } as never,
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            progress_db: 80,
            progress_redis: 75,
            message: '正在处理',
            metrics: { cpu: 50 },
          },
        },
      })

      const result = await store.fetchTaskProgress('j1')

      expect(store.currentTask!.progress).toBe(80)
      expect(store.currentTask!.progress_redis).toEqual({
        progress: 75,
        message: '正在处理',
        metrics: { cpu: 50 },
      })
      expect(result).toBeTruthy()
    })

    it('currentTask 为 null 时不报错', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { progress_db: 80 } },
      })

      const result = await store.fetchTaskProgress('j1')

      expect(store.currentTask).toBeNull()
      expect(result).toBeTruthy()
    })

    it('非零 code 时返回 json 但不更新 currentTask', async () => {
      const store = useTasksStore()
      store.$patch({
        currentTask: { job_id: 'j1', status: 'running', progress: 30 } as never,
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: 'failed' },
      })

      const result = await store.fetchTaskProgress('j1')

      expect(store.currentTask!.progress).toBe(30)
      expect(result).toBeTruthy()
    })

    it('progress_db 缺失时保留原进度', async () => {
      const store = useTasksStore()
      store.$patch({
        currentTask: { job_id: 'j1', status: 'running', progress: 30 } as never,
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { progress_redis: 75 } },
      })

      await store.fetchTaskProgress('j1')

      expect(store.currentTask!.progress).toBe(30)
    })

    it('网络错误时返回 null 并设置错误', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      const result = await store.fetchTaskProgress('j1')

      expect(result).toBeNull()
      expect(store.error).toBeTruthy()
    })
  })

  describe('cancelTask', () => {
    it('成功取消任务并更新列表中的状态', async () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [
          { job_id: 'j1', status: 'running' },
          { job_id: 'j2', status: 'queued' },
        ] as never,
      })
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, message: '已取消' },
      })

      const result = await store.cancelTask('j1')

      expect(store.tasks[0].status).toBe('cancelled')
      expect(store.tasks[1].status).toBe('queued')
      expect(result).toEqual({ code: 0, message: '已取消' })
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('取消任务同时更新 currentTask', async () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [{ job_id: 'j1', status: 'running' }] as never,
        currentTask: { job_id: 'j1', status: 'running' } as never,
      })
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0 },
      })

      await store.cancelTask('j1')

      expect(store.currentTask!.status).toBe('cancelled')
    })

    it('currentTask 是其他任务时不更新', async () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [{ job_id: 'j1', status: 'running' }] as never,
        currentTask: { job_id: 'j2', status: 'running' } as never,
      })
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0 },
      })

      await store.cancelTask('j1')

      expect(store.currentTask!.status).toBe('running')
    })

    it('任务不在列表中时不报错', async () => {
      const store = useTasksStore()
      store.$patch({ tasks: [] as never })
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0 },
      })

      const result = await store.cancelTask('j1')

      expect(result).toEqual({ code: 0 })
    })

    it('非零 code 时设置错误信息', async () => {
      const store = useTasksStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '无法取消已完成任务' },
      })

      await store.cancelTask('j1')

      expect(store.error).toBe('无法取消已完成任务')
      expect(store.tasks).toEqual([])
    })

    it('非零 code 无 message 时使用默认错误', async () => {
      const store = useTasksStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })

      await store.cancelTask('j1')

      expect(store.error).toBe('Failed to cancel task')
    })

    it('网络错误时返回 null 并设置错误', async () => {
      const store = useTasksStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      const result = await store.cancelTask('j1')

      expect(result).toBeNull()
      expect(store.error).toBeTruthy()
      expect(store.loading).toBe(false)
    })
  })

  describe('fetchStats', () => {
    it('成功获取统计信息', async () => {
      const store = useTasksStore()
      const statsData = {
        total_tasks: 100,
        active_tasks: 5,
        queued_tasks: 3,
        completed_tasks: 80,
        failed_tasks: 12,
        max_concurrent: 10,
        available_slots: 5,
      }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: statsData },
      })

      const result = await store.fetchStats()

      expect(store.stats).toEqual(statsData)
      expect(result).toEqual({ code: 0, data: statsData })
    })

    it('非零 code 时不更新 stats', async () => {
      const store = useTasksStore()
      const originalStats = { ...store.stats }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: 'failed' },
      })

      const result = await store.fetchStats()

      expect(store.stats).toEqual(originalStats)
      expect(result).toEqual({ code: 1, message: 'failed' })
    })

    it('网络错误时返回 null 并设置错误', async () => {
      const store = useTasksStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      const result = await store.fetchStats()

      expect(result).toBeNull()
      expect(store.error).toBeTruthy()
    })
  })

  describe('updateTaskFromSSE', () => {
    it('更新列表中匹配的任务', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [
          { job_id: 'j1', status: 'running', progress: 30 },
          { job_id: 'j2', status: 'queued', progress: 0 },
        ] as never,
      })

      store.updateTaskFromSSE('j1', 'completed', 100)

      expect(store.tasks[0].status).toBe('completed')
      expect(store.tasks[0].progress).toBe(100)
      expect(store.tasks[1].status).toBe('queued')
    })

    it('同时更新匹配的 currentTask', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [{ job_id: 'j1', status: 'running', progress: 30 }] as never,
        currentTask: { job_id: 'j1', status: 'running', progress: 30 } as never,
      })

      store.updateTaskFromSSE('j1', 'completed', 100)

      expect(store.currentTask!.status).toBe('completed')
      expect(store.currentTask!.progress).toBe(100)
    })

    it('currentTask 是其他任务时不更新', () => {
      const store = useTasksStore()
      store.$patch({
        currentTask: { job_id: 'j2', status: 'running', progress: 30 } as never,
      })

      store.updateTaskFromSSE('j1', 'completed', 100)

      expect(store.currentTask!.status).toBe('running')
      expect(store.currentTask!.progress).toBe(30)
    })

    it('任务不在列表中时不报错', () => {
      const store = useTasksStore()
      store.$patch({ tasks: [] as never })

      expect(() => store.updateTaskFromSSE('j1', 'completed', 100)).not.toThrow()
    })

    it('currentTask 为 null 时不报错', () => {
      const store = useTasksStore()

      expect(() => store.updateTaskFromSSE('j1', 'completed', 100)).not.toThrow()
      expect(store.currentTask).toBeNull()
    })
  })

  describe('reset', () => {
    it('重置所有状态', () => {
      const store = useTasksStore()
      store.$patch({
        tasks: [{ job_id: 'j1', status: 'running' }] as never,
        currentTask: { job_id: 'j1', status: 'running' } as never,
        loading: true,
        error: 'some error',
        stats: {
          total_tasks: 10,
          active_tasks: 5,
          queued_tasks: 2,
          completed_tasks: 2,
          failed_tasks: 1,
          max_concurrent: 10,
          available_slots: 5,
        },
      })

      store.reset()

      expect(store.tasks).toEqual([])
      expect(store.currentTask).toBeNull()
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
      // stats 不重置（reset 只重置 tasks/currentTask/loading/error）
      expect(store.stats.total_tasks).toBe(10)
    })
  })
})
