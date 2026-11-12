import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorldModelStore } from '@/stores/worldModel'
import type { WorldModelPredictRequest, WorldModelPredictResponse } from '@/contracts/world_model'
import http from '@/utils/http'

vi.mock('@/utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

describe('world model prediction state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('clears an earlier prediction when the next request fails', async () => {
    const store = useWorldModelStore()
    const previous = {
      predicted_trajectory: [], trajectory_metrics: {},
      model_info: { weights_source: 'checkpoint' },
    } as unknown as WorldModelPredictResponse
    const request: WorldModelPredictRequest = {
      model_uri: 'model://world_model/test', horizon: 2,
      current_state: { spindle_speed: 8000 }, candidate_action: { spindle_speed_delta: 0 },
    }
    vi.mocked(http.post).mockResolvedValueOnce({ data: { data: previous } })
    await store.predict(request)
    expect(store.lastPrediction?.model_info.weights_source).toBe('checkpoint')
    vi.mocked(http.post).mockRejectedValueOnce(new Error('模型缺少几何输入'))
    expect(await store.predict(request)).toBeNull()
    expect(store.lastPrediction).toBeNull()
    expect(store.error).toContain('几何输入')
    expect(store.predicting).toBe(false)
  })

  it('discards an in-flight prediction after its inputs are cleared', async () => {
    const store = useWorldModelStore()
    let finish!: (value: unknown) => void
    vi.mocked(http.post).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.predict({ model_uri: 'model://world_model/test', horizon: 2,
      current_state: { spindle_speed: 8000 }, candidate_action: { spindle_speed_delta: 0 } })
    store.clearLastPrediction()
    finish({ data: { data: { predicted_trajectory: [] } } })
    expect(await pending).toBeNull()
    expect(store.lastPrediction).toBeNull()
    expect(store.predicting).toBe(false)
  })
})
