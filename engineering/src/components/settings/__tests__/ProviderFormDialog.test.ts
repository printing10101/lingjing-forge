import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import ProviderFormDialog from '@/components/settings/ProviderFormDialog.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, any>) => {
      if (params) return `${key}:${JSON.stringify(params)}`
      return key
    },
  }),
}))

// Mock @/api/llmProviders
const mockProviderTypeMeta = vi.hoisted(() => ({
  ollama: {
    value: 'ollama',
    label: 'Ollama',
    description: '本地 Ollama 服务',
    category: 'local',
    default_base_url: 'http://localhost:11434',
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: false,
  },
  lmstudio: {
    value: 'lmstudio',
    label: 'LM Studio',
    description: '本地 LM Studio',
    category: 'local',
    default_base_url: 'http://localhost:1234',
    default_capabilities: ['chat'],
    needs_api_key: false,
  },
  openai: {
    value: 'openai',
    label: 'OpenAI',
    description: 'OpenAI 云服务',
    category: 'cloud',
    default_base_url: 'https://api.openai.com/v1',
    default_capabilities: ['chat', 'streaming', 'function_calling'],
    needs_api_key: true,
  },
  anthropic: {
    value: 'anthropic',
    label: 'Anthropic',
    description: 'Anthropic 云服务',
    category: 'cloud',
    default_base_url: 'https://api.anthropic.com',
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: true,
  },
}))
vi.mock('@/api/llmProviders', () => ({
  PROVIDER_TYPE_META: mockProviderTypeMeta,
}))

// Mock @/stores/llmProviders
const mockStore = vi.hoisted(() => ({
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
}))
vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => mockStore,
}))

const mockProvider = vi.hoisted(() => ({
  provider_id: 'test-1',
  name: '测试供应商',
  provider_type: 'ollama',
  base_url: 'http://localhost:11434',
  api_key: '',
  default_model: 'llama2',
  capabilities: ['chat'],
  priority: 10,
  timeout: 60,
  max_retries: 3,
  retry_delay: 1.0,
  enabled: true,
  created_at: '',
  updated_at: '',
}))

