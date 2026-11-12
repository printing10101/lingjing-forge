import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorldModel from '@/views/WorldModel.vue'
import WorldModelPredictPanel from '@/components/world_model/WorldModelPredictPanel.vue'
import { getInputRequirements, prepareGeometry } from '@/api/worldModel'
import { ACTION_FIELD_VALUES } from '@/contracts/world_model'

const store = vi.hoisted(() => ({ versions: [], currentVersion: null, versionPagination: { total: 0, limit: 50, offset: 0 },
  totalPages: 0, anyLoading: false, versionsLoading: false, versionLoading: false, predicting: false,
  error: null, lastPrediction: null, lastPredictionMaxChatter: 0, lastPredictionStepCount: 0,
  fetchVersions: vi.fn(async () => ({})), clearLastPrediction: vi.fn(), predict: vi.fn(async () => ({})),
}))
vi.mock('@/stores/worldModel', () => ({ useWorldModelStore: () => store }))
vi.mock('@/api/worldModel', () => ({ getInputRequirements: vi.fn(), prepareGeometry: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getInputRequirements).mockImplementation(async model_uri => ({ model_uri, use_fusion: true,
    feature_dim: 4, state_feedback: false, feedback_fields: [], training_history_steps: [2],
    geometry_sources: [{ task_id: 'one', name: 'one', calibrated: true, updated_at: 1 },
      { task_id: 'two', name: 'two', calibrated: true, updated_at: 2 }] }))
  vi.mocked(prepareGeometry).mockResolvedValue({ geometry: { bbox_dimensions: [1, 2, 3], feature_vector: [0, 0, 0, 0], symmetry_score: 0, complexity_score: 0 },
    source: { task_id: 'one', name: 'one', updated_at: 1, feature_count: 1, scale_source: 'upstream_calibration', millimeters_per_unit: 1 }, notes: [] })
})

describe('WorldModel history import wiring', () => {
  it('imports the selected file, locks manual states, predicts full history and invalidates on part change', async () => {
    const wrapper = mount(WorldModel, { global: { stubs: {
      WorldModelVersionList: true, WorldModelVersionDetail: true, WorldModelPredictPanel: true,
      ElSwitch: { props: ['modelValue'], emits: ['update:modelValue'],
        template: '<input class="history-toggle" type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />' },
    } } })
    try {
      await flushPromises()
      await wrapper.find('select').setValue('one')
      const read = wrapper.findAll('button').find(button => button.text().includes('读取所选零件几何'))!
      await read.trigger('click'); await flushPromises()
      await wrapper.find('.history-toggle').setValue(true)
      const panel = wrapper.findComponent(WorldModelPredictPanel)
      expect(panel.props('predictionDisabled')).toBe(true)
      const history = { schema_version: 1, source: 'UI test synthetic', sample_period_seconds: 0.1,
        samples: [0, 1].map(index => ({ time_seconds: index * 0.1,
          current_state: { spindle_speed: index + 1, feed_rate: 2, depth_of_cut: 0.5, tool_wear: 0.1, vibration_rms: 0.2, temperature: 25 },
          action: Object.fromEntries(ACTION_FIELD_VALUES.map(field => [field, index * 0.1])) })) }
      const file = { name: 'history.json', size: 500, text: async () => JSON.stringify(history) }
      const input = wrapper.find('input[type=file]')
      Object.defineProperty(input.element, 'files', { value: [file] })
      await input.trigger('change'); await flushPromises()
      expect(wrapper.text()).toContain('UI test synthetic')
      expect(panel.props('currentState')).toEqual(history.samples[1].current_state)
      expect(panel.props('stateReadOnly')).toBe(true)
      expect(panel.props('predictionDisabled')).toBe(false)
      panel.vm.$emit('predict'); await flushPromises()
      expect(store.predict).toHaveBeenCalledWith(expect.objectContaining({ current_state: {}, fusion_history: history, feature_task_id: 'one' }))
      await wrapper.find('select').setValue('two'); await flushPromises()
      expect(panel.props('stateReadOnly')).toBe(false)
      expect(panel.props('predictionDisabled')).toBe(true)
      expect(wrapper.text()).not.toContain('UI test synthetic')
    } finally { wrapper.unmount() }
  })
})
