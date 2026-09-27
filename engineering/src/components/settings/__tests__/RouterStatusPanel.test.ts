import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import RouterStatusPanel from '@/components/settings/RouterStatusPanel.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, any>) => {
      if (params) return `${key}:${JSON.stringify(params)}`
      return key
    },
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Share: { name: 'Share', render: () => null },
  Refresh: { name: 'Refresh', render: () => null },
  Right: { name: 'Right', render: () => null },
}))

// Mock @/api/llmProviders
const mockRoutingStrategyMeta = vi.hoisted(() => ([
  {
    value: 'round_robin',
    label: '轮询',
    description: '轮询策略描述',
  },
  {
    value: 'priority',
    label: '优先级',
    description: '优先级策略描述',
  },
  {
    value: 'latency',
    label: '延迟优先',
    description: '延迟优先策略描述',
  },
]))
vi.mock('@/api/llmProviders', () => ({
  ROUTING_STRATEGY_META: mockRoutingStrategyMeta,
}))

// Mock @/stores/llmProviders
const mockStore = vi.hoisted(() => ({
  loading: false,
  routerStatus: null as any,
  refreshStatus: vi.fn(),
}))
vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => mockStore,
}))

const buildRouterStatus = (overrides: Partial<any> = {}) => ({
  current_strategy: 'round_robin',
  active_provider_id: 'provider-1',
  available_providers: 3,
  total_latency_samples: 100,
  cache_hit_rate: 0.5,
  fallback_chain: ['provider-1', 'provider-2'],
  ...overrides,
})

