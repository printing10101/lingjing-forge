import { computed, ref, watch, type Ref } from 'vue'
import { getInputRequirements, prepareGeometry } from '@/api/worldModel'
import { extractErrorMessage } from '@/utils/error-handler'
import { STATE_FIELD_LABELS, STATE_FIELD_VALUES, type WorldModelGeometryInput,
  type WorldModelInputRequirements, type WorldModelPredictRequest } from '@/contracts/world_model'
import { validateWorldModelHistory } from './useWorldModelHistory'
import type { WorldModelHistory } from '@/contracts/world_model'

export const FEEDBACK_LABELS: Record<string, string> = {
  surface_roughness: '当前表面粗糙度 Ra (μm)',
  cutting_force_norm: '当前力通道归一化值（按模型口径）',
  mrr_norm: '当前去除率归一化值（按模型口径）',
  confidence: '当前状态可信度 (0–1)',
}
const DYNAMICS_FIELDS = ['spindle_speed', 'feed_rate', 'depth_of_cut', 'tool_wear', 'vibration_rms', 'temperature']
const STATE_LABELS: Readonly<Record<string, string>> = STATE_FIELD_LABELS

export function buildWorldModelRequest(
  modelUri: string, requirements: WorldModelInputRequirements | null,
  geometry: WorldModelGeometryInput | null, currentState: Record<string, number | undefined>,
  actions: Record<string, number>, horizon: number,
  history?: WorldModelHistory | null,
): WorldModelPredictRequest {
  if (!requirements || requirements.model_uri !== modelUri) throw new Error('请先读取当前模型的输入要求。')
  if (requirements.use_fusion && !geometry) throw new Error('请选择零件并读取几何。')
  if (requirements.use_fusion && geometry?.geometry.feature_vector.length !== requirements.feature_dim) {
    throw new Error('当前零件特征与模型结构不匹配，请选择兼容的模型。')
  }
  if (history !== undefined) {
    if (!requirements.use_fusion || !geometry) throw new Error('历史预测需要已审核零件和融合模型。')
    if (!history) throw new Error('请导入有效历史文件。')
    validateWorldModelHistory(history, requirements)
    return { model_uri: modelUri, current_state: {}, candidate_action: { ...actions }, horizon,
      feature_task_id: geometry.source.task_id, feature_task_updated_at: geometry.source.updated_at,
      millimeters_per_unit: geometry.source.millimeters_per_unit,
      fusion_history: JSON.parse(JSON.stringify(history)) as WorldModelHistory }
  }
  const state = { ...currentState }
  if (requirements.state_feedback && Number.isFinite(state.temperature)) {
    state.temperature_over_100c = state.temperature! / 100
  }
  const required = [...(requirements.use_fusion ? DYNAMICS_FIELDS : STATE_FIELD_VALUES), ...requirements.feedback_fields]
  for (const field of new Set(required)) {
    if (!Number.isFinite(state[field])) {
      throw new Error(`请填写${FEEDBACK_LABELS[field] || STATE_LABELS[field] || field}。`)
    }
    if (field !== 'temperature' && field !== 'temperature_over_100c' && state[field]! < 0) {
      throw new Error(`${FEEDBACK_LABELS[field] || STATE_LABELS[field] || field}不能为负数。`)
    }
    if (['confidence', 'chatter_probability'].includes(field) && state[field]! > 1) {
      throw new Error('概率与可信度必须在0到1之间。')
    }
  }
  const request: WorldModelPredictRequest = {
    model_uri: modelUri, current_state: Object.fromEntries(Object.entries(state).filter(([, value]) => Number.isFinite(value))) as Record<string, number>,
    candidate_action: { ...actions }, horizon,
  }
  if (requirements.use_fusion && geometry) {
    request.feature_task_id = geometry.source.task_id
    request.feature_task_updated_at = geometry.source.updated_at
    request.millimeters_per_unit = geometry.source.millimeters_per_unit
  }
  return request
}

export function useWorldModelInputs(modelUri: Ref<string>) {
  const requirements = ref<WorldModelInputRequirements | null>(null)
  const geometry = ref<WorldModelGeometryInput | null>(null)
  const taskId = ref('')
  const scale = ref<number>()
  const loading = ref(false)
  const preparing = ref(false)
  const error = ref('')
  let modelRevision = 0
  let geometryRevision = 0

  const source = computed(() => requirements.value?.geometry_sources.find(item => item.task_id === taskId.value))
  const feedbackFields = computed(() => (requirements.value?.feedback_fields || []).filter(
    field => !STATE_FIELD_VALUES.some(value => value === field) && field !== 'temperature_over_100c'))

  watch(modelUri, () => {
    modelRevision++
    geometryRevision++
    requirements.value = null
    geometry.value = null
    taskId.value = ''
    scale.value = undefined
    loading.value = false
    preparing.value = false
    error.value = ''
  }, { flush: 'sync' })
  watch([taskId, scale], () => {
    geometryRevision++
    geometry.value = null
    preparing.value = false
    error.value = ''
  }, { flush: 'sync' })
  watch(taskId, () => { scale.value = undefined }, { flush: 'sync' })

  async function inspectModel() {
    const revision = ++modelRevision
    const uri = modelUri.value
    geometryRevision++
    requirements.value = null
    geometry.value = null
    taskId.value = ''
    scale.value = undefined
    loading.value = true
    error.value = ''
    try {
      const result = await getInputRequirements(uri)
      if (revision === modelRevision) requirements.value = result
    } catch (err) {
      if (revision === modelRevision) error.value = extractErrorMessage(err, '模型输入要求读取失败')
    } finally {
      if (revision === modelRevision) loading.value = false
    }
  }

  async function readGeometry() {
    geometry.value = null
    if (!source.value) { error.value = '请选择已审核的零件。'; return }
    if (!source.value.calibrated && (!Number.isFinite(scale.value) || scale.value! <= 0)) {
      error.value = '请填写每个网格单位对应的毫米数。'; return
    }
    const revision = ++geometryRevision
    const scaleMm = source.value.calibrated ? 1 : scale.value
    preparing.value = true
    error.value = ''
    try {
      const result = await prepareGeometry(taskId.value, scaleMm)
      if (revision === geometryRevision) geometry.value = result
    } catch (err) {
      if (revision === geometryRevision) error.value = extractErrorMessage(err, '零件几何读取失败')
    } finally {
      if (revision === geometryRevision) preparing.value = false
    }
  }
  return { requirements, geometry, taskId, scale, source, feedbackFields, loading, preparing, error, inspectModel, readGeometry }
}
