<template>
  <el-dialog v-model="visible.hidden_state" :title="t('explainability.generate.hiddenState')" width="500px" :close-on-click-modal="false">
    <el-form label-width="120px" label-position="left">
      <el-form-item :label="t('explainability.fields.modelUri')"><el-input v-model="hs.model_uri" placeholder="model://LTC-ChatterPredictor/1.0.0" /></el-form-item>
      <el-form-item :label="t('explainability.projectionMethod')">
        <el-select v-model="hs.projection_method" class="ex-dialog-select"><el-option v-for="pm in PROJECTION_METHOD_VALUES" :key="pm" :label="PROJECTION_METHOD_LABELS[pm]" :value="pm" /></el-select>
      </el-form-item>
      <el-form-item :label="t('explainability.projectionDim')"><el-input-number v-model="hs.projection_dim" :min="2" :max="3" :step="1" /></el-form-item>
      <el-form-item :label="t('explainability.maxFrames')"><el-input-number v-model="hs.max_frames" :min="1" :max="10000" :step="100" /></el-form-item>
      <el-form-item :label="t('explainability.sourceSnapshotId')"><el-input v-model="hs.source_snapshot_id" placeholder="可选" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible.hidden_state = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="store.generatingHiddenState" @click="generateHiddenState">{{ t('common.generate') }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="visible.gate_dynamics" :title="t('explainability.generate.gateDynamics')" width="500px" :close-on-click-modal="false">
    <el-form label-width="120px" label-position="left">
      <el-form-item :label="t('explainability.fields.modelUri')"><el-input v-model="gd.model_uri" placeholder="model://LTC-ChatterPredictor/1.0.0" /></el-form-item>
      <el-form-item :label="t('explainability.anomalySigma')"><el-input-number v-model="gd.anomaly_sigma" :min="1.0" :max="5.0" :step="0.1" /></el-form-item>
      <el-form-item :label="t('explainability.sourceSnapshotId')"><el-input v-model="gd.source_snapshot_id" placeholder="可选" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible.gate_dynamics = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="store.generatingGateDynamics" @click="generateGateDynamics">{{ t('common.generate') }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="visible.counterfactual" :title="t('explainability.generate.counterfactual')" width="560px" :close-on-click-modal="false">
    <el-form label-width="120px" label-position="left">
      <el-form-item :label="t('explainability.fields.modelUri')"><el-input v-model="cf.model_uri" placeholder="model://LTC-ChatterPredictor/1.0.0" /></el-form-item>
      <el-form-item :label="t('explainability.perturbedFeature')"><el-input v-model="cf.perturbed_feature" placeholder="如 spindle_speed" /></el-form-item>
      <el-form-item :label="t('explainability.perturbationStep')"><el-input-number v-model="cf.perturbation_step" :min="0.01" :max="0.5" :step="0.01" /></el-form-item>
      <el-divider content-position="left">{{ t('explainability.baseInput') }}</el-divider>
      <div class="ex-base-input-grid">
        <el-form-item v-for="field in STATE_FIELD_VALUES" :key="field" :label="STATE_FIELD_LABELS[field].split(' ')[0]" label-width="100px">
          <el-input-number v-model="cf.base_input[field]" :step="0.1" controls-position="right" class="ex-base-input" />
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="visible.counterfactual = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="store.generatingCounterfactual" @click="generateCounterfactual">{{ t('common.generate') }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="visible.confidence" :title="t('explainability.generate.confidence')" width="500px" :close-on-click-modal="false">
    <el-form label-width="120px" label-position="left">
      <el-form-item :label="t('explainability.fields.modelUri')"><el-input v-model="con.model_uri" placeholder="model://LTC-ChatterPredictor/1.0.0" /></el-form-item>
      <el-form-item :label="t('explainability.sampleCount')"><el-input-number v-model="con.sample_count" :min="5" :max="200" :step="5" /></el-form-item>
      <el-divider content-position="left">{{ t('explainability.inputData') }}</el-divider>
      <div class="ex-base-input-grid">
        <el-form-item v-for="field in STATE_FIELD_VALUES" :key="field" :label="STATE_FIELD_LABELS[field].split(' ')[0]" label-width="100px">
          <el-input-number v-model="con.input_data[field]" :step="0.1" controls-position="right" class="ex-base-input" />
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="visible.confidence = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="store.generatingConfidence" @click="generateConfidence">{{ t('common.generate') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { useExplainabilityStore } from '@/stores/explainability'
import {
  PROJECTION_METHOD_VALUES, PROJECTION_METHOD_LABELS,
  DEFAULT_PROJECTION_METHOD, DEFAULT_PROJECTION_DIM, DEFAULT_MAX_FRAMES,
  DEFAULT_ANOMALY_SIGMA, DEFAULT_PERTURBATION_STEP, DEFAULT_SAMPLE_COUNT,
  type ExplanationType, type GenerateHiddenStateRequest, type GenerateGateDynamicsRequest,
  type GenerateCounterfactualRequest, type GenerateConfidenceRequest,
} from '@/contracts/explainability'
import { STATE_FIELD_VALUES, STATE_FIELD_LABELS } from '@/contracts/world_model'

const { t } = useI18n()
const store = useExplainabilityStore()

const emit = defineEmits<{ generated: [] }>()

const visible = reactive({ hidden_state: false, gate_dynamics: false, counterfactual: false, confidence: false })

const hs = reactive({ model_uri: 'model://LTC-ChatterPredictor/1.0.0', projection_method: DEFAULT_PROJECTION_METHOD, projection_dim: DEFAULT_PROJECTION_DIM, max_frames: DEFAULT_MAX_FRAMES, source_snapshot_id: '' })
const gd = reactive({ model_uri: 'model://LTC-ChatterPredictor/1.0.0', anomaly_sigma: DEFAULT_ANOMALY_SIGMA, source_snapshot_id: '' })
const cf = reactive({ model_uri: 'model://LTC-ChatterPredictor/1.0.0', perturbed_feature: 'spindle_speed', perturbation_step: DEFAULT_PERTURBATION_STEP, base_input: { spindle_speed: 8000, feed_rate: 1200, depth_of_cut: 1.5, width_of_cut: 6.0, tool_wear: 0.05, vibration_rms: 0.8, temperature: 45.0, chatter_probability: 0.1 } as Record<string, number> })
const con = reactive({ model_uri: 'model://LTC-ChatterPredictor/1.0.0', sample_count: DEFAULT_SAMPLE_COUNT, input_data: { spindle_speed: 8000, feed_rate: 1200, depth_of_cut: 1.5, width_of_cut: 6.0, tool_wear: 0.05, vibration_rms: 0.8, temperature: 45.0, chatter_probability: 0.1 } as Record<string, number> })

defineExpose({ open(type: ExplanationType) { (visible as Record<string, boolean>)[type] = true } })

async function generate(name: string, request: object, key: string): Promise<void> {
  const result = await (store as unknown as Record<string, (req: object) => Promise<unknown> | unknown>)[key](request)
  if (!result) { ElMessage.error(store.error || t('explainability.generateFailed')); return }
  ElMessage.success(t('explainability.generateSuccess'));
  (visible as Record<string, boolean>)[name] = false
  emit('generated')
}

async function generateHiddenState(): Promise<void> {
  const req: GenerateHiddenStateRequest = { model_uri: hs.model_uri, projection_method: hs.projection_method, projection_dim: hs.projection_dim, max_frames: hs.max_frames, source_snapshot_id: hs.source_snapshot_id || null }
  await generate('hidden_state', req, 'generateHiddenStateExplanation')
}
async function generateGateDynamics(): Promise<void> {
  const req: GenerateGateDynamicsRequest = { model_uri: gd.model_uri, anomaly_sigma: gd.anomaly_sigma, source_snapshot_id: gd.source_snapshot_id || null }
  await generate('gate_dynamics', req, 'generateGateDynamicsExplanation')
}
async function generateCounterfactual(): Promise<void> {
  const req: GenerateCounterfactualRequest = { model_uri: cf.model_uri, base_input: { ...cf.base_input }, perturbed_feature: cf.perturbed_feature, perturbation_step: cf.perturbation_step, source_snapshot_id: null }
  await generate('counterfactual', req, 'generateCounterfactualExplanation')
}
async function generateConfidence(): Promise<void> {
  const req: GenerateConfidenceRequest = { model_uri: con.model_uri, input_data: { ...con.input_data }, sample_count: con.sample_count, source_snapshot_id: null }
  await generate('confidence', req, 'generateConfidenceExplanation')
}
</script>

<style scoped>
.ex-dialog-select { width: 100%; }
.ex-base-input-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 12px; }
.ex-base-input { width: 100%; }
</style>
