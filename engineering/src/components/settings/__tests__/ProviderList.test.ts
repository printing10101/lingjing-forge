import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import ProviderList from '@/components/settings/ProviderList.vue'

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
  CircleCheck: { name: 'CircleCheck', render: () => null },
  ArrowDown: { name: 'ArrowDown', render: () => null },
}))

// Mock @/api/llmProviders
const mockProviderTypeMeta = vi.hoisted(() => ({
  ollama: {
    value: 'ollama',
    label: 'Ollama',
    description: '本地 Ollama',
    category: 'local',
    default_base_url: 'http://localhost:11434',
    default_capabilities: ['chat'],
    needs_api_key: false,
  },
  openai: {
    value: 'openai',
    label: 'OpenAI',
    description: 'OpenAI 云服务',
    category: 'cloud',
    default_base_url: 'https://api.openai.com/v1',
    default_capabilities: ['chat'],
    needs_api_key: true,
  },
}))
vi.mock('@/api/llmProviders', () => ({
  PROVIDER_TYPE_META: mockProviderTypeMeta,
}))

// Mock @/stores/llmProviders
const mockStore = vi.hoisted(() => ({
  providers: [] as any[],
  healthChecking: {} as Record<string, boolean>,
}))
vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => mockStore,
}))

const buildProvider = (overrides: Partial<any> = {}) => ({
  provider_id: 'p1',
  name: '供应商1',
  provider_type: 'ollama',
  base_url: 'http://localhost:11434',
  api_key: '',
  api_key_set: false,
  default_model: 'llama2',
  capabilities: ['chat'],
  priority: 0,
  timeout: 60,
  max_retries: 3,
  retry_delay: 1.0,
  enabled: true,
  is_active: false,
  last_health_status: null,
  last_latency_ms: null,
  created_at: '',
  updated_at: '',
  ...overrides,
})

