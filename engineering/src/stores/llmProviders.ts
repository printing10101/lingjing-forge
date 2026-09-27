/**
 * LLM Provider 管理 Pinia Store
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/llmProviders'
import { PROVIDER_TYPE_META } from '@/api/llmProviders'
import type {
  LLMProvider,
  RegistryStatus,
  RouterStatus,
  DetectedProvider,
  ProviderUpsertRequest,
  ChatTestRequest,
  ChatTestResponse,
  HealthCheckResult,
  ModelInfo,
  ProviderType,
} from '@/types/llmProvider'

export const useLLMProvidersStore = defineStore('llmProviders', () => {
// State
  const providers = ref<LLMProvider[]>([])
  const status = ref<RegistryStatus | null>(null)
  const routerStatus = ref<RouterStatus | null>(null)
  const detected = ref<DetectedProvider[]>([])

  const loading = ref(false)
  const detecting = ref(false)
  const healthChecking = ref<Record<string, boolean>>({})
  const testing = ref(false)

  /** 最近一次探测耗时（ms） */
  const lastDetectDuration = ref<number | null>(null)

// Getters
  const activeProvider = computed(() => providers.value.find((p) => p.is_active) ?? null)
  const enabledProviders = computed(() => providers.value.filter((p) => p.enabled))
  const localProviders = computed(() =>
    providers.value.filter((p) => PROVIDER_TYPE_META[p.provider_type]?.category === 'local'),
  )
  const cloudProviders = computed(() =>
    providers.value.filter((p) => PROVIDER_TYPE_META[p.provider_type]?.category === 'cloud'),
  )
  const hasActiveProvider = computed(() => activeProvider.value !== null)
  const encryptionAvailable = computed(() => status.value?.encryption_available ?? false)

