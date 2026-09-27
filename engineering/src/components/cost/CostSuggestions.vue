<template>
  <el-card
    v-if="suggestions.length > 0"
    shadow="hover"
    class="optimization-card"
  >
    <template #header>
      <div class="card-header">
        <span>{{ t('costDashboard.suggestionsTitle') }}</span>
        <el-button
          size="small"
          :loading="loading"
          @click="$emit('refresh')"
        >
          <el-icon style="margin-right:4px">
            <Refresh />
          </el-icon>{{ t('costDashboard.btnRefresh') }}
        </el-button>
      </div>
    </template>

    <el-row :gutter="16">
      <el-col
        v-for="s in suggestions"
        :key="s.suggestion_id"
        :span="8"
      >
        <el-card
          shadow="never"
          class="suggestion-card"
        >
          <div class="suggestion-header">
            <el-tag
              :type="s.priority === 'high' ? 'danger' : 'warning'"
              size="small"
            >
              {{ s.priority === 'high' ? t('costDashboard.priorityHigh') : t('costDashboard.priorityMedium') }}
            </el-tag>
            <el-tag
              size="small"
              type="info"
              style="margin-left:4px"
            >
              {{ suggestionCategory(s.category) }}
            </el-tag>
          </div>
          <h4 class="suggestion-title">
            {{ s.title }}
          </h4>
          <p class="suggestion-desc">
            {{ s.description }}
          </p>
          <div class="suggestion-stats">
            <div class="stat">
              <span class="stat-value text-danger">{{ formatCost(s.current_cost) }}</span>
              <span class="stat-label">{{ t('costDashboard.statCurrentCost') }}</span>
            </div>
            <div class="stat">
              <span class="stat-value text-success">{{ formatCost(s.estimated_savings) }}</span>
              <span class="stat-label">{{ t('costDashboard.statEstimatedSavings') }}</span>
            </div>
            <div class="stat">
              <span class="stat-value text-warning">{{ s.savings_percentage.toFixed(0) }}%</span>
              <span class="stat-label">{{ t('costDashboard.statSavingsPercentage') }}</span>
            </div>
          </div>
          <p class="suggestion-reco">
            <strong>{{ t('costDashboard.recommendationLabel') }}</strong>{{ s.recommendation }}
          </p>
        </el-card>
      </el-col>
    </el-row>
  </el-card>
</template>

<script lang="ts" setup>
import { Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps({
  suggestions: {
    type: Array as PropType<any[]>,
    required: true,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
})

defineEmits<{
  'refresh': []
}>()

function formatCost(value: number): string {
  if (value >= 1) return `$${value.toFixed(2)}`
  if (value >= 0.01) return `$${value.toFixed(4)}`
  return `$${value.toFixed(6)}`
}

function suggestionCategory(cat: string): string {
  const map: Record<string, string> = {
    model_optimization: t('costDashboard.categoryModelOptimization'),
    resource_optimization: t('costDashboard.categoryResourceOptimization'),
    training_efficiency: t('costDashboard.categoryTrainingEfficiency'),
  }
  return map[cat] || cat
}
</script>

<style scoped>
.optimization-card {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.suggestion-card {
  margin-bottom: 8px;
}

.suggestion-header {
  margin-bottom: 8px;
}

.suggestion-title {
  margin: 8px 0;
  font-size: 15px;
  color: var(--text-primary);
}

.suggestion-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 12px;
  line-height: 1.6;
}

.suggestion-stats {
  display: flex;
  gap: 16px;
  margin-bottom: 12px;
  padding: 8px 0;
  border-top: 1px solid var(--border-light);
  border-bottom: 1px solid var(--border-light);
}

.suggestion-stats .stat {
  flex: 1;
  text-align: center;
}

.suggestion-stats .stat-value {
  display: block;
  font-size: 16px;
  font-weight: 700;
}

.suggestion-stats .stat-label {
  display: block;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.suggestion-reco {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.text-danger { color: var(--error); }
.text-success { color: var(--success); }
.text-warning { color: var(--warning); }
</style>