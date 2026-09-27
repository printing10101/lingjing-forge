<!--
  QualityStatsCards - 质检统计卡片
  兼容层：直接使用通用 StatsCards 组件
  
  ## 注意
  此文件为兼容层，实际实现已迁移到 @/components/base/StatsCards.vue
-->
<template>
  <StatsCards
    :cards="stats"
    :auto-wrap="true"
    :size="size"
  />
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import StatsCards from '@/components/base/StatsCards.vue'

export interface QualityStatsCardItem {
  label: string
  value: string | number
  icon: Component | string
  type: 'primary' | 'success' | 'danger' | 'warning' | 'info' | 'default'
  subLabel?: string
}

interface Props {
  stats: QualityStatsCardItem[]
  size?: 'small' | 'default' | 'large'
}

withDefaults(defineProps<Props>(), {
  size: 'default',
})
</script>