// Actions

  /** 加载所有 Provider + 注册表状态 + 路由器状态 */
  async function loadAll(): Promise<void> {
    loading.value = true
    try {
      const [list, statusResp, routerResp] = await Promise.all([
        api.listProviders(),
        // 状态查询失败时降级为 null，不阻塞列表加载，但需记录便于排查
        api.getRegistryStatus().catch((e: unknown) => {
          console.warn('[llmProviders] getRegistryStatus failed:', e)
          return null
        }),
        api.getRouterStatus().catch((e: unknown) => {
          console.warn('[llmProviders] getRouterStatus failed:', e)
          return null
        }),
      ])
      providers.value = list
      status.value = statusResp
      routerStatus.value = routerResp
    } catch (e: unknown) {
      handleError(e, '加载 LLM Provider 列表失败')
    } finally {
      loading.value = false
    }
  }

  /** 刷新注册表状态（轻量） */
  async function refreshStatus(): Promise<void> {
    try {
      const [statusResp, routerResp] = await Promise.all([
        api.getRegistryStatus().catch((e: unknown) => {
          console.warn('[llmProviders] getRegistryStatus failed:', e)
          return null
        }),
        api.getRouterStatus().catch((e: unknown) => {
          console.warn('[llmProviders] getRouterStatus failed:', e)
          return null
        }),
      ])
      status.value = statusResp
      routerStatus.value = routerResp
    } catch (e: unknown) {
      // 状态刷新失败属于辅助功能，不阻塞主流程，仅记录日志
      console.warn('[llmProviders] refreshStatus failed:', e)
    }
  }

  /** 自动探测预览（不写库） */
  async function previewDetect(): Promise<void> {
    detecting.value = true
    try {
      const resp = await api.previewAutoDetect()
      detected.value = resp.detected
      lastDetectDuration.value = resp.duration_ms
    } catch (e: unknown) {
      handleError(e, '自动探测预览失败')
      detected.value = []
    } finally {
      detecting.value = false
    }
  }

  /** 自动探测并导入 */
  async function importDetectedProviders(): Promise<{ imported: number; activated: string | null }> {
    detecting.value = true
    try {
      const resp = await api.importDetected()
      ElMessage.success(`已导入 ${resp.imported} 个 Provider`)
      await loadAll()
      return resp
    } catch (e: unknown) {
      handleError(e, '导入探测结果失败')
      throw e
    } finally {
      detecting.value = false
    }
  }

  /** 新增 Provider */
  async function createProvider(payload: ProviderUpsertRequest): Promise<LLMProvider | null> {
    try {
      const created = await api.createProvider(payload)
      ElMessage.success(`Provider "${created.name}" 已创建`)
      await loadAll()
      return created
    } catch (e: unknown) {
      handleError(e, '创建 Provider 失败')
      return null
    }
  }

  /** 更新 Provider */
  async function updateProvider(
    providerId: string,
    payload: Partial<ProviderUpsertRequest> & { api_key?: string | null },
  ): Promise<LLMProvider | null> {
    try {
      const updated = await api.updateProvider(providerId, payload)
      ElMessage.success(`Provider "${updated.name}" 已更新`)
      await loadAll()
      return updated
    } catch (e: unknown) {
      handleError(e, '更新 Provider 失败')
      return null
    }
  }

  /** 删除 Provider */
  async function deleteProvider(providerId: string): Promise<boolean> {
    try {
      await api.deleteProvider(providerId)
      ElMessage.success('Provider 已删除')
      await loadAll()
      return true
    } catch (e: unknown) {
      handleError(e, '删除 Provider 失败')
      return false
    }
  }

  /** 激活 Provider（互斥） */
  async function activateProvider(providerId: string): Promise<boolean> {
    try {
      await api.activateProvider(providerId)
      ElMessage.success('已切换激活 Provider')
      await loadAll()
      return true
    } catch (e: unknown) {
      handleError(e, '激活 Provider 失败')
      return false
    }
  }

  /** 启用/禁用 Provider */
  async function setEnabled(providerId: string, enabled: boolean): Promise<boolean> {
    try {
      await api.setProviderEnabled(providerId, enabled)
      ElMessage.success(enabled ? '已启用' : '已禁用')
      await loadAll()
      return true
    } catch (e: unknown) {
      handleError(e, '切换启用状态失败')
      return false
    }
  }

  /** 健康检查单个 Provider */
  async function checkHealth(providerId: string): Promise<HealthCheckResult | null> {
    healthChecking.value[providerId] = true
    try {
      const result = await api.checkProviderHealth(providerId)
      // 本地更新该 Provider 的健康状态字段
      const idx = providers.value.findIndex((p) => p.provider_id === providerId)
      if (idx >= 0) {
        const p = { ...providers.value[idx] }
        p.last_health_check = result.checked_at
        p.last_health_status = result.healthy ? 'healthy' : 'unhealthy'
        p.last_latency_ms = result.latency_ms
        providers.value[idx] = p
      }
      if (!result.healthy) {
        ElMessage.warning(`健康检查异常: ${result.error ?? result.status}`)
      }
      return result
    } catch (e: unknown) {
      handleError(e, '健康检查失败')
      return null
    } finally {
      healthChecking.value[providerId] = false
    }
  }

  /** 列出 Provider 可用模型 */
  async function listModels(providerId: string): Promise<ModelInfo[]> {
    try {
      return await api.listProviderModels(providerId)
    } catch (e: unknown) {
      handleError(e, '获取模型列表失败')
      return []
    }
  }

  /** 测试调用 */
  async function testChat(
    providerId: string,
    payload: ChatTestRequest,
  ): Promise<ChatTestResponse | null> {
    testing.value = true
    try {
      const result = await api.testProvider(providerId, payload)
      ElMessage.success(`调用成功，耗时 ${result.latency_ms}ms`)
      return result
    } catch (e: unknown) {
      handleError(e, '调用测试失败')
      return null
    } finally {
      testing.value = false
    }
  }

// Helpers

  function handleError(e: unknown, fallback: string): void {
    const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(msg || fallback)
    console.error('[LLMProviders]', e)
  }

  function getCategoryLabel(type: ProviderType): string {
    const meta = PROVIDER_TYPE_META[type]
    return meta ? meta.label : type
  }

  return {
    // state
    providers,
    status,
    routerStatus,
    detected,
    loading,
    detecting,
    healthChecking,
    testing,
    lastDetectDuration,
    // getters
    activeProvider,
    enabledProviders,
    localProviders,
    cloudProviders,
    hasActiveProvider,
    encryptionAvailable,
    // actions
    loadAll,
    refreshStatus,
    previewDetect,
    importDetectedProviders,
    createProvider,
    updateProvider,
    deleteProvider,
    activateProvider,
    setEnabled,
    checkHealth,
    listModels,
    testChat,
    // helpers
    getCategoryLabel,
  }
})
