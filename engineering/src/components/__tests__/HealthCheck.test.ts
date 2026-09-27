import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import HealthCheck from '@/components/HealthCheck.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params && 'message' in params) return `${key}:${params.message}`
      if (params && 'count' in params) return `${key}:${params.count}`
      if (params && 'errors' in params) return `${key}:${params.errors}`
      return key
    },
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Loading: { name: 'Loading', template: '<i />' },
  RefreshRight: { name: 'RefreshRight', template: '<i />' },
  CopyDocument: { name: 'CopyDocument', template: '<i />' },
  CircleCheckFilled: { name: 'CircleCheckFilled', template: '<i />' },
  CircleCloseFilled: { name: 'CircleCloseFilled', template: '<i />' },
  WarningFilled: { name: 'WarningFilled', template: '<i />' },
}))

const elMessageError = vi.hoisted(() => vi.fn())
const elMessageSuccess = vi.hoisted(() => vi.fn())
// Mock element-plus
vi.mock('element-plus', () => ({
  ElMessage: {
    error: (...a: unknown[]) => elMessageError(...a),
    success: (...a: unknown[]) => elMessageSuccess(...a),
    info: vi.fn(),
    warning: vi.fn(),
  },
  ElTag: { template: '<span class="el-tag"><slot /></span>', props: ['type', 'size', 'effect'] },
  ElButton: {
    template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
    props: ['type', 'size', 'loading', 'disabled', 'icon'],
    emits: ['click'],
  },
  ElIcon: { template: '<span class="el-icon"><slot /></span>', props: ['size'] },
  ElAlert: {
    template: '<div class="el-alert"><slot /></div>',
    props: ['title', 'type', 'closable', 'showIcon'],
  },
  ElCollapseTransition: {
    template: '<div class="el-collapse-transition"><slot /></div>',
  },
}))

