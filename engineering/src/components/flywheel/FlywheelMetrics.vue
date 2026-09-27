<template>
  <div v-loading="metricsLoading" class="tab-content">
    <div class="filter-bar">
      <span class="filter-label">{{ t('flywheel.daysRangeLabel') }}</span>
      <el-select
        :model-value="metricsDays"
        size="small"
        style="width: 120px"
        @update:model-value="handleDaysChange"
      >
        <el-option :value="1" label="1 天" />
        <el-option :value="7" label="7 天" />
        <el-option :value="14" label="14 天" />
        <el-option :value="30" label="30 天" />
        <el-option :value="90" label="90 天" />
      </el-select>
      <el-button
        size="small"
        type="primary"
        @click="$emit('refresh')"
      >
        {{ t('flywheel.btnRefresh') }}
      </el-button>
    </div>

    <el-card class="section-card" shadow="never">
      <template #header>
        <span class="section-title">{{ t('flywheel.currentMetricsTitle') }}</span>
      </template>
      <el-empty
        v-if="!currentMetrics && !metricsLoading"
        :description="t('flywheel.emptyNoCurrentMetrics')"
        :image-size="60"
      />
      <el-descriptions
        v-else-if="currentMetrics"
        :column="3"
        border
      >
        <el-descriptions-item :label="t('flywheel.metricDataVolume')">
          {{ store.formatNumber(currentMetrics.data_volume) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('flywheel.metricModelQuality')">
          {{ store.formatPercent(currentMetrics.model_quality) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('flywheel.metricAdoptionRate')">
          {{ store.formatPercent(currentMetrics.adoption_rate) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('flywheel.metricUncertainty')">
          {{ (currentMetrics.uncertainty_mean ?? 0).toFixed(3) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('flywheel.metricFeedbackDelay')">
          {{ store.formatNumber(currentMetrics.feedback_delay) }}
          {{ t('flywheel.unitMinutes') }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('flywheel.metricTimestamp')">
          {{ store.formatTime(currentMetrics.timestamp) }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header>
        <span class="section-title">
          {{ t('flywheel.historicalMetricsTitle') }}
          <span class="period-hint">
            ({{ t('flywheel.periodDaysLabel', { days: metricsPeriodDays }) }})
          </span>
        </span>
      </template>
      <el-empty
        v-if="historicalMetrics.length === 0"
        :description="t('flywheel.emptyNoHistoricalMetrics')"
        :image-size="60"
      />
      <el-table
        v-else
        :data="historicalMetrics"
        size="small"
        stripe
        max-height="480"
      >
        <el-table-column
          prop="timestamp"
          :label="t('flywheel.colTimestamp')"
          width="200"
        >
          <template #default="{ row }">
            {{ store.formatTime(row.timestamp) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="data_volume"
          :label="t('flywheel.metricDataVolume')"
          width="140"
        >
          <template #default="{ row }">
            {{ store.formatNumber(row.data_volume) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="model_quality"
          :label="t('flywheel.metricModelQuality')"
          width="140"
        >
          <template #default="{ row }">
            {{ store.formatPercent(row.model_quality) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="adoption_rate"
          :label="t('flywheel.metricAdoptionRate')"
          width="140"
        >
          <template #default="{ row }">
            {{ store.formatPercent(row.adoption_rate) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="uncertainty_mean"
          :label="t('flywheel.metricUncertainty')"
          width="140"
        >
          <template #default="{ row }">
            {{ (row.uncertainty_mean ?? 0).toFixed(3) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="feedback_delay"
          :label="t('flywheel.metricFeedbackDelay')"
        >
          <template #default="{ row }">
            {{ store.formatNumber(row.feedback_delay) }}
            {{ t('flywheel.unitMinutes') }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useFlywheelStore } from '@/stores/flywheel'
import type { FlywheelMetricPoint } from '@/stores/flywheel'

const { t } = useI18n()
const store = useFlywheelStore()

defineProps<{
  metricsLoading: boolean
  currentMetrics: FlywheelMetricPoint | null
  historicalMetrics: FlywheelMetricPoint[]
  metricsPeriodDays: number
  metricsDays: number
}>()

const emit = defineEmits<{
  'update:metrics-days': [days: number]
  refresh: []
}>()

function handleDaysChange(value: number): void {
  emit('update:metrics-days', value)
}
</script>

<style scoped>
.tab-content {
  padding: 8px 4px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.filter-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
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

.period-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: normal;
  margin-left: 4px;
}
</style>