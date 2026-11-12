import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, ref } from 'vue'
import { buildWorldModelRequest, useWorldModelInputs } from '@/composables/useWorldModelInputs'
import { getInputRequirements, prepareGeometry } from '@/api/worldModel'
import type { WorldModelGeometryInput, WorldModelInputRequirements } from '@/contracts/world_model'

vi.mock('@/api/worldModel', () => ({ getInputRequirements: vi.fn(), prepareGeometry: vi.fn() }))

const requirements: WorldModelInputRequirements = {
  model_uri: 'model://world_model/test', use_fusion: true, feature_dim: 32,
  state_feedback: false, feedback_fields: [], geometry_sources: [
    { task_id: 'one', name: 'one.stl', updated_at: 1, calibrated: false },
    { task_id: 'two', name: 'two.stl', updated_at: 2, calibrated: true },
  ],
}
const geometry: WorldModelGeometryInput = {
  geometry: { bbox_dimensions: [10, 20, 30], feature_vector: Array(32).fill(0), symmetry_score: 0.5, complexity_score: 0.2 },
  source: { task_id: 'one', name: 'one.stl', updated_at: 1, feature_count: 2, scale_source: 'user_supplied', millimeters_per_unit: 2 }, notes: [],
}
const state = { spindle_speed: 8000, feed_rate: 1000, depth_of_cut: 1, width_of_cut: 2,
  tool_wear: 0.1, vibration_rms: 0.3, temperature: 25, chatter_probability: 0.2 }

describe('prediction request validation', () => {
  it('submits the task reference and scale instead of fabricated geometry', () => {
    const result = buildWorldModelRequest(requirements.model_uri, requirements, geometry, state, {}, 3)
    expect(result.feature_task_id).toBe('one')
    expect(result.feature_task_updated_at).toBe(1)
    expect(result.millimeters_per_unit).toBe(2)
    expect(result.unified_state).toBeUndefined()
  })
  it('allows compatible legacy models without geometry', () => {
    const result = buildWorldModelRequest(requirements.model_uri, { ...requirements, use_fusion: false }, null, state, {}, 3)
    expect(result.feature_task_id).toBeUndefined()
  })
  it('requires all feedback fields and converts Celsius to the documented temperature channel', () => {
    const feedback = { ...requirements, state_feedback: true, feedback_fields: ['chatter_probability', 'tool_wear', 'surface_roughness', 'vibration_rms', 'temperature_over_100c', 'cutting_force_norm', 'mrr_norm', 'confidence'] }
    expect(() => buildWorldModelRequest(requirements.model_uri, feedback, geometry, state, {}, 3)).toThrow('粗糙度')
    const result = buildWorldModelRequest(requirements.model_uri, feedback, geometry,
      { ...state, surface_roughness: 1, cutting_force_norm: 0.2, mrr_norm: 0.1, confidence: 0.9 }, {}, 3)
    expect(result.current_state.temperature_over_100c).toBe(0.25)
  })
  it('blocks missing, stale, incompatible, or invalid inputs', () => {
    expect(() => buildWorldModelRequest(requirements.model_uri, null, geometry, state, {}, 3)).toThrow('模型')
    expect(() => buildWorldModelRequest('changed', requirements, geometry, state, {}, 3)).toThrow('模型')
    expect(() => buildWorldModelRequest(requirements.model_uri, requirements, null, state, {}, 3)).toThrow('几何')
    expect(() => buildWorldModelRequest(requirements.model_uri, { ...requirements, feature_dim: 4 }, geometry, state, {}, 3)).toThrow('结构')
    expect(() => buildWorldModelRequest(requirements.model_uri, requirements, geometry, { ...state, tool_wear: undefined }, {}, 3)).toThrow('磨损')
    expect(() => buildWorldModelRequest(requirements.model_uri, requirements, geometry, { ...state, tool_wear: -1 }, {}, 3)).toThrow('负数')
    expect(() => buildWorldModelRequest(requirements.model_uri, { ...requirements, feedback_fields: ['chatter_probability'] }, geometry, { ...state, chatter_probability: 2 }, {}, 3)).toThrow('0到1')
  })
})

describe('input source lifecycle', () => {
  const scopes: ReturnType<typeof effectScope>[] = []
  function setup() {
    const scope = effectScope()
    scopes.push(scope)
    const uri = ref(requirements.model_uri)
    const inputs = scope.run(() => useWorldModelInputs(uri))!
    return { uri, inputs }
  }
  afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()); vi.resetAllMocks() })

  it('requires scale, loads geometry and invalidates it when changing parts', async () => {
    vi.mocked(getInputRequirements).mockResolvedValue(requirements)
    vi.mocked(prepareGeometry).mockResolvedValue(geometry)
    const { inputs } = setup()
    await inputs.inspectModel()
    await inputs.readGeometry()
    expect(inputs.error.value).toContain('选择')
    inputs.taskId.value = 'one'
    await inputs.readGeometry()
    expect(inputs.error.value).toContain('毫米')
    expect(prepareGeometry).not.toHaveBeenCalled()
    inputs.scale.value = 2
    await inputs.readGeometry()
    expect(inputs.geometry.value?.source.task_id).toBe('one')
    inputs.taskId.value = 'two'
    expect(inputs.geometry.value).toBeNull()
    expect(inputs.scale.value).toBeUndefined()
    await inputs.readGeometry()
    expect(prepareGeometry).toHaveBeenLastCalledWith('two', 1)
  })
  it('discards delayed model requirements from a previous selection', async () => {
    let finish!: (value: WorldModelInputRequirements) => void
    vi.mocked(getInputRequirements).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const { uri, inputs } = setup()
    const old = inputs.inspectModel()
    uri.value = 'model://world_model/new'
    vi.mocked(getInputRequirements).mockResolvedValueOnce({ ...requirements, model_uri: uri.value })
    await inputs.inspectModel()
    finish(requirements)
    await old
    expect(inputs.requirements.value?.model_uri).toBe(uri.value)
    expect(inputs.loading.value).toBe(false)
  })
  it('discards delayed geometry and shows current errors', async () => {
    vi.mocked(getInputRequirements).mockResolvedValue(requirements)
    let finish!: (value: WorldModelGeometryInput) => void
    vi.mocked(prepareGeometry).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const { inputs } = setup()
    await inputs.inspectModel()
    inputs.taskId.value = 'one'
    inputs.scale.value = 2
    const old = inputs.readGeometry()
    inputs.taskId.value = 'two'
    finish(geometry)
    await old
    expect(inputs.geometry.value).toBeNull()
    vi.mocked(prepareGeometry).mockRejectedValueOnce(new Error('网格已移除'))
    await inputs.readGeometry()
    expect(inputs.error.value).toContain('网格已移除')
    vi.mocked(getInputRequirements).mockRejectedValueOnce(new Error('权重不存在'))
    await inputs.inspectModel()
    expect(inputs.error.value).toContain('权重不存在')
    expect(inputs.requirements.value).toBeNull()
  })
})
