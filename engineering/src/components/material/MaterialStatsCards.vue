<!--
  MaterialStatsCards - 物料统计卡片
  兼容层：直接使用通用 StatsCards 组件
  
  ## 注意
  此文件为兼容层，实际实现已迁移到 @/components/base/StatsCards.vue
-->
<template>
  <StatsCards
    :cards="cards"
    :auto-wrap="true"
    :size="size"
  />
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import StatsCards from '@/components/base/StatsCards.vue'

export interface MaterialStatsCard {
  label: string
  value: number
  icon: Component | string
  type: 'success' | 'warning' | 'danger' | 'info' | 'default'
  subLabel?: string
}

interface Props {
  cards: MaterialStatsCard[]
  size?: 'small' | 'default' | 'large'
}

withDefaults(defineProps<Props>(), {
  size: 'default',
})
</script>
