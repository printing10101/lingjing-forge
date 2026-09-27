<template>
  <div v-loading="loading" class="tab-content">
    <el-empty
      v-if="!status && !loading"
      :description="t('flywheel.emptyNoStatus')"
      :image-size="80"
    />
    <template v-else-if="status">
      <el-row :gutter="16" class="metric-cards">
        <el-col :span="6">
          <div class="metric-card metric-card--health">
            <div class="metric-card__label">{{ t('flywheel.metricHealthScore') }}</div>
            <div class="metric-card__value">
              {{ store.formatNumber(status.health_score) }}
            </div>
            <div class="metric-card__unit">/ 100</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricDataVolume') }}</div>
            <div class="metric-card__value">
              {{ store.formatNumber(status.data_volume) }}
            </div>
            <div class="metric-card__unit">{{ t('flywheel.unitRecords') }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricModelQuality') }}</div>
            <div class="metric-card__value">
              {{ store.formatPercent(status.model_quality) }}
            </div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricAdoptionRate') }}</div>
            <div class="metric-card__value">
              {{ store.formatPercent(status.adoption_rate) }}
            </div>
          </div>
        </el-col>
      </el-row>

      <el-row :gutter="16" class="metric-cards">
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricUncertainty') }}</div>
            <div class="metric-card__value">
              {{ (status.uncertainty_mean ?? 0).toFixed(3) }}
            </div>
            <div class="metric-card__unit">/ 1</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricFeedbackDelay') }}</div>
            <div class="metric-card__value">
              {{ store.formatNumber(status.feedback_delay) }}
            </div>
            <div class="metric-card__unit">{{ t('flywheel.unitMinutes') }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricStatus') }}</div>
            <div class="metric-card__value">
              <el-tag :type="healthTagType">{{ healthStatusLabel }}</el-tag>
            </div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-card">
            <div class="metric-card__label">{{ t('flywheel.metricTimestamp') }}</div>
            <div class="metric-card__value metric-card__value--small">
              {{ store.formatTime(status.timestamp) }}
            </div>
          </div>
        </el-col>
      </el-row>

      <el-card class="section-card" shadow="never">
        <template #header>
          <span class="section-title">{{ t('flywheel.weeklyReportTitle') }}</span>
          <el-button
            size="small"
            link
            :loading="reportLoading"
            @click="$emit('fetch-weekly-report')"
          >
            {{ t('flywheel.btnRegenerate') }}
          </el-button>
        </template>
        <el-empty
          v-if="!weeklyReport && !reportLoading"
          :description="t('flywheel.emptyNoReport')"
          :image-size="60"
        />
        <div v-else-if="weeklyReport" class="report-body">
          <el-descriptions :column="2" border>
            <el-descriptions-item :label="t('flywheel.reportGeneratedAt')">
              {{ store.formatTime(weeklyReport.generated_at) }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('flywheel.reportType')">
              {{ weeklyReport.report_type }}
            </el-descriptions-item>
            <el-descriptions-item
              v-if="weeklyReport.period"
              :label="t('flywheel.reportPeriod')"
            >
              {{ weeklyReport.period.start }} ~ {{ weeklyReport.period.end }}
            </el-descriptions-item>
            <el-descriptions-item
              v-if="weeklyReport.summary"
              :label="t('flywheel.reportSummary')"
            >
              <pre class="report-summary">{{ JSON.stringify(weeklyReport.summary, null, 2) }}</pre>
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useFlywheelStore } from '@/stores/flywheel'
import type { FlywheelStatus, FlywheelWeeklyReport } from '@/stores/flywheel'

const { t } = useI18n()
const store = useFlywheelStore()

defineProps<{
  status: FlywheelStatus | null
  loading: boolean
  weeklyReport: FlywheelWeeklyReport | null
  reportLoading: boolean
  healthTagType: 'success' | 'warning' | 'danger' | 'info'
  healthStatusLabel: string
}>()

defineEmits<{
  'fetch-weekly-report': []
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

.metric-card--health {
  background: linear-gradient(135deg, var(--accent-light), var(--el-bg-color));
  border-color: var(--brand-200);
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

.metric-card__value--small {
  font-size: 13px;
  font-weight: 500;
}

.metric-card__unit {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
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

.report-body {
  padding: 4px 0;
}

.report-summary {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
}
</style>