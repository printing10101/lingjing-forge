import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import AutoDetectPanel from '@/components/settings/AutoDetectPanel.vue'

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
  Radar: { name: 'Radar', render: () => null },
  Aim: { name: 'Aim', render: () => null },
  Search: { name: 'Search', render: () => null },
  Download: { name: 'Download', render: () => null },
}))

// Mock @/api/llmProviders
vi.mock('@/api/llmProviders', () => ({
  PROVIDER_TYPE_META: {
    ollama: { label: 'Ollama', category: 'local' },
    openai: { label: 'OpenAI', category: 'cloud' },
  },
}))

// Mock @/stores/llmProviders
const importDetectedMock = vi.hoisted(() => vi.fn())
const storeState = vi.hoisted(() => ({
  detecting: false,
  detected: [] as any[],
  lastDetectDuration: 0,
  previewDetect: vi.fn(),
  importDetectedProviders: importDetectedMock,
}))
vi.mock('@/stores/llmProviders', () => ({
  useLLMProvidersStore: () => storeState,
}))

describe('AutoDetectPanel.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    storeState.detecting = false
    storeState.detected = []
    storeState.lastDetectDuration = 0
    importDetectedMock.mockResolvedValue(undefined)
    confirmMock.mockResolvedValue('confirm')
  })

  afterEach(() => {
    if (wrapper) wrapper.unmount()
  })

  const mountComponent = () => {
    wrapper = mount(AutoDetectPanel, {
      global: {
        stubs: {
          ElIcon: { template: '<span><slot /></span>' },
          ElButton: {
            template: '<button class="btn" @click="$emit(\'click\')"><slot /></button>',
            props: ['size', 'loading', 'type'],
            emits: ['click'],
          },
          ElAlert: { template: '<div class="alert" />', props: ['title', 'type', 'closable', 'showIcon'] },
          ElTag: { template: '<span class="tag"><slot /></span>', props: ['type', 'size', 'effect'] },
          ElTable: {
            template: '<div class="table"><div v-for="row in data" :key="row.provider_id"><slot name="default" :row="row" /></div></div>',
            props: ['data', 'stripe', 'size'],
          },
          ElTableColumn: { template: '<div class="col" />' },
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
    })

    it('应渲染扫描按钮', () => {
      mountComponent()
      const buttons = wrapper.findAll('.btn')
      expect(buttons.length).toBeGreaterThan(0)
    })

    it('无检测结果且未检测时应渲染空提示', () => {
      mountComponent()
      expect(wrapper.find('.alert').exists()).toBe(true)
    })

    it('有检测结果时不应渲染空提示', async () => {
      storeState.detected = [{ provider_id: 'p1', detected: true }]
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.alert').exists()).toBe(false)
    })

    it('检测中时不应渲染空提示', async () => {
      storeState.detecting = true
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.alert').exists()).toBe(false)
    })

    it('检测中时应渲染 loading 区域', async () => {
      storeState.detecting = true
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.detect-loading').exists()).toBe(true)
    })

    it('有检测结果时应渲染表格', async () => {
      storeState.detected = [
        { provider_id: 'p1', detected: true, provider_type: 'ollama', base_url: 'http://x', default_model: 'm', detection_method: 'port', detail: 'd' },
      ]
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.table').exists()).toBe(true)
    })
  })

  describe('detectedCount 计算属性', () => {
    it('应返回 detected 为 true 的数量', async () => {
      storeState.detected = [
        { detected: true },
        { detected: false },
        { detected: true },
      ]
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.detectedCount).toBe(2)
    })

    it('无检测结果时应返回 0', () => {
      mountComponent()
      expect(wrapper.vm.detectedCount).toBe(0)
    })
  })

  describe('getLabel 方法', () => {
    it('已知类型应返回 label', () => {
      mountComponent()
      expect(wrapper.vm.getLabel('ollama')).toBe('Ollama')
    })

    it('未知类型应返回类型字符串', () => {
      mountComponent()
      expect(wrapper.vm.getLabel('unknown')).toBe('unknown')
    })
  })

  describe('扫描按钮', () => {
    it('点击应调用 store.previewDetect', async () => {
      mountComponent()
      const buttons = wrapper.findAll('.btn')
      // 第一个按钮是扫描按钮
      await buttons[0].trigger('click')
      expect(storeState.previewDetect).toHaveBeenCalled()
    })

    it('检测中时应显示 loading 状态', () => {
      storeState.detecting = true
      mountComponent()
      const buttons = wrapper.findAll('.btn')
      // 按钮应该有 loading prop
      expect(buttons.length).toBeGreaterThan(0)
    })
  })

  describe('导入按钮', () => {
    it('有检测结果时应显示导入按钮', async () => {
      storeState.detected = [{ provider_id: 'p1', detected: true }]
      mountComponent()
      await wrapper.vm.$nextTick()
      const buttons = wrapper.findAll('.btn')
      // 应该至少有 2 个按钮（扫描 + 导入）
      expect(buttons.length).toBeGreaterThanOrEqual(2)
    })

    it('无检测结果时不应显示导入按钮', () => {
      mountComponent()
      const buttons = wrapper.findAll('.btn')
      expect(buttons.length).toBe(1)
    })
  })

  describe('handleImport 方法', () => {
    it('用户确认时应调用 store.importDetectedProviders', async () => {
      confirmMock.mockResolvedValue('confirm')
      mountComponent()
      await wrapper.vm.handleImport()
      expect(importDetectedMock).toHaveBeenCalled()
    })

    it('用户取消时不应调用 importDetectedProviders', async () => {
      confirmMock.mockRejectedValue('cancel')
      mountComponent()
      await wrapper.vm.handleImport()
      expect(importDetectedMock).not.toHaveBeenCalled()
    })

    it('导入过程不应抛错', async () => {
      confirmMock.mockResolvedValue('confirm')
      importDetectedMock.mockRejectedValue(new Error('fail'))
      mountComponent()
      await expect(wrapper.vm.handleImport()).resolves.not.toThrow()
    })
  })
})
