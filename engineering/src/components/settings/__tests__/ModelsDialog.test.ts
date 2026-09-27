import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import ModelsDialog from '@/components/settings/ModelsDialog.vue'
import type { LLMProvider, ModelInfo } from '@/types/llmProvider'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, any>) => {
      if (params) {
        return `${key}:${JSON.stringify(params)}`
      }
      return key
    },
  }),
}))

// Mock element-plus ElMessage
const elMessageSuccess = vi.hoisted(() => vi.fn())
const elMessageWarning = vi.hoisted(() => vi.fn())
vi.mock('element-plus', () => ({
  ElMessage: {
    success: (...args: any[]) => elMessageSuccess(...args),
    warning: (...args: any[]) => elMessageWarning(...args),
  },
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Search: { name: 'Search', render: () => null },
  Refresh: { name: 'Refresh', render: () => null },
}))

// Mock clipboard
const writeTextMock = vi.fn(() => Promise.resolve())
Object.defineProperty(globalThis.navigator, 'clipboard', {
  value: { writeText: writeTextMock },
  configurable: true,
})
const listModelsMock = vi.hoisted(() => vi.fn())
vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => ({
    listModels: listModelsMock,
  }),
}))

const baseProvider: LLMProvider = {
  provider_id: 'p1',
  name: 'Ollama Local',
  provider_type: 'ollama',
  base_url: 'http://localhost:11434',
  api_key_set: false,
  default_model: 'llama3',
  enabled: true,
  is_active: true,
  priority: 1,
  timeout: 30,
  max_retries: 3,
  retry_delay: 1,
  last_health_status: 'healthy',
  last_latency_ms: 100,
} as unknown as LLMProvider