describe('ProviderFormDialog.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.createProvider.mockResolvedValue(null)
    mockStore.updateProvider.mockResolvedValue(null)
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, any> = {}) => {
    wrapper = mount(ProviderFormDialog, {
      props: {
        visible: true,
        mode: 'create',
        provider: null,
        ...props,
      },
      global: {
        stubs: {
          // 注意：必须用 PascalCase 键（ElDialog/ElForm/...）才能覆盖 setup.ts 的全局 stub
          ElDialog: {
            template: '<div><slot /><slot name="footer" /></div>',
            props: ['modelValue', 'title', 'width', 'closeOnClickModal'],
            emits: ['update:modelValue', 'open'],
          },
          ElForm: { template: '<form><slot /></form>' },
          ElFormItem: { template: '<div class="form-item"><slot /></div>' },
          ElInput: { template: '<input />' },
          ElSelect: { template: '<select><slot /></select>' },
          ElOptionGroup: { template: '<optgroup><slot /></optgroup>' },
          ElOption: { template: '<option />' },
          ElButton: {
            template: '<button @click="$emit(\'click\')"><slot /></button>',
            emits: ['click'],
          },
          ElSlider: { template: '<input type="range" />' },
          ElInputNumber: { template: '<input type="number" />' },
          ElSwitch: { template: '<button class="switch" />' },
          ElDivider: { template: '<hr />' },
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

    it('应渲染表单', () => {
      mountComponent()
      expect(wrapper.find('form').exists()).toBe(true)
    })

    it('应渲染多个表单项', () => {
      mountComponent()
      expect(wrapper.findAll('.form-item').length).toBeGreaterThan(5)
    })

    it('应渲染取消和保存按钮', () => {
      mountComponent()
      const buttons = wrapper.findAll('button')
      // 至少有取消、保存按钮（可能还有 switch）
      expect(buttons.length).toBeGreaterThanOrEqual(2)
    })
  })

  describe('初始状态', () => {
    it('saving 初始值应为 false', () => {
      mountComponent()
      expect(wrapper.vm.saving).toBe(false)
    })

    it('form 初始值应正确', () => {
      mountComponent()
      expect(wrapper.vm.form.provider_id).toBe('')
      expect(wrapper.vm.form.name).toBe('')
      expect(wrapper.vm.form.provider_type).toBe('')
      expect(wrapper.vm.form.base_url).toBe('')
      expect(wrapper.vm.form.api_key).toBe('')
      expect(wrapper.vm.form.default_model).toBe('')
      expect(wrapper.vm.form.capabilities).toEqual(['chat'])
      expect(wrapper.vm.form.priority).toBe(0)
      expect(wrapper.vm.form.timeout).toBe(60)
      expect(wrapper.vm.form.max_retries).toBe(3)
      expect(wrapper.vm.form.retry_delay).toBe(1.0)
      expect(wrapper.vm.form.enabled).toBe(true)
    })
  })

  describe('localTypes 计算属性', () => {
    it('应返回 category 为 local 的类型', () => {
      mountComponent()
      const types = wrapper.vm.localTypes
      expect(types.length).toBe(2)
      expect(types[0].category).toBe('local')
      expect(types[1].category).toBe('local')
    })
  })

  describe('cloudTypes 计算属性', () => {
    it('应返回 category 为 cloud 的类型', () => {
      mountComponent()
      const types = wrapper.vm.cloudTypes
      expect(types.length).toBe(2)
      expect(types[0].category).toBe('cloud')
      expect(types[1].category).toBe('cloud')
    })
  })

  describe('selectedMeta 计算属性', () => {
    it('未选择类型时应返回 null', () => {
      mountComponent()
      expect(wrapper.vm.selectedMeta).toBeNull()
    })

    it('选择类型后应返回对应元数据', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'ollama'
      expect(wrapper.vm.selectedMeta).not.toBeNull()
      expect(wrapper.vm.selectedMeta.value).toBe('ollama')
    })

    it('选择未知类型时应返回 null', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'unknown'
      expect(wrapper.vm.selectedMeta).toBeNull()
    })
  })

  describe('needsApiKey 计算属性', () => {
    it('未选择类型时应为 false', () => {
      mountComponent()
      expect(wrapper.vm.needsApiKey).toBe(false)
    })

    it('选择 Ollama 时应为 false', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'ollama'
      expect(wrapper.vm.needsApiKey).toBe(false)
    })

    it('选择 OpenAI 时应为 true', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'openai'
      expect(wrapper.vm.needsApiKey).toBe(true)
    })
  })

  describe('urlPlaceholder 计算属性', () => {
    it('未选择类型时应返回默认占位符', () => {
      mountComponent()
      expect(wrapper.vm.urlPlaceholder).toBe('https://...')
    })

    it('选择类型后应返回对应默认 base_url', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'ollama'
      expect(wrapper.vm.urlPlaceholder).toBe('http://localhost:11434')
    })

    it('类型无默认 base_url 时应返回 https://...', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'unknown'
      expect(wrapper.vm.urlPlaceholder).toBe('https://...')
    })
  })

  describe('urlTip 计算属性', () => {
    it('未选择类型时应返回默认提示', () => {
      mountComponent()
      expect(wrapper.vm.urlTip).toBe('providerFormDialog.tipBaseUrlDefault')
    })

    it('选择类型后应返回类型描述', () => {
      mountComponent()
      wrapper.vm.form.provider_type = 'ollama'
      expect(wrapper.vm.urlTip).toBe('本地 Ollama 服务')
    })
  })

  describe('onTypeChange 方法', () => {
    it('空类型时应直接返回', () => {
      mountComponent()
      wrapper.vm.form.base_url = 'existing'
      wrapper.vm.onTypeChange('')
      expect(wrapper.vm.form.base_url).toBe('existing')
    })

    it('未知类型时应直接返回', () => {
      mountComponent()
      wrapper.vm.onTypeChange('unknown' as any)
      // 不应抛错
      expect(wrapper.vm.form.base_url).toBe('')
    })

    it('base_url 为空时应填充默认 base_url', () => {
      mountComponent()
      wrapper.vm.form.base_url = ''
      wrapper.vm.onTypeChange('ollama')
      expect(wrapper.vm.form.base_url).toBe('http://localhost:11434')
    })

    it('base_url 已有值时不应覆盖', () => {
      mountComponent()
      wrapper.vm.form.base_url = 'http://custom:8080'
      wrapper.vm.onTypeChange('ollama')
      expect(wrapper.vm.form.base_url).toBe('http://custom:8080')
    })

    it('capabilities 为空时应填充默认 capabilities', () => {
      mountComponent()
      wrapper.vm.form.capabilities = []
      wrapper.vm.onTypeChange('ollama')
      expect(wrapper.vm.form.capabilities).toEqual(['chat', 'streaming'])
    })

    it('capabilities 仅含 chat 时应填充默认 capabilities', () => {
      mountComponent()
      wrapper.vm.form.capabilities = ['chat']
      wrapper.vm.onTypeChange('ollama')
      expect(wrapper.vm.form.capabilities).toEqual(['chat', 'streaming'])
    })

    it('capabilities 含其他值时不应覆盖', () => {
      mountComponent()
      wrapper.vm.form.capabilities = ['vision']
      wrapper.vm.onTypeChange('ollama')
      expect(wrapper.vm.form.capabilities).toEqual(['vision'])
    })
  })

  describe('handleOpen 方法', () => {
    it('edit 模式且有 provider 时应填充表单', () => {
      mountComponent({ mode: 'edit', provider: mockProvider })
      wrapper.vm.handleOpen()
      expect(wrapper.vm.form.provider_id).toBe('test-1')
      expect(wrapper.vm.form.name).toBe('测试供应商')
      expect(wrapper.vm.form.provider_type).toBe('ollama')
      expect(wrapper.vm.form.base_url).toBe('http://localhost:11434')
      expect(wrapper.vm.form.api_key).toBe('')
      expect(wrapper.vm.form.default_model).toBe('llama2')
      expect(wrapper.vm.form.capabilities).toEqual(['chat'])
      expect(wrapper.vm.form.priority).toBe(10)
      expect(wrapper.vm.form.timeout).toBe(60)
      expect(wrapper.vm.form.max_retries).toBe(3)
      expect(wrapper.vm.form.retry_delay).toBe(1.0)
      expect(wrapper.vm.form.enabled).toBe(true)
    })

    it('edit 模式 api_key 应清空', () => {
      mountComponent({ mode: 'edit', provider: { ...mockProvider, api_key: 'secret' } })
      wrapper.vm.handleOpen()
      expect(wrapper.vm.form.api_key).toBe('')
    })

    it('create 模式应重置表单', () => {
      mountComponent({ mode: 'create', provider: null })
      // 先填充一些值
      wrapper.vm.form.provider_id = 'temp'
      wrapper.vm.form.name = 'temp'
      wrapper.vm.handleOpen()
      expect(wrapper.vm.form.provider_id).toBe('')
      expect(wrapper.vm.form.name).toBe('')
      expect(wrapper.vm.form.provider_type).toBe('')
      expect(wrapper.vm.form.capabilities).toEqual(['chat'])
      expect(wrapper.vm.form.priority).toBe(0)
      expect(wrapper.vm.form.timeout).toBe(60)
      expect(wrapper.vm.form.enabled).toBe(true)
    })
  })

  describe('handleSave 方法', () => {
    it('无 formRef 时应直接返回', async () => {
      mountComponent()
      wrapper.vm.formRef = null
      await wrapper.vm.handleSave()
      expect(mockStore.createProvider).not.toHaveBeenCalled()
    })

    it('provider_type 为空时应直接返回', async () => {
      mountComponent()
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_type = ''
      await wrapper.vm.handleSave()
      expect(mockStore.createProvider).not.toHaveBeenCalled()
    })

    it('create 模式应调用 store.createProvider', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'new-1'
      wrapper.vm.form.name = '新供应商'
      wrapper.vm.form.provider_type = 'ollama'
      mockStore.createProvider.mockResolvedValue({ provider_id: 'new-1' })
      await wrapper.vm.handleSave()
      expect(mockStore.createProvider).toHaveBeenCalledWith(expect.objectContaining({
        provider_id: 'new-1',
        name: '新供应商',
        provider_type: 'ollama',
      }))
    })

    it('edit 模式应调用 store.updateProvider', async () => {
      mountComponent({ mode: 'edit', provider: mockProvider })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'test-1'
      wrapper.vm.form.name = '更新名称'
      wrapper.vm.form.provider_type = 'ollama'
      mockStore.updateProvider.mockResolvedValue({ provider_id: 'test-1' })
      await wrapper.vm.handleSave()
      expect(mockStore.updateProvider).toHaveBeenCalledWith('test-1', expect.objectContaining({
        provider_id: 'test-1',
        name: '更新名称',
      }))
    })

    it('api_key 有值时 payload 应包含 api_key', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'new-1'
      wrapper.vm.form.name = '新供应商'
      wrapper.vm.form.provider_type = 'openai'
      wrapper.vm.form.api_key = 'sk-secret'
      mockStore.createProvider.mockResolvedValue({ provider_id: 'new-1' })
      await wrapper.vm.handleSave()
      const payload = mockStore.createProvider.mock.calls[0][0]
      expect(payload.api_key).toBe('sk-secret')
    })

    it('api_key 为空时 payload 不应包含 api_key', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'new-1'
      wrapper.vm.form.name = '新供应商'
      wrapper.vm.form.provider_type = 'ollama'
      wrapper.vm.form.api_key = ''
      mockStore.createProvider.mockResolvedValue({ provider_id: 'new-1' })
      await wrapper.vm.handleSave()
      const payload = mockStore.createProvider.mock.calls[0][0]
      expect(payload.api_key).toBeUndefined()
    })

    it('create 成功时应触发 saved 事件', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'new-1'
      wrapper.vm.form.name = '新供应商'
      wrapper.vm.form.provider_type = 'ollama'
      mockStore.createProvider.mockResolvedValue({ provider_id: 'new-1' })
      await wrapper.vm.handleSave()
      expect(wrapper.emitted('saved')).toBeTruthy()
    })

    it('create 返回 null 时不应触发 saved 事件', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'new-1'
      wrapper.vm.form.name = '新供应商'
      wrapper.vm.form.provider_type = 'ollama'
      mockStore.createProvider.mockResolvedValue(null)
      await wrapper.vm.handleSave()
      expect(wrapper.emitted('saved')).toBeFalsy()
    })

    it('edit 成功时应触发 saved 事件', async () => {
      mountComponent({ mode: 'edit', provider: mockProvider })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'test-1'
      wrapper.vm.form.name = '更新'
      wrapper.vm.form.provider_type = 'ollama'
      mockStore.updateProvider.mockResolvedValue({ provider_id: 'test-1' })
      await wrapper.vm.handleSave()
      expect(wrapper.emitted('saved')).toBeTruthy()
    })

    it('validate 失败时应直接返回', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockRejectedValue(new Error('validation failed')),
      }
      await wrapper.vm.handleSave()
      expect(mockStore.createProvider).not.toHaveBeenCalled()
    })

    it('saving 状态应正确切换', async () => {
      mountComponent({ mode: 'create' })
      wrapper.vm.formRef = {
        validate: vi.fn().mockResolvedValue(undefined),
      }
      wrapper.vm.form.provider_id = 'new-1'
      wrapper.vm.form.name = '新供应商'
      wrapper.vm.form.provider_type = 'ollama'
      mockStore.createProvider.mockResolvedValue({ provider_id: 'new-1' })
      await wrapper.vm.handleSave()
      // 完成后应回到 false
      expect(wrapper.vm.saving).toBe(false)
    })
  })

  describe('rules', () => {
    it('provider_id 应有必填规则', () => {
      mountComponent()
      expect(wrapper.vm.rules.provider_id).toBeDefined()
      expect(wrapper.vm.rules.provider_id.length).toBeGreaterThan(0)
    })

    it('name 应有必填规则', () => {
      mountComponent()
      expect(wrapper.vm.rules.name).toBeDefined()
    })

    it('provider_type 应有必填规则', () => {
      mountComponent()
      expect(wrapper.vm.rules.provider_type).toBeDefined()
    })
  })
})
