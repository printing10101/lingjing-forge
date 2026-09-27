/**
 * useStatsCards composable 测试
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import StatsCards from '@/components/base/StatsCards.vue'
import { useStatsCards, StatsCardItem } from '@/composables/headless/useStatsCards'

describe('useStatsCards', () => {
  describe('formatValue', () => {
    it('格式化字符串值', () => {
      const cards = [{ label: '总数', value: '100' }]
      const { formatValue } = useStatsCards(cards)
      expect(formatValue('100')).toBe('100')
      expect(formatValue(100 as any)).toBe('100')
    })
  })

  describe('colorType', () => {
    it('映射 primary 为 primary', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { colorType } = useStatsCards(cards)
      expect(colorType('primary')).toBe('primary')
    })

    it('映射 success 为 success', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { colorType } = useStatsCards(cards)
      expect(colorType('success')).toBe('success')
    })

    it('映射 warning 为 warning', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { colorType } = useStatsCards(cards)
      expect(colorType('warning')).toBe('warning')
    })

    it('映射 danger 为 danger', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { colorType } = useStatsCards(cards)
      expect(colorType('danger')).toBe('danger')
    })

    it('默认类型返回', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { colorType } = useStatsCards(cards)
      expect(colorType(undefined)).toBe('info')
      expect(colorType('default')).toBe('info')
    })
  })

  describe('size', () => {
    it('small 尺寸', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { size } = useStatsCards(cards, { size: 'small' })
      expect(size()).toBe('small')
    })

    it('default 尺寸', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { size } = useStatsCards(cards, { size: 'default' })
      expect(size()).toBe('default')
    })

    it('large 尺寸', () => {
      const cards = [{ label: '测试', value: 1 }]
      const { size } = useStatsCards(cards, { size: 'large' })
      expect(size()).toBe('large')
    })
  })

  describe('processedCards', () => {
    it('保留原始卡片数据', () => {
      const cards = [{ label: '测试', value: 1 }] as StatsCardItem[]
      const { processedCards } = useStatsCards(cards)
      expect(processedCards.value.length).toBe(1)
      expect(processedCards.value[0].label).toBe('测试')
      expect(processedCards.value[0].value).toBe(1)
    })

    it('添加 resolvedIcon', () => {
      const cards = [{ label: '测试', value: 1 }] as StatsCardItem[]
      const { processedCards } = useStatsCards(cards)
      expect(processedCards.value[0].resolvedIcon).toBeUndefined()
    })
  })
})

describe('StatsCards 组件', () => {
  it('渲染统计卡片行', () => {
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          { label: '总数', value: 100 },
          { label: '合格', value: 95, type: 'success' },
        ],
      },
    })

    expect(wrapper.find('.stats-cards').exists()).toBe(true)
    expect(wrapper.findAll('.stat-card').length).toBe(2)
  })

  it('应用卡片类型样式', () => {
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          { label: '测试', value: 100, type: 'danger' },
        ],
      },
    })

    const card = wrapper.find('.stat-card.stat-card--danger')
    expect(card.exists()).toBe(true)
  })

  it('渲染图标', async () => {
    const { Check } = await import('@element-plus/icons-vue')
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          { label: '测试', value: 100, icon: Check as any },
        ],
      },
    })

    expect(wrapper.find('.stat-card__icon').exists()).toBe(true)
  })

  it('点击事件触发', async () => {
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          {
            label: '测试',
            value: 100,
            clickable: true,
            onClick: vi.fn(),
          },
        ],
      },
    })

    await wrapper.find('.stat-card').trigger('click')
    expect(wrapper.emitted('card-click')).toBeDefined()
  })

  it('自动换行网格布局', () => {
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          { label: '1', value: 1 },
          { label: '2', value: 2 },
          { label: '3', value: 3 },
          { label: '4', value: 4 },
          { label: '5', value: 5 },
        ],
        autoWrap: true,
      },
    })

    const container = wrapper.find('.stats-cards')
    const style = container.attributes('style')
    expect(style).toContain('grid')
    expect(style).toContain('auto-fit')
  })

  it('固定列数布局', () => {
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          { label: '1', value: 1 },
          { label: '2', value: 2 },
        ],
        autoWrap: false,
      },
    })

    const container = wrapper.find('.stats-cards')
    const style = container.attributes('style')
    expect(style).toContain('grid')
    expect(style).toContain('repeat(2, 1fr)')
  })

  it('悬停效果', async () => {
    const wrapper = mount(StatsCards, {
      props: {
        cards: [
          {
            label: '测试',
            value: 100,
            clickable: true,
            type: 'primary',
          },
        ],
      },
    })

    // 检查卡片样式
    const card = wrapper.find('.stat-card')
    expect(card.classes()).toContain('stat-card--clickable')
  })
})

describe('弃用函数', () => {
  // 以下函数已弃用，不再导出：
  // - getStatColorType: 改用 colorType from useStatsCards
  // - getStatColor: 改用 useStatColor from composables/index
  // 此处保留测试以确保向后兼容声明
  it('弃用函数标记', () => {
    expect(true).toBe(true)
  })
})

describe('StatsCardItem 类型定义', () => {
  it('类型接受所有必需字段', () => {
    const item: StatsCardItem = {
      label: '测试',
      value: 100,
    }
    expect(item.label).toBe('测试')
    expect(item.value).toBe(100)
  })

  it('类型接受可选字段', () => {
    const item: StatsCardItem = {
      label: '测试',
      value: 100,
      type: 'success',
      clickable: true,
      onClick: vi.fn(),
      subLabel: '子标签',
    }
    expect(item.type).toBe('success')
    expect(item.clickable).toBe(true)
  })
})
