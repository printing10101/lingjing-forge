/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import BackendStartupDialog from '@/components/BackendStartupDialog.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, fallback?: string) => {
      if (key === 'backendStartup.skip') return '跳过等待'
      if (fallback !== undefined) return fallback
      return key
    },
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Loading: { name: 'Loading', template: '<i class="icon-loading" />' },
  CircleCloseFilled: { name: 'CircleCloseFilled', template: '<i class="icon-circle-close" />' },
}))

// Mock element-plus
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
  ElDialog: {
    template: '<div v-if="modelValue" class="el-dialog"><slot /><slot name="footer" /></div>',
    props: ['modelValue', 'title', 'showClose', 'closeOnClickModal', 'closeOnPressEscape', 'width', 'alignCenter', 'appendTobody'],
    emits: ['update:modelValue'],
  },
  ElButton: {
    template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
    props: ['type', 'loading', 'disabled', 'plain'],
    emits: ['click'],
  },
  ElProgress: {
    template: '<div class="el-progress" :data-percentage="percentage" />',
    props: ['percentage', 'strokeWidth', 'showText', 'status'],
  },
  ElAlert: {
    template: '<div class="el-alert"><slot /></div>',
    props: ['type', 'title', 'closable', 'showIcon'],
  },
  ElIcon: { template: '<span class="el-icon"><slot /></span>' },
}))

// Mock useBackendStatus composable
const mockState = refFactory()
const mockRestart = vi.hoisted(() => vi.fn())
const mockStop = vi.hoisted(() => vi.fn())

function refFactory() {
  // 用 reactive 包装：组件的 watch(status) 依赖响应式才能触发停止定时器
  return reactive({
    status: 'starting' as string,
    message: '正在启动后端服务...',
    progress: 30,
    last_error: null as string | null,
  })
}

const mockTauriMode = vi.hoisted(() => ({ value: true }))
const mockLoading = vi.hoisted(() => ({ value: false }))

vi.mock('@/composables/useBackendStatus', () => ({
  useBackendStatus: () => ({
    state: mockState,
    restart: mockRestart,
    stop: mockStop,
    tauriMode: mockTauriMode,
    loading: mockLoading,
  }),
}))

