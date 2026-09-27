import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import LLMEngineSettings from '@/components/settings/LLMEngineSettings.vue'
import type { LLMProvider } from '@/types/llmProvider'

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

// Mock element-plus ElMessageBox
const confirmMock = vi.hoisted(() => vi.fn())
vi.mock('element-plus', () => ({
  ElMessageBox: {
    confirm: (...args: unknown[]) => confirmMock(...args),
  },
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Cpu: { name: 'Cpu', render: () => null },
  Refresh: { name: 'Refresh', render: () => null },
  Connection: { name: 'Connection', render: () => null },
  Plus: { name: 'Plus', render: () => null },
}))

// Mock @/stores/llmProviders
const loadAllMock = vi.hoisted(() => vi.fn())
const deleteProviderMock = vi.hoisted(() => vi.fn())
const checkHealthMock = vi.hoisted(() => vi.fn())
const activateProviderMock = vi.hoisted(() => vi.fn())
const setEnabledMock = vi.hoisted(() => vi.fn())
const storeState = vi.hoisted(() => ({
  loading: false,
  hasActiveProvider: false,
  encryptionAvailable: false,
  status: null as any,
  providers: [] as LLMProvider[],
  enabledProviders: [] as LLMProvider[],
  localProviders: [] as LLMProvider[],
  cloudProviders: [] as LLMProvider[],
  activeProvider: null as LLMProvider | null,
  loadAll: loadAllMock,
  deleteProvider: deleteProviderMock,
  checkHealth: checkHealthMock,
  activateProvider: activateProviderMock,
  setEnabled: setEnabledMock,
}))

vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => storeState,
}))

// Mock 子组件
vi.mock('@/components/settings/ProviderList.vue', () => ({
  default: {
    name: 'ProviderList',
    template: '<div class="mock-provider-list" />',
    emits: ['edit', 'test', 'health', 'activate', 'enable', 'delete', 'view-models'],
  },
}))

vi.mock('@/components/settings/ProviderFormDialog.vue', () => ({
  default: {
    name: 'ProviderFormDialog',
    template: '<div class="mock-provider-form-dialog" />',
    props: ['visible', 'mode', 'provider'],
    emits: ['update:visible', 'saved'],
  },
}))

vi.mock('@/components/settings/AutoDetectPanel.vue', () => ({
  default: {
    name: 'AutoDetectPanel',
    template: '<div class="mock-auto-detect-panel" />',
  },
}))

vi.mock('@/components/settings/RouterStatusPanel.vue', () => ({
  default: {
    name: 'RouterStatusPanel',
    template: '<div class="mock-router-status-panel" />',
  },
}))

vi.mock('@/components/settings/ModelsDialog.vue', () => ({
  default: {
    name: 'ModelsDialog',
    template: '<div class="mock-models-dialog" />',
    props: ['visible', 'provider'],
    emits: ['update:visible'],
  },
}))

vi.mock('@/components/settings/TestDialog.vue', () => ({
  default: {
    name: 'TestDialog',
    template: '<div class="mock-test-dialog" />',
    props: ['visible', 'provider'],
    emits: ['update:visible'],
  },
}))

