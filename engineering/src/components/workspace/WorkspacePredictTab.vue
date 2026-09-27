<template>
  <div>
    <el-form :model="predictForm" label-width="120px">
      <el-form-item :label="$t('workspace.modelName')">
        <el-select v-model="predictForm.modelName" :placeholder="$t('workspace.selectModel')">
          <el-option label="CFC-Fast" value="CFC-Fast" />
          <el-option label="LTC-TimeSeries" value="LTC-TimeSeries" />
          <el-option label="Hybrid-Multimodal" value="Hybrid-Multimodal" />
        </el-select>
      </el-form-item>
      <el-form-item :label="$t('workspace.inputData')">
        <el-input v-model="predictForm.inputData" type="textarea" :rows="4" :placeholder="$t('workspace.inputDataPlaceholder')" />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="predictForm.returnConfidence">{{ $t('workspace.returnConfidence') }}</el-checkbox>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="predicting" @click="handlePredict">
          {{ $t('workspace.runPredict') }}
        </el-button>
      </el-form-item>
    </el-form>

    <div v-if="predictResponse" class="result-section">
      <el-divider />
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item :label="$t('workspace.predictionValue')">{{ predictResponse.value }}</el-descriptions-item>
        <el-descriptions-item v-if="predictResponse.confidence != null" :label="$t('workspace.confidence')">
          <ConfidenceIndicator :confidence="predictResponse.confidence" />
        </el-descriptions-item>
        <el-descriptions-item :label="$t('workspace.inferenceTime')">{{ predictResponse.inference_time }}ms</el-descriptions-item>
      </el-descriptions>
      <div v-if="predictResponse.reasoning" class="reasoning-block">
        <strong>{{ $t('workspace.reasoning') }}:</strong> {{ predictResponse.reasoning }}
      </div>
      <div v-if="predictResponse.alternatives?.length" class="alternatives-section">
        <AcceptModifyReject
          :alternatives="predictResponse.alternatives"
          @accept="handleAlternativeAccept"
          @modify="handleAlternativeModify"
          @reject="() => {}"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import http from '@/utils/http'
import ConfidenceIndicator from '@/components/ConfidenceIndicator.vue'
import AcceptModifyReject from '@/components/AcceptModifyReject.vue'
import { API_CONFIG, buildApiPath } from '@/config/api'

const { t } = useI18n()
const predicting = ref(false)

interface PredictResponse {
  value: string | number | number[]
  confidence?: number
  reasoning?: string
  inference_time: number
  alternatives?: Array<{ plan_id: string; parameters: Record<string, unknown>; expected_outcome: string; confidence: number; reasoning: string }>
}

const predictForm = reactive({
  modelName: 'CFC-Fast',
  inputData: '',
  returnConfidence: true,
})

const predictResponse = ref<PredictResponse | null>(null)

async function handlePredict(): Promise<void> {
  predicting.value = true
  try {
    const res = await http.post<PredictResponse>(buildApiPath(API_CONFIG.LNN, '/predict'), {
      model_name: predictForm.modelName,
      input_data: predictForm.inputData,
      return_confidence: predictForm.returnConfidence,
    })
    predictResponse.value = res.data
    ElMessage.success(t('workspace.predictSuccess'))
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || t('workspace.predictFailed'))
  } finally {
    predicting.value = false
  }
}

function handleAlternativeAccept(_alt: unknown): void {}
function handleAlternativeModify(_alt: unknown): void {}
</script>
