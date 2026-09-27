<template>
  <el-card class="wm-predict-card">
    <template #header>
      <div class="wm-predict-card__header">
        <span>{{ t('worldModel.trajectoryPrediction') }}</span>
        <el-tag v-if="lastPrediction" size="small" type="success">
          {{ t('worldModel.predictionReady') }}
        </el-tag>
      </div>
    </template>

    <!-- 预测表单 -->
    <el-form label-width="140px" label-position="left">
      <el-form-item :label="t('worldModel.fields.modelUri')">
        <el-input
          :model-value="modelUri"
          placeholder="model://world_model/1.0.0"
          @update:model-value="emit('update:model-uri', $event)"
        />
      </el-form-item>
      <el-form-item :label="t('worldModel.horizon')">
        <el-input-number
          :model-value="horizon"
          :min="MIN_HORIZON"
          :max="MAX_HORIZON"
          :step="1"
          @update:model-value="handleHorizonUpdate"
        />
        <span class="wm-form-hint">{{ t('worldModel.horizonHint') }}</span>
      </el-form-item>

      <!-- 当前状态输入 -->
      <el-divider content-position="left">{{ t('worldModel.currentState') }}</el-divider>
      <div class="wm-state-grid">
        <el-form-item
          v-for="field in STATE_FIELD_VALUES"
          :key="field"
          :label="STATE_FIELD_LABELS[field]"
          label-width="120px"
        >
          <el-input-number
            :model-value="currentState[field]"
            :step="0.01"
            controls-position="right"
            class="wm-state-input"
            @update:model-value="handleStateUpdate(field, $event)"
          />
        </el-form-item>
      </div>

      <!-- 候选动作输入 -->
      <el-divider content-position="left">{{ t('worldModel.candidateAction') }}</el-divider>
      <div class="wm-state-grid">
        <el-form-item
          v-for="field in ACTION_FIELD_VALUES"
          :key="field"
          :label="ACTION_FIELD_LABELS[field]"
          label-width="120px"
        >
          <el-input-number
            :model-value="candidateAction[field]"
            :step="0.05"
            :min="-1"
            :max="1"
            controls-position="right"
            class="wm-state-input"
            @update:model-value="handleActionUpdate(field, $event)"
          />
        </el-form-item>
      </div>

      <el-form-item>
        <el-button
          type="primary"
          :loading="predicting"
          :icon="VideoPlay"
          @click="emit('predict')"
        >
          {{ t('worldModel.runPrediction') }}
        </el-button>
        <el-button @click="emit('reset-form')">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>

    <!-- 预测结果 -->
    <div v-if="lastPrediction" class="wm-predict-result">
      <el-divider content-position="left">{{ t('worldModel.predictionResult') }}</el-divider>

      <!-- 汇总指标 -->
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item :label="t('worldModel.metrics.meanChatter')">
          <span :class="{ 'wm-metric--warn': lastPredictionMaxChatter > 0.3 }">
            {{ lastPrediction.trajectory_metrics.mean_chatter_probability.toFixed(4) }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('worldModel.metrics.maxChatter')">
          <span :class="{ 'wm-metric--warn': lastPredictionMaxChatter > 0.5 }">
            {{ lastPrediction.trajectory_metrics.max_chatter_probability.toFixed(4) }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('worldModel.metrics.cumulativeWear')">
          {{ lastPrediction.trajectory_metrics.cumulative_tool_wear.toFixed(4) }} mm
        </el-descriptions-item>
        <el-descriptions-item :label="t('worldModel.metrics.finalRoughness')">
          {{ lastPrediction.trajectory_metrics.final_surface_roughness.toFixed(4) }} μm
        </el-descriptions-item>
      </el-descriptions>

      <!-- 模型信息 -->
      <el-descriptions :column="2" border size="small" class="wm-model-info">
        <el-descriptions-item :label="t('worldModel.modelInfo.version')">
          {{ lastPrediction.model_info.world_model_version }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('worldModel.modelInfo.uncertainty')">
          {{ lastPrediction.model_info.uncertainty_estimate.toFixed(4) }}
        </el-descriptions-item>
      </el-descriptions>

      <!-- 轨迹表格 -->
      <div class="wm-trajectory-section">
        <div class="wm-trajectory-section__title">
          {{ t('worldModel.trajectorySteps') }}（{{ lastPredictionStepCount }}）
        </div>
        <el-table
          :data="lastPrediction.predicted_trajectory"
          size="small"
          max-height="320"
          border
        >
          <el-table-column prop="step" label="step" width="60" />
          <el-table-column label="chatter_prob" width="120">
            <template #default="{ row }">
              <span :class="{ 'wm-metric--warn': row.chatter_probability > 0.3 }">
                {{ row.chatter_probability.toFixed(4) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="tool_wear_inc" width="120">
            <template #default="{ row }">
              {{ row.tool_wear_increment.toFixed(4) }}
            </template>
          </el-table-column>
          <el-table-column label="Ra (μm)" width="100">
            <template #default="{ row }">
              {{ row.surface_roughness.toFixed(4) }}
            </template>
          </el-table-column>
          <el-table-column label="confidence" width="100">
            <template #default="{ row }">
              {{ row.confidence.toFixed(4) }}
            </template>
          </el-table-column>
          <el-table-column label="predicted_state">
            <template #default="{ row }">
              <pre class="wm-json-inline">{{ formatStateBrief(row.predicted_state) }}</pre>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 安全提示 -->
      <el-alert
        :title="t('worldModel.safetyNotice')"
        type="warning"
        :closable="false"
        show-icon
        class="wm-safety-alert"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { VideoPlay } from '@element-plus/icons-vue'
import {
  STATE_FIELD,
  STATE_FIELD_VALUES,
  STATE_FIELD_LABELS,
  ACTION_FIELD_VALUES,
  ACTION_FIELD_LABELS,
  MIN_HORIZON,
  MAX_HORIZON,
  type WorldModelPredictResponse,
} from '@/contracts/world_model'

const { t } = useI18n()

defineProps<{
  modelUri: string
  horizon: number
  currentState: Record<string, number>
  candidateAction: Record<string, number>
  predicting: boolean
  lastPrediction: WorldModelPredictResponse | null
  lastPredictionMaxChatter: number
  lastPredictionStepCount: number
}>()

const emit = defineEmits<{
  'update:model-uri': [value: string]
  'update:horizon': [value: number]
  'update-state': [field: string, value: number]
  'update-action': [field: string, value: number]
  'predict': []
  'reset-form': []
}>()

function handleHorizonUpdate(value: number | undefined): void {
  if (value !== undefined) {
    emit('update:horizon', value)
  }
}

function handleStateUpdate(field: string, value: number | undefined): void {
  if (value !== undefined) {
    emit('update-state', field, value)
  }
}

function handleActionUpdate(field: string, value: number | undefined): void {
  if (value !== undefined) {
    emit('update-action', field, value)
  }
}

function formatStateBrief(state: Record<string, number>): string {
  const keys = [STATE_FIELD.CHATTER_PROBABILITY, STATE_FIELD.TOOL_WEAR, STATE_FIELD.VIBRATION_RMS]
  return keys
    .map((k) => `${STATE_FIELD_LABELS[k].split(' ')[0]}=${state[k]?.toFixed(3) ?? '—'}`)
    .join(', ')
}
</script>

<style scoped>
.wm-predict-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.wm-form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.wm-state-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 16px;
}

.wm-state-input {
  width: 100%;
}

.wm-predict-result {
  margin-top: 8px;
}

.wm-model-info {
  margin-top: 12px;
}

.wm-trajectory-section {
  margin-top: 16px;
}

.wm-trajectory-section__title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
}

.wm-json-inline {
  margin: 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: pre-wrap;
}

.wm-metric--warn {
  color: var(--state-error);
  font-weight: 600;
}

.wm-safety-alert {
  margin-top: 16px;
}
</style>