describe('ModelsDialog.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    listModelsMock.mockResolvedValue([])
  })

  afterEach(() => {
    if (wrapper) wrapper.unmount()
  })

  const mountComponent = (props: Record<string, any> = {}) => {
    wrapper = mount(ModelsDialog, {
      props: {
        visible: true,
        provider: baseProvider,
        ...props,
      },
      global: {
        stubs: {
          ElDialog: {
            template: '<div><slot /><slot name="footer" /></div>',
            props: ['modelValue', 'title', 'width', 'closeOnClickModal', 'appendToBody'],
            emits: ['update:modelValue', 'open'],
          },
          ElDescriptions: { template: '<div class="descriptions"><slot /></div>' },
          ElDescriptionsItem: { template: '<div class="desc-item"><slot /></div>', props: ['label', 'span'] },
          ElTag: { template: '<span class="tag"><slot /></span>', props: ['type', 'size', 'effect'] },
          ElInput: { template: '<input class="filter-input" />', props: ['modelValue', 'size', 'clearable', 'placeholder'] },
          ElButton: { template: '<button class="btn"><slot /></button>', props: ['size', 'loading', 'type', 'text'] },
          ElIcon: { template: '<span><slot /></span>' },
          ElEmpty: { template: '<div class="empty" />', props: ['description', 'imageSize'] },
          ElTable: {
            template: '<div class="table"><slot /><div v-for="row in data" :key="row.id"><slot name="default" :row="row" /></div></div>',
            props: ['data', 'size', 'stripe', 'maxHeight', 'emptyText'],
          },
          ElTableColumn: { template: '<div class="col" />' },
          ElAlert: { template: '<div class="alert" />', props: ['title', 'type', 'closable', 'showIcon'] },
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

    it('无 provider 时应渲染空状态', () => {
      mountComponent({ provider: null })
      expect(wrapper.find('.empty').exists()).toBe(true)
    })

    it('有 provider 时不应渲染空状态', () => {
      mountComponent()
      expect(wrapper.find('.empty').exists()).toBe(false)
    })

    it('有 provider 时应渲染 provider 元信息', () => {
      mountComponent()
      expect(wrapper.find('.descriptions').exists()).toBe(true)
    })

    it('应渲染模型工具栏', () => {
      mountComponent()
      expect(wrapper.find('.models-toolbar').exists()).toBe(true)
    })

    it('应渲染过滤输入框', () => {
      mountComponent()
      expect(wrapper.find('.filter-input').exists()).toBe(true)
    })

    it('应渲染刷新按钮', () => {
      mountComponent()
      const buttons = wrapper.findAll('.btn')
      expect(buttons.length).toBeGreaterThan(0)
    })

    it('应渲染关闭按钮', () => {
      mountComponent()
      const footer = wrapper.find('button')
      expect(footer.exists()).toBe(true)
    })
  })

  describe('初始状态', () => {
    it('models 初始值应为空数组', () => {
      mountComponent()
      expect(wrapper.vm.models).toEqual([])
    })

    it('loading 初始值应为 false', () => {
      mountComponent()
      expect(wrapper.vm.loading).toBe(false)
    })

    it('errorMsg 初始值应为空字符串', () => {
      mountComponent()
      expect(wrapper.vm.errorMsg).toBe('')
    })

    it('filter 初始值应为空字符串', () => {
      mountComponent()
      expect(wrapper.vm.filter).toBe('')
    })
  })

  describe('title 计算属性', () => {
    it('无 provider 时应返回默认标题', () => {
      mountComponent({ provider: null })
      expect(wrapper.vm.title).toBe('settings.modelsDialog.titleDefault')
    })

    it('有 provider 时应返回带名称的标题', () => {
      mountComponent()
      expect(wrapper.vm.title).toContain('settings.modelsDialog.titleSuffix')
      expect(wrapper.vm.title).toContain('Ollama Local')
    })
  })

  describe('filteredModels 计算属性', () => {
    it('无过滤词时应返回全部模型', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: 'Llama 3', owned_by: 'meta' },
        { id: 'qwen2', name: 'Qwen 2', owned_by: 'alibaba' },
      ] as any
      mountComponent()
      wrapper.vm.models = models
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.filteredModels.length).toBe(2)
    })

    it('有过滤词时应返回匹配的模型（按 id）', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: 'Llama 3', owned_by: 'meta' },
        { id: 'qwen2', name: 'Qwen 2', owned_by: 'alibaba' },
      ] as any
      mountComponent()
      wrapper.vm.models = models
      wrapper.vm.filter = 'llama'
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.filteredModels.length).toBe(1)
      expect(wrapper.vm.filteredModels[0].id).toBe('llama3')
    })

    it('有过滤词时应返回匹配的模型（按 name）', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: 'Llama 3', owned_by: 'meta' },
        { id: 'qwen2', name: 'Qwen 2', owned_by: 'alibaba' },
      ] as any
      mountComponent()
      wrapper.vm.models = models
      wrapper.vm.filter = 'QWEN'
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.filteredModels.length).toBe(1)
      expect(wrapper.vm.filteredModels[0].id).toBe('qwen2')
    })

    it('无匹配时应返回空数组', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: 'Llama 3', owned_by: 'meta' },
      ] as any
      mountComponent()
      wrapper.vm.models = models
      wrapper.vm.filter = 'nonexistent'
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.filteredModels.length).toBe(0)
    })

    it('过滤词仅含空格时应返回全部模型', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: 'Llama 3', owned_by: 'meta' },
      ] as any
      mountComponent()
      wrapper.vm.models = models
      wrapper.vm.filter = '   '
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.filteredModels.length).toBe(1)
    })

    it('name 为 undefined 时不应抛错', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: undefined, owned_by: 'meta' },
      ] as any
      mountComponent()
      wrapper.vm.models = models
      wrapper.vm.filter = 'anything'
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.filteredModels.length).toBe(0)
    })
  })

  describe('loadModels 方法', () => {
    it('无 provider 时应直接返回', async () => {
      mountComponent({ provider: null })
      await wrapper.vm.loadModels()
      expect(listModelsMock).not.toHaveBeenCalled()
    })

    it('成功时应设置 models', async () => {
      const models: ModelInfo[] = [
        { id: 'llama3', name: 'Llama 3', owned_by: 'meta' },
      ] as any
      listModelsMock.mockResolvedValue(models)
      mountComponent()
      await wrapper.vm.loadModels()
      expect(wrapper.vm.models.length).toBe(1)
      expect(wrapper.vm.loading).toBe(false)
      expect(wrapper.vm.errorMsg).toBe('')
    })

    it('返回空数组时应设置错误提示', async () => {
      listModelsMock.mockResolvedValue([])
      mountComponent()
      await wrapper.vm.loadModels()
      expect(wrapper.vm.models.length).toBe(0)
      expect(wrapper.vm.errorMsg).toBe('settings.modelsDialog.errorZeroModels')
    })

    it('抛错时应设置错误消息', async () => {
      listModelsMock.mockRejectedValue(new Error('network error'))
      mountComponent()
      await wrapper.vm.loadModels()
      expect(wrapper.vm.errorMsg).toBe('network error')
      expect(wrapper.vm.models).toEqual([])
      expect(wrapper.vm.loading).toBe(false)
    })

    it('抛错无 message 时应使用默认错误消息', async () => {
      listModelsMock.mockRejectedValue({})
      mountComponent()
      await wrapper.vm.loadModels()
      expect(wrapper.vm.errorMsg).toBe('settings.modelsDialog.errorLoadFailed')
    })

    it('加载过程中 loading 应为 true', async () => {
      let resolveFn: (v: unknown) => void = () => {}
      listModelsMock.mockReturnValue(new Promise((r) => (resolveFn = r)))
      mountComponent()
      const promise = wrapper.vm.loadModels()
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.loading).toBe(true)
      resolveFn([])
      await promise
      expect(wrapper.vm.loading).toBe(false)
    })
  })

  describe('onOpen 方法', () => {
    it('应重置 models/errorMsg/filter', () => {
      mountComponent()
      wrapper.vm.models = [{ id: 'x' }] as any
      wrapper.vm.errorMsg = 'old error'
      wrapper.vm.filter = 'old filter'
      wrapper.vm.onOpen()
      expect(wrapper.vm.models).toEqual([])
      expect(wrapper.vm.errorMsg).toBe('')
      expect(wrapper.vm.filter).toBe('')
    })

    it('有 provider 时应调用 loadModels', async () => {
      mountComponent()
      wrapper.vm.onOpen()
      await flushPromises()
      expect(listModelsMock).toHaveBeenCalled()
    })

    it('无 provider 时不应调用 loadModels', () => {
      mountComponent({ provider: null })
      const spy = vi.spyOn(wrapper.vm, 'loadModels')
      wrapper.vm.onOpen()
      expect(spy).not.toHaveBeenCalled()
    })
  })

  describe('onVisibleChange 方法', () => {
    it('应触发 update:visible 事件', () => {
      mountComponent()
      wrapper.vm.onVisibleChange(false)
      expect(wrapper.emitted('update:visible')).toBeTruthy()
      expect(wrapper.emitted('update:visible')![0]).toEqual([false])
    })
  })

  describe('onClose 方法', () => {
    it('应触发 update:visible 事件并传 false', () => {
      mountComponent()
      wrapper.vm.onClose()
      expect(wrapper.emitted('update:visible')).toBeTruthy()
      expect(wrapper.emitted('update:visible')![0]).toEqual([false])
    })
  })

  describe('copyModelId 方法', () => {
    it('应调用 clipboard.writeText', async () => {
      mountComponent()
      await wrapper.vm.copyModelId('llama3')
      expect(writeTextMock).toHaveBeenCalledWith('llama3')
    })

    it('成功时应调用 ElMessage.success', async () => {
      mountComponent()
      wrapper.vm.copyModelId('llama3')
      await flushPromises()
      expect(elMessageSuccess).toHaveBeenCalled()
      expect(elMessageSuccess.mock.calls[0][0]).toContain('llama3')
    })

    it('clipboard 不可用时应调用 ElMessage.warning', async () => {
      writeTextMock.mockRejectedValueOnce(new Error('denied'))
      mountComponent()
      wrapper.vm.copyModelId('llama3')
      await flushPromises()
      expect(elMessageWarning).toHaveBeenCalled()
    })
  })

  describe('watch provider', () => {
    it('provider_id 变化且 visible 时应调用 loadModels', async () => {
      mountComponent()
      await wrapper.setProps({
        provider: { ...baseProvider, provider_id: 'p2' } as any,
      })
      await flushPromises()
      expect(listModelsMock).toHaveBeenCalled()
    })

    it('provider_id 变化但不可见时不应调用 loadModels', async () => {
      mountComponent({ visible: false })
      const spy = vi.spyOn(wrapper.vm, 'loadModels')
      await wrapper.setProps({
        provider: { ...baseProvider, provider_id: 'p2' } as any,
      })
      expect(spy).not.toHaveBeenCalled()
    })
  })
})
