import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import StepImportDialog from '@/components/step_import/StepImportDialog.vue'

// 提供完整 axios 形状（auto-mock 的 create 返回 undefined，会让 http.ts 的
// axios.create(...).interceptors 在模块加载期崩溃——全量跑时顺序相关 flaky）
vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    })),
  },
}))

describe('StepImportDialog.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('should render dialog when visible', () => {
    const wrapper = mount(StepImportDialog, {
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    expect(wrapper.find('.el-dialog').exists()).toBe(true)
  })

  it('should display upload area when no file selected', () => {
    const wrapper = mount(StepImportDialog, {
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    expect(wrapper.find('.el-upload').exists()).toBe(true)
  })

  it('should accept only .step and .stp file types', () => {
    const wrapper = mount(StepImportDialog, {
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    const upload = wrapper.find('.el-upload')
    expect(upload.attributes('accept')).toContain('.step')
    expect(upload.attributes('accept')).toContain('.stp')
  })
})
