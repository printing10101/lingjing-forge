/**
 * 扩展点注册表 composable
 *
 * 对应后端 app/plugins/extension_registry.py（ADR-005 阶段 3 p3-5）。
 * 前端扩展点注册表：管理插件向工作区/设置页/命令面板等扩展点注入的组件贡献。
 *
 * 与后端的关系：
 *   - 后端 ExtensionRegistry：插件在 on_load 时通过 context.extension_registry.register()
 *     注册 handler 类型的扩展点贡献（后端调用）
 *   - 前端 useExtensionRegistry：管理 component_url 类型的扩展点贡献（前端渲染）
 *   - 两者通过插件 manifest 中的扩展点声明关联，前端在加载插件清单时同步注册
 *
 * 全局单例：使用 module-level state，所有调用 useExtensionRegistry() 的组件
 * 共享同一份注册表，避免重复初始化。
 */

import { ref, computed, type Ref, type ComputedRef } from 'vue'
import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'
import type {
  ExtensionPointContribution,
  BuiltinExtensionPoint,
  PluginInfo,
  PluginManifest,
} from '@/contracts/plugin'

// 类型定义

/** 前端扩展点 handler 函数类型（与后端 handler 字符串引用不同，前端直接持有函数）。 */
export type ExtensionHandler = (payload: Record<string, unknown>) => unknown | Promise<unknown>

/** 前端扩展点贡献（在契约基础上扩展 handler 函数引用）。 */
export interface FrontendContribution extends Omit<ExtensionPointContribution, 'handler'> {
  /** 前端 handler 函数（与 component_url 二选一）。 */
  handler_fn?: ExtensionHandler
  /** 异步组件加载器（由 loadComponent 创建）。 */
  component_loader?: () => Promise<unknown>
}

/** 注册贡献时的参数。 */
export interface RegisterContributionOptions {
  /** 贡献者插件 ID。 */
  plugin_id: string
  /** 扩展点名。 */
  extension_point: string
  /** handler 函数（与 component_url 二选一）。 */
  handler?: ExtensionHandler
  /** 前端组件 URL（如 'ltc_chatter/DashboardPanel.vue'）。 */
  component_url?: string
  /** 组件 props 默认值。 */
  props?: Record<string, unknown>
  /** 额外元信息。 */
  metadata?: Record<string, unknown>
}

/** 后端插件列表响应。 */
interface ListPluginsResponse {
  plugins: Array<PluginInfo | Record<string, unknown>>
  total: number
}

/** 后端统一响应壳。 */
interface ApiEnvelope<T> {
  data: T
  message?: string
}

// 全局单例状态（module-level，所有组件共享）

/** 全部注册的扩展点贡献（按注册顺序）。 */
const _contributions: Ref<FrontendContribution[]> = ref([])

/** 按 plugin_id → 注册数 的反向索引（用于快速卸载）。 */
const _pluginIndex: Map<string, Set<number>> = new Map()

/** 注册顺序计数器（用于稳定排序）。 */
let _orderCounter = 0

/** 后端插件清单缓存（避免重复拉取）。 */
const _backendManifests: Ref<PluginManifest[]> = ref([])

/** 后端同步状态。 */
const _syncing = ref(false)

/** 后端同步错误（最近一次）。 */
const _syncError: Ref<string | null> = ref(null)

// 注册表核心 API

/**
 * 注册扩展点贡献。
 *
 * 同一插件可向同一扩展点注册多个贡献（按注册顺序保留）。
 * component_url 与 handler 二选一：component_url 用于 UI 渲染，handler 用于行为调用。
 */
function register(options: RegisterContributionOptions): FrontendContribution {
  if (!options.plugin_id) {
    throw new Error('[useExtensionRegistry] register: plugin_id 不能为空')
  }
  if (!options.extension_point) {
    throw new Error('[useExtensionRegistry] register: extension_point 不能为空')
  }
  if (!options.handler && !options.component_url) {
    throw new Error(
      '[useExtensionRegistry] register: 必须提供 handler 或 component_url 之一',
    )
  }

  const contribution: FrontendContribution = {
    extension_point: options.extension_point,
    plugin_id: options.plugin_id,
    props: options.props ?? {},
    metadata: options.metadata ?? {},
  }

  if (options.handler) {
    contribution.handler_fn = options.handler
  }
  if (options.component_url) {
    contribution.component_url = options.component_url
    contribution.component_loader = createComponentLoader(options.component_url)
  }

  const index = _contributions.value.length
  _contributions.value.push(contribution)

  // 维护反向索引
  let indices = _pluginIndex.get(options.plugin_id)
  if (!indices) {
    indices = new Set()
    _pluginIndex.set(options.plugin_id, indices)
  }
  indices.add(index)

  _orderCounter++
  return contribution
}

