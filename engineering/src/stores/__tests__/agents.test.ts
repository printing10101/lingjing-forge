import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '@/stores/agents'

// mock http 客户端
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

// 保留 error-handler 真实逻辑
vi.mock('@/utils/error-handler', async () => {
  const actual = await vi.importActual<typeof import('@/utils/error-handler')>('@/utils/error-handler')
  return { ...actual }
})

import http from '@/utils/http'

describe('useAgentStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('初始 agents 为空数组', () => {
      const store = useAgentStore()
      expect(store.agents).toEqual([])
    })

    it('初始 currentAgent 为 null', () => {
      const store = useAgentStore()
      expect(store.currentAgent).toBeNull()
    })

    it('初始 loading 为 false', () => {
      const store = useAgentStore()
      expect(store.loading).toBe(false)
    })

    it('初始 detailLoading 为 false', () => {
      const store = useAgentStore()
      expect(store.detailLoading).toBe(false)
    })

    it('初始 error 为 null', () => {
      const store = useAgentStore()
      expect(store.error).toBeNull()
    })

    it('初始 statusFilter 为 null', () => {
      const store = useAgentStore()
      expect(store.statusFilter).toBeNull()
    })
  })

  describe('computed', () => {
    it('activeAgents 过滤出 busy 和 recovering 状态', () => {
      const store = useAgentStore()
      store.$patch({
        agents: [
          { agent_id: 'a1', status: 'busy' },
          { agent_id: 'a2', status: 'idle' },
          { agent_id: 'a3', status: 'recovering' },
          { agent_id: 'a4', status: 'error' },
        ] as never,
      })
      expect(store.activeAgents).toHaveLength(2)
      expect(store.activeAgents[0].agent_id).toBe('a1')
      expect(store.activeAgents[1].agent_id).toBe('a3')
    })

    it('idleAgents 过滤出 idle 状态', () => {
      const store = useAgentStore()
      store.$patch({
        agents: [
          { agent_id: 'a1', status: 'idle' },
          { agent_id: 'a2', status: 'busy' },
          { agent_id: 'a3', status: 'idle' },
        ] as never,
      })
      expect(store.idleAgents).toHaveLength(2)
    })

    it('errorAgents 过滤出 error 和 stopped 状态', () => {
      const store = useAgentStore()
      store.$patch({
        agents: [
          { agent_id: 'a1', status: 'error' },
          { agent_id: 'a2', status: 'stopped' },
          { agent_id: 'a3', status: 'busy' },
        ] as never,
      })
      expect(store.errorAgents).toHaveLength(2)
    })

    it('statusStats 汇总各状态数量', () => {
      const store = useAgentStore()
      store.$patch({
        agents: [
          { agent_id: 'a1', status: 'busy' },
          { agent_id: 'a2', status: 'idle' },
          { agent_id: 'a3', status: 'error' },
          { agent_id: 'a4', status: 'stopped' },
          { agent_id: 'a5', status: 'recovering' },
        ] as never,
      })
      expect(store.statusStats).toEqual({
        total: 5,
        active: 2,
        idle: 1,
        error: 2,
      })
    })

    it('statusStats 在无 Agent 时全部为 0', () => {
      const store = useAgentStore()
      expect(store.statusStats).toEqual({
        total: 0,
        active: 0,
        idle: 0,
        error: 0,
      })
    })
  })

  describe('formatTime', () => {
    it('空时间戳返回 -', () => {
      const store = useAgentStore()
      expect(store.formatTime('')).toBe('-')
      expect(store.formatTime(0)).toBe('-')
    })

    it('数字时间戳（秒）转换为本地时间字符串', () => {
      const store = useAgentStore()
      const ts = 1700000000
      const result = store.formatTime(ts)
      // 应该是 zh-CN 本地时间格式
      expect(result).not.toBe('-')
      expect(typeof result).toBe('string')
    })

    it('字符串时间戳直接转换', () => {
      const store = useAgentStore()
      const result = store.formatTime('2024-01-01T00:00:00Z')
      expect(result).not.toBe('-')
      expect(typeof result).toBe('string')
    })
  })

  describe('statusTagType', () => {
    it('各状态返回对应标签类型', () => {
      const store = useAgentStore()
      expect(store.statusTagType('idle')).toBe('info')
      expect(store.statusTagType('busy')).toBe('success')
      expect(store.statusTagType('paused')).toBe('warning')
      expect(store.statusTagType('error')).toBe('danger')
      expect(store.statusTagType('stopped')).toBe('info')
      expect(store.statusTagType('recovering')).toBe('warning')
    })

    it('未知状态降级为 info', () => {
      const store = useAgentStore()
      expect(store.statusTagType('unknown')).toBe('info')
    })
  })

  describe('statusLabel', () => {
    it('各状态返回中文标签', () => {
      const store = useAgentStore()
      expect(store.statusLabel('idle')).toBe('空闲')
      expect(store.statusLabel('busy')).toBe('忙碌')
      expect(store.statusLabel('paused')).toBe('暂停')
      expect(store.statusLabel('error')).toBe('错误')
      expect(store.statusLabel('stopped')).toBe('已停止')
      expect(store.statusLabel('recovering')).toBe('恢复中')
    })

    it('未知状态返回原值', () => {
      const store = useAgentStore()
      expect(store.statusLabel('unknown')).toBe('unknown')
    })
  })

  describe('fetchAgents', () => {
    it('获取列表成功时保存到 agents', async () => {
      const agents = [
        { agent_id: 'a1', status: 'idle' },
        { agent_id: 'a2', status: 'busy' },
      ]
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: agents },
      })
      const store = useAgentStore()
      await store.fetchAgents()
      expect(store.agents).toHaveLength(2)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('后端返回空 data 时降级为空数组', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: null },
      })
      const store = useAgentStore()
      await store.fetchAgents()
      expect(store.agents).toEqual([])
    })

    it('设置 statusFilter 时携带 status 参数', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: [] },
      })
      const store = useAgentStore()
      store.$patch({ statusFilter: 'busy' })
      await store.fetchAgents()
      expect(http.get).toHaveBeenCalledWith(expect.any(String), { params: { status: 'busy' } })
    })

    it('网络异常时设置 error', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '服务不可用' } },
      })
      const store = useAgentStore()
      await store.fetchAgents()
      expect(store.error).toBe('服务不可用')
      expect(store.loading).toBe(false)
    })
  })

  describe('fetchAgentDetail', () => {
    it('获取详情成功时保存到 currentAgent', async () => {
      const detail = { agent_id: 'a1', status: 'idle', checkpoint: null }
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: detail },
      })
      const store = useAgentStore()
      const result = await store.fetchAgentDetail('a1')
      expect(store.currentAgent).toEqual(detail)
      expect(result).toEqual(detail)
      expect(store.detailLoading).toBe(false)
    })

    it('网络异常时设置 error 并返回 null', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useAgentStore()
      const result = await store.fetchAgentDetail('a1')
      expect(result).toBeNull()
      expect(store.error).toBe('网络错误')
      expect(store.detailLoading).toBe(false)
    })
  })

  describe('saveCheckpoint', () => {
    it('保存检查点成功时返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { checkpoint_id: 'cp1' } },
      })
      const store = useAgentStore()
      const result = await store.saveCheckpoint('a1', { epoch: 10 })
      expect(result).toEqual({ checkpoint_id: 'cp1' })
      expect(http.post).toHaveBeenCalledWith(expect.stringContaining('/checkpoints/save'), { epoch: 10 })
    })
  })

  describe('rollbackCheckpoint', () => {
    it('回滚成功时返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { success: true } },
      })
      const store = useAgentStore()
      const result = await store.rollbackCheckpoint('a1', 'cp1')
      expect(result).toEqual({ success: true })
      expect(http.post).toHaveBeenCalledWith(
        expect.stringContaining('/checkpoints/rollback'),
        { checkpoint_id: 'cp1' },
      )
    })
  })

  describe('cloneAgent', () => {
    it('克隆成功时返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { new_agent_id: 'a2' } },
      })
      const store = useAgentStore()
      const result = await store.cloneAgent('a1', 'a2')
      expect(result).toEqual({ new_agent_id: 'a2' })
      expect(http.post).toHaveBeenCalledWith(
        expect.stringContaining('/clone'),
        { target_agent_id: 'a2' },
      )
    })
  })

  describe('resumeAgent', () => {
    it('恢复成功时返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { status: 'idle' } },
      })
      const store = useAgentStore()
      const result = await store.resumeAgent('a1')
      expect(result).toEqual({ status: 'idle' })
      expect(http.post).toHaveBeenCalledWith(expect.stringContaining('/resume'))
    })
  })

  describe('deleteAgent', () => {
    it('删除成功时从 agents 列表中移除', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { code: 0 } })
      const store = useAgentStore()
      store.$patch({
        agents: [
          { agent_id: 'a1', status: 'idle' },
          { agent_id: 'a2', status: 'busy' },
        ] as never,
      })
      await store.deleteAgent('a1')
      expect(store.agents).toHaveLength(1)
      expect(store.agents[0].agent_id).toBe('a2')
    })

    it('删除不存在的 agent 时列表不变', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { code: 0 } })
      const store = useAgentStore()
      store.$patch({
        agents: [{ agent_id: 'a1', status: 'idle' }] as never,
      })
      await store.deleteAgent('unknown')
      expect(store.agents).toHaveLength(1)
    })
  })
})