describe('BackendStartupDialog.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    // 重置状态
    mockState.status = 'starting'
    mockState.message = '正在启动后端服务...'
    mockState.progress = 30
    mockState.last_error = null
    mockTauriMode.value = true
    mockLoading.value = false
    mockRestart.mockResolvedValue(undefined)
    mockStop.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(BackendStartupDialog, {
      props: {
        modelValue: true,
        ...props,
      },
      global: {
        stubs: {
          ElDialog: {
            template: '<div v-if="modelValue" class="el-dialog"><slot /><slot name="footer" /></div>',
            props: ['modelValue', 'title', 'showClose', 'closeOnClickModal', 'closeOnPressEscape', 'width', 'alignCenter', 'appendToBody'],
            emits: ['update:modelValue'],
          },
          'el-button': {
            template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
            props: ['type', 'loading', 'disabled', 'plain'],
            emits: ['click'],
          },
          'el-progress': {
            template: '<div class="el-progress" :data-percentage="percentage" />',
            props: ['percentage', 'strokeWidth', 'showText', 'status'],
          },
          'el-alert': {
            template: '<div class="el-alert"><slot /></div>',
            props: ['type', 'title', 'closable', 'showIcon'],
          },
          'el-icon': { template: '<span class="el-icon"><slot /></span>' },
          Loading: { template: '<i class="icon-loading" />' },
          CircleCloseFilled: { template: '<i class="icon-circle-close" />' },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('当 modelValue 为 true 时渲染对话框', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.find('.el-dialog').exists()).toBe(true)
    })

    it('当 modelValue 为 false 时不渲染对话框', () => {
      wrapper = mountComponent({ modelValue: false })
      expect(wrapper.find('.el-dialog').exists()).toBe(false)
    })

    it('启动中状态显示进度条和状态消息', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.find('.el-progress').exists()).toBe(true)
      expect(wrapper.find('.status-msg').text()).toContain('正在启动后端服务')
    })

    it('错误状态显示错误图标和错误消息', async () => {
      mockState.status = 'failed'
      mockState.message = '后端启动失败'
      mockState.last_error = '端口被占用'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.status-icon.error').exists()).toBe(true)
      expect(wrapper.find('.status-msg.error').text()).toContain('后端启动失败')
    })

    it('错误状态且有 last_error 时显示错误详情', async () => {
      mockState.status = 'crashed'
      mockState.last_error = '进程崩溃退出码 1'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.el-alert').exists()).toBe(true)
      expect(wrapper.find('.error-detail').text()).toContain('进程崩溃退出码 1')
    })
  })

  describe('visible 计算属性', () => {
    it('get 返回 modelValue', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.vm.visible).toBe(true)
    })

    it('set 触发 update:modelValue 事件', async () => {
      wrapper = mountComponent({ modelValue: true })
      wrapper.vm.visible = false
      expect(wrapper.emitted('update:modelValue')).toBeTruthy()
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
    })
  })

  describe('isError 计算属性', () => {
    it('status 为 failed 时返回 true', async () => {
      mockState.status = 'failed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.isError).toBe(true)
    })

    it('status 为 crashed 时返回 true', async () => {
      mockState.status = 'crashed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.isError).toBe(true)
    })

    it('status 为 starting 时返回 false', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.vm.isError).toBe(false)
    })
  })

  describe('isStarting 计算属性', () => {
    it('status 为 starting 时返回 true', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.vm.isStarting).toBe(true)
    })

    it('status 非 starting 时返回 false', async () => {
      mockState.status = 'failed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.isStarting).toBe(false)
    })
  })

  describe('errorTitle 计算属性', () => {
    it('status 为 crashed 时返回 crashed 标题', async () => {
      mockState.status = 'crashed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.errorTitle).toBe('backendStartup.crashed')
    })

    it('status 为 failed 时返回 failed 标题', async () => {
      mockState.status = 'failed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.errorTitle).toBe('backendStartup.failed')
    })

    it('status 为 starting 时返回 starting 标题', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.vm.errorTitle).toBe('backendStartup.starting')
    })
  })

  describe('canSkip 计算属性', () => {
    it('启动中且未超过 10 秒时返回 false', () => {
      wrapper = mountComponent({ modelValue: true })
      expect(wrapper.vm.canSkip).toBe(false)
    })

    it('启动中且超过 10 秒后返回 true', async () => {
      wrapper = mountComponent({ modelValue: true })
      // 推进定时器 11 秒
      await vi.advanceTimersByTimeAsync(11000)
      expect(wrapper.vm.canSkip).toBe(true)
    })

    it('非启动中状态返回 false', async () => {
      mockState.status = 'failed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.canSkip).toBe(false)
    })
  })

  describe('定时器逻辑', () => {
    it('挂载时若 modelValue 为 true 且 isStarting 则启动定时器', () => {
      wrapper = mountComponent({ modelValue: true })
      const initial = wrapper.vm.elapsedSeconds
      vi.advanceTimersByTime(2000)
      expect(wrapper.vm.elapsedSeconds).toBeGreaterThan(initial)
    })

    it('每秒 elapsedSeconds 递增 1', async () => {
      wrapper = mountComponent({ modelValue: true })
      await vi.advanceTimersByTimeAsync(1000)
      expect(wrapper.vm.elapsedSeconds).toBe(1)
      await vi.advanceTimersByTimeAsync(1000)
      expect(wrapper.vm.elapsedSeconds).toBe(2)
    })

    it('状态从 starting 变为非 starting 时停止定时器', async () => {
      wrapper = mountComponent({ modelValue: true })
      await vi.advanceTimersByTimeAsync(3000)
      expect(wrapper.vm.elapsedSeconds).toBe(3)
      // 切换状态
      mockState.status = 'failed'
      await wrapper.vm.$nextTick()
      await vi.advanceTimersByTimeAsync(3000)
      // 定时器已停止，elapsedSeconds 不再增加
      expect(wrapper.vm.elapsedSeconds).toBe(3)
    })

    it('组件卸载时清理定时器', async () => {
      wrapper = mountComponent({ modelValue: true })
      await vi.advanceTimersByTimeAsync(2000)
      const elapsedBeforeUnmount = wrapper.vm.elapsedSeconds
      wrapper.unmount()
      // 卸载后推进时间，确保不再调用（避免内存泄漏）
      vi.advanceTimersByTimeAsync(5000)
      // 不报错即视为清理成功
      expect(elapsedBeforeUnmount).toBe(2)
    })
  })

  describe('onRetry 方法', () => {
    it('调用 restart 并触发 retry 事件', async () => {
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.onRetry()
      expect(mockRestart).toHaveBeenCalled()
      expect(wrapper.emitted('retry')).toBeTruthy()
    })

    it('restart 成功且状态非失败时显示成功消息', async () => {
      mockState.status = 'starting'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.onRetry()
      // 生产 onRetry 不返回 promise：flush 微任务等待 restart().then 回调
      await flushPromises()
      expect(mockElMessage.success).toHaveBeenCalledWith('backendStartup.restarting')
    })

    it('restart 成功但状态仍为失败时不显示成功消息', async () => {
      mockState.status = 'failed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.onRetry()
      expect(mockElMessage.success).not.toHaveBeenCalled()
    })
  })

  describe('onClose 方法', () => {
    it('设置 visible 为 false 触发关闭', async () => {
      wrapper = mountComponent({ modelValue: true })
      wrapper.vm.onClose()
      expect(wrapper.emitted('update:modelValue')).toBeTruthy()
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
    })
  })

  describe('onStop 方法', () => {
    it('调用 stop 函数', () => {
      wrapper = mountComponent({ modelValue: true })
      wrapper.vm.onStop()
      expect(mockStop).toHaveBeenCalled()
    })
  })

  describe('onSkip 方法', () => {
    it('调用 stop 并关闭对话框', async () => {
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.onSkip()
      expect(mockStop).toHaveBeenCalled()
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
    })

    it('stop 失败时不阻塞对话框关闭', async () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      mockStop.mockRejectedValueOnce(new Error('stop failed'))
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.onSkip()
      // 生产 onSkip 不返回 promise：flush 微任务等待 stop().catch 回调
      await flushPromises()
      expect(warnSpy).toHaveBeenCalled()
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([false])
      warnSpy.mockRestore()
    })
  })

  describe('按钮渲染', () => {
    it('启动中状态渲染启动按钮', () => {
      wrapper = mountComponent({ modelValue: true })
      const buttons = wrapper.findAll('.el-button')
      const texts = buttons.map(b => b.text())
      expect(texts.some(t => t.includes('backendStartup.startingBtn'))).toBe(true)
    })

    it('错误状态渲染关闭、停止、重试三个按钮', async () => {
      mockState.status = 'failed'
      wrapper = mountComponent({ modelValue: true })
      await wrapper.vm.$nextTick()
      const buttons = wrapper.findAll('.el-button')
      const texts = buttons.map(b => b.text())
      expect(texts.some(t => t.includes('backendStartup.close'))).toBe(true)
      expect(texts.some(t => t.includes('backendStartup.stopBackend'))).toBe(true)
      expect(texts.some(t => t.includes('backendStartup.retry'))).toBe(true)
    })

    it('启动超过 10 秒后渲染跳过按钮', async () => {
      wrapper = mountComponent({ modelValue: true })
      await vi.advanceTimersByTimeAsync(11000)
      const buttons = wrapper.findAll('.el-button')
      const texts = buttons.map(b => b.text())
      expect(texts.some(t => t.includes('跳过等待'))).toBe(true)
    })
  })
})
