import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { reactive } from 'vue'
import { mount, VueWrapper } from '@vue/test-utils'
import TestDialog from '@/components/settings/TestDialog.vue'

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
  Delete: { name: 'Delete', render: () => null },
  VideoPlay: { name: 'VideoPlay', render: () => null },
  RefreshLeft: { name: 'RefreshLeft', render: () => null },
}))

// Mock @/stores/llmProviders（reactive：computed 依赖 store.testing 需要响应式追踪）
const mockStore = reactive({
  testing: false,
  testChat: vi.fn(),
})
vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => mockStore,
}))

const mockProvider = {
  provider_id: 'test-provider-1',
  name: '测试供应商',
  provider_type: 'openai',
  base_url: 'http://localhost:11434',
  default_model: 'gpt-4',
  api_key: '',
  enabled: true,
  created_at: '',
  updated_at: '',
}

describe('TestDialog.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.testing = false
    mockStore.testChat.mockResolvedValue(null)
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, any> = {}) => {
    wrapper = mount(TestDialog, {
      props: {
        visible: true,
        provider: mockProvider as any,
        ...props,
      },
      global: {
        stubs: {
          'el-dialog': {
            template: '<div><slot /><slot name="footer" /></div>',
            props: ['modelValue', 'title', 'width', 'closeOnClickModal', 'appendToBody'],
            emits: ['update:modelValue', 'open', 'closed'],
          },
          'el-descriptions': { template: '<div class="descriptions"><slot /></div>' },
          'el-descriptions-item': { template: '<div class="desc-item"><slot /></div>' },
          'el-empty': { template: '<div class="empty" />' },
          'el-select': { template: '<select><slot /></select>' },
          'el-option': { template: '<option />' },
          'el-input': { template: '<input />' },
          'el-button': {
            template: '<button @click="$emit(\'click\')"><slot /></button>',
            emits: ['click'],
          },
          'el-icon': { template: '<span><slot /></span>' },
          'el-slider': { template: '<input type="range" />' },
          'el-tag': { template: '<span class="tag"><slot /></span>' },
          'el-alert': {
            template: '<div class="alert"><slot /></div>',
            emits: ['close'],
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

    it('无 provider 时应渲染空状态', () => {
      mountComponent({ provider: null })
      expect(wrapper.find('.empty-state').exists()).toBe(true)
    })

    it('有 provider 时应渲染供应商概要', () => {
      mountComponent()
      expect(wrapper.find('.provider-meta').exists()).toBe(true)
    })

    it('应渲染消息输入区', () => {
      mountComponent()
      expect(wrapper.find('.messages-area').exists()).toBe(true)
    })

    it('应渲染参数区域', () => {
      mountComponent()
      expect(wrapper.find('.params-area').exists()).toBe(true)
    })

    it('应渲染操作栏', () => {
      mountComponent()
      expect(wrapper.find('.action-bar').exists()).toBe(true)
    })

    it('无结果时不应渲染结果区域', () => {
      mountComponent()
      expect(wrapper.find('.result-area').exists()).toBe(false)
    })
  })

  describe('初始状态', () => {
    it('messages 应有一个默认消息', () => {
      mountComponent()
      expect(wrapper.vm.messages.length).toBe(1)
    })

    it('默认消息 role 应为 user', () => {
      mountComponent()
      expect(wrapper.vm.messages[0].role).toBe('user')
    })

    it('params.max_tokens 初始值应为 256', () => {
      mountComponent()
      expect(wrapper.vm.params.max_tokens).toBe(256)
    })

    it('params.temperature 初始值应为 0.7', () => {
      mountComponent()
      expect(wrapper.vm.params.temperature).toBe(0.7)
    })

    it('params.model 初始值应为空字符串', () => {
      mountComponent()
      expect(wrapper.vm.params.model).toBe('')
    })

    it('result 初始值应为 null', () => {
      mountComponent()
      expect(wrapper.vm.result).toBeNull()
    })

    it('errorMsg 初始值应为空字符串', () => {
      mountComponent()
      expect(wrapper.vm.errorMsg).toBe('')
    })
  })

  describe('title 计算属性', () => {
    it('无 provider 时应返回默认标题', () => {
      mountComponent({ provider: null })
      expect(wrapper.vm.title).toBe('settings.testDialog.titleDefault')
    })

    it('有 provider 时应返回带名称的标题', () => {
      mountComponent()
      expect(wrapper.vm.title).toContain('settings.testDialog.titleSuffix')
      expect(wrapper.vm.title).toContain('测试供应商')
    })
  })

  describe('canSubmit 计算属性', () => {
    it('store.testing 为 true 时应返回 false', () => {
      mountComponent()
      mockStore.testing = true
      expect(wrapper.vm.canSubmit).toBe(false)
    })

    it('所有消息内容为空时应返回 false', () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: '' }]
      expect(wrapper.vm.canSubmit).toBe(false)
    })

    it('有消息内容时应返回 true', () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      expect(wrapper.vm.canSubmit).toBe(true)
    })

    it('消息内容为纯空格时应返回 false', () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: '   ' }]
      expect(wrapper.vm.canSubmit).toBe(false)
    })
  })

  describe('resultTagType 计算属性', () => {
    it('result 为 null 时应返回 info', () => {
      mountComponent()
      expect(wrapper.vm.resultTagType).toBe('info')
    })

    it('finish_reason 为 stop 时应返回 success', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'stop', content: '', latency_ms: 100, provider_id: '', model: '', usage: null }
      expect(wrapper.vm.resultTagType).toBe('success')
    })

    it('finish_reason 为 length 时应返回 warning', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'length', content: '', latency_ms: 100, provider_id: '', model: '', usage: null }
      expect(wrapper.vm.resultTagType).toBe('warning')
    })

    it('finish_reason 为 max_tokens 时应返回 warning', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'max_tokens', content: '', latency_ms: 100, provider_id: '', model: '', usage: null }
      expect(wrapper.vm.resultTagType).toBe('warning')
    })

    it('finish_reason 为其他值时应返回 info', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'other', content: '', latency_ms: 100, provider_id: '', model: '', usage: null }
      expect(wrapper.vm.resultTagType).toBe('info')
    })
  })

  describe('addMessage 方法', () => {
    it('应添加一条新消息', () => {
      mountComponent()
      const initialLength = wrapper.vm.messages.length
      wrapper.vm.addMessage()
      expect(wrapper.vm.messages.length).toBe(initialLength + 1)
    })

    it('新消息 role 应为 user', () => {
      mountComponent()
      wrapper.vm.addMessage()
      const lastMessage = wrapper.vm.messages[wrapper.vm.messages.length - 1]
      expect(lastMessage.role).toBe('user')
    })

    it('新消息内容应为空', () => {
      mountComponent()
      wrapper.vm.addMessage()
      const lastMessage = wrapper.vm.messages[wrapper.vm.messages.length - 1]
      expect(lastMessage.content).toBe('')
    })
  })

  describe('removeMessage 方法', () => {
    it('应删除指定索引的消息', () => {
      mountComponent()
      wrapper.vm.addMessage()
      wrapper.vm.addMessage()
      const lengthBefore = wrapper.vm.messages.length
      wrapper.vm.removeMessage(1)
      expect(wrapper.vm.messages.length).toBe(lengthBefore - 1)
    })
  })

  describe('resetMessages 方法', () => {
    it('应重置消息为默认消息', () => {
      mountComponent()
      wrapper.vm.addMessage()
      wrapper.vm.addMessage()
      wrapper.vm.resetMessages()
      expect(wrapper.vm.messages.length).toBe(1)
      expect(wrapper.vm.messages[0].role).toBe('user')
    })

    it('应清空 result', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'stop', content: 'test', latency_ms: 100, provider_id: '', model: '', usage: null }
      wrapper.vm.resetMessages()
      expect(wrapper.vm.result).toBeNull()
    })

    it('应清空 errorMsg', () => {
      mountComponent()
      wrapper.vm.errorMsg = 'error'
      wrapper.vm.resetMessages()
      expect(wrapper.vm.errorMsg).toBe('')
    })
  })

  describe('runTest 方法', () => {
    it('无 provider 时应直接返回', async () => {
      mountComponent({ provider: null })
      await wrapper.vm.runTest()
      expect(mockStore.testChat).not.toHaveBeenCalled()
    })

    it('canSubmit 为 false 时应直接返回', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: '' }]
      await wrapper.vm.runTest()
      expect(mockStore.testChat).not.toHaveBeenCalled()
    })

    it('应调用 store.testChat 并传递 payload', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      mockStore.testChat.mockResolvedValue({
        finish_reason: 'stop',
        content: 'response',
        latency_ms: 100,
        provider_id: 'test-provider-1',
        model: 'gpt-4',
        usage: { prompt_tokens: 5, completion_tokens: 5, total_tokens: 10 },
      })
      await wrapper.vm.runTest()
      expect(mockStore.testChat).toHaveBeenCalledWith('test-provider-1', expect.objectContaining({
        messages: [{ role: 'user', content: 'hello' }],
        max_tokens: 256,
        temperature: 0.7,
      }))
    })

    it('params.model 有值时应包含在 payload 中', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      wrapper.vm.params.model = 'custom-model'
      mockStore.testChat.mockResolvedValue(null)
      await wrapper.vm.runTest()
      const payload = mockStore.testChat.mock.calls[0][1]
      expect(payload.model).toBe('custom-model')
    })

    it('params.model 为空时不应包含 model 字段', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      wrapper.vm.params.model = ''
      mockStore.testChat.mockResolvedValue(null)
      await wrapper.vm.runTest()
      const payload = mockStore.testChat.mock.calls[0][1]
      expect(payload.model).toBeUndefined()
    })

    it('成功时应设置 result', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      const mockResp = {
        finish_reason: 'stop',
        content: 'response text',
        latency_ms: 100,
        provider_id: 'test-provider-1',
        model: 'gpt-4',
        usage: { prompt_tokens: 5, completion_tokens: 5, total_tokens: 10 },
      }
      mockStore.testChat.mockResolvedValue(mockResp)
      await wrapper.vm.runTest()
      expect(wrapper.vm.result).toEqual(mockResp)
    })

    it('store 返回 null 时应设置 errorMsg', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      mockStore.testChat.mockResolvedValue(null)
      await wrapper.vm.runTest()
      expect(wrapper.vm.errorMsg).toBe('settings.testDialog.errorInvokeFailed')
    })

    it('store 抛错时应设置 errorMsg', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      mockStore.testChat.mockRejectedValue(new Error('Network Error'))
      await wrapper.vm.runTest()
      expect(wrapper.vm.errorMsg).toBe('Network Error')
    })

    it('store 抛无消息错误时应使用默认错误文案', async () => {
      mountComponent()
      wrapper.vm.messages = [{ role: 'user', content: 'hello' }]
      mockStore.testChat.mockRejectedValue({})
      await wrapper.vm.runTest()
      expect(wrapper.vm.errorMsg).toBe('settings.testDialog.errorTestFailed')
    })

    it('应过滤内容为空的消息', async () => {
      mountComponent()
      wrapper.vm.messages = [
        { role: 'user', content: 'hello' },
        { role: 'system', content: '' },
        { role: 'user', content: '   ' },
      ]
      mockStore.testChat.mockResolvedValue(null)
      await wrapper.vm.runTest()
      const payload = mockStore.testChat.mock.calls[0][1]
      expect(payload.messages.length).toBe(1)
      expect(payload.messages[0].content).toBe('hello')
    })
  })

  describe('onOpen 方法', () => {
    it('应清空 result', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'stop', content: 'test', latency_ms: 100, provider_id: '', model: '', usage: null }
      wrapper.vm.onOpen()
      expect(wrapper.vm.result).toBeNull()
    })

    it('应清空 errorMsg', () => {
      mountComponent()
      wrapper.vm.errorMsg = 'error'
      wrapper.vm.onOpen()
      expect(wrapper.vm.errorMsg).toBe('')
    })

    it('应重置 params.model', () => {
      mountComponent()
      wrapper.vm.params.model = 'custom-model'
      wrapper.vm.onOpen()
      expect(wrapper.vm.params.model).toBe('')
    })

    it('messages 为空时应重置消息', () => {
      mountComponent()
      wrapper.vm.messages = []
      wrapper.vm.onOpen()
      expect(wrapper.vm.messages.length).toBe(1)
    })
  })

  describe('onClosed 方法', () => {
    it('应清空 result', () => {
      mountComponent()
      wrapper.vm.result = { finish_reason: 'stop', content: 'test', latency_ms: 100, provider_id: '', model: '', usage: null }
      wrapper.vm.onClosed()
      expect(wrapper.vm.result).toBeNull()
    })

    it('应清空 errorMsg', () => {
      mountComponent()
      wrapper.vm.errorMsg = 'error'
      wrapper.vm.onClosed()
      expect(wrapper.vm.errorMsg).toBe('')
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
})
