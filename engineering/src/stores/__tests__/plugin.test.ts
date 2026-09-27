import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePluginStore } from '@/stores/plugin'
import type { Plugin, PluginDetail } from '@/stores/plugin'

// mock http 客户端
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import http from '@/utils/http'

// 创建测试用插件对象
function makePlugin(overrides: Partial<Plugin> = {}): Plugin {
  return {
    id: 'p1',
    name: '测试插件',
    version: '1.0.0',
    author: 'tester',
    description: '用于测试的插件',
    entry_point: 'main.py',
    plugin_type: 'adapter',
    capabilities: ['read'],
    dependencies: [],
    config_schema: {},
    min_core_version: '4.0.0',
    max_core_version: '5.0.0',
    plugin_path: '/plugins/p1',
    status: 'enabled',
    config: {},
    ...overrides,
  }
}

// 创建测试用插件详情对象
function makePluginDetail(overrides: Partial<PluginDetail> = {}): PluginDetail {
  return {
    metadata: makePlugin(),
    has_instance: true,
    context_keys: ['ctx1'],
    dependency_tree: null,
    capabilities: ['read'],
    ...overrides,
  }
}

describe('usePluginStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('初始 plugins 为空数组', () => {
      const store = usePluginStore()
      expect(store.plugins).toEqual([])
    })

    it('初始 currentPlugin 为 null', () => {
      const store = usePluginStore()
      expect(store.currentPlugin).toBeNull()
    })

    it('初始 loading 为 false', () => {
      const store = usePluginStore()
      expect(store.loading).toBe(false)
    })

    it('初始 error 为 null', () => {
      const store = usePluginStore()
      expect(store.error).toBeNull()
    })
  })

  describe('getters', () => {
    it('enabledPlugins 过滤出 status=enabled 的插件', () => {
      const store = usePluginStore()
      store.$patch({
        plugins: [
          makePlugin({ id: 'p1', status: 'enabled' }),
          makePlugin({ id: 'p2', status: 'disabled' }),
          makePlugin({ id: 'p3', status: 'enabled' }),
        ] as never,
      })
      expect(store.enabledPlugins).toHaveLength(2)
      expect(store.enabledPlugins[0].id).toBe('p1')
      expect(store.enabledPlugins[1].id).toBe('p3')
    })

    it('disabledPlugins 过滤出 status=disabled 的插件', () => {
      const store = usePluginStore()
      store.$patch({
        plugins: [
          makePlugin({ id: 'p1', status: 'enabled' }),
          makePlugin({ id: 'p2', status: 'disabled' }),
        ] as never,
      })
      expect(store.disabledPlugins).toHaveLength(1)
      expect(store.disabledPlugins[0].id).toBe('p2')
    })

    it('adapterPlugins 过滤出 plugin_type=adapter 的插件', () => {
      const store = usePluginStore()
      store.$patch({
        plugins: [
          makePlugin({ id: 'p1', plugin_type: 'adapter' }),
          makePlugin({ id: 'p2', plugin_type: 'data_source' }),
          makePlugin({ id: 'p3', plugin_type: 'adapter' }),
        ] as never,
      })
      expect(store.adapterPlugins).toHaveLength(2)
    })

    it('dataSourcePlugins 过滤出 plugin_type=data_source 的插件', () => {
      const store = usePluginStore()
      store.$patch({
        plugins: [
          makePlugin({ id: 'p1', plugin_type: 'data_source' }),
          makePlugin({ id: 'p2', plugin_type: 'adapter' }),
        ] as never,
      })
      expect(store.dataSourcePlugins).toHaveLength(1)
      expect(store.dataSourcePlugins[0].id).toBe('p1')
    })

    it('analyzerPlugins 过滤出 plugin_type=analyzer 的插件', () => {
      const store = usePluginStore()
      store.$patch({
        plugins: [
          makePlugin({ id: 'p1', plugin_type: 'analyzer' }),
          makePlugin({ id: 'p2', plugin_type: 'adapter' }),
        ] as never,
      })
      expect(store.analyzerPlugins).toHaveLength(1)
    })

    it('visualizationPlugins 过滤出 plugin_type=visualization 的插件', () => {
      const store = usePluginStore()
      store.$patch({
        plugins: [
          makePlugin({ id: 'p1', plugin_type: 'visualization' }),
          makePlugin({ id: 'p2', plugin_type: 'adapter' }),
        ] as never,
      })
      expect(store.visualizationPlugins).toHaveLength(1)
    })
  })

  describe('fetchPlugins', () => {
    it('成功获取插件列表', async () => {
      const store = usePluginStore()
      const plugins = [makePlugin({ id: 'p1' }), makePlugin({ id: 'p2' })]
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { plugins } },
      })

      await store.fetchPlugins()

      expect(store.plugins).toEqual(plugins)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('网络错误时设置 error', async () => {
      const store = usePluginStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      await store.fetchPlugins()

      expect(store.error).toBe('Network Error')
      expect(store.loading).toBe(false)
      expect(store.plugins).toEqual([])
    })

    it('请求过程中 loading 为 true', async () => {
      const store = usePluginStore()
      let resolveFn: (val: unknown) => void
      ;(http.get as ReturnType<typeof vi.fn>).mockReturnValue(
        new Promise((resolve) => {
          resolveFn = resolve
        }),
      )

      const promise = store.fetchPlugins()
      expect(store.loading).toBe(true)

      resolveFn!({ data: { data: { plugins: [] } } })
      await promise

      expect(store.loading).toBe(false)
    })
  })

  describe('fetchPluginDetail', () => {
    it('成功获取插件详情', async () => {
      const store = usePluginStore()
      const detail = makePluginDetail()
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: detail },
      })

      await store.fetchPluginDetail('p1')

      expect(store.currentPlugin).toEqual(detail)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('网络错误时设置 error', async () => {
      const store = usePluginStore()
      ;(http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network Error'))

      await store.fetchPluginDetail('p1')

      expect(store.error).toBe('Network Error')
      expect(store.loading).toBe(false)
      expect(store.currentPlugin).toBeNull()
    })
  })

  describe('enablePlugin', () => {
    it('成功启用插件并刷新列表', async () => {
      const store = usePluginStore()
      const plugins = [makePlugin({ id: 'p1', status: 'disabled' })]
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { plugins } },
      })

      await store.enablePlugin('p1')

      expect(http.post).toHaveBeenCalled()
      expect(http.get).toHaveBeenCalled()
      expect(store.plugins).toEqual(plugins)
      expect(store.loading).toBe(false)
    })

    it('启用失败时设置 error', async () => {
      const store = usePluginStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('启用失败'))

      await store.enablePlugin('p1')

      expect(store.error).toBe('启用失败')
      expect(store.loading).toBe(false)
    })

    it('启用失败时不调用 fetchPlugins', async () => {
      const store = usePluginStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('fail'))

      await store.enablePlugin('p1')

      expect(http.get).not.toHaveBeenCalled()
    })
  })

  describe('disablePlugin', () => {
    it('成功禁用插件并刷新列表', async () => {
      const store = usePluginStore()
      const plugins = [makePlugin({ id: 'p1', status: 'enabled' })]
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { plugins } },
      })

      await store.disablePlugin('p1')

      expect(http.post).toHaveBeenCalled()
      expect(store.plugins).toEqual(plugins)
      expect(store.loading).toBe(false)
    })

    it('禁用失败时设置 error', async () => {
      const store = usePluginStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('禁用失败'))

      await store.disablePlugin('p1')

      expect(store.error).toBe('禁用失败')
      expect(store.loading).toBe(false)
    })
  })

  describe('uninstallPlugin', () => {
    it('成功卸载插件并刷新列表', async () => {
      const store = usePluginStore()
      const plugins = [makePlugin({ id: 'p2' })]
      ;(http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({})
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { plugins } },
      })

      await store.uninstallPlugin('p1')

      expect(http.delete).toHaveBeenCalled()
      expect(store.plugins).toEqual(plugins)
      expect(store.loading).toBe(false)
    })

    it('卸载失败时设置 error', async () => {
      const store = usePluginStore()
      ;(http.delete as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('卸载失败'))

      await store.uninstallPlugin('p1')

      expect(store.error).toBe('卸载失败')
      expect(store.loading).toBe(false)
    })

    it('卸载失败时不调用 fetchPlugins', async () => {
      const store = usePluginStore()
      ;(http.delete as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('fail'))

      await store.uninstallPlugin('p1')

      expect(http.get).not.toHaveBeenCalled()
    })
  })

  describe('updatePluginConfig', () => {
    it('成功更新配置并刷新列表', async () => {
      const store = usePluginStore()
      const config = { key: 'value' }
      const plugins = [makePlugin({ id: 'p1' })]
      ;(http.put as ReturnType<typeof vi.fn>).mockResolvedValue({})
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { plugins } },
      })

      await store.updatePluginConfig('p1', config)

      expect(http.put).toHaveBeenCalledWith(expect.any(String), config)
      expect(store.plugins).toEqual(plugins)
      expect(store.loading).toBe(false)
    })

    it('更新失败时设置 error', async () => {
      const store = usePluginStore()
      ;(http.put as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('更新失败'))

      await store.updatePluginConfig('p1', {})

      expect(store.error).toBe('更新失败')
      expect(store.loading).toBe(false)
    })
  })

  describe('reloadPlugin', () => {
    it('成功重载插件并刷新列表', async () => {
      const store = usePluginStore()
      const plugins = [makePlugin({ id: 'p1' })]
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { plugins } },
      })

      await store.reloadPlugin('p1')

      expect(http.post).toHaveBeenCalled()
      expect(store.plugins).toEqual(plugins)
      expect(store.loading).toBe(false)
    })

    it('重载失败时设置 error', async () => {
      const store = usePluginStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('重载失败'))

      await store.reloadPlugin('p1')

      expect(store.error).toBe('重载失败')
      expect(store.loading).toBe(false)
    })

    it('重载失败时不调用 fetchPlugins', async () => {
      const store = usePluginStore()
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('fail'))

      await store.reloadPlugin('p1')

      expect(http.get).not.toHaveBeenCalled()
    })
  })
})
