/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import GeneralSettings from '@/components/settings/GeneralSettings.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  InfoFilled: { name: 'InfoFilled', template: '<i class="icon-info" />' },
  Monitor: { name: 'Monitor', template: '<i class="icon-monitor" />' },
  Cpu: { name: 'Cpu', template: '<i class="icon-cpu" />' },
  Coin: { name: 'Coin', template: '<i class="icon-coin" />' },
  Refresh: { name: 'Refresh', template: '<i class="icon-refresh" />' },
  Setting: { name: 'Setting', template: '<i class="icon-setting" />' },
  Connection: { name: 'Connection', template: '<i class="icon-connection" />' },
  Tools: { name: 'Tools', template: '<i class="icon-tools" />' },
  Check: { name: 'Check', template: '<i class="icon-check" />' },
  RefreshLeft: { name: 'RefreshLeft', template: '<i class="icon-refresh-left" />' },
}))

// Mock element-plus
const mockElMessageBox = vi.hoisted(() => ({ alert: vi.fn().mockResolvedValue(undefined) }))
const mockElMessage = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('element-plus', () => ({
  ElMessageBox: mockElMessageBox,
  ElMessage: mockElMessage,
}))

// Mock useSettingsStore
const mockSaveSettings = vi.hoisted(() => vi.fn())
const mockResetSettings = vi.hoisted(() => vi.fn())
const mockStore = vi.hoisted(() => ({
  settings: {
    aiMode: 'local',
    localModel: 'qwen3.5:35b-128k',
    device: 'cpu',
    hardwareTier: 'standard',
    lightweightMode: false,
    offlineMode: false,
  },
  saveSettings: mockSaveSettings,
  resetSettings: mockResetSettings,
}))
vi.mock('@/stores/settings', () => ({
  useSettingsStore: () => mockStore,
}))

// Mock useVersionStore
const mockFetchVersionInfo = vi.hoisted(() => vi.fn())
const mockCheckConsistency = vi.hoisted(() => vi.fn())
const mockVersionStore = vi.hoisted(() => ({
  frontendVersion: '4.0.0',
  frontendCommit: 'abc123',
  rustVersion: '4.0.0',
  rustCommit: 'def456',
  pythonVersion: '4.0.0',
  pythonCommit: 'ghi789',
  isConsistent: true,
  inconsistencyDetails: null,
  isLoading: false,
  fetchVersionInfo: mockFetchVersionInfo,
  checkConsistency: mockCheckConsistency,
}))
vi.mock('@/stores/version', () => ({
  useVersionStore: () => mockVersionStore,
}))

// Mock useSettings composable
const mockHandleLocaleChange = vi.hoisted(() => vi.fn())
vi.mock('@/composables/useSettings', () => ({
  useSettings: () => ({
    currentLocale: { value: 'zh-CN' },
    handleLocaleChange: mockHandleLocaleChange,
  }),
}))

