/**
 * StatsCards 统计卡片通用 Composable
 * 
 * 用于生成统计卡片数据和处理逻辑，支持类型匹配、图标映射、格式化等功能。
 * 
 * @example
 * ```typescript
 * const { stats, colorType } = useStatsCards([
 *   { label: '总数', value: 100, icon: 'DataAnalysis' },
 *   { label: '合格', value: 95, icon: 'CircleCheck', type: 'success' }
 * ])
 * ```
 */
import { computed } from 'vue'
import type { Component } from 'vue'

export interface StatsCardItem {
  label: string
  value: string | number
  icon?: Component | string
  /** 卡片类型（用于设置颜色） */
  type?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'default'
  /** 是否可点击 */
  clickable?: boolean
  /** 点击事件回调 */
  onClick?: () => void
  /** 自定义头部插槽内容 */
  headerSlot?: boolean
  /** 头部文本 */
  headerText?: string
  /** 子文本（可选） */
  subLabel?: string
  /** 底部操作插槽 */
  actionSlot?: boolean
  /** 底部操作按钮文本 */
  actionText?: string
  /** 底部操作按钮类型 */
  actionType?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'default'
}

/** 卡片尺寸枚举 */
export type CardSize = 'small' | 'default' | 'large'

interface UseStatsCardsOptions {
  /** 是否自动换行（grid 模式） */
  autoWrap?: boolean
  /** 卡片尺寸 */
  size?: CardSize
}

/**
 * 统计卡片 Composable
 * @param cards - 卡片数据列表
 * @param options - 配置选项
 */
export function useStatsCards(
  cards: StatsCardItem[],
  options: UseStatsCardsOptions = {},
) {
  const { size = 'default' } = options

  /**
   * 将图标名转换为 Component
   */
  function resolveIcon(icon: Component | string | undefined): Component | undefined {
    if (!icon) return undefined
    if (typeof icon === 'object') return icon
    // 字符串转图标组件
    return undefined as any // TODO: 动态图标导入
  }

  /**
   * 格式化数值显示
   */
  function formatValue(value: string | number): string {
    return String(value)
  }

  /**
   * 获取卡片的颜色类型（Element Plus tag type）
   */
  function getColorType(type: string | undefined): string {
    const map: Record<string, string> = {
      primary: 'primary',
      success: 'success',
      warning: 'warning',
      danger: 'danger',
      info: 'info',
      default: 'info',
    }
    return map[type || 'default'] || 'info'
  }

  /**
   * 获取卡片尺寸（Element Plus button size）
   */
  function getSize(): CardSize {
    const map: Record<CardSize, CardSize> = {
      small: 'small',
      default: 'default',
      large: 'large',
    }
    return map[size] || 'default'
  }

  /**
   * 计算属性：卡片列表图标
   */
  const processedCards = computed(() =>
    cards.map((card) => ({
      ...card,
      resolvedIcon: resolveIcon(card.icon),
    })),
  )

  return {
    processedCards,
    formatValue,
    colorType: getColorType,
    size: getSize,
  }
}

/**
 * 获取统计卡片的颜色 tag type
 * @deprecated 请使用 useStatsCards 返回的 colorType
 */
export function getStatColorType(type: string | undefined): string {
  const map: Record<string, string> = {
    primary: 'primary',
    success: 'success',
    warning: 'warning',
    danger: 'danger',
    info: 'info',
    default: '',
  }
  return map[type || 'default'] || ''
}
