<!--
  KpiCards - KPI 统计卡片
  兼容层：基于通用 StatsCards + 趋势箭头支持
  
  ## 特性
  - 支持趋势箭头（↑/↓）
  - 支持自定义颜色和背景
  - 基于通用 StatsCards 组件实现
-->
<template>
  <StatsCards
    :cards="cards"
    :auto-wrap="true"
    :size="size"
  />
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import type { Component } from 'vue'
import StatsCards from '@/components/base/StatsCards.vue'

/** 兼容旧的接口 */
export interface KPICard {
  title: string
  value: string
  change: string
  isPositive: boolean
  icon: object
  color: string
  iconBg: string
}

interface Props {
  kpiCards: KPICard[]
  size?: 'small' | 'default' | 'large'
}

const props = withDefaults(defineProps<Props>(), {
  size: 'default',
})

/** 转换为通用 StatsCard 格式 */
const cards = computed(() =>
  props.kpiCards.map((kpi) => ({
    label: kpi.title,
    value: kpi.value,
    icon: kpi.icon as Component,
    subLabel: `${kpi.isPositive ? '↑' : '↓'} ${kpi.change}`,
    type: 'default' as const, // 由自定义样式控制
    customStyle: {
      '--stat-card-icon-size': '24px',
      '--stat-card-icon-bg': kpi.iconBg,
      '--stat-card-icon-color': kpi.color,
    },
  })),
)
</script>

<style scoped>
/* 自定义颜色支持 */
/* 通过 CSS 变量注入，保持兼容性 */
</style>