describe('HealthCheck.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
    delete (window as any).__TAURI__
  })

  const mountComponent = () => {
    wrapper = mount(HealthCheck)
    return wrapper
  }

  const setItems = (items: unknown[]) => {
    wrapper.vm.items = items
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.health-check-panel').exists()).toBe(true)
    })

    it('应该渲染状态栏', () => {
      mountComponent()
      expect(wrapper.find('.health-status-bar').exists()).toBe(true)
    })

    it('应该渲染重新检查按钮', () => {
      mountComponent()
      expect(wrapper.find('.status-actions').exists()).toBe(true)
    })
  })

  describe('overallStatus 计算属性', () => {
    it('items 为空时返回空字符串', () => {
      mountComponent()
      expect(wrapper.vm.overallStatus).toBe('')
    })

    it('全部 ok 时返回 ok', () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'ok', name: 'A', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'b', status: 'ok', name: 'B', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
      ])
      expect(wrapper.vm.overallStatus).toBe('ok')
    })

    it('存在 warning 无 error 时返回 warning', () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'ok', name: 'A', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'b', status: 'warning', name: 'B', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
      ])
      expect(wrapper.vm.overallStatus).toBe('warning')
    })

    it('存在 error 时返回 error', () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'warning', name: 'A', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'b', status: 'error', name: 'B', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
      ])
      expect(wrapper.vm.overallStatus).toBe('error')
    })
  })

  describe('errorCount / warningCount 计算属性', () => {
    it('应正确统计 error 数量', () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'error', name: 'A', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'b', status: 'ok', name: 'B', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'c', status: 'error', name: 'C', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
      ])
      expect(wrapper.vm.errorCount).toBe(2)
    })

    it('应正确统计 warning 数量', () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'warning', name: 'A', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'b', status: 'warning', name: 'B', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
        { id: 'c', status: 'ok', name: 'C', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false },
      ])
      expect(wrapper.vm.warningCount).toBe(2)
    })
  })

  describe('statusLabel 方法', () => {
    it('ok 应返回对应翻译', () => {
      mountComponent()
      expect(wrapper.vm.statusLabel('ok')).toBe('healthCheck.statusOk')
    })

    it('warning 应返回对应翻译', () => {
      mountComponent()
      expect(wrapper.vm.statusLabel('warning')).toBe('healthCheck.statusWarning')
    })

    it('error 应返回对应翻译', () => {
      mountComponent()
      expect(wrapper.vm.statusLabel('error')).toBe('healthCheck.statusError')
    })

    it('未知状态应返回原值', () => {
      mountComponent()
      expect(wrapper.vm.statusLabel('unknown')).toBe('unknown')
    })
  })

  describe('statusTagType 方法', () => {
    it('ok 应映射为 success', () => {
      mountComponent()
      expect(wrapper.vm.statusTagType('ok')).toBe('success')
    })

    it('warning 应映射为 warning', () => {
      mountComponent()
      expect(wrapper.vm.statusTagType('warning')).toBe('warning')
    })

    it('error 应映射为 danger', () => {
      mountComponent()
      expect(wrapper.vm.statusTagType('error')).toBe('danger')
    })

    it('未知状态应映射为 info', () => {
      mountComponent()
      expect(wrapper.vm.statusTagType('other')).toBe('info')
    })
  })

  describe('toggleExpand 方法', () => {
    it('展开未展开的项', () => {
      mountComponent()
      wrapper.vm.toggleExpand('a')
      expect(wrapper.vm.expandedId).toBe('a')
    })

    it('再次点击已展开的项应收起', () => {
      mountComponent()
      wrapper.vm.expandedId = 'a'
      wrapper.vm.toggleExpand('a')
      expect(wrapper.vm.expandedId).toBe(null)
    })

    it('点击不同项应切换展开', () => {
      mountComponent()
      wrapper.vm.expandedId = 'a'
      wrapper.vm.toggleExpand('b')
      expect(wrapper.vm.expandedId).toBe('b')
    })
  })

  describe('runAllChecks 方法（无 Tauri 环境）', () => {
    it('非 Tauri 环境应调用 ElMessage.error', async () => {
      mountComponent()
      await wrapper.vm.runAllChecks()
      expect(elMessageError).toHaveBeenCalled()
      expect(wrapper.vm.checking).toBe(false)
    })

    it('调用后应重置 checking 为 false', async () => {
      mountComponent()
      wrapper.vm.checking = true
      await wrapper.vm.runAllChecks()
      expect(wrapper.vm.checking).toBe(false)
    })

    it('调用后应重置 expandedId', async () => {
      mountComponent()
      wrapper.vm.expandedId = 'a'
      await wrapper.vm.runAllChecks()
      expect(wrapper.vm.expandedId).toBe(null)
    })
  })

  describe('runAllChecks 方法（模拟 Tauri 环境）', () => {
    const mockInvoke = vi.fn()
    beforeEach(async () => {
      mockInvoke.mockReset()
      ;(window as any).__TAURI__ = { invoke: mockInvoke }
      vi.resetModules()
      // 动态 import 的 @tauri-apps/api/core 需要 mock
      vi.doMock('@tauri-apps/api/core', () => ({ invoke: mockInvoke }))
    })

    afterEach(() => {
      vi.doUnmock('@tauri-apps/api/core')
    })

    it('成功时应填充 items', async () => {
      const mockResults = [
        { id: 'a', status: 'ok', name: 'A', message: 'ok', details: '', version: '1.0', fix_action: null, fix_description: null, fix_auto: false },
      ]
      mockInvoke.mockResolvedValueOnce(mockResults)
      mountComponent()
      await wrapper.vm.runAllChecks()
      expect(mockInvoke).toHaveBeenCalledWith('run_health_check')
      expect(wrapper.vm.items.length).toBe(1)
      expect(wrapper.vm.checking).toBe(false)
    })
  })

  describe('copyDiagnostics 方法（无 Tauri 环境）', () => {
    it('非 Tauri 环境应调用 ElMessage.error', async () => {
      mountComponent()
      await wrapper.vm.copyDiagnostics()
      expect(elMessageError).toHaveBeenCalled()
    })
  })

  describe('retrySingleCheck 方法（无 Tauri 环境）', () => {
    it('非 Tauri 环境应调用 ElMessage.error 并重置 singleCheckingId', async () => {
      mountComponent()
      setItems([{ id: 'a', status: 'error', name: 'A', message: '', details: '', version: null, fix_action: null, fix_description: null, fix_auto: false }])
      await wrapper.vm.retrySingleCheck('a')
      expect(elMessageError).toHaveBeenCalled()
      expect(wrapper.vm.singleCheckingId).toBe(null)
    })
  })

  describe('runAutoFix 方法（无 Tauri 环境）', () => {
    it('非 Tauri 环境应调用 ElMessage.error 并重置 fixingId', async () => {
      mountComponent()
      setItems([{ id: 'a', status: 'error', name: 'A', message: '', details: '', version: null, fix_action: 'fix', fix_description: 'desc', fix_auto: true }])
      await wrapper.vm.runAutoFix('a')
      expect(elMessageError).toHaveBeenCalled()
      expect(wrapper.vm.fixingId).toBe(null)
    })
  })

  describe('渲染', () => {
    it('有 items 时应渲染检查卡片', async () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'ok', name: '检查项A', message: '正常', details: '详情', version: '1.0', fix_action: null, fix_description: null, fix_auto: false },
      ])
      await wrapper.vm.$nextTick()
      expect(wrapper.findAll('.check-card').length).toBe(1)
    })

    it('展开卡片应显示详情', async () => {
      mountComponent()
      setItems([
        { id: 'a', status: 'error', name: 'A', message: '错误', details: '错误详情', version: null, fix_action: 'fix', fix_description: '修复说明', fix_auto: true },
      ])
      await wrapper.vm.$nextTick()
      wrapper.vm.toggleExpand('a')
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.expandedId).toBe('a')
    })
  })
})
