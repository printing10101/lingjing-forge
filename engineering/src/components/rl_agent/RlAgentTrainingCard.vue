<template>
  <el-card class="rl-training-card">
    <template #header>
      <div class="rl-training-card__header">
        <span>{{ t('rlAgent.trainingControl') }}</span>
        <el-tag
          v-if="store.trainingStatus"
          :type="TRAINING_STATUS_TAG_TYPE[store.trainingStatus.status]"
          size="small"
        >
          {{ TRAINING_STATUS_LABELS[store.trainingStatus.status] }}
        </el-tag>
      </div>
    </template>

    <div v-loading="store.trainingStatusLoading" class="rl-training-status">
      <el-empty
        v-if="!store.trainingStatusLoading && !store.trainingStatus"
        :description="t('rlAgent.noTrainingStatus')"
      />
      <template v-else-if="store.trainingStatus">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item :label="t('rlAgent.training.status')">
            <el-tag :type="TRAINING_STATUS_TAG_TYPE[store.trainingStatus.status]" size="small">
              {{ TRAINING_STATUS_LABELS[store.trainingStatus.status] }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('rlAgent.training.progress')">
            {{ store.trainingProgress.toFixed(1) }}%
          </el-descriptions-item>
          <el-descriptions-item :label="t('rlAgent.training.currentStep')">
            {{ store.trainingStatus.current_step }} / {{ store.trainingStatus.max_steps }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('rlAgent.training.currentEpisode')">
            {{ store.trainingStatus.current_episode }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('rlAgent.training.startedAt')">
            {{ formatDateTime(store.trainingStatus.started_at) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('rlAgent.training.finishedAt')">
            {{ formatDateTime(store.trainingStatus.finished_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="store.trainingStatus.metrics" class="rl-training-metrics">
          <div class="rl-training-metrics__title">{{ t('rlAgent.training.metrics') }}</div>
          <div class="rl-metrics-grid">
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">policy_loss</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.policy_loss.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">value_loss</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.value_loss.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">entropy</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.entropy.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">approx_kl</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.approx_kl.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">clip_fraction</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.clip_fraction.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">mean_reward</span>
              <span class="rl-metric-item__value rl-metric-item__value--highlight">
                {{ store.trainingStatus.metrics.mean_reward.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">epsilon</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.epsilon.toFixed(4) }}
              </span>
            </div>
            <div class="rl-metric-item">
              <span class="rl-metric-item__label">elapsed</span>
              <span class="rl-metric-item__value">
                {{ store.trainingStatus.metrics.elapsed_seconds.toFixed(1) }}s
              </span>
            </div>
          </div>
        </div>

        <el-alert
          v-if="store.trainingStatus.error_message"
          :title="t('rlAgent.training.errorOccurred')"
          :description="store.trainingStatus.error_message"
          type="error"
          :closable="false"
          show-icon
          class="rl-training-error"
        />
      </template>
    </div>

    <div class="rl-training-controls">
      <el-form :inline="true" class="rl-training-form">
        <el-form-item :label="t('rlAgent.training.maxSteps')">
          <el-input-number
            v-model="trainingForm.max_steps"
            :min="MIN_MAX_STEPS"
            :max="MAX_MAX_STEPS"
            :step="10000"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item :label="t('rlAgent.training.algorithm')">
          <el-select v-model="trainingForm.algorithm" class="rl-training-select">
            <el-option
              v-for="algo in POLICY_ALGORITHM_VALUES"
              :key="algo"
              :label="POLICY_ALGORITHM_LABELS[algo]"
              :value="algo"
              :disabled="algo !== POLICY_ALGORITHM.PPO"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('rlAgent.training.target')">
          <el-select v-model="trainingForm.optimization_target" class="rl-training-select">
            <el-option
              v-for="target in OPTIMIZATION_TARGET_VALUES"
              :key="target"
              :label="OPTIMIZATION_TARGET_LABELS[target]"
              :value="target"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <div class="rl-training-buttons">
        <el-button
          type="primary"
          :icon="VideoPlay"
          :loading="store.startingTraining"
          :disabled="store.isTraining"
          @click="handleStartTraining"
        >
          {{ t('rlAgent.training.start') }}
        </el-button>
        <el-button
          type="danger"
          :icon="VideoPause"
          :loading="store.stoppingTraining"
          :disabled="!store.isTraining"
          @click="handleStopTraining"
        >
          {{ t('rlAgent.training.stop') }}
        </el-button>
        <el-button :icon="Refresh" @click="handleFetchTrainingStatus">
          {{ t('rlAgent.training.refresh') }}
        </el-button>
      </div>

      <el-alert
        :title="t('rlAgent.training.offlineNotice')"
        type="info"
        :closable="false"
        show-icon
        class="rl-training-notice"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { reactive, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, VideoPlay, VideoPause } from '@element-plus/icons-vue'
import { useRlAgentStore } from '@/stores/rlAgent'
import {
  OPTIMIZATION_TARGET_VALUES,
  OPTIMIZATION_TARGET_LABELS,
  POLICY_ALGORITHM,
  POLICY_ALGORITHM_VALUES,
  POLICY_ALGORITHM_LABELS,
  TRAINING_STATUS_LABELS,
  TRAINING_STATUS_TAG_TYPE,
  DEFAULT_MAX_STEPS,
  MIN_MAX_STEPS,
  MAX_MAX_STEPS,
  DEFAULT_OPTIMIZATION_TARGET,
  DEFAULT_POLICY_ALGORITHM,
  type OptimizationTarget,
  type PolicyAlgorithm,
} from '@/contracts/rl_agent'
import { formatDateTime } from '@/utils/dateTime'

const { t } = useI18n()
const store = useRlAgentStore()

defineEmits<{ trainingChanged: [] }>()

const trainingForm = reactive({
  max_steps: DEFAULT_MAX_STEPS,
  algorithm: DEFAULT_POLICY_ALGORITHM as PolicyAlgorithm,
  optimization_target: DEFAULT_OPTIMIZATION_TARGET as OptimizationTarget,
  seed: null as number | null,
})

let trainingPollTimer: ReturnType<typeof setInterval> | null = null

async function handleFetchTrainingStatus(): Promise<void> {
  try {
    await store.fetchTrainingStatus()
  } catch (e: unknown) {
    console.warn('[RLAgent] fetchTrainingStatus failed:', e)
  }
}

function startTrainingPolling(): void {
  stopTrainingPolling()
  trainingPollTimer = setInterval(() => {
    void handleFetchTrainingStatus()
    if (store.isTrainingTerminal) {
      stopTrainingPolling()
    }
  }, 3000)
}

function stopTrainingPolling(): void {
  if (trainingPollTimer) {
    clearInterval(trainingPollTimer)
    trainingPollTimer = null
  }
}

async function handleStartTraining(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('rlAgent.training.startConfirm', { steps: trainingForm.max_steps }),
      t('rlAgent.training.startTitle'),
      { type: 'warning' },
    )
  } catch {
    return
  }
  const result = await store.startTraining({
    max_steps: trainingForm.max_steps,
    algorithm: trainingForm.algorithm,
    optimization_target: trainingForm.optimization_target,
    seed: trainingForm.seed,
  })
  if (!result) {
    ElMessage.error(store.error || t('rlAgent.training.startFailed'))
  } else {
    ElMessage.success(t('rlAgent.training.startSuccess'))
    startTrainingPolling()
  }
}

async function handleStopTraining(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('rlAgent.training.stopConfirm'),
      t('rlAgent.training.stopTitle'),
      { type: 'warning' },
    )
  } catch {
    return
  }
  const result = await store.stopTraining()
  if (!result) {
    ElMessage.error(store.error || t('rlAgent.training.stopFailed'))
  } else {
    ElMessage.success(t('rlAgent.training.stopSuccess'))
    stopTrainingPolling()
  }
}

onUnmounted(() => {
  stopTrainingPolling()
})
</script>

<style scoped>
.rl-training-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.rl-training-status {
  margin-bottom: 16px;
}

.rl-training-metrics {
  margin-top: 12px;
}

.rl-training-metrics__title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
}

.rl-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.rl-metric-item {
  display: flex;
  flex-direction: column;
  padding: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-xs);
  background: var(--el-fill-color-blank);
}

.rl-metric-item__label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

.rl-metric-item__value {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  font-family: var(--font-mono);
}

.rl-metric-item__value--highlight {
  color: var(--state-success);
}

.rl-training-error {
  margin-top: 12px;
}

.rl-training-form {
  margin-bottom: 8px;
}

.rl-training-select {
  width: 160px;
}

.rl-training-buttons {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}

.rl-training-notice {
  margin-top: 8px;
}
</style>
