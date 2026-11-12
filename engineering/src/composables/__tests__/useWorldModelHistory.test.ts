import { describe, expect, it } from 'vitest'
import { effectScope, ref } from 'vue'
import { ACTION_FIELD_VALUES, type WorldModelHistory, type WorldModelInputRequirements } from '@/contracts/world_model'
import { useWorldModelHistory, validateWorldModelHistory } from '@/composables/useWorldModelHistory'
import { buildWorldModelRequest } from '@/composables/useWorldModelInputs'

function history(): WorldModelHistory {
  return { schema_version: 1, source: 'synthetic fixture', sample_period_seconds: 0.1,
    samples: [0, 1, 2].map(index => ({ time_seconds: index * 0.1,
      current_state: { spindle_speed: index + 1, feed_rate: 2, depth_of_cut: 0.5, tool_wear: 0.1, vibration_rms: 0.2, temperature: 25 },
      action: Object.fromEntries(ACTION_FIELD_VALUES.map(field => [field, index * 0.1])) })) }
}
const requirements: WorldModelInputRequirements = { model_uri: 'model://world_model/test', use_fusion: true,
  feature_dim: 4, state_feedback: false, feedback_fields: [], geometry_sources: [], training_history_steps: [3] }
const geometry = { geometry: { bbox_dimensions: [1, 2, 3], feature_vector: [0, 0, 0, 0], symmetry_score: 0, complexity_score: 0 },
  source: { task_id: 'reviewed', name: 'part', updated_at: 2, feature_count: 1, scale_source: 'user_supplied' as const, millimeters_per_unit: 1 }, notes: [] }

describe('history validation and request', () => {
  it('sends full history with empty manual state and an independent request copy', () => {
    const input = history()
    const request = buildWorldModelRequest(requirements.model_uri, requirements, geometry, {}, {}, 2, input)
    expect(request.current_state).toEqual({})
    expect(request.fusion_history?.samples).toHaveLength(3)
    expect(request.feature_task_id).toBe('reviewed')
    input.samples[0].current_state.spindle_speed = 99
    expect(request.fusion_history?.samples[0].current_state.spindle_speed).toBe(1)
    expect(() => buildWorldModelRequest(requirements.model_uri, requirements, geometry, {}, {}, 2, null)).toThrow('导入')
    expect(() => buildWorldModelRequest(requirements.model_uri, { ...requirements, use_fusion: false }, null, {}, {}, 2, input)).toThrow('融合模型')
    expect(() => validateWorldModelHistory(input, { ...requirements, training_history_steps: [20] })).toThrow('不匹配')
  })
  it.each([
    (h: WorldModelHistory) => { h.schema_version = 2 as 1 },
    (h: WorldModelHistory) => { h.source = ' ' },
    (h: WorldModelHistory) => { h.sample_period_seconds = 0 },
    (h: WorldModelHistory) => { h.samples = [] },
    (h: WorldModelHistory) => { h.samples[1].time_seconds = 0 },
    (h: WorldModelHistory) => { h.samples[1].time_seconds = 0.11 },
    (h: WorldModelHistory) => { h.samples[0].current_state.tool_wear = -1 },
    (h: WorldModelHistory) => { delete h.samples[0].current_state.feed_rate },
    (h: WorldModelHistory) => { delete h.samples[0].action.spindle_speed_delta },
    (h: WorldModelHistory) => { h.samples[0].action.spindle_speed_delta = Infinity },
  ])('rejects incomplete or invalid history', mutate => {
    const value = history(); mutate(value)
    expect(() => validateWorldModelHistory(value)).toThrow()
  })
  it('requires latest feedback and derives temperature from Celsius', () => {
    const feedback = { ...requirements, state_feedback: true,
      feedback_fields: ['chatter_probability', 'tool_wear', 'surface_roughness', 'temperature_over_100c', 'cutting_force_norm', 'mrr_norm', 'confidence'] }
    const input = history()
    expect(() => validateWorldModelHistory(input, feedback)).toThrow('反馈')
    Object.assign(input.samples[2].current_state, { chatter_probability: 0.2, surface_roughness: 1.5, cutting_force_norm: 0.3, mrr_norm: 0.4, confidence: 0.9 })
    expect(validateWorldModelHistory(input, feedback)).toBe(input)
    expect(input.samples[2].current_state.temperature_over_100c).toBeUndefined()
    input.samples[2].current_state.confidence = 2
    expect(() => validateWorldModelHistory(input, feedback)).toThrow('0到1')
  })
})

describe('history file lifecycle', () => {
  it('reads files and clears history when selecting another part', async () => {
    const scope = effectScope(), context = ref('part1')
    const input = scope.run(() => useWorldModelHistory(context))!
    try {
      await input.load({ name: 'one.json', size: 200, text: async () => JSON.stringify(history()) })
      expect(input.history.value?.samples).toHaveLength(3)
      expect(input.enabled.value).toBe(true)
      context.value = 'part2'
      expect(input.history.value).toBeNull()
      expect(input.enabled.value).toBe(false)
      expect(input.fileName.value).toBe('')
    } finally { scope.stop() }
  })
  it('discards late reads after context changes or newer files', async () => {
    const scope = effectScope(), context = ref('part1')
    const input = scope.run(() => useWorldModelHistory(context))!
    try {
      let finish!: (text: string) => void
      const pending = input.load({ name: 'old.json', size: 1, text: () => new Promise(resolve => { finish = resolve }) })
      context.value = 'part2'
      await input.load({ name: 'new.json', size: 200, text: async () => JSON.stringify({ ...history(), source: 'new' }) })
      finish(JSON.stringify(history())); await pending
      expect(input.history.value?.source).toBe('new')
      input.enabled.value = false
      expect(input.history.value).toBeNull()
    } finally { scope.stop() }
  })
  it('keeps import mode blocked on invalid JSON, oversize files or read failures', async () => {
    const scope = effectScope(), context = ref('part')
    const input = scope.run(() => useWorldModelHistory(context))!
    try {
      await input.load({ name: 'bad.json', size: 1, text: async () => '{' })
      expect(input.error.value).toContain('JSON')
      expect(input.enabled.value).toBe(true)
      expect(input.history.value).toBeNull()
      await input.load({ name: 'big.json', size: 2 ** 21, text: async () => '' })
      expect(input.error.value).toContain('1MB')
      await input.load({ name: 'big-content.json', size: 1, text: async () => 'a'.repeat(2 ** 21) })
      expect(input.error.value).toContain('1MB')
      await input.load({ name: 'fail.json', size: 1, text: async () => { throw new Error('file removed') } })
      expect(input.error.value).toContain('file removed')
      expect(input.loading.value).toBe(false)
    } finally { scope.stop() }
  })
})
