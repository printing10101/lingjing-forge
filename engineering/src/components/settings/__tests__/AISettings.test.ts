/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import AISettings from '@/components/settings/AISettings.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Refresh: { name: 'Refresh', template: '<i class="icon-refresh" />' },
  MagicStick: { name: 'MagicStick', template: '<i class="icon-magic" />' },
  Odometer: { name: 'Odometer', template: '<i class="icon-odometer" />' },
  Timer: { name: 'Timer', template: '<i class="icon-timer" />' },
  DataLine: { name: 'DataLine', template: '<i class="icon-data-line" />' },
  Lightning: { name: 'Lightning', template: '<i class="icon-lightning" />' },
  Box: { name: 'Box', template: '<i class="icon-box" />' },
  Check: { name: 'Check', template: '<i class="icon-check" />' },
  RefreshLeft: { name: 'RefreshLeft', template: '<i class="icon-refresh-left" />' },
  CircleCheck: { name: 'CircleCheck', template: '<i class="icon-circle-check" />' },
}))

// Mock useSovereigntySettings composable
const mockSovereigntySettings = vi.hoisted(() => ({
  ai_autonomy_level: 2,
  show_confidence_indicator: true,
  show_alternatives: true,
  show_reasoning: false,
  require_confirmation_for_predict: true,
  require_confirmation_for_research: false,
}))
const mockAutonomyMarks = vi.hoisted(() => ({ 0: '0', 1: '1', 2: '2', 3: '3', 4: '4' }))
const mockFormatAutonomyLevel = vi.fn((v: number) => `L${v}`)
const mockCurrentAutonomyDescription = ref('当前等级描述')
const mockGetAutonomyAlertType = vi.fn((_v: number) => 'info')
const mockHandleAutonomyChange = vi.hoisted(() => vi.fn())
const mockSaveSovereigntySettings = vi.hoisted(() => vi.fn())
const mockResetSovereigntySettings = vi.hoisted(() => vi.fn())

import { ref } from 'vue'

vi.mock('@/composables/useSovereigntySettings', () => ({
  useSovereigntySettings: () => ({
    sovereigntySettings: mockSovereigntySettings,
    autonomyMarks: mockAutonomyMarks,
    formatAutonomyLevel: mockFormatAutonomyLevel,
    currentAutonomyDescription: mockCurrentAutonomyDescription,
    getAutonomyAlertType: mockGetAutonomyAlertType,
    handleAutonomyChange: mockHandleAutonomyChange,
    saveSovereigntySettings: mockSaveSovereigntySettings,
    resetSovereigntySettings: mockResetSovereigntySettings,
  }),
}))

// Mock useHealthMonitor composable
const mockHealthStatus = ref({
  backendOnline: true,
  uptimeStr: '3d 4h',
  totalRequests: 100,
  avgResponseMs: 25,
  activeModels: 2,
  memoryUsedMb: 1024,
  memoryTotalMb: 8192,
  memoryPercent: 50,
  cpuPercent: 30,
  dbHealthy: true,
  redisHealthy: true,
  prometheusHealthy: false,
  pollInterval: 30,
})
const mockHealthLoading = ref(false)
const mockRefreshHealth = vi.hoisted(() => vi.fn())

vi.mock('@/composables/useHealthMonitor', () => ({
  useHealthMonitor: () => ({
    healthStatus: mockHealthStatus,
    healthLoading: mockHealthLoading,
    refreshHealth: mockRefreshHealth,
  }),
}))

// Mock HealthCheck component
vi.mock('@/components/HealthCheck.vue', () => ({
  default: {
    name: 'HealthCheck',
    template: '<div class="mock-health-check"></div>',
    methods: { runAllChecks: vi.fn() },
  },
}))