describe('LLMEngineSettings.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    storeState.loading = false
    storeState.hasActiveProvider = false
    storeState.encryptionAvailable = false
    storeState.status = null
    storeState.providers = []
    storeState.enabledProviders = []
    storeState.localProviders = []
    storeState.cloudProviders = []
    storeState.activeProvider = null
    confirmMock.mockResolvedValue('confirm')
    deleteProviderMock.mockResolvedValue(undefined)
  })

  afterEach(() => {
    if (wrapper) wrapper.unmount()
  })

  const mountComponent = () => {
    wrapper = mount(LLMEngineSettings, {
      global: {
        stubs: {
          'el-icon': { template: '<span><slot /></span>' },
          'el-button': {
            template: '<button class="btn" @click="$emit(\'click\')"><slot /></button>',
            props: ['size', 'loading', 'type', 'circle'],
            emits: ['click'],
          },
          'el-tag': { template: '<span class="tag"><slot /></span>', props: ['type', 'size', 'effect'] },
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
      expect(wrapper.find('.llm-engine-settings').exists()).toBe(true)
    })

    it('应渲染 AI 引擎状态卡片', () => {
      mountComponent()
      const cards = wrapper.findAll('.content-card')
      expect(cards.length).toBeGreaterThanOrEqual(1)
    })

    it('应渲染 Provider 列表卡片', () => {
      mountComponent()
      expect(wrapper.find('.mock-provider-list').exists()).toBe(true)
    })

    it('应渲染 AutoDetectPanel 子组件', () => {
      mountComponent()
      expect(wrapper.find('.mock-auto-detect-panel').exists()).toBe(true)
    })

    it('应渲染 RouterStatusPanel 子组件', () => {
      mountComponent()
      expect(wrapper.find('.mock-router-status-panel').exists()).toBe(true)
    })

    it('应渲染 ProviderFormDialog 子组件', () => {
      mountComponent()
      expect(wrapper.find('.mock-provider-form-dialog').exists()).toBe(true)
    })

    it('应渲染 ModelsDialog 子组件', () => {
      mountComponent()
      expect(wrapper.find('.mock-models-dialog').exists()).toBe(true)
    })

    it('应渲染 TestDialog 子组件', () => {
      mountComponent()
      expect(wrapper.find('.mock-test-dialog').exists()).toBe(true)
    })
  })

  describe('初始状态', () => {
    it('formDialogVisible 初始值应为 false', () => {
      mountComponent()
      expect(wrapper.vm.formDialogVisible).toBe(false)
    })

    it('formMode 初始值应为 create', () => {
      mountComponent()
      expect(wrapper.vm.formMode).toBe('create')
    })

    it('editingProvider 初始值应为 null', () => {
      mountComponent()
      expect(wrapper.vm.editingProvider).toBeNull()
    })

    it('modelsDialogVisible 初始值应为 false', () => {
      mountComponent()
      expect(wrapper.vm.modelsDialogVisible).toBe(false)
    })

    it('modelsProvider 初始值应为 null', () => {
      mountComponent()
      expect(wrapper.vm.modelsProvider).toBeNull()
    })

    it('testDialogVisible 初始值应为 false', () => {
      mountComponent()
      expect(wrapper.vm.testDialogVisible).toBe(false)
    })

    it('testProvider 初始值应为 null', () => {
      mountComponent()
      expect(wrapper.vm.testProvider).toBeNull()
    })
  })

  describe('onMounted', () => {
    it('挂载时应调用 store.loadAll', () => {
      mountComponent()
      expect(loadAllMock).toHaveBeenCalled()
    })
  })

  describe('刷新按钮', () => {
    it('点击应调用 store.loadAll', async () => {
      mountComponent()
      // 状态卡片头部有刷新按钮（circle）
      const buttons = wrapper.findAll('.el-button')
      // 找到 circle 的刷新按钮
      const refreshBtn = buttons[0]
      await refreshBtn.trigger('click')
      expect(loadAllMock).toHaveBeenCalled()
    })
  })

  describe('新增 Provider 按钮', () => {
    it('点击应打开创建对话框', async () => {
      mountComponent()
      const buttons = wrapper.findAll('.el-button')
      // Provider 列表卡片头部有"新增"按钮
      // 找到 type=primary 的按钮（最后一个）
      const addBtn = buttons[buttons.length - 1]
      await addBtn.trigger('click')
      expect(wrapper.vm.formDialogVisible).toBe(true)
      expect(wrapper.vm.formMode).toBe('create')
      expect(wrapper.vm.editingProvider).toBeNull()
    })
  })

  describe('openCreateDialog 方法', () => {
    it('应设置 formMode 为 create', () => {
      mountComponent()
      wrapper.vm.openCreateDialog()
      expect(wrapper.vm.formMode).toBe('create')
    })

    it('应清空 editingProvider', () => {
      mountComponent()
      wrapper.vm.editingProvider = {} as any
      wrapper.vm.openCreateDialog()
      expect(wrapper.vm.editingProvider).toBeNull()
    })

    it('应设置 formDialogVisible 为 true', () => {
      mountComponent()
      wrapper.vm.openCreateDialog()
      expect(wrapper.vm.formDialogVisible).toBe(true)
    })
  })

  describe('openEditDialog 方法', () => {
    it('应设置 formMode 为 edit', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openEditDialog(provider)
      expect(wrapper.vm.formMode).toBe('edit')
    })

    it('应设置 editingProvider', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openEditDialog(provider)
      expect(wrapper.vm.editingProvider).toStrictEqual(provider)
    })

    it('应设置 formDialogVisible 为 true', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openEditDialog(provider)
      expect(wrapper.vm.formDialogVisible).toBe(true)
    })
  })

  describe('openModelsDialog 方法', () => {
    it('应设置 modelsProvider', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openModelsDialog(provider)
      expect(wrapper.vm.modelsProvider).toStrictEqual(provider)
    })

    it('应设置 modelsDialogVisible 为 true', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openModelsDialog(provider)
      expect(wrapper.vm.modelsDialogVisible).toBe(true)
    })
  })

  describe('openTestDialog 方法', () => {
    it('应设置 testProvider', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openTestDialog(provider)
      expect(wrapper.vm.testProvider).toStrictEqual(provider)
    })

    it('应设置 testDialogVisible 为 true', () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.vm.openTestDialog(provider)
      expect(wrapper.vm.testDialogVisible).toBe(true)
    })
  })

  describe('handleDelete 方法', () => {
    it('用户确认时应调用 store.deleteProvider', async () => {
      confirmMock.mockResolvedValue('confirm')
      mountComponent()
      const provider = { provider_id: 'p1', name: 'Test' } as any
      await wrapper.vm.handleDelete(provider)
      expect(deleteProviderMock).toHaveBeenCalledWith('p1')
    })

    it('用户取消时不应调用 deleteProvider', async () => {
      confirmMock.mockRejectedValue('cancel')
      mountComponent()
      const provider = { provider_id: 'p1', name: 'Test' } as any
      await wrapper.vm.handleDelete(provider)
      expect(deleteProviderMock).not.toHaveBeenCalled()
    })

    it('删除过程不应抛错', async () => {
      confirmMock.mockResolvedValue('confirm')
      deleteProviderMock.mockRejectedValue(new Error('fail'))
      mountComponent()
      const provider = { provider_id: 'p1', name: 'Test' } as any
      await expect(wrapper.vm.handleDelete(provider)).resolves.not.toThrow()
    })

    it('应使用包含 provider 信息的确认消息', async () => {
      confirmMock.mockResolvedValue('confirm')
      mountComponent()
      const provider = { provider_id: 'p1', name: 'TestProvider' } as any
      await wrapper.vm.handleDelete(provider)
      expect(confirmMock).toHaveBeenCalled()
      const message = confirmMock.mock.calls[0][0]
      expect(message).toContain('TestProvider')
      expect(message).toContain('p1')
    })
  })

  describe('onFormSaved 方法', () => {
    it('应设置 formDialogVisible 为 false', () => {
      mountComponent()
      wrapper.vm.formDialogVisible = true
      wrapper.vm.onFormSaved()
      expect(wrapper.vm.formDialogVisible).toBe(false)
    })
  })

  describe('ProviderList 事件处理', () => {
    it('edit 事件应调用 openEditDialog', async () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('edit', provider)
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.editingProvider).toStrictEqual(provider)
      expect(wrapper.vm.formMode).toBe('edit')
    })

    it('test 事件应调用 openTestDialog', async () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('test', provider)
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.testProvider).toStrictEqual(provider)
    })

    it('health 事件应调用 store.checkHealth', async () => {
      mountComponent()
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('health', 'p1')
      await wrapper.vm.$nextTick()
      expect(checkHealthMock).toHaveBeenCalledWith('p1')
    })

    it('activate 事件应调用 store.activateProvider', async () => {
      mountComponent()
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('activate', 'p1')
      await wrapper.vm.$nextTick()
      expect(activateProviderMock).toHaveBeenCalledWith('p1')
    })

    it('enable 事件应调用 store.setEnabled', async () => {
      mountComponent()
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('enable', 'p1', true)
      await wrapper.vm.$nextTick()
      expect(setEnabledMock).toHaveBeenCalledWith('p1', true)
    })

    it('delete 事件应调用 handleDelete', async () => {
      mountComponent()
      const provider = { provider_id: 'p1', name: 'Test' } as any
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('delete', provider)
      await wrapper.vm.$nextTick()
      // handleDelete 会调用 ElMessageBox.confirm（副作用）
      expect(confirmMock).toHaveBeenCalled()
    })

    it('view-models 事件应调用 openModelsDialog', async () => {
      mountComponent()
      const provider = { provider_id: 'p1' } as any
      wrapper.findComponent({ name: 'ProviderList' }).vm.$emit('view-models', provider)
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.modelsProvider).toStrictEqual(provider)
    })
  })
})
