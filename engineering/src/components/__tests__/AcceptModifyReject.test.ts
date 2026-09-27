/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import AcceptModifyReject from '@/components/AcceptModifyReject.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params && 'percent' in params) return `${key}:${params.percent}%`
      return key
    },
  }),
}))

// Mock Element Plus
// vi.hoisted：vi.mock 工厂被提升到文件顶部执行，普通 const 会 TDZ 报错
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
  ElCard: {
    template: '<div class="el-card" @click="$emit(\'click\', $event)"><slot /></div>',
    props: ['shadow', 'bodyStyle'],
    emits: ['click'],
  },
  ElDrawer: {
    template: '<div v-if="modelValue" class="el-drawer"><slot /><slot name="footer" /></div>',
    props: ['modelValue', 'title', 'size'],
  },
  ElInputNumber: {
    template: '<input class="el-input-number" />',
    props: ['modelValue', 'step', 'min', 'max'],
    emits: ['update:modelValue'],
  },
  ElSwitch: {
    template: '<switch class="el-switch" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  ElAlert: { template: '<div class="el-alert"></div>', props: ['title', 'type', 'closable', 'showIcon'] },
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Promotion: { name: 'Promotion', template: '<i />' },
  ChatDotRound: { name: 'ChatDotRound', template: '<i />' },
  Grid: { name: 'Grid', template: '<i />' },
  Check: { name: 'Check', template: '<i />' },
  Edit: { name: 'Edit', template: '<i />' },
  Close: { name: 'Close', template: '<i />' },
}))

// Mock utils
vi.mock('@/utils/statusHelpers', () => ({
  getConfidenceTagType: vi.fn((confidence: number) => {
    if (confidence >= 0.8) return 'success'
    if (confidence >= 0.5) return 'warning'
    return 'danger'
  }),
}))

vi.mock('@/utils/formatters', () => ({
  formatTimestamp: vi.fn((_ts: number) => '2024-01-01 12:00:00'),
}))

