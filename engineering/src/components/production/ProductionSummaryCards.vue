<template>
  <div
    v-loading="loading"
    class="summary-row"
  >
    <template v-if="summaryCards.length > 0">
      <el-card
        v-for="item in summaryCards"
        :key="item.label"
        shadow="hover"
        class="summary-card"
      >
        <div class="summary-card__header">
          <span class="summary-card__label">{{ item.label }}</span>
          <el-icon
            :size="16"
            class="summary-card__trend"
            :class="item.trendClass"
          >
            <component :is="item.trendIcon" />
          </el-icon>
        </div>
        <span class="summary-card__value">{{ item.value }}</span>
        <span class="summary-card__unit">{{ item.unit }}</span>
      </el-card>
    </template>
    <el-empty
      v-else
      :description="t('productionReport.loadFailed')"
      :image-size="60"
    />
  </div>
</template>

<script setup lang="ts">
import { type Component } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface SummaryCard {
  label: string
  value: string
  unit: string
  trendIcon: Component
  trendClass: string
}

defineProps<{
  summaryCards: SummaryCard[]
  loading: boolean
}>()
</script>

<style scoped>
.summary-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.summary-card {
  text-align: center;
}

.summary-card__header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-bottom: 12px;
}

.summary-card__label {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.summary-card__trend {
  color: var(--success);
}

.summary-card__trend.trend-down {
  color: var(--error);
}

.summary-card__trend.trend-stable {
  color: var(--warning);
}

.summary-card__value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

.summary-card__unit {
  font-size: 14px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

@media (max-width: 900px) {
  .summary-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>