import { ref, watch, type Ref } from 'vue'
import { ACTION_FIELD_VALUES, type WorldModelHistory, type WorldModelInputRequirements } from '@/contracts/world_model'

export const HISTORY_DYNAMICS = ['spindle_speed', 'feed_rate', 'depth_of_cut', 'tool_wear', 'vibration_rms', 'temperature']
const MAX_FILE_BYTES = 1024 * 1024

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && Number.isFinite(Math.fround(value))
}

export function validateWorldModelHistory(value: unknown, requirements?: WorldModelInputRequirements): WorldModelHistory {
  if (!record(value) || value.schema_version !== 1) throw new Error('历史文件必须使用 schema_version=1。')
  if (typeof value.source !== 'string' || !value.source.trim() || value.source.length > 512) throw new Error('请在历史文件中声明来源 source（1至512字）。')
  const period = value.sample_period_seconds
  if (!finite(period) || period <= 0) throw new Error('历史采样间隔必须为正有限秒数。')
  if (!Array.isArray(value.samples) || value.samples.length < 2 || value.samples.length > 256) throw new Error('历史文件须含2至256条记录。')
  const expected = requirements?.training_history_steps
  if (expected?.length && !expected.includes(value.samples.length)) throw new Error(`历史长度与训练记录${expected.join('/')}不匹配；不自动截断或填充。`)
  let previous: number | undefined
  for (const sample of value.samples) {
    if (!record(sample) || typeof sample.time_seconds !== 'number' || !Number.isFinite(sample.time_seconds)) throw new Error('每条历史记录须含有限时间 time_seconds。')
    const time = sample.time_seconds
    if (previous !== undefined && (time <= previous || Math.abs(time - previous - period) > period * 1.1e-5)) throw new Error('历史时间须严格递增且等间隔；不自动重采样。')
    previous = time
    if (!record(sample.current_state) || !record(sample.action)) throw new Error('每条记录须含 current_state 与 action。')
    for (const field of HISTORY_DYNAMICS) {
      const number = sample.current_state[field]
      if (!finite(number) || (field !== 'temperature' && number < 0)) throw new Error(`历史状态${field}缺失或无效。`)
    }
    for (const field of ACTION_FIELD_VALUES) {
      if (!finite(sample.action[field])) throw new Error(`历史动作${field}缺失或无效，不能自动补零。`)
    }
  }
  if (requirements?.state_feedback) {
    const state = { ...value.samples[value.samples.length - 1].current_state }
    state.temperature_over_100c = state.temperature / 100
    for (const field of requirements.feedback_fields) {
      if (!finite(state[field]) || (field !== 'temperature_over_100c' && state[field] < 0)) throw new Error(`末条历史状态缺少有效反馈${field}。`)
    }
    if (['confidence', 'chatter_probability'].some(field => state[field] < 0 || state[field] > 1)) throw new Error('末条概率和可信度须在0到1之间。')
  }
  return value as unknown as WorldModelHistory
}

export function useWorldModelHistory(context: Ref<string>) {
  const enabled = ref(false)
  const history = ref<WorldModelHistory | null>(null)
  const fileName = ref('')
  const fileKey = ref(0)
  const loading = ref(false)
  const error = ref('')
  let revision = 0
  function clear() {
    revision++
    history.value = null
    fileName.value = ''
    loading.value = false
    error.value = ''
    fileKey.value++
  }
  watch(context, () => { clear(); enabled.value = false }, { flush: 'sync' })
  watch(enabled, clear, { flush: 'sync' })
  async function load(file: Pick<File, 'size' | 'name' | 'text'>) {
    enabled.value = true
    clear()
    const request = revision
    fileName.value = file.name
    loading.value = true
    try {
      if (file.size > MAX_FILE_BYTES) throw new Error('历史文件不能超过1MB。')
      const text = await file.text()
      if (new TextEncoder().encode(text).length > MAX_FILE_BYTES) throw new Error('历史文件不能超过1MB。')
      const value = validateWorldModelHistory(JSON.parse(text))
      if (request === revision) history.value = value
    } catch (err) {
      if (request === revision) error.value = err instanceof SyntaxError ? '历史文件不是有效JSON。' : err instanceof Error ? err.message : '历史文件读取失败。'
    } finally {
      if (request === revision) loading.value = false
    }
  }
  return { enabled, history, fileName, fileKey, loading, error, clear, load }
}
