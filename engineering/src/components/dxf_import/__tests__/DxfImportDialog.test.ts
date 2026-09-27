/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import DxfImportDialog from '@/components/dxf_import/DxfImportDialog.vue'

// Mock: vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

// Mock: element-plus（ElMessage）
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
}))

// Mock: @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Upload: { name: 'Upload', template: '<i />' },
  Refresh: { name: 'Refresh', template: '<i />' },
  Close: { name: 'Close', template: '<i />' },
  Document: { name: 'Document', template: '<i />' },
}))

// Mock: dynamic import of project store（handleImportToProject 内部动态加载）
const mockProjectStore = vi.hoisted(() => ({
  manifest: {
    resources: [] as Array<Record<string, unknown>>,
  },
  markModified: vi.fn(),
}))
vi.mock('@/stores/project', () => ({
  useProjectStore: () => mockProjectStore,
}))

// Mock: formatters（store 展示用）
vi.mock('@/utils/formatters', () => ({
  formatFileSize: vi.fn((size: number) => `${size} B`),
}))

describe('DxfImportDialog.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockProjectStore.manifest.resources = []
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = () => {
    wrapper = shallowMount(DxfImportDialog, {
      global: {
        plugins: [createPinia()],
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.dxf-import-container').exists()).toBe(true)
    })

    it('空闲状态应渲染文件上传子组件', () => {
      mountComponent()
      expect(wrapper.findComponent({ name: 'DxfFileUpload' }).exists()).toBe(true)
    })
  })

  describe('handleFileSelected 方法', () => {
    it('导入成功时不应提示错误', async () => {
      mountComponent()
      // 使用真实 store（组件内部 useDxfImportStore）
      const store = wrapper.vm.store
      const okSpy = vi.spyOn(store, 'importDxfFile').mockResolvedValue(true)
      await wrapper.vm.handleFileSelected(new File(['dxf'], 'a.dxf'))
      expect(okSpy).toHaveBeenCalled()
      expect(mockElMessage.error).not.toHaveBeenCalled()
    })

    it('导入失败时提示错误', async () => {
      mountComponent()
      const store = wrapper.vm.store
      vi.spyOn(store, 'importDxfFile').mockResolvedValue(false)
      await wrapper.vm.handleFileSelected(new File(['dxf'], 'a.dxf'))
      expect(mockElMessage.error).toHaveBeenCalled()
    })
  })

  describe('handleRetry 方法', () => {
    it('调用 store.reset 回到空闲状态', () => {
      mountComponent()
      const store = wrapper.vm.store
      const resetSpy = vi.spyOn(store, 'reset')
      wrapper.vm.handleRetry()
      expect(resetSpy).toHaveBeenCalled()
    })
  })

  describe('handleClose 方法', () => {
    it('调用 store.closeDialog', () => {
      mountComponent()
      const store = wrapper.vm.store
      const closeSpy = vi.spyOn(store, 'closeDialog')
      wrapper.vm.handleClose()
      expect(closeSpy).toHaveBeenCalled()
    })
  })

  describe('handleImportToProject 方法', () => {
    it('有解析结果时把图纸加入工程并提示成功', async () => {
      mountComponent()
      const store = wrapper.vm.store
      vi.spyOn(store, 'parseResult', 'get').mockReturnValue({
        file_name: 'a.dxf',
        lines_count: 3,
        arcs_count: 1,
        circles_count: 2,
      })
      vi.spyOn(store, 'currentFileId', 'get').mockReturnValue('file-1')
      vi.spyOn(store, 'closeDialog').mockImplementation(() => {})

      await wrapper.vm.handleImportToProject()

      expect(mockProjectStore.manifest.resources).toHaveLength(1)
      expect(mockProjectStore.manifest.resources[0].type).toBe('drawing')
      expect(mockProjectStore.manifest.resources[0].original_name).toBe('a.dxf')
      expect(mockProjectStore.markModified).toHaveBeenCalled()
      expect(mockElMessage.success).toHaveBeenCalled()
      expect(store.closeDialog).toHaveBeenCalled()
    })

    it('无解析结果时不加入工程但提示成功', async () => {
      mountComponent()
      const store = wrapper.vm.store
      vi.spyOn(store, 'parseResult', 'get').mockReturnValue(null)
      vi.spyOn(store, 'closeDialog').mockImplementation(() => {})

      await wrapper.vm.handleImportToProject()

      expect(mockProjectStore.manifest.resources).toHaveLength(0)
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('操作失败时提示错误', async () => {
      mountComponent()
      const store = wrapper.vm.store
      vi.spyOn(store, 'parseResult', 'get').mockImplementation(() => {
        throw new Error('boom')
      })

      await wrapper.vm.handleImportToProject()

      expect(mockElMessage.error).toHaveBeenCalled()
    })
  })

  describe('featuresCount 计算属性', () => {
    it('无特征结果时返回 0', () => {
      mountComponent()
      expect(wrapper.vm.featuresCount).toBe(0)
    })

    it('有特征结果时返回孔+平面数', () => {
      mountComponent()
      const store = wrapper.vm.store
      vi.spyOn(store, 'featureResult', 'get').mockReturnValue({
        hole_count: 4,
        plane_count: 2,
      })
      expect(wrapper.vm.featuresCount).toBe(6)
    })
  })
})