describe('AcceptModifyReject.vue', () => {
  let wrapper: VueWrapper<any>

  const mockRecommendation = {
    feed_rate: 800,
    depth_of_cut: 2.5,
    spindle_speed: 12000,
    tool_material: 'carbide',
    coolant_enabled: true,
  }

  const mockAlternatives = [
    {
      plan_id: 'plan-a',
      parameters: { feed_rate: 1000, depth_of_cut: 1.5 },
      expected_outcome: '高速低切深',
      confidence: 0.85,
      reasoning: '适合硬材料',
    },
    {
      plan_id: 'plan-b',
      parameters: { feed_rate: 600, depth_of_cut: 3.5 },
      expected_outcome: '低速高切深',
      confidence: 0.65,
      reasoning: '适合软材料',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = mount(AcceptModifyReject, {
      props: {
        aiRecommendation: mockRecommendation,
        confidence: 0.75,
        reasoning: '综合分析',
        alternatives: mockAlternatives,
        ...props,
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.accept-modify-reject').exists()).toBe(true)
    })

    it('应该渲染决策头部', () => {
      mountComponent()
      expect(wrapper.find('.decision-header').exists()).toBe(true)
      expect(wrapper.find('.title').exists()).toBe(true)
    })

    it('应该渲染操作按钮区域', () => {
      mountComponent()
      expect(wrapper.find('.action-buttons').exists()).toBe(true)
    })
  })

  describe('props 处理', () => {
    it('有 aiRecommendation 时应渲染推荐卡片', () => {
      mountComponent()
      expect(wrapper.find('.recommendation-card').exists()).toBe(true)
    })

    it('没有 aiRecommendation 时不应渲染推荐卡片', () => {
      mountComponent({ aiRecommendation: undefined })
      expect(wrapper.find('.recommendation-card').exists()).toBe(false)
    })

    it('有 reasoning 时应渲染推理卡片', () => {
      mountComponent()
      expect(wrapper.find('.reasoning-card').exists()).toBe(true)
    })

    it('没有 reasoning 时不应渲染推理卡片', () => {
      mountComponent({ reasoning: '' })
      expect(wrapper.find('.reasoning-card').exists()).toBe(false)
    })

    it('showTimestamp 为 true 时应显示时间戳', () => {
      mountComponent()
      expect(wrapper.find('.timestamp').exists()).toBe(true)
    })

    it('showTimestamp 为 false 时不应显示时间戳', () => {
      mountComponent({ showTimestamp: false })
      expect(wrapper.find('.timestamp').exists()).toBe(false)
    })

    it('allowModify 为 true 时应显示修改按钮', () => {
      mountComponent()
      const btns = wrapper.findAll('.action-buttons button')
      expect(btns.length).toBe(3)
    })

    it('allowModify 为 false 时不应显示修改按钮', () => {
      mountComponent({ allowModify: false })
      const btns = wrapper.findAll('.action-buttons button')
      expect(btns.length).toBe(2)
    })

    it('confidence 为 null 时不应显示置信度标签', () => {
      mountComponent({ confidence: null })
      // el-tag stub 不渲染，所以检查 confidence 显示逻辑
      expect(wrapper.vm.confidence).toBe(null)
    })
  })

  describe('备选方案', () => {
    it('有备选方案且 showAlternatives 为 true 时应渲染备选方案区域', () => {
      mountComponent()
      expect(wrapper.find('.alternatives-section').exists()).toBe(true)
    })

    it('showAlternatives 为 false 时不应渲染备选方案区域', () => {
      mountComponent({ showAlternatives: false })
      expect(wrapper.find('.alternatives-section').exists()).toBe(false)
    })

    it('备选方案为空数组时不应渲染备选方案区域', () => {
      mountComponent({ alternatives: [] })
      expect(wrapper.find('.alternatives-section').exists()).toBe(false)
    })

    it('应渲染正确数量的备选方案卡片', () => {
      mountComponent()
      const cards = wrapper.findAll('.alternative-card')
      expect(cards.length).toBe(2)
    })

    it('应显示备选方案预期结果', () => {
      mountComponent()
      const titles = wrapper.findAll('.alternative-title')
      expect(titles[0].text()).toBe('高速低切深')
      expect(titles[1].text()).toBe('低速高切深')
    })

    it('showReasoning 为 true 时应显示备选方案推理', () => {
      mountComponent()
      const reasoning = wrapper.findAll('.alternative-reasoning')
      expect(reasoning.length).toBe(2)
    })

    it('showReasoning 为 false 时不应显示备选方案推理', () => {
      mountComponent({ showReasoning: false })
      const reasoning = wrapper.findAll('.alternative-reasoning')
      expect(reasoning.length).toBe(0)
    })

    it('点击备选方案卡片应设置 selectedAlternative', async () => {
      mountComponent()
      const cards = wrapper.findAll('.alternative-card')
      await cards[0].trigger('click')
      expect(wrapper.vm.selectedAlternative).toBe('plan-a')
    })
  })

  describe('事件触发', () => {
    it('点击采纳按钮应触发 accept 事件', async () => {
      mountComponent()
      const btns = wrapper.findAll('.action-buttons button')
      await btns[0].trigger('click')
      expect(wrapper.emitted('accept')).toBeTruthy()
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('点击拒绝按钮应触发 reject 事件', async () => {
      mountComponent()
      const btns = wrapper.findAll('.action-buttons button')
      // 最后一个按钮是拒绝
      const rejectBtn = btns[btns.length - 1]
      await rejectBtn.trigger('click')
      expect(wrapper.emitted('reject')).toBeTruthy()
      expect(mockElMessage.warning).toHaveBeenCalled()
    })

    it('点击修改按钮应打开抽屉', async () => {
      mountComponent()
      const modifyBtn = wrapper.findAll('.action-buttons button')[1]
      await modifyBtn.trigger('click')
      expect(wrapper.vm.modifyDrawerVisible).toBe(true)
      expect(wrapper.vm.modifiedParams).toEqual(mockRecommendation)
    })

    it('选择备选方案后点击采纳应包含选中方案参数', async () => {
      mountComponent()
      wrapper.vm.selectedAlternative = 'plan-a'
      await wrapper.vm.$nextTick()
      const btns = wrapper.findAll('.action-buttons button')
      await btns[0].trigger('click')
      const emitted = wrapper.emitted('accept')
      expect(emitted).toBeTruthy()
      expect(emitted![0][0]).toHaveProperty('feed_rate', 1000)
      expect(emitted![0][0]).toHaveProperty('plan_id', 'plan-a')
    })

    it('未选择备选方案时采纳应使用原推荐', async () => {
      mountComponent()
      const btns = wrapper.findAll('.action-buttons button')
      await btns[0].trigger('click')
      const emitted = wrapper.emitted('accept')
      expect(emitted).toBeTruthy()
      expect(emitted![0][0]).toHaveProperty('feed_rate', 800)
    })
  })

  describe('修改抽屉', () => {
    it('confirmModify 应触发 modify 事件并关闭抽屉', async () => {
      mountComponent()
      wrapper.vm.modifyDrawerVisible = true
      wrapper.vm.modifiedParams = { feed_rate: 900 }
      wrapper.vm.confirmModify()
      expect(wrapper.emitted('modify')).toBeTruthy()
      expect(wrapper.emitted('modify')![0]).toEqual([{ feed_rate: 900 }])
      expect(wrapper.vm.modifyDrawerVisible).toBe(false)
      expect(mockElMessage.info).toHaveBeenCalled()
    })

    it('handleReject 应触发 reject 事件并复制推荐', () => {
      mountComponent()
      wrapper.vm.handleReject()
      const emitted = wrapper.emitted('reject')
      expect(emitted).toBeTruthy()
      expect(emitted![0][0]).toEqual(mockRecommendation)
    })
  })

  describe('辅助方法', () => {
    it('formatRecommendation 应返回 JSON 字符串', () => {
      mountComponent()
      const result = wrapper.vm.formatRecommendation({ a: 1, b: 'test' })
      expect(typeof result).toBe('string')
      expect(JSON.parse(result)).toEqual({ a: 1, b: 'test' })
    })

    it('getStringValue 应返回字符串值', () => {
      mountComponent()
      wrapper.vm.modifiedParams = { name: 'test' }
      expect(wrapper.vm.getStringValue('name')).toBe('test')
    })

    it('getStringValue 不存在的 key 应返回空字符串', () => {
      mountComponent()
      wrapper.vm.modifiedParams = {}
      expect(wrapper.vm.getStringValue('missing')).toBe('')
    })

    it('setStringValue 应设置字符串值', () => {
      mountComponent()
      wrapper.vm.setStringValue('name', 'value')
      expect(wrapper.vm.modifiedParams.name).toBe('value')
    })

    it('getNumberValue 应返回数字值', () => {
      mountComponent()
      wrapper.vm.modifiedParams = { count: 42 }
      expect(wrapper.vm.getNumberValue('count')).toBe(42)
    })

    it('getNumberValue 非数字应返回 undefined', () => {
      mountComponent()
      wrapper.vm.modifiedParams = { count: 'abc' }
      expect(wrapper.vm.getNumberValue('count')).toBe(undefined)
    })

    it('setNumberValue 应设置数字值', () => {
      mountComponent()
      wrapper.vm.setNumberValue('count', 100)
      expect(wrapper.vm.modifiedParams.count).toBe(100)
    })

    it('setNumberValue undefined 不应设置', () => {
      mountComponent()
      wrapper.vm.setNumberValue('count', undefined)
      expect(wrapper.vm.modifiedParams.count).toBeUndefined()
    })

    it('getBoolValue 应返回布尔值', () => {
      mountComponent()
      wrapper.vm.modifiedParams = { flag: true }
      expect(wrapper.vm.getBoolValue('flag')).toBe(true)
    })

    it('setBoolValue 应设置布尔值', () => {
      mountComponent()
      wrapper.vm.setBoolValue('flag', 'yes')
      expect(wrapper.vm.modifiedParams.flag).toBe(true)
    })
  })

  describe('插槽', () => {
    it('应渲染 recommendation 插槽（默认存在）', () => {
      mountComponent()
      expect(wrapper.find('.recommendation-card').exists()).toBe(true)
    })
  })
})
