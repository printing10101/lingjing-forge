import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import http from '@/utils/http'
import { extractErrorMessage } from '@/utils/error-handler'
import { API_CONFIG, buildApiPath } from '@/config/api'

/**
 * 插件元数据接口
 * 描述插件的基本信息、配置和能力。
 */
export interface Plugin {
  id: string
  name: string
  version: string
  author: string
  description: string
  entry_point: string
  plugin_type: string
  capabilities: string[]
  dependencies: Array<{ name: string; version: string; required: boolean }>
  config_schema: Record<string, unknown>
  min_core_version: string
  max_core_version: string
  plugin_path: string
  status: string
  config: Record<string, unknown>
  enabled_at?: number
  disabled_at?: number
  installed_at?: number
}

/** Worker 进程信息接口 */
export interface WorkerInfo {
  status: string
  pid: number
  port: number
  uptime: number
}

/** 依赖节点接口 */
export interface DependencyNode {
  id: string
  name: string
  version: string
  status?: string
  dependencies?: DependencyNode[]
}

/** 插件详情接口 */
export interface PluginDetail {
  metadata: Plugin
  has_instance: boolean
  context_keys: string[]
  dependency_tree: DependencyNode | null
  capabilities: string[]
  worker?: WorkerInfo
}

/**
 * 插件管理 Store (Composition API)
 *
 * V3.0: 从 Options API 重写为 Composition API，与其余 20 个 Store 风格统一。
 */
export const usePluginStore = defineStore('plugin', () => {
// State
  const plugins = ref<Plugin[]>([])
  const currentPlugin = ref<PluginDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

// Getters
  const enabledPlugins = computed(() => plugins.value.filter((p) => p.status === 'enabled'))
  const disabledPlugins = computed(() => plugins.value.filter((p) => p.status === 'disabled'))
  const adapterPlugins = computed(() => plugins.value.filter((p) => p.plugin_type === 'adapter'))
  const dataSourcePlugins = computed(() => plugins.value.filter((p) => p.plugin_type === 'data_source'))
  const analyzerPlugins = computed(() => plugins.value.filter((p) => p.plugin_type === 'analyzer'))
  const visualizationPlugins = computed(() => plugins.value.filter((p) => p.plugin_type === 'visualization'))

// Shared helpers
  function setLoading(flag: boolean) {
    loading.value = flag
    if (flag) error.value = null
  }

// Actions
  async function fetchPlugins() {
    setLoading(true)
    try {
      const response = await http.get(API_CONFIG.PLUGINS)
      plugins.value = response.data.data.plugins
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchPluginDetail(pluginId: string) {
    setLoading(true)
    try {
      const response = await http.get(buildApiPath(API_CONFIG.PLUGINS, `/${pluginId}`))
      currentPlugin.value = response.data.data
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function enablePlugin(pluginId: string) {
    setLoading(true)
    try {
      await http.post(buildApiPath(API_CONFIG.PLUGINS, `/${pluginId}/enable`))
      await fetchPlugins()
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function disablePlugin(pluginId: string) {
    setLoading(true)
    try {
      await http.post(buildApiPath(API_CONFIG.PLUGINS, `/${pluginId}/disable`))
      await fetchPlugins()
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function uninstallPlugin(pluginId: string) {
    setLoading(true)
    try {
      await http.delete(buildApiPath(API_CONFIG.PLUGINS, `/${pluginId}`))
      await fetchPlugins()
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function updatePluginConfig(pluginId: string, config: Record<string, unknown>) {
    setLoading(true)
    try {
      await http.put(buildApiPath(API_CONFIG.PLUGINS, `/${pluginId}/config`), config)
      await fetchPlugins()
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function reloadPlugin(pluginId: string) {
    setLoading(true)
    try {
      await http.post(buildApiPath(API_CONFIG.PLUGINS, `/${pluginId}/reload`))
      await fetchPlugins()
    } catch (err: unknown) {
      error.value = extractErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  return {
    // State
    plugins,
    currentPlugin,
    loading,
    error,
    // Getters
    enabledPlugins,
    disabledPlugins,
    adapterPlugins,
    dataSourcePlugins,
    analyzerPlugins,
    visualizationPlugins,
    // Actions
    fetchPlugins,
    fetchPluginDetail,
    enablePlugin,
    disablePlugin,
    uninstallPlugin,
    updatePluginConfig,
    reloadPlugin,
  }
})
