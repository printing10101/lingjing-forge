import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RuleEditor from '@/views/RuleEditor.vue'

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

describe('RuleEditor.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('should render the rule editor main view', () => {
    const wrapper = mount(RuleEditor, {
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    expect(wrapper.exists()).toBe(true)
  })
})