describe('RouterStatusPanel.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.loading = false
    mockStore.routerStatus = null
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = () => {
    wrapper = mount(RouterStatusPanel, {
      global: {
        stubs: {
          'el-icon': { template: '<span><slot /></span>' },
          'el-button': {
            template: '<button @click="$emit(\'click\')"><slot /></button>',
            emits: ['click'],
          },
          'el-empty': { template: '<div class="empty" />' },
          'el-tag': { template: '<span class="tag"><slot /></span>' },
          'el-progress': { template: '<div class="progress" />' },
          'el-alert': { template: '<div class="alert"><slot /></div>' },
          'el-select': { template: '<select><slot /></select>' },
          'el-option': { template: '<option />' },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
    })

    it('应渲染根容器 content-card', () => {
      mountComponent()
      expect(wrapper.find('.content-card').exists()).toBe(true)
    })

    it('应渲染标题', () => {
      mountComponent()
      expect(wrapper.find('.content-card__title').exists()).toBe(true)
      expect(wrapper.find('.content-card__title').text()).toContain('settings.routerStatus.title')
    })

    it('应渲染刷新按钮', () => {
      mountComponent()
      expect(wrapper.find('.header-actions').exists()).toBe(true)
    })

    it('无 routerStatus 时应渲染空状态', () => {
      mountComponent()
      expect(wrapper.find('.empty-state').exists()).toBe(true)
    })

    it('有 routerStatus 时不应渲染空状态', () => {
      mockStore.routerStatus = buildRouterStatus()
      mountComponent()
      expect(wrapper.find('.empty-state').exists()).toBe(false)
    })

    it('有 routerStatus 时应渲染状态网格', () => {
      mockStore.routerStatus = buildRouterStatus()
      mountComponent()
      expect(wrapper.find('.status-grid').exists()).toBe(true)
    })

    it('有 routerStatus 时应渲染策略说明', () => {
      mockStore.routerStatus = buildRouterStatus()
      mountComponent()
      expect(wrapper.find('.strategy-alert').exists()).toBe(true)
    })

    it('有 routerStatus 时应渲染策略切换器', () => {
      mockStore.routerStatus = buildRouterStatus()
      mountComponent()
      expect(wrapper.find('.strategy-switcher').exists()).toBe(true)
    })
  })

  describe('初始状态', () => {
    it('selectedStrategy 初始值应为空字符串', () => {
      mountComponent()
      expect(wrapper.vm.selectedStrategy).toBe('')
    })

    it('strategyOptions 应返回 ROUTING_STRATEGY_META', () => {
      mountComponent()
      expect(wrapper.vm.strategyOptions.length).toBe(3)
    })
  })

  describe('watch routerStatus.current_strategy', () => {
    it('routerStatus 有策略时应更新 selectedStrategy', async () => {
      mockStore.routerStatus = buildRouterStatus({ current_strategy: 'priority' })
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.selectedStrategy).toBe('priority')
    })

    it('routerStatus 为 null 时不应更新 selectedStrategy', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.selectedStrategy).toBe('')
    })
  })

  describe('strategyMeta 计算属性', () => {
    it('无 current_strategy 时应返回默认值', () => {
      mountComponent()
      expect(wrapper.vm.strategyMeta.label).toBe('-')
      expect(wrapper.vm.strategyMeta.description).toBe('settings.routerStatus.notLoadedYet')
    })

    it('有 current_strategy 时应返回对应元数据', () => {
      mockStore.routerStatus = buildRouterStatus({ current_strategy: 'round_robin' })
      mountComponent()
      expect(wrapper.vm.strategyMeta.label).toBe('轮询')
      expect(wrapper.vm.strategyMeta.description).toBe('轮询策略描述')
    })

    it('未知策略应返回未知策略描述', () => {
      mockStore.routerStatus = buildRouterStatus({ current_strategy: 'unknown' as any })
      mountComponent()
      expect(wrapper.vm.strategyMeta.label).toBe('unknown')
      expect(wrapper.vm.strategyMeta.description).toBe('settings.routerStatus.unknownStrategy')
    })
  })

  describe('cacheHitStatus 计算属性', () => {
    it('cache_hit_rate >= 0.7 时应返回 success', () => {
      mockStore.routerStatus = buildRouterStatus({ cache_hit_rate: 0.7 })
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('success')
    })

    it('cache_hit_rate = 0.8 时应返回 success', () => {
      mockStore.routerStatus = buildRouterStatus({ cache_hit_rate: 0.8 })
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('success')
    })

    it('cache_hit_rate 0.3-0.69 时应返回 warning', () => {
      mockStore.routerStatus = buildRouterStatus({ cache_hit_rate: 0.5 })
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('warning')
    })

    it('cache_hit_rate = 0.3 时应返回 warning', () => {
      mockStore.routerStatus = buildRouterStatus({ cache_hit_rate: 0.3 })
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('warning')
    })

    it('cache_hit_rate < 0.3 时应返回 exception', () => {
      mockStore.routerStatus = buildRouterStatus({ cache_hit_rate: 0.2 })
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('exception')
    })

    it('cache_hit_rate = 0 时应返回 exception', () => {
      mockStore.routerStatus = buildRouterStatus({ cache_hit_rate: 0 })
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('exception')
    })

    it('routerStatus 为 null 时应返回 exception', () => {
      mountComponent()
      expect(wrapper.vm.cacheHitStatus).toBe('exception')
    })
  })

  describe('strategyLabel 方法', () => {
    it('已知策略应返回对应 label', () => {
      mountComponent()
      expect(wrapper.vm.strategyLabel('round_robin')).toBe('轮询')
    })

    it('未知策略应返回策略值本身', () => {
      mountComponent()
      expect(wrapper.vm.strategyLabel('unknown' as any)).toBe('unknown')
    })
  })

  describe('onStrategyChange 方法', () => {
    it('无 routerStatus 时不应修改 selectedStrategy', () => {
      mountComponent()
      wrapper.vm.selectedStrategy = 'priority'
      wrapper.vm.onStrategyChange('priority')
      // 无 routerStatus，selectedStrategy 保持
      expect(wrapper.vm.selectedStrategy).toBe('priority')
    })

    it('有 routerStatus 时应还原 selectedStrategy 为当前策略', () => {
      mockStore.routerStatus = buildRouterStatus({ current_strategy: 'round_robin' })
      mountComponent()
      wrapper.vm.selectedStrategy = 'priority'
      wrapper.vm.onStrategyChange('priority')
      expect(wrapper.vm.selectedStrategy).toBe('round_robin')
    })

    it('有 routerStatus 但 selectedStrategy 为空时不应还原', () => {
      mockStore.routerStatus = buildRouterStatus({ current_strategy: 'round_robin' })
      mountComponent()
      wrapper.vm.selectedStrategy = ''
      wrapper.vm.onStrategyChange('')
      expect(wrapper.vm.selectedStrategy).toBe('')
    })
  })

  describe('刷新按钮', () => {
    it('点击刷新按钮应调用 store.refreshStatus', async () => {
      mountComponent()
      const button = wrapper.find('.header-actions button')
      await button.trigger('click')
      expect(mockStore.refreshStatus).toHaveBeenCalled()
    })
  })

  describe('回退链渲染', () => {
    it('有 fallback_chain 时应渲染对应数量的标签', () => {
      mockStore.routerStatus = buildRouterStatus({ fallback_chain: ['p1', 'p2', 'p3'] })
      mountComponent()
      const tags = wrapper.findAll('.fallback-tag')
      expect(tags.length).toBe(3)
    })

    it('fallback_chain 为空时应渲染空文本', () => {
      mockStore.routerStatus = buildRouterStatus({ fallback_chain: [] })
      mountComponent()
      expect(wrapper.find('.empty-text').exists()).toBe(true)
    })
  })

  describe('活跃供应商渲染', () => {
    it('有 active_provider_id 时应渲染供应商 ID', () => {
      mockStore.routerStatus = buildRouterStatus({ active_provider_id: 'provider-1' })
      mountComponent()
      const monoSpans = wrapper.findAll('.mono')
      // 至少有 active_provider_id 的 mono
      expect(monoSpans.length).toBeGreaterThan(0)
    })

    it('无 active_provider_id 时应渲染未激活文本', () => {
      mockStore.routerStatus = buildRouterStatus({ active_provider_id: '' })
      mountComponent()
      expect(wrapper.find('.empty-text').exists()).toBe(true)
    })
  })
})