describe('GeneralSettings.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockElMessageBox.alert.mockResolvedValue(undefined)
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = () => {
    wrapper = shallowMount(GeneralSettings, {
      global: {
        stubs: {
          'el-alert': { template: '<div class="el-alert"><slot /></div>', props: ['title', 'type', 'closable', 'showIcon'] },
          'el-icon': { template: '<span class="el-icon"><slot /></span>', props: ['size'] },
          'el-tag': { template: '<span class="el-tag"><slot /></span>', props: ['type', 'size'] },
          'el-button': {
            template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
            props: ['type', 'size', 'loading', 'disabled'],
            emits: ['click'],
          },
          'el-form': { template: '<form class="el-form"><slot /></form>', props: ['model', 'labelWidth'] },
          'el-form-item': { template: '<div class="el-form-item"><slot /></div>', props: ['label'] },
          'el-select': { template: '<select class="el-select"><slot /></select>', props: ['modelValue'] },
          'el-option': { template: '<option class="el-option" />', props: ['label', 'value'] },
          'el-radio-group': { template: '<div class="el-radio-group"><slot /></div>', props: ['modelValue'] },
          'el-radio': { template: '<label class="el-radio"><slot /></label>', props: ['value'] },
          'el-switch': { template: '<div class="el-switch" />', props: ['modelValue'] },
          'el-divider': { template: '<hr class="el-divider" />' },
          InfoFilled: { template: '<i class="icon-info" />' },
          Monitor: { template: '<i class="icon-monitor" />' },
          Cpu: { template: '<i class="icon-cpu" />' },
          Coin: { template: '<i class="icon-coin" />' },
          Refresh: { template: '<i class="icon-refresh" />' },
          Setting: { template: '<i class="icon-setting" />' },
          Connection: { template: '<i class="icon-connection" />' },
          Tools: { template: '<i class="icon-tools" />' },
          Check: { template: '<i class="icon-check" />' },
          RefreshLeft: { template: '<i class="icon-refresh-left" />' },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('成功挂载并渲染根容器', () => {
      wrapper = mountComponent()
      expect(wrapper.find('.general-settings').exists()).toBe(true)
    })

    it('渲染版本信息卡片', () => {
      wrapper = mountComponent()
      expect(wrapper.find('.content-card').exists()).toBe(true)
    })

    it('版本一致时不显示警告', () => {
      wrapper = mountComponent()
      expect(wrapper.find('.version-warning').exists()).toBe(false)
    })

    it('版本不一致时显示警告', async () => {
      mockVersionStore.isConsistent = false
      mockVersionStore.inconsistencyDetails = ['前端版本不一致'] as any
      wrapper = mountComponent()
      expect(wrapper.find('.version-warning').exists()).toBe(true)
      // 恢复
      mockVersionStore.isConsistent = true
      mockVersionStore.inconsistencyDetails = null
    })

    it('渲染前端版本号', () => {
      wrapper = mountComponent()
      expect(wrapper.text()).toContain('4.0.0')
    })
  })

  describe('hardwareTierDescription 计算属性', () => {
    it('minimal 档位返回对应描述', () => {
      mockStore.settings.hardwareTier = 'minimal'
      wrapper = mountComponent()
      expect(wrapper.vm.hardwareTierDescription).toBe('settings.hardwareTierMinimalDesc')
    })

    it('standard 档位返回对应描述', () => {
      mockStore.settings.hardwareTier = 'standard'
      wrapper = mountComponent()
      expect(wrapper.vm.hardwareTierDescription).toBe('settings.hardwareTierStandardDesc')
    })

    it('high 档位返回对应描述', () => {
      mockStore.settings.hardwareTier = 'high'
      wrapper = mountComponent()
      expect(wrapper.vm.hardwareTierDescription).toBe('settings.hardwareTierHighDesc')
    })

    it('ultra 档位返回对应描述', () => {
      mockStore.settings.hardwareTier = 'ultra'
      wrapper = mountComponent()
      expect(wrapper.vm.hardwareTierDescription).toBe('settings.hardwareTierUltraDesc')
    })

    it('未知档位返回空字符串', () => {
      mockStore.settings.hardwareTier = 'unknown'
      wrapper = mountComponent()
      expect(wrapper.vm.hardwareTierDescription).toBe('')
    })
  })

  describe('handleHardwareTierChange 方法', () => {
    it('切换到 minimal 时自动启用 lightweightMode', () => {
      mockStore.settings.lightweightMode = false
      wrapper = mountComponent()
      wrapper.vm.handleHardwareTierChange('minimal')
      expect(mockStore.settings.lightweightMode).toBe(true)
      expect(mockSaveSettings).toHaveBeenCalled()
    })

    it('切换到非 minimal 时不清除 lightweightMode', () => {
      mockStore.settings.lightweightMode = true
      wrapper = mountComponent()
      wrapper.vm.handleHardwareTierChange('high')
      expect(mockStore.settings.lightweightMode).toBe(true)
      expect(mockSaveSettings).toHaveBeenCalled()
    })
  })

  describe('handleSyncEnv 方法', () => {
    it('成功时弹出 ElMessageBox.alert 并显示成功消息', async () => {
      mockStore.settings.hardwareTier = 'standard'
      mockStore.settings.lightweightMode = false
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      expect(mockElMessageBox.alert).toHaveBeenCalled()
      expect(mockElMessage.success).toHaveBeenCalledWith('settings.hardwareTierSyncSuccess')
      expect(wrapper.vm.syncingEnv).toBe(false)
    })

    it('minimal 档位时 skipOllama 为 true', async () => {
      mockStore.settings.hardwareTier = 'minimal'
      mockStore.settings.lightweightMode = false
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      const callArgs = mockElMessageBox.alert.mock.calls[0][0]
      expect(callArgs).toContain('LNN_SKIP_OLLAMA=true')
    })

    it('非 minimal 且 lightweightMode 为 true 时 skipOllama 为 true', async () => {
      mockStore.settings.hardwareTier = 'high'
      mockStore.settings.lightweightMode = true
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      const callArgs = mockElMessageBox.alert.mock.calls[0][0]
      expect(callArgs).toContain('LNN_SKIP_OLLAMA=true')
    })

    it('非 minimal 且 lightweightMode 为 false 时 skipOllama 为 false', async () => {
      mockStore.settings.hardwareTier = 'high'
      mockStore.settings.lightweightMode = false
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      const callArgs = mockElMessageBox.alert.mock.calls[0][0]
      expect(callArgs).toContain('LNN_SKIP_OLLAMA=false')
    })

    it('lightweightMode 为 true 时 MAX_CONCURRENT_AI 为 1', async () => {
      mockStore.settings.hardwareTier = 'standard'
      mockStore.settings.lightweightMode = true
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      const callArgs = mockElMessageBox.alert.mock.calls[0][0]
      expect(callArgs).toContain('LNN_MAX_CONCURRENT_AI=1')
    })

    it('lightweightMode 为 false 时 MAX_CONCURRENT_AI 为 2', async () => {
      mockStore.settings.hardwareTier = 'standard'
      mockStore.settings.lightweightMode = false
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      const callArgs = mockElMessageBox.alert.mock.calls[0][0]
      expect(callArgs).toContain('LNN_MAX_CONCURRENT_AI=2')
    })

    it('用户取消弹窗时不显示错误消息', async () => {
      mockElMessageBox.alert.mockRejectedValueOnce(new Error('cancel'))
      wrapper = mountComponent()
      await wrapper.vm.handleSyncEnv()
      expect(mockElMessage.error).not.toHaveBeenCalled()
      expect(wrapper.vm.syncingEnv).toBe(false)
    })
  })

  describe('refreshVersions 方法', () => {
    it('调用 versionStore.fetchVersionInfo 和 checkConsistency', () => {
      wrapper = mountComponent()
      wrapper.vm.refreshVersions()
      expect(mockFetchVersionInfo).toHaveBeenCalled()
      expect(mockCheckConsistency).toHaveBeenCalled()
    })
  })

  describe('代理 store 和 composables', () => {
    it('store 来自 useSettingsStore', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.store).toBe(mockStore)
    })

    it('versionStore 来自 useVersionStore', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.versionStore).toBe(mockVersionStore)
    })

    it('currentLocale 来自 useSettings', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.currentLocale.value).toBe('zh-CN')
    })

    it('handleLocaleChange 来自 useSettings', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.handleLocaleChange).toBe(mockHandleLocaleChange)
    })
  })
})
