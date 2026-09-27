import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// mock element-plus 的 ElMessage
const elMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
}))

// mock LLM Provider API 模块
// vi.hoisted：async 工厂（importActual 模式）在模块加载期引用 apiMocks，
// 普通 const 会 TDZ 报错（Cannot access 'apiMocks' before initialization）
const apiMocks = vi.hoisted(() => ({
  listProviders: vi.fn(),
  getRegistryStatus: vi.fn(),
  getActiveProvider: vi.fn(),
  getProviderTypes: vi.fn(),
  getCapabilities: vi.fn(),
  getProvider: vi.fn(),
  listProviderModels: vi.fn(),
  checkProviderHealth: vi.fn(),
  previewAutoDetect: vi.fn(),
  importDetected: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  activateProvider: vi.fn(),
  setProviderEnabled: vi.fn(),
  testProvider: vi.fn(),
  getRouterStatus: vi.fn(),
  getRoutingStrategies: vi.fn(),
}))

vi.mock('@/api/llmProviders', async () => {
  const actual = await vi.importActual<typeof import('@/api/llmProviders')>('@/api/llmProviders')
  return {
    ...actual,
    ...apiMocks,
  }
})

import { useLLMProvidersStore } from '@/stores/llmProviders'
import type { LLMProvider } from '@/types/llmProvider'

function makeProvider(over: Partial<LLMProvider> = {}): LLMProvider {
  return {
    provider_id: 'p1',
    name: 'Ollama',
    provider_type: 'ollama',
    base_url: 'http://localhost:11434',
    api_key_set: false,
    default_model: 'qwen2.5',
    timeout: 30,
    max_retries: 2,
    retry_delay: 1,
    enabled: true,
    is_active: false,
    priority: 0,
    capabilities: ['chat'],
    extra: {},
    ...over,
  }
}

