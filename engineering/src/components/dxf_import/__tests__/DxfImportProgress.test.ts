/**
 * DxfImportProgress.vue 组件测试
 *
 * 覆盖范围：
 *   1. isUploading 分支（上传/解析文案）
 *   2. 当前文件名显示
 *   3. isError → 进度条 exception 状态
 *   4. 进度详情（uploadProgress / parseProgress 带参数）
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

vi.mock('@element-plus/icons-vue', () => ({
  Loading: { name: 'Loading', template: '<i class="icon-loading" />' },
}))

import DxfImportProgress from '@/components/dxf_import/DxfImportProgress.vue'

describe('DxfImportProgress.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(DxfImportProgress, {
      props: {
        isUploading: false,
        isError: false,
        currentFileName: 'drawing.dxf',
        overallProgress: 50,
        uploadProgress: 30,
        parseProgress: 60,
        ...props,
      },
      global: {
        // 模板用 $t 全局属性（非 useI18n 的 t）
        mocks: {
          $t: (key: string) => key,
        },
        stubs: {
          ElIcon: { template: '<span class="el-icon"><slot /></span>' },
          ElProgress: {
            template:
              '<div class="el-progress" :data-status="status" :data-percentage="percentage" />',
            props: ['percentage', 'status', 'strokeWidth', 'striped', 'stripedFlow'],
          },
        },
      },
    })
    return wrapper
  }

  it('isUploading 为 true 时显示上传文案', () => {
    mountComponent({ isUploading: true })
    expect(wrapper.text()).toContain('dxfImportDialog.uploading')
    expect(wrapper.text()).toContain('dxfImportDialog.uploadProgress')
  })

  it('isUploading 为 false 时显示解析文案', () => {
    mountComponent()
    expect(wrapper.text()).toContain('dxfImportDialog.parsing')
    expect(wrapper.text()).toContain('dxfImportDialog.parseProgress')
  })

  it('显示当前文件名', () => {
    mountComponent({ currentFileName: 'part-01.dxf' })
    expect(wrapper.find('.file-name-inline').text()).toBe('part-01.dxf')
  })

  it('isError 为 true 时进度条为 exception 状态', () => {
    mountComponent({ isError: true })
    expect(wrapper.find('.el-progress').attributes('data-status')).toBe('exception')
  })

  it('isError 为 false 时进度条无 exception 状态', () => {
    mountComponent()
    expect(wrapper.find('.el-progress').attributes('data-status')).toBeUndefined()
  })

  it('进度条显示 overallProgress', () => {
    mountComponent({ overallProgress: 75 })
    expect(wrapper.find('.el-progress').attributes('data-percentage')).toBe('75')
  })
})
