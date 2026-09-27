<template>
  <div class="tab-content">
    <el-row :gutter="16" class="metric-cards">
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-card__label">{{ t('flywheel.feedbackDataVolume') }}</div>
          <div class="metric-card__value">
            {{ store.formatNumber(feedbackStats.dataVolume) }}
          </div>
          <div class="metric-card__unit">{{ t('flywheel.unitRecords') }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-card__label">{{ t('flywheel.feedbackAdoptionRate') }}</div>
          <div class="metric-card__value">
            {{ store.formatPercent(feedbackStats.adoptionRate) }}
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-card__label">{{ t('flywheel.feedbackDelay') }}</div>
          <div class="metric-card__value">
            {{ store.formatNumber(feedbackStats.feedbackDelay) }}
          </div>
          <div class="metric-card__unit">{{ t('flywheel.unitMinutes') }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric-card">
          <div class="metric-card__label">{{ t('flywheel.feedbackHealthScore') }}</div>
          <div class="metric-card__value">
            {{ store.formatNumber(feedbackStats.healthScore) }}
          </div>
          <div class="metric-card__unit">/ 100</div>
        </div>
      </el-col>
    </el-row>

    <el-alert
      :title="t('flywheel.feedbackAdoptionHint')"
      type="info"
      show-icon
      :closable="false"
      class="info-banner"
    />

    <el-card class="section-card" shadow="never">
      <template #header>
        <span class="section-title">{{ t('flywheel.metricDefinitionsTitle') }}</span>
        <el-button
          size="small"
          link
          :loading="definitionsLoading"
          @click="$emit('reload-definitions')"
        >
          {{ t('flywheel.btnReload') }}
        </el-button>
      </template>
      <el-table
        :data="metricDefinitions"
        size="small"
        stripe
        :empty-text="t('flywheel.emptyNoDefinitions')"
      >
        <el-table-column
          prop="name"
          :label="t('flywheel.colMetricName')"
          width="180"
        />
        <el-table-column
          prop="description"
          :label="t('flywheel.colMetricDescription')"
        />
        <el-table-column
          prop="unit"
          :label="t('flywheel.colMetricUnit')"
          width="100"
        />
        <el-table-column
          prop="range"
          :label="t('flywheel.colMetricRange')"
          width="120"
        />
        <el-table-column
          prop="calculation"
          :label="t('flywheel.colMetricCalculation')"
          show-overflow-tooltip
        />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useFlywheelStore } from '@/stores/flywheel'
import type { MetricDefinition, FeedbackStats } from '@/stores/flywheel'

const { t } = useI18n()
const store = useFlywheelStore()

defineProps<{
  feedbackStats: FeedbackStats
  metricDefinitions: MetricDefinition[]
  definitionsLoading: boolean
}>()

defineEmits<{
  'reload-definitions': []
}>()
</script>

<style scoped>
.tab-content {
  padding: 8px 4px;
}

.metric-cards {
  margin-bottom: 16px;
}

.metric-card {
  padding: 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--radius-sm);
  background: var(--el-bg-color);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.metric-card:hover {
  border-color: var(--brand-300);
  box-shadow: var(--shadow-sm);
}

.metric-card__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

.metric-card__value {
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  line-height: 1.4;
}

.metric-card__unit {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.info-banner {
  margin-bottom: 16px;
}

.section-card {
  margin-bottom: 16px;
}

.section-card :deep(.el-card__header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
</style>