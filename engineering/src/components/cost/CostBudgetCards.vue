<template>
  <el-row :gutter="16" class="budget-status-row">
    <el-col
      v-for="bp in budgetProgresses"
      :key="bp.key"
      :span="6"
    >
      <el-card
        shadow="hover"
        class="budget-card"
        :class="'budget-' + bp.status"
      >
        <div class="budget-card-title">
          {{ bp.label }}
        </div>
        <el-progress
          :percentage="bp.percentage"
          :status="bp.progressStatus"
          :stroke-width="8"
        />
        <div class="budget-card-detail">
          <span class="used">{{ bp.usedStr }}</span>
          <span class="separator">/</span>
          <span class="limit">{{ bp.limitStr }}</span>
        </div>
        <el-tag
          :type="bp.tagType"
          size="small"
        >
          {{ bp.statusLabel }}
        </el-tag>
      </el-card>
    </el-col>
  </el-row>
</template>

<script lang="ts" setup>
import type { PropType } from 'vue'

interface BudgetProgress {
  key: string
  label: string
  percentage: number
  progressStatus?: 'success' | 'warning' | 'exception'
  used: number
  limit: number
  usedStr: string
  limitStr: string
  status: string
  tagType: 'success' | 'warning' | 'danger' | 'info'
  statusLabel: string
}

defineProps({
  budgetProgresses: {
    type: Array as PropType<BudgetProgress[]>,
    required: true,
    default: () => [],
  },
})
</script>

<style scoped>
.budget-status-row {
  margin-bottom: 16px;
}

.budget-card {
  text-align: center;
}

.budget-card.budget-warning {
  border-color: var(--warning);
}

.budget-card.budget-exceeded {
  border-color: var(--error);
}

.budget-card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.budget-card-detail {
  margin: 8px 0;
  font-size: 13px;
}

.budget-card-detail .used {
  color: var(--accent-primary);
  font-weight: 600;
}

.budget-card-detail .separator {
  color: var(--border-medium);
  margin: 0 4px;
}

.budget-card-detail .limit {
  color: var(--text-tertiary);
}
</style>