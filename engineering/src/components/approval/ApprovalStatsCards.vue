<!--
  ApprovalStatsCards - 审批统计卡片
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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Component } from 'vue'
import { Check, Clock, CircleClose } from '@element-plus/icons-vue'
import StatsCards from '@/components/base/StatsCards.vue'

interface Props {
  counts: {
    pending: number
    under_review: number
    approved: number
    rejected: number
  }
  size?: 'small' | 'default' | 'large'
}

const props = withDefaults(defineProps<Props>(), {
  size: 'default',
})

const { t } = useI18n()

/** 定义图标映射 */
function getIconForType(type: string): Component | undefined {
  switch (type) {
    case 'pending':
      return Clock
    case 'under_review':
      return Clock
    case 'approved':
      return Check
    case 'rejected':
      return CircleClose
    default:
      return undefined
  }
}

/** 统计卡片列表 */
const stats = computed(() => [
  {
    label: t('approvalDashboard.statPending'),
    value: props.counts.pending,
    icon: getIconForType('pending'),
    type: 'warning' as const,
    subLabel: `等待审批 ${props.counts.pending} 个`,
  },
  {
    label: t('approvalDashboard.statUnderReview'),
    value: props.counts.under_review,
    icon: getIconForType('under_review'),
    type: 'info' as const,
    subLabel: `审核中 ${props.counts.under_review} 个`,
  },
  {
    label: t('approvalDashboard.statApproved'),
    value: props.counts.approved,
    icon: getIconForType('approved'),
    type: 'success' as const,
    subLabel: `已批准 ${props.counts.approved} 个`,
  },
  {
    label: t('approvalDashboard.statRejected'),
    value: props.counts.rejected,
    icon: getIconForType('rejected'),
    type: 'danger' as const,
    subLabel: `已拒绝 ${props.counts.rejected} 个`,
  },
])

defineEmits<{
  (e: 'card-click', card: { type: string }): void
}>()
</script>
