<template>
  <div>
    <el-divider />

    <div
      v-if="dryRunResult"
      class="train-plan-section"
    >
      <h4>{{ t('workspace.trainingPlanSummary') }}</h4>

      <el-alert
        :title="t('workspace.trainingConfidence', { confidence: (dryRunResult.confidence * 100).toFixed(0) })"
        :type="getConfidenceAlertType(dryRunResult.confidence)"
        :closable="false"
        show-icon
        style="margin-bottom: 16px;"
      />

      <el-descriptions
        :column="2"
        border
      >
        <el-descriptions-item :label="t('workspace.estDuration')">
          {{ dryRunResult.training_plan.estimated_duration_minutes.toFixed(1) }} {{ t('common.minutes') }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('workspace.estMemory')">
          {{ dryRunResult.training_plan.estimated_memory_mb.toFixed(1) }} MB
        </el-descriptions-item>
        <el-descriptions-item :label="t('workspace.datasetSamples')">
          {{ dryRunResult.training_plan.dataset_samples }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('workspace.trainValSplit')">
          {{ dryRunResult.training_plan.train_val_split.ratio }}
        </el-descriptions-item>
        <el-descriptions-item
          :label="t('workspace.estGpuMemory')"
          :span="2"
        >
          {{ dryRunResult.training_plan.estimated_gpu_memory_mb ? `${dryRunResult.training_plan.estimated_gpu_memory_mb.toFixed(1)} MB` : 'N/A (CPU)' }}
        </el-descriptions-item>
      </el-descriptions>

      <div
        v-if="dryRunResult.training_plan.potential_risks.length > 0"
        class="risks-section"
      >
        <h5>{{ t('workspace.potentialRisks') }}</h5>
        <el-alert
          v-for="(risk, idx) in dryRunResult.training_plan.potential_risks"
          :key="`risk-${idx}`"
          :title="risk"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 8px;"
        />
      </div>

      <div
        v-if="dryRunResult.training_plan.recommendations.length > 0"
        class="recommendations-section"
      >
        <h5>{{ t('workspace.trainingRecommendations') }}</h5>
        <ul>
          <li
            v-for="(rec, idx) in dryRunResult.training_plan.recommendations"
            :key="`rec-${idx}`"
          >
            {{ rec }}
          </li>
        </ul>
      </div>

      <div class="reasoning-section">
        <h5>{{ t('workspace.aiReasoningDesc') }}</h5>
        <p>{{ dryRunResult.reasoning }}</p>
      </div>

      <el-divider />

      <div class="confirm-section">
        <el-checkbox
          :model-value="trainPlanConfirmed"
          @update:model-value="onTrainPlanConfirmedChange($event)"
        >
          {{ t('workspace.confirmTraining') }}
        </el-checkbox>
      </div>
    </div>

    <div
      v-if="trainResult"
      class="train-result-section"
    >
      <h4>{{ t('workspace.trainingMonitor') }}</h4>

      <div
        v-if="currentJobId"
        class="job-info"
      >
        <el-descriptions
          :column="2"
          border
          size="small"
        >
          <el-descriptions-item :label="t('workspace.jobId')">
            <el-tag
              type="info"
              size="small"
              class="job-id-tag"
            >
              {{ currentJobId }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('common.status')">
            <el-tag :type="getTaskStatusTagType(sse.currentStatus || 'queued')">
              {{ getTaskStatusLabel(sse.currentStatus || 'queued') }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <div
        v-if="sse.currentStatus === 'running' || (sse.progress ?? 0) > 0"
        class="progress-section"
      >
        <el-progress
          :percentage="Math.round(sse.progress ?? 0)"
          :stroke-width="20"
          :status="sse.currentStatus === 'completed' ? 'success' : sse.currentStatus === 'failed' ? 'exception' : undefined"
        />
        <div class="progress-info">
          <span
            v-if="sse.lastProgressData?.message"
            class="progress-message"
          >
            {{ sse.lastProgressData.message }}
          </span>
          <span
            v-if="sse.lastProgressData"
            class="progress-metrics"
          >
            Train Loss: {{ ((sse.lastProgressData.train_loss as number) ?? 0).toFixed(6) }} | 
            Val Loss: {{ ((sse.lastProgressData.val_loss as number) ?? 0).toFixed(6) }}
          </span>
        </div>
      </div>

      <div
        v-if="lossHistory.length > 0"
        class="loss-curves"
      >
        <h5>Loss {{ t('ruleEditor.result') }}</h5>
        <div class="chart-container">
          <canvas
            ref="lossChartCanvas"
            width="800"
            height="300"
          />
        </div>
      </div>

      <div
        v-if="sse.currentStatus === 'running'"
        class="cancel-section"
      >
        <el-button
          type="danger"
          :loading="cancelling"
          @click="$emit('cancel-training')"
        >
          {{ t('workspace.cancelTraining') }}
        </el-button>
      </div>

      <el-alert
        v-if="sse.currentStatus === 'completed'"
        :title="t('workspace.trainingCompleted')"
        type="success"
        :closable="false"
        show-icon
        style="margin-top: 16px;"
      />

      <el-alert
        v-if="sse.currentStatus === 'failed'"
        :title="t('workspace.trainingFailed') + ': ' + (sse.error || t('common.unknownError'))"
        type="error"
        :closable="false"
        show-icon
        style="margin-top: 16px;"
      />

      <el-alert
        v-if="sse.currentStatus === 'cancelled'"
        :title="t('workspace.trainingCancelled')"
        type="warning"
        :closable="false"
        show-icon
        style="margin-top: 16px;"
      />

      <pre v-if="sse.currentStatus === 'completed' && sse.lastProgressData">{{ JSON.stringify(sse.lastProgressData, null, 2) }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, toRefs } from 'vue'
import { useI18n } from 'vue-i18n'
import { getTaskStatusTagType, getTaskStatusLabel } from '@/utils/statusHelpers'

const { t } = useI18n()

interface DryRunResult {
  is_dry_run: boolean
  training_plan: {
    estimated_duration_minutes: number
    estimated_memory_mb: number
    estimated_gpu_memory_mb?: number
    dataset_samples: number
    train_val_split: { train: number; validation: number; ratio: string }
    potential_risks: string[]
    recommendations: string[]
  }
  confidence: number
  reasoning: string
}

interface TrainResult {
  job_id: string
  status: string
  message?: string
}

interface SSEState {
  currentStatus: string | null
  progress: number
  lastProgressData: Record<string, unknown> | null
  error: string | null
}

const props = defineProps<{
  dryRunResult: DryRunResult | null
  trainResult: TrainResult | null
  currentJobId: string | null
  sse: SSEState
  lossHistory: number[]
  valLossHistory: number[]
  cancelling: boolean
  trainPlanConfirmed: boolean
}>()

const emit = defineEmits<{
  'cancel-training': []
  'update:train-plan-confirmed': [value: boolean]
}>()

function onTrainPlanConfirmedChange(val: string | boolean | number) {
  emit('update:train-plan-confirmed', val === true)
}

const { lossHistory, valLossHistory } = toRefs(props)

const lossChartCanvas = ref<HTMLCanvasElement | null>(null)

function getConfidenceAlertType(confidence: number): 'success' | 'warning' | 'error' | 'info' {
  if (confidence >= 0.8) return 'success'
  if (confidence >= 0.5) return 'warning'
  return 'error'
}

function drawLossChart() {
  const canvas = lossChartCanvas.value
  if (!canvas) return

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const width = canvas.width
  const height = canvas.height
  const padding = 60

  const rootStyle = getComputedStyle(document.documentElement)
  const readColor = (varName: string, fallback: string): string =>
    rootStyle.getPropertyValue(varName).trim() || fallback

  const chartGridColor = readColor('--chart-grid', '#e0dbd4')
  const chartLabelColor = readColor('--chart-axis-label', '#6e6960')
  const chartTrainColor = readColor('--chart-series-train', '#8B7D6B')
  const chartValColor = readColor('--chart-series-val', '#D4A857')

  ctx.clearRect(0, 0, width, height)

  const allLosses = [...lossHistory.value, ...valLossHistory.value]
  if (allLosses.length === 0) return

  const maxLoss = Math.max(...allLosses)
  const minLoss = Math.min(...allLosses)
  const lossRange = maxLoss - minLoss || 1

  const epochs = Math.max(lossHistory.value.length, valLossHistory.value.length)

  ctx.strokeStyle = chartGridColor
  ctx.lineWidth = 1
  for (let i = 0; i <= 5; i++) {
    const y = padding + (i / 5) * (height - 2 * padding)
    ctx.beginPath()
    ctx.moveTo(padding, y)
    ctx.lineTo(width - padding, y)
    ctx.stroke()

    ctx.fillStyle = chartLabelColor
    ctx.font = '12px Arial'
    ctx.textAlign = 'right'
    const lossValue = maxLoss - (i / 5) * lossRange
    ctx.fillText(lossValue.toFixed(6), padding - 5, y + 4)
  }

  ctx.fillStyle = chartLabelColor
  ctx.font = '12px Arial'
  ctx.textAlign = 'center'
  ctx.fillText('Epoch', width / 2, height - 10)

  function drawLine(data: number[], color: string, label: string) {
    if (data.length === 0) return
    if (!ctx) return

    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.beginPath()

    for (let i = 0; i < data.length; i++) {
      const x = padding + (i / (epochs - 1 || 1)) * (width - 2 * padding)
      const y = padding + ((maxLoss - data[i]) / lossRange) * (height - 2 * padding)

      if (i === 0) {
        ctx.moveTo(x, y)
      } else {
        ctx.lineTo(x, y)
      }
    }
    ctx.stroke()

    ctx.fillStyle = color
    ctx.font = 'bold 12px Arial'
    ctx.textAlign = 'left'
    ctx.fillText(label, width - padding + 5, padding)
  }

  drawLine(lossHistory.value, chartTrainColor, 'Train Loss')
  drawLine(valLossHistory.value, chartValColor, 'Val Loss')
}

watch(() => lossHistory.value.length, () => {
  if (lossChartCanvas.value) {
    drawLossChart()
  }
})
</script>

<style scoped>
.train-plan-section {
  margin-top: 16px;
}

.risks-section {
  margin: 16px 0;
}

.risks-section h5 {
  margin: 0 0 12px 0;
  color: var(--warning);
  font-weight: 600;
}

.recommendations-section {
  margin: 16px 0;
}

.recommendations-section h5 {
  margin: 0 0 12px 0;
  color: var(--success);
  font-weight: 600;
}

.recommendations-section ul {
  margin: 0;
  padding-left: 20px;
  color: var(--text-secondary);
  line-height: 1.8;
}

.confirm-section {
  margin: 20px 0;
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  text-align: center;
  border: 1px solid var(--border-light);
}

.confirm-section .el-checkbox {
  font-size: 16px;
  font-weight: 600;
}

.train-result-section {
  margin-top: 16px;
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-light);
}

pre {
  margin: 8px 0 0 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>