describe('ProviderList.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    mockStore.providers = []
    mockStore.healthChecking = {}
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, any> = {}) => {
    wrapper = mount(ProviderList, {
      props: {
        loading: false,
        ...props,
      },
      global: {
        stubs: {
          'el-alert': { template: '<div class="alert"><slot /></div>' },
          'el-table': { template: '<table class="table"><slot /></table>' },
          'el-table-column': { template: '<td class="col"><slot name="default" :row="row" /></td>', data() { return { row: {} } } },
          'el-tag': { template: '<span class="tag"><slot /></span>' },
          'el-icon': { template: '<span><slot /></span>' },
          'el-button': {
            template: '<button @click="$emit(\'click\')"><slot /></button>',
            emits: ['click'],
          },
          'el-dropdown': { template: '<div class="dropdown"><slot /><slot name="dropdown" /></div>', emits: ['command'] },
          'el-dropdown-menu': { template: '<div class="dropdown-menu"><slot /></div>' },
          'el-dropdown-item': {
            template: '<div class="dropdown-item" @click="$emit(\'command\', command)"><slot /></div>',
            props: ['command'],
            emits: ['command'],
          },
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

    it('应渲染根容器', () => {
      mountComponent()
      expect(wrapper.find('.provider-list').exists()).toBe(true)
    })

    it('无供应商且非加载中时应渲染空状态提示', () => {
      mountComponent({ loading: false })
      expect(wrapper.find('.el-alert').exists()).toBe(true)
    })

    it('有供应商时不应渲染空状态提示', () => {
      mockStore.providers = [buildProvider()]
      mountComponent({ loading: false })
      expect(wrapper.find('.el-alert').exists()).toBe(false)
    })

    it('加载中且有供应商时不应渲染空状态提示', () => {
      mockStore.providers = []
      mountComponent({ loading: true })
      expect(wrapper.find('.alert').exists()).toBe(false)
    })
  })

  describe('计算属性', () => {
    it('providers 应返回 store.providers', () => {
      mockStore.providers = [buildProvider(), buildProvider({ provider_id: 'p2' })]
      mountComponent()
      expect(wrapper.vm.providers.length).toBe(2)
    })

    it('healthChecking 应返回 store.healthChecking', () => {
      mockStore.healthChecking = { p1: true }
      mountComponent()
      expect(wrapper.vm.healthChecking.p1).toBe(true)
    })
  })

  describe('getTypeLabel 方法', () => {
    it('已知类型应返回对应 label', () => {
      mountComponent()
      expect(wrapper.vm.getTypeLabel('ollama')).toBe('Ollama')
    })

    it('未知类型应返回类型字符串本身', () => {
      mountComponent()
      expect(wrapper.vm.getTypeLabel('unknown' as any)).toBe('unknown')
    })
  })

  describe('getCategory 方法', () => {
    it('ollama 应返回 local', () => {
      mountComponent()
      expect(wrapper.vm.getCategory('ollama')).toBe('local')
    })

    it('openai 应返回 cloud', () => {
      mountComponent()
      expect(wrapper.vm.getCategory('openai')).toBe('cloud')
    })

    it('未知类型应返回 cloud', () => {
      mountComponent()
      expect(wrapper.vm.getCategory('unknown' as any)).toBe('cloud')
    })
  })

  describe('getTypeTagType 方法', () => {
    it('local 类型应返回 success', () => {
      mountComponent()
      expect(wrapper.vm.getTypeTagType('ollama')).toBe('success')
    })

    it('cloud 类型应返回 warning', () => {
      mountComponent()
      expect(wrapper.vm.getTypeTagType('openai')).toBe('warning')
    })
  })

  describe('getLatencyClass 方法', () => {
    it('延迟 < 500 应返回 latency-good', () => {
      mountComponent()
      expect(wrapper.vm.getLatencyClass(100)).toBe('latency-good')
    })

    it('延迟 = 499 应返回 latency-good', () => {
      mountComponent()
      expect(wrapper.vm.getLatencyClass(499)).toBe('latency-good')
    })

    it('延迟 500-1999 应返回 latency-warn', () => {
      mountComponent()
      expect(wrapper.vm.getLatencyClass(500)).toBe('latency-warn')
    })

    it('延迟 1999 应返回 latency-warn', () => {
      mountComponent()
      expect(wrapper.vm.getLatencyClass(1999)).toBe('latency-warn')
    })

    it('延迟 >= 2000 应返回 latency-bad', () => {
      mountComponent()
      expect(wrapper.vm.getLatencyClass(2000)).toBe('latency-bad')
    })

    it('延迟 5000 应返回 latency-bad', () => {
      mountComponent()
      expect(wrapper.vm.getLatencyClass(5000)).toBe('latency-bad')
    })
  })

  describe('handleCommand 方法', () => {
    it('edit 命令应触发 edit 事件', () => {
      mountComponent()
      const provider = buildProvider()
      wrapper.vm.handleCommand('edit', provider)
      expect(wrapper.emitted('edit')).toBeTruthy()
      expect(wrapper.emitted('edit')![0]).toEqual([provider])
    })

    it('models 命令应触发 view-models 事件', () => {
      mountComponent()
      const provider = buildProvider()
      wrapper.vm.handleCommand('models', provider)
      expect(wrapper.emitted('view-models')).toBeTruthy()
      expect(wrapper.emitted('view-models')![0]).toEqual([provider])
    })

    it('test 命令应触发 test 事件', () => {
      mountComponent()
      const provider = buildProvider()
      wrapper.vm.handleCommand('test', provider)
      expect(wrapper.emitted('test')).toBeTruthy()
      expect(wrapper.emitted('test')![0]).toEqual([provider])
    })

    it('delete 命令应触发 delete 事件', () => {
      mountComponent()
      const provider = buildProvider()
      wrapper.vm.handleCommand('delete', provider)
      expect(wrapper.emitted('delete')).toBeTruthy()
      expect(wrapper.emitted('delete')![0]).toEqual([provider])
    })

    it('未知命令不应触发任何事件', () => {
      mountComponent()
      const provider = buildProvider()
      wrapper.vm.handleCommand('unknown', provider)
      expect(wrapper.emitted('edit')).toBeFalsy()
      expect(wrapper.emitted('test')).toBeFalsy()
      expect(wrapper.emitted('delete')).toBeFalsy()
      expect(wrapper.emitted('view-models')).toBeFalsy()
    })
  })
})