describe('useLLMProvidersStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMocks).forEach((m) => m.mockReset())
    Object.values(elMessageMock).forEach((m) => m.mockReset())
  })

  describe('initial state', () => {
    it('providers 初始为空数组', () => {
      const store = useLLMProvidersStore()
      expect(store.providers).toEqual([])
    })

    it('status 初始为 null', () => {
      const store = useLLMProvidersStore()
      expect(store.status).toBeNull()
    })

    it('routerStatus 初始为 null', () => {
      const store = useLLMProvidersStore()
      expect(store.routerStatus).toBeNull()
    })

    it('loading 初始为 false', () => {
      const store = useLLMProvidersStore()
      expect(store.loading).toBe(false)
    })

    it('detecting 初始为 false', () => {
      const store = useLLMProvidersStore()
      expect(store.detecting).toBe(false)
    })

    it('testing 初始为 false', () => {
      const store = useLLMProvidersStore()
      expect(store.testing).toBe(false)
    })

    it('lastDetectDuration 初始为 null', () => {
      const store = useLLMProvidersStore()
      expect(store.lastDetectDuration).toBeNull()
    })
  })

  describe('getters', () => {
    it('activeProvider 返回 is_active 的 provider', () => {
      const store = useLLMProvidersStore()
      store.$patch({
        providers: [
          makeProvider({ provider_id: 'a', is_active: false }),
          makeProvider({ provider_id: 'b', is_active: true }),
        ] as never,
      })
      expect(store.activeProvider?.provider_id).toBe('b')
    })

    it('无激活 provider 时 activeProvider 为 null', () => {
      const store = useLLMProvidersStore()
      store.$patch({ providers: [makeProvider({ is_active: false })] as never })
      expect(store.activeProvider).toBeNull()
    })

    it('enabledProviders 过滤 enabled 为 true 的', () => {
      const store = useLLMProvidersStore()
      store.$patch({
        providers: [
          makeProvider({ provider_id: 'a', enabled: true }),
          makeProvider({ provider_id: 'b', enabled: false }),
        ] as never,
      })
      expect(store.enabledProviders.length).toBe(1)
      expect(store.enabledProviders[0].provider_id).toBe('a')
    })

    it('localProviders 过滤 category 为 local 的', () => {
      const store = useLLMProvidersStore()
      store.$patch({
        providers: [
          makeProvider({ provider_id: 'a', provider_type: 'ollama' }),
          makeProvider({ provider_id: 'b', provider_type: 'openai' }),
        ] as never,
      })
      expect(store.localProviders.length).toBe(1)
      expect(store.localProviders[0].provider_id).toBe('a')
    })

    it('cloudProviders 过滤 category 为 cloud 的', () => {
      const store = useLLMProvidersStore()
      store.$patch({
        providers: [
          makeProvider({ provider_id: 'a', provider_type: 'ollama' }),
          makeProvider({ provider_id: 'b', provider_type: 'openai' }),
        ] as never,
      })
      expect(store.cloudProviders.length).toBe(1)
      expect(store.cloudProviders[0].provider_id).toBe('b')
    })

    it('hasActiveProvider 反映是否存在激活 provider', () => {
      const store = useLLMProvidersStore()
      expect(store.hasActiveProvider).toBe(false)
      store.$patch({ providers: [makeProvider({ is_active: true })] as never })
      expect(store.hasActiveProvider).toBe(true)
    })

    it('encryptionAvailable 反映 status.encryption_available', () => {
      const store = useLLMProvidersStore()
      expect(store.encryptionAvailable).toBe(false)
      store.$patch({ status: { encryption_available: true } as never })
      expect(store.encryptionAvailable).toBe(true)
    })
  })

  describe('loadAll', () => {
    it('成功加载 provider 列表、状态和路由状态', async () => {
      apiMocks.listProviders.mockResolvedValue([makeProvider()])
      apiMocks.getRegistryStatus.mockResolvedValue({ encryption_available: true })
      apiMocks.getRouterStatus.mockResolvedValue({ current_strategy: 'active_only' })
      const store = useLLMProvidersStore()
      await store.loadAll()
      expect(store.providers.length).toBe(1)
      expect(store.status).toMatchObject({ encryption_available: true })
      expect(store.routerStatus).toMatchObject({ current_strategy: 'active_only' })
      expect(store.loading).toBe(false)
    })

    it('getRegistryStatus 失败时降级为 null 不阻塞', async () => {
      apiMocks.listProviders.mockResolvedValue([makeProvider()])
      apiMocks.getRegistryStatus.mockRejectedValue(new Error('status err'))
      apiMocks.getRouterStatus.mockResolvedValue({ current_strategy: 'active_only' })
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      await store.loadAll()
      expect(store.providers.length).toBe(1)
      expect(store.status).toBeNull()
      expect(store.routerStatus).toMatchObject({ current_strategy: 'active_only' })
      warnSpy.mockRestore()
    })

    it('getRouterStatus 失败时降级为 null 不阻塞', async () => {
      apiMocks.listProviders.mockResolvedValue([])
      apiMocks.getRegistryStatus.mockResolvedValue({ encryption_available: false })
      apiMocks.getRouterStatus.mockRejectedValue(new Error('router err'))
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      await store.loadAll()
      expect(store.routerStatus).toBeNull()
      warnSpy.mockRestore()
    })

    it('listProviders 失败时调用 handleError 并显示错误', async () => {
      const apiErr = new Error('加载失败') as Error & { response?: unknown }
      apiErr.response = { data: { detail: '加载失败' } }
      apiMocks.listProviders.mockRejectedValue(apiErr)
      // getRegistryStatus/getRouterStatus 必须返回 Promise（vi.fn() 默认返回
      // undefined，loadAll 里 .catch 会抛 TypeError 抢占 catch 分支）
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      await store.loadAll()
      expect(elMessageMock.error).toHaveBeenCalledWith('加载失败')
      expect(store.loading).toBe(false)
      errSpy.mockRestore()
    })

    it('listProviders 失败无 detail 时使用 fallback', async () => {
      apiMocks.listProviders.mockRejectedValue(new Error('boom'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      await store.loadAll()
      expect(elMessageMock.error).toHaveBeenCalledWith('加载 LLM Provider 列表失败')
      errSpy.mockRestore()
    })
  })

  describe('refreshStatus', () => {
    it('成功刷新状态和路由状态', async () => {
      apiMocks.getRegistryStatus.mockResolvedValue({ encryption_available: true })
      apiMocks.getRouterStatus.mockResolvedValue({ current_strategy: 'local_first' })
      const store = useLLMProvidersStore()
      await store.refreshStatus()
      expect(store.status).toMatchObject({ encryption_available: true })
      expect(store.routerStatus).toMatchObject({ current_strategy: 'local_first' })
    })

    it('状态刷新失败时仅记录日志不抛错', async () => {
      apiMocks.getRegistryStatus.mockRejectedValue(new Error('err'))
      apiMocks.getRouterStatus.mockRejectedValue(new Error('err'))
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      await expect(store.refreshStatus()).resolves.toBeUndefined()
      warnSpy.mockRestore()
    })
  })

  describe('previewDetect', () => {
    it('成功时保存探测结果和耗时', async () => {
      apiMocks.previewAutoDetect.mockResolvedValue({
        detected: [makeProvider({ provider_id: 'd1' }) as never],
        scanned_count: 5,
        detected_count: 1,
        duration_ms: 800,
      })
      const store = useLLMProvidersStore()
      await store.previewDetect()
      expect(store.detected.length).toBe(1)
      expect(store.lastDetectDuration).toBe(800)
      expect(store.detecting).toBe(false)
    })

    it('失败时清空 detected 并显示错误', async () => {
      apiMocks.previewAutoDetect.mockRejectedValue(new Error('detect err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      store.$patch({ detected: [makeProvider() as never] })
      await store.previewDetect()
      expect(store.detected).toEqual([])
      expect(elMessageMock.error).toHaveBeenCalled()
      expect(store.detecting).toBe(false)
      errSpy.mockRestore()
    })
  })

  describe('importDetectedProviders', () => {
    it('成功导入后刷新列表', async () => {
      apiMocks.importDetected.mockResolvedValue({ imported: 2, activated: 'p1' })
      apiMocks.listProviders.mockResolvedValue([makeProvider()])
      apiMocks.getRegistryStatus.mockResolvedValue({ encryption_available: false })
      apiMocks.getRouterStatus.mockResolvedValue({ current_strategy: 'active_only' })
      const store = useLLMProvidersStore()
      const result = await store.importDetectedProviders()
      expect(result.imported).toBe(2)
      expect(elMessageMock.success).toHaveBeenCalled()
      expect(store.detecting).toBe(false)
    })

    it('失败时抛出错误并显示错误消息', async () => {
      apiMocks.importDetected.mockRejectedValue(new Error('import err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      await expect(store.importDetectedProviders()).rejects.toThrow('import err')
      expect(elMessageMock.error).toHaveBeenCalled()
      expect(store.detecting).toBe(false)
      errSpy.mockRestore()
    })
  })

  describe('createProvider', () => {
    it('成功创建后刷新列表并返回 provider', async () => {
      const created = makeProvider({ provider_id: 'new', name: 'New' })
      apiMocks.createProvider.mockResolvedValue(created)
      apiMocks.listProviders.mockResolvedValue([created])
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const store = useLLMProvidersStore()
      const result = await store.createProvider({ provider_id: 'new', name: 'New', provider_type: 'ollama' })
      expect(result).not.toBeNull()
      expect(elMessageMock.success).toHaveBeenCalled()
    })

    it('失败时返回 null 并显示错误', async () => {
      apiMocks.createProvider.mockRejectedValue(new Error('create err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      const result = await store.createProvider({ provider_id: 'x', name: 'X', provider_type: 'ollama' })
      expect(result).toBeNull()
      expect(elMessageMock.error).toHaveBeenCalled()
      errSpy.mockRestore()
    })
  })

  describe('updateProvider', () => {
    it('成功更新后刷新列表', async () => {
      const updated = makeProvider({ name: 'Updated' })
      apiMocks.updateProvider.mockResolvedValue(updated)
      apiMocks.listProviders.mockResolvedValue([updated])
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const store = useLLMProvidersStore()
      const result = await store.updateProvider('p1', { name: 'Updated' })
      expect(result).not.toBeNull()
      expect(elMessageMock.success).toHaveBeenCalled()
    })

    it('失败时返回 null', async () => {
      apiMocks.updateProvider.mockRejectedValue(new Error('update err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      const result = await store.updateProvider('p1', { name: 'X' })
      expect(result).toBeNull()
      errSpy.mockRestore()
    })
  })

  describe('deleteProvider', () => {
    it('成功删除返回 true', async () => {
      apiMocks.deleteProvider.mockResolvedValue(undefined)
      apiMocks.listProviders.mockResolvedValue([])
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const store = useLLMProvidersStore()
      const result = await store.deleteProvider('p1')
      expect(result).toBe(true)
      expect(elMessageMock.success).toHaveBeenCalled()
    })

    it('失败时返回 false', async () => {
      apiMocks.deleteProvider.mockRejectedValue(new Error('del err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      const result = await store.deleteProvider('p1')
      expect(result).toBe(false)
      errSpy.mockRestore()
    })
  })

  describe('activateProvider', () => {
    it('成功激活返回 true', async () => {
      apiMocks.activateProvider.mockResolvedValue(makeProvider())
      apiMocks.listProviders.mockResolvedValue([makeProvider({ is_active: true })])
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const store = useLLMProvidersStore()
      expect(await store.activateProvider('p1')).toBe(true)
    })

    it('失败时返回 false', async () => {
      apiMocks.activateProvider.mockRejectedValue(new Error('act err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      expect(await store.activateProvider('p1')).toBe(false)
      errSpy.mockRestore()
    })
  })

  describe('setEnabled', () => {
    it('启用成功显示启用消息', async () => {
      apiMocks.setProviderEnabled.mockResolvedValue(makeProvider())
      apiMocks.listProviders.mockResolvedValue([])
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const store = useLLMProvidersStore()
      expect(await store.setEnabled('p1', true)).toBe(true)
      expect(elMessageMock.success).toHaveBeenCalledWith('已启用')
    })

    it('禁用成功显示禁用消息', async () => {
      apiMocks.setProviderEnabled.mockResolvedValue(makeProvider())
      apiMocks.listProviders.mockResolvedValue([])
      apiMocks.getRegistryStatus.mockResolvedValue({})
      apiMocks.getRouterStatus.mockResolvedValue({})
      const store = useLLMProvidersStore()
      expect(await store.setEnabled('p1', false)).toBe(true)
      expect(elMessageMock.success).toHaveBeenCalledWith('已禁用')
    })

    it('失败时返回 false', async () => {
      apiMocks.setProviderEnabled.mockRejectedValue(new Error('err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      expect(await store.setEnabled('p1', true)).toBe(false)
      errSpy.mockRestore()
    })
  })

  describe('checkHealth', () => {
    it('健康时更新 provider 健康状态', async () => {
      apiMocks.checkProviderHealth.mockResolvedValue({
        provider_id: 'p1',
        healthy: true,
        latency_ms: 50,
        status: 'ok',
        checked_at: '2026-01-01T00:00:00Z',
      })
      const store = useLLMProvidersStore()
      store.$patch({ providers: [makeProvider()] as never })
      const result = await store.checkHealth('p1')
      expect(result).not.toBeNull()
      expect(store.providers[0].last_health_status).toBe('healthy')
      expect(store.healthChecking['p1']).toBe(false)
    })

    it('不健康时显示警告', async () => {
      apiMocks.checkProviderHealth.mockResolvedValue({
        provider_id: 'p1',
        healthy: false,
        latency_ms: null,
        status: 'down',
        error: '连接超时',
        checked_at: '2026-01-01T00:00:00Z',
      })
      const store = useLLMProvidersStore()
      store.$patch({ providers: [makeProvider()] as never })
      await store.checkHealth('p1')
      expect(elMessageMock.warning).toHaveBeenCalled()
      expect(store.providers[0].last_health_status).toBe('unhealthy')
    })

    it('异常时返回 null', async () => {
      apiMocks.checkProviderHealth.mockRejectedValue(new Error('health err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      const result = await store.checkHealth('p1')
      expect(result).toBeNull()
      expect(store.healthChecking['p1']).toBe(false)
      errSpy.mockRestore()
    })

    it('provider 不在列表中时不报错', async () => {
      apiMocks.checkProviderHealth.mockResolvedValue({
        provider_id: 'p1',
        healthy: true,
        latency_ms: 10,
        status: 'ok',
        checked_at: '2026-01-01T00:00:00Z',
      })
      const store = useLLMProvidersStore()
      store.$patch({ providers: [] as never })
      const result = await store.checkHealth('p1')
      expect(result).not.toBeNull()
    })
  })

  describe('listModels', () => {
    it('成功返回模型列表', async () => {
      apiMocks.listProviderModels.mockResolvedValue([{ id: 'm1' }])
      const store = useLLMProvidersStore()
      const result = await store.listModels('p1')
      expect(result.length).toBe(1)
    })

    it('失败时返回空数组', async () => {
      apiMocks.listProviderModels.mockRejectedValue(new Error('err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      const result = await store.listModels('p1')
      expect(result).toEqual([])
      errSpy.mockRestore()
    })
  })

  describe('testChat', () => {
    it('成功返回测试结果', async () => {
      apiMocks.testProvider.mockResolvedValue({
        provider_id: 'p1',
        content: 'hello',
        model: 'qwen',
        finish_reason: 'stop',
        usage: { prompt_tokens: 5, completion_tokens: 1, total_tokens: 6 },
        latency_ms: 120,
      })
      const store = useLLMProvidersStore()
      const result = await store.testChat('p1', { messages: [{ role: 'user', content: 'hi' }] })
      expect(result).not.toBeNull()
      expect(elMessageMock.success).toHaveBeenCalled()
      expect(store.testing).toBe(false)
    })

    it('失败时返回 null', async () => {
      apiMocks.testProvider.mockRejectedValue(new Error('test err'))
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const store = useLLMProvidersStore()
      const result = await store.testChat('p1', { messages: [] })
      expect(result).toBeNull()
      expect(store.testing).toBe(false)
      errSpy.mockRestore()
    })
  })

  describe('getCategoryLabel', () => {
    it('已知类型返回对应 label', () => {
      const store = useLLMProvidersStore()
      expect(store.getCategoryLabel('ollama')).toBe('Ollama')
      expect(store.getCategoryLabel('openai')).toBe('OpenAI')
    })
  })
})