/**
 * 取消注册。
 * @param plugin_id 插件 ID
 * @param extension_point 可选，指定时只取消该扩展点的贡献；不指定时取消该插件所有贡献
 * @returns 取消的贡献数量
 */
function unregister(plugin_id: string, extension_point?: string): number {
  if (!plugin_id) return 0

  let removedCount = 0
  const newList: FrontendContribution[] = []
  const newIndexMap: Map<string, Set<number>> = new Map()

  for (const c of _contributions.value) {
    if (c.plugin_id === plugin_id && (!extension_point || c.extension_point === extension_point)) {
      removedCount++
      continue
    }
    const newIndex = newList.length
    newList.push(c)
    let indices = newIndexMap.get(c.plugin_id)
    if (!indices) {
      indices = new Set()
      newIndexMap.set(c.plugin_id, indices)
    }
    indices.add(newIndex)
  }

  _contributions.value = newList
  _pluginIndex.clear()
  newIndexMap.forEach((v, k) => _pluginIndex.set(k, v))

  return removedCount
}

/**
 * 列出某扩展点的所有贡献（响应式）。
 */
function list(extension_point: string): FrontendContribution[] {
  return _contributions.value.filter((c) => c.extension_point === extension_point)
}

/**
 * 列出某扩展点的所有贡献（ComputedRef，用于模板自动响应）。
 */
function listComputed(extension_point: string): ComputedRef<FrontendContribution[]> {
  return computed(() => list(extension_point))
}

/**
 * 获取某插件的所有贡献。
 */
function getContributionsByPlugin(plugin_id: string): FrontendContribution[] {
  return _contributions.value.filter((c) => c.plugin_id === plugin_id)
}

/**
 * 调用某扩展点的所有 handler，返回结果列表（按注册顺序）。
 *
 * 单个 handler 失败不阻塞其他 handler，失败的结果位置为 undefined。
 */
async function invokeAll(
  extension_point: string,
  payload: Record<string, unknown>,
): Promise<unknown[]> {
  const regs = list(extension_point)
  const results: unknown[] = []
  for (const reg of regs) {
    if (!reg.handler_fn) {
      results.push(undefined)
      continue
    }
    try {
      const result = await reg.handler_fn(payload)
      results.push(result)
    } catch (e: unknown) {
      // 单个 handler 失败不阻塞其他 handler
      console.warn(
        `[useExtensionRegistry] invokeAll handler failed for '${reg.plugin_id}':`,
        e,
      )
      results.push(undefined)
    }
  }
  return results
}

/**
 * 调用单个贡献的 handler。
 */
async function invoke(
  contribution: FrontendContribution,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (!contribution.handler_fn) {
    throw new Error(
      `[useExtensionRegistry] invoke: contribution '${contribution.plugin_id}/'${contribution.extension_point} 无 handler`,
    )
  }
  return contribution.handler_fn(payload)
}

/**
 * 清空所有注册（主要用于测试）。
 */
function clear(): void {
  _contributions.value = []
  _pluginIndex.clear()
  _orderCounter = 0
}

// 组件加载器工厂

/**
 * 创建异步组件加载器。
 *
 * 通过 Vite import.meta.glob 静态收集所有插件/视图/组件模块，
 * 运行时按 component_url 查找——避免动态 import 变量路径（Vite
 * 无法静态分析含 `/` 和缺扩展名的变量 import）。
 *
 * component_url 格式约定：
 *   - "plugin_id/ComponentName.vue" → src/plugins/<plugin_id>/ComponentName.vue
 *   - 完整路径 "/views/XxxPanel.vue" → src/views/XxxPanel.vue
 *   - 单段 "XxxPanel.vue" → src/components/XxxPanel.vue
 */

// Vite 静态收集：所有可加载的前端组件模块（构建时展开，键为模块路径）
const componentModules = import.meta.glob([
  '@/plugins/**/*.vue',
  '@/views/**/*.vue',
  '@/components/**/*.vue',
])

function createComponentLoader(component_url: string): () => Promise<unknown> {
  return async () => {
    try {
      // 规整化路径：去掉前导 '/'，确保 .vue 扩展名
      let normalized = component_url.replace(/^\/+/, '')
      if (!normalized.endsWith('.vue')) {
        normalized += '.vue'
      }

      // 候选相对路径（不含 /src/ 前缀），按 plugins → views → components 优先级
      const candidates = [
        `plugins/${normalized}`,
        `views/${normalized.replace(/^views\//, '')}`,
        `components/${normalized.replace(/^components\//, '')}`,
      ]

      for (const candidate of candidates) {
        // import.meta.glob 键形如 "/src/plugins/dialect-manager/DialectManagerPanel.vue"
        const matchedKey = Object.keys(componentModules).find((key) =>
          key.endsWith(`/${candidate}`),
        )
        if (matchedKey) {
          const module = await componentModules[matchedKey]()
          return (module as { default?: unknown }).default ?? module
        }
      }
      // 未匹配到任何模块
      const importError = new Error(
        `[useExtensionRegistry] 找不到组件 '${component_url}'（已扫描 plugins/views/components）`,
      )
      throw importError
    } catch (e: unknown) {
      console.error(
        `[useExtensionRegistry] loadComponent failed for '${component_url}':`,
        e,
      )
      throw e
    }
  }
}

