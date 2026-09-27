/**
 * DxfFileUpload.vue 组件测试
 *
 * 覆盖范围：
 *   1. 渲染（上传区/隐藏文件输入/accept 属性）
 *   2. 点击拖拽区触发文件选择器
 *   3. 拖拽事件维护 is-dragover 状态
 *   4. onFileInputChange（提取文件/无文件/重置 value）
 *   5. 格式校验（.dxf 通过 emit；非 dxf 报错；>50MB 警告）
 *   6. onFileDrop 拖拽文件
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params) {
        let result = key
        Object.entries(params).forEach(([k, v]) => {
          result = result.replace(`{${k}}`, String(v))
        })
        return result
      }
      return key
    },
  }),
}))

const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
}))

vi.mock('@element-plus/icons-vue', () => ({
  UploadFilled: { name: 'UploadFilled', template: '<i class="icon-upload-filled" />' },
}))

import DxfFileUpload from '@/components/dxf_import/DxfFileUpload.vue'

function makeFile(name: string, size = 1024): File {
  return new File([new ArrayBuffer(size)], name)
}

describe('DxfFileUpload.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  const mountComponent = () => {
    wrapper = shallowMount(DxfFileUpload, {
      global: {
        stubs: {
          ElIcon: { template: '<span class="el-icon"><slot /></span>' },
          ElAlert: {
            template: '<div class="el-alert" :title="title"><slot /></div>',
            props: ['title', 'type', 'closable', 'showIcon'],
          },
        },
      },
    })
    return wrapper
  }

  describe('渲染', () => {
    it('渲染上传区与隐藏文件输入', () => {
      mountComponent()
      expect(wrapper.find('.upload-section').exists()).toBe(true)
      expect(wrapper.find('.drop-zone').exists()).toBe(true)
      const input = wrapper.find('input[type="file"]')
      expect(input.exists()).toBe(true)
      expect(input.attributes('accept')).toBe('.dxf')
    })

    it('点击拖拽区触发文件选择器', async () => {
      mountComponent()
      const input = wrapper.find('input[type="file"]').element as HTMLInputElement
      const clickSpy = vi.spyOn(input, 'click').mockImplementation(() => {})
      await wrapper.find('.drop-zone').trigger('click')
      expect(clickSpy).toHaveBeenCalled()
      clickSpy.mockRestore()
    })
  })

  describe('拖拽状态', () => {
    it('dragenter/dragover 时添加 is-dragover class', async () => {
      mountComponent()
      const zone = wrapper.find('.drop-zone')
      await zone.trigger('dragenter')
      expect(zone.classes()).toContain('is-dragover')
      await zone.trigger('dragover')
      expect(zone.classes()).toContain('is-dragover')
    })

    it('dragleave 时移除 is-dragover class', async () => {
      mountComponent()
      const zone = wrapper.find('.drop-zone')
      await zone.trigger('dragenter')
      await zone.trigger('dragleave')
      expect(zone.classes()).not.toContain('is-dragover')
    })
  })

  describe('文件输入变更', () => {
    it('有文件时触发 file-selected 事件', async () => {
      mountComponent()
      const input = wrapper.find('input[type="file"]')
      Object.defineProperty(input.element, 'files', {
        value: [makeFile('drawing.dxf')],
        configurable: true,
      })
      await input.trigger('change')
      expect(wrapper.emitted('file-selected')).toBeTruthy()
      expect((wrapper.emitted('file-selected')![0][0] as File).name).toBe('drawing.dxf')
    })

    it('没有文件时不触发 file-selected', async () => {
      mountComponent()
      const input = wrapper.find('input[type="file"]')
      Object.defineProperty(input.element, 'files', {
        value: [],
        configurable: true,
      })
      await input.trigger('change')
      expect(wrapper.emitted('file-selected')).toBeUndefined()
    })

    it('处理后重置 input value', async () => {
      mountComponent()
      // happy-dom 的 input.value 与 files 联动不可靠，直接调 vm 方法验证重置逻辑
      const target = {
        files: [makeFile('drawing.dxf')],
        value: '/fake/path/drawing.dxf',
      }
      await wrapper.vm.onFileInputChange({ target })
      expect(target.value).toBe('')
      expect(wrapper.emitted('file-selected')).toBeTruthy()
    })
  })

  describe('格式校验', () => {
    it('非 .dxf 文件报错且不触发 file-selected', async () => {
      mountComponent()
      const input = wrapper.find('input[type="file"]')
      Object.defineProperty(input.element, 'files', {
        value: [makeFile('drawing.stp')],
        configurable: true,
      })
      await input.trigger('change')
      expect(mockElMessage.error).toHaveBeenCalledWith('dxfImportDialog.invalidFormat')
      expect(wrapper.find('.el-alert').exists()).toBe(true)
      expect(wrapper.emitted('file-selected')).toBeUndefined()
    })

    it('超过 50MB 时警告但仍触发 file-selected', async () => {
      mountComponent()
      const bigFile = makeFile('big.dxf', 51 * 1024 * 1024)
      const input = wrapper.find('input[type="file"]')
      Object.defineProperty(input.element, 'files', {
        value: [bigFile],
        configurable: true,
      })
      await input.trigger('change')
      expect(mockElMessage.warning).toHaveBeenCalledWith('dxfImportDialog.largeFileWarning')
      expect(wrapper.emitted('file-selected')).toBeTruthy()
    })

    it('拖拽 .dxf 文件触发 file-selected', async () => {
      mountComponent()
      const zone = wrapper.find('.drop-zone')
      await zone.trigger('drop', {
        dataTransfer: { files: [makeFile('drop.dxf')] },
      })
      expect(wrapper.emitted('file-selected')).toBeTruthy()
      expect((wrapper.emitted('file-selected')![0][0] as File).name).toBe('drop.dxf')
    })

    it('拖拽非 .dxf 文件报错', async () => {
      mountComponent()
      const zone = wrapper.find('.drop-zone')
      await zone.trigger('drop', {
        dataTransfer: { files: [makeFile('drop.txt')] },
      })
      expect(mockElMessage.error).toHaveBeenCalledWith('dxfImportDialog.invalidFormat')
      expect(wrapper.emitted('file-selected')).toBeUndefined()
    })
  })
})