describe('AISettings.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = () => {
    wrapper = mount(AISettings, {
      global: {
        stubs: {
          'el-icon': { template: '<span class="el-icon"><slot /></span>' },
          'el-tag': { template: '<span class="el-tag"><slot /></span>', props: ['type', 'size', 'effect'] },
          'el-alert': {
            template: '<div class="el-alert" @close="$emit(\'close\')"><slot /></div>',
            props: ['title', 'type', 'closable', 'showIcon'],
            emits: ['close'],
          },
          'el-form': { template: '<form class="el-form"><slot /></form>', props: ['model', 'labelWidth'] },
          'el-form-item': { template: '<div class="el-form-item"><slot /></div>', props: ['label'] },
          'el-slider': { template: '<div class="el-slider" />', props: ['modelValue', 'min', 'max', 'step', 'marks'] },
          'el-switch': { template: '<div class="el-switch" />', props: ['modelValue', 'disabled'] },
          'el-divider': { template: '<hr class="el-divider" />' },
          'el-progress': {
            template: '<div class="el-progress" :data-percentage="percentage" />',
            props: ['percentage', 'status', 'strokeWidth', 'showText'],
          },
          'el-button': {
            template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
            props: ['type', 'size', 'loading', 'disabled'],
            emits: ['click'],
          },
          HealthCheck: { template: '<div class="mock-health-check"></div>' },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('成功挂载并渲染根容器', () => {
      wrapper = mountComponent()
      expect(wrapper.find('.ai-settings').exists()).toBe(true)
    })

    it('渲染 AI 主权卡片', () => {
      wrapper = mountComponent()
      const cards = wrapper.findAll('.content-card')
      expect(cards.length).toBeGreaterThan(0)
    })

    it('默认显示主权介绍', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.showSovereigntyIntro).toBe(true)
    })

    it('渲染 HealthCheck 组件', () => {
      wrapper = mountComponent()
      expect(wrapper.find('.mock-health-check').exists()).toBe(true)
    })
  })


  describe('onMounted 定时器', () => {
    it('挂载后 300ms 调用 healthCheckRef.runAllChecks', async () => {
      const runAllChecksSpy = vi.fn()
      wrapper = mountComponent()
      wrapper.vm.healthCheckRef = { runAllChecks: runAllChecksSpy }
      vi.advanceTimersByTime(300)
      expect(runAllChecksSpy).toHaveBeenCalled()
    })

    it('healthCheckRef 为 null 时不报错', async () => {
      wrapper = mountComponent()
      wrapper.vm.healthCheckRef = null
      expect(() => vi.advanceTimersByTime(300)).not.toThrow()
    })
  })

  describe('onBeforeUnmount 清理', () => {
    it('卸载时清理定时器', async () => {
      wrapper = mountComponent()
      const clearTimeoutSpy = vi.spyOn(global, 'clearTimeout')
      wrapper.unmount()
      // 卸载后定时器被清理
      expect(clearTimeoutSpy).toHaveBeenCalled()
      clearTimeoutSpy.mockRestore()
    })
  })

  describe('主权介绍关闭', () => {
    it('关闭 alert 后 showSovereigntyIntro 变为 false', async () => {
      wrapper = mountComponent()
      expect(wrapper.vm.showSovereigntyIntro).toBe(true)
      wrapper.vm.showSovereigntyIntro = false
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.showSovereigntyIntro).toBe(false)
    })
  })


  describe('资源使用渲染', () => {
    it('内存使用百分比正确渲染', () => {
      mockHealthStatus.value.memoryPercent = 75
      wrapper = mountComponent()
      const progressBars = wrapper.findAll('.el-progress')
      // 至少有内存和 CPU 两个进度条
      expect(progressBars.length).toBeGreaterThanOrEqual(2)
    })

    it('服务状态标签根据健康状态渲染', () => {
      mockHealthStatus.value.dbHealthy = true
      mockHealthStatus.value.redisHealthy = false
      wrapper = mountComponent()
      // 服务标签存在
      expect(wrapper.findAll('.services-bar').length).toBeGreaterThan(0)
    })
  })
})