/**
 * 加载贡献的组件（返回组件定义对象）。
 *
 * 使用 shallowRef 避免对组件对象做深度响应式（Vue 组件不应被代理）。
 */
async function loadComponent(
  contribution: FrontendContribution,
): Promise<unknown | null> {
  if (!contribution.component_loader) {
    return null
  }
  return contribution.component_loader()
}

// 后端同步：从已启用插件拉取 manifest，注册声明的扩展点贡献

/**
 * 从后端拉取已安装插件清单，同步扩展点贡献。
 *
 * 后端目前未单独暴露扩展点贡献 API，本函数通过 /api/v1/plugins 列表拉取
 * 已启用插件的 manifest，根据 manifest 中的扩展点声明注册前端贡献。
 *
 * 注意：当前阶段后端 manifest 不直接声明前端组件贡献（component_url），
 * 实际贡献由前端插件在加载时主动调用 register() 注册。本函数主要用于
 * 同步插件状态，触发前端插件的初始化逻辑。
 */
async function syncFromBackend(): Promise<void> {
  if (_syncing.value) return
  _syncing.value = true
  _syncError.value = null

  try {
    const res = await http.get<ApiEnvelope<ListPluginsResponse>>(
      API_CONFIG.PLUGINS,
    )
    const data = res.data.data
    const manifests: PluginManifest[] = []
    for (const item of data.plugins) {
      // 兼容两种返回格式：PluginInfo（含 manifest 字段）或直接 manifest dict
      const manifest = (item as PluginInfo).manifest ?? (item as unknown as PluginManifest)
      if (manifest && manifest.id) {
        manifests.push(manifest)
      }
    }
    _backendManifests.value = manifests
  } catch (e: unknown) {
    _syncError.value = e instanceof Error ? e.message : String(e)
    console.warn('[useExtensionRegistry] syncFromBackend failed:', e)
  } finally {
    _syncing.value = false
  }
}

// 派生状态

/** 所有已注册的扩展点名（去重）。 */
const registeredExtensionPoints: ComputedRef<string[]> = computed(() => {
  const set = new Set<string>()
  for (const c of _contributions.value) {
    set.add(c.extension_point)
  }
  return Array.from(set)
})

/** 按扩展点分组的贡献（用于调试/概览）。 */
const contributionsByExtensionPoint: ComputedRef<Record<string, FrontendContribution[]>> =
  computed(() => {
    const groups: Record<string, FrontendContribution[]> = {}
    for (const c of _contributions.value) {
      if (!groups[c.extension_point]) {
        groups[c.extension_point] = []
      }
      groups[c.extension_point].push(c)
    }
    return groups
  })

/** 工作区面板贡献（最常用的扩展点）。 */
const workspacePanels: ComputedRef<FrontendContribution[]> = listComputed('workspace.panel')

/** 设置页 tab 贡献。 */
const settingsTabs: ComputedRef<FrontendContribution[]> = listComputed('settings.tab')

/** 命令面板命令贡献。 */
const commandPaletteCommands: ComputedRef<FrontendContribution[]> = listComputed(
  'command_palette.command',
)

// composable 入口

/**
 * 扩展点注册表 composable.
 *
 * 全局单例：多次调用返回同一份状态，无需在组件间传递。
 */
export function useExtensionRegistry() {
  return {
    // 状态
    contributions: _contributions,
    backendManifests: _backendManifests,
    syncing: _syncing,
    syncError: _syncError,
    registeredExtensionPoints,
    contributionsByExtensionPoint,
    workspacePanels,
    settingsTabs,
    commandPaletteCommands,

    // 注册表 API
    register,
    unregister,
    list,
    listComputed,
    getContributionsByPlugin,
    invokeAll,
    invoke,
    clear,

    // 组件加载
    loadComponent,

    // 后端同步
    syncFromBackend,
  }
}

// 导出工具函数（供非组件场景使用，如路由守卫、main.ts 初始化）

export const extensionRegistry = {
  register,
  unregister,
  list,
  listComputed,
  getContributionsByPlugin,
  invokeAll,
  invoke,
  clear,
  loadComponent,
  syncFromBackend,
  // 状态（只读视图）
  get contributions() {
    return _contributions
  },
  get backendManifests() {
    return _backendManifests
  },
  get syncing() {
    return _syncing
  },
  get syncError() {
    return _syncError
  },
}

export type {
  ExtensionPointContribution,
  BuiltinExtensionPoint,
  PluginInfo,
  PluginManifest,
